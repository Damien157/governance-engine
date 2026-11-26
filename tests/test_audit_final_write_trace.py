"""A failed final-decision audit write leaves its own trace.

When the stack tightens the ops decision but the linked ``stack_tightened``
row cannot be written, the stack stays BLOCK and records the failure:
first as a minimal signed row (``result=stack_final_write_failed``) in the
same hash chain, else as a hashed, linked line in an append-only fallback
file. govern() never raises and never loosens.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import multiprocessing
import os
import stat
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in reversed((ROOT / "src", ROOT, ROOT / "hais", ROOT / "imprint" / "src", ROOT / "haven2" / "src")):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from certified_governance_unified import CryptoEngine  # noqa: E402
from governed_stack import GovernedStack  # noqa: E402

CONTROL = {"action": "control", "payload": {"text": "step"}, "u_nom": 5.0}
SENTINEL = "SECRET-VALUE-123"


def _mp_append(path, n, tag):
    """Child-process worker: append n fallback records to the same file."""
    for i in range(n):
        GovernedStack._append_fallback_record(path, {"event": "stack_final_write_failed", "w": tag, "i": i})


class TestAuditFinalWriteTrace(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.crypto = CryptoEngine(private_key_path=None)

    def make_stack(self, extra_config=None) -> GovernedStack:
        d = tempfile.mkdtemp()
        cfg = {"db_path": os.path.join(d, "a.db"), "signing_key_path": os.path.join(d, "k.pem"), "log_level": 40}
        cfg.update(extra_config or {})
        s = GovernedStack(config=cfg, crypto=self.crypto)
        storage = s.engine.storage
        self.addCleanup(storage.close)
        orig = s.haven2.step

        def step(**kw):
            r = orig(**kw)
            try:
                r.open = True
            except Exception:
                object.__setattr__(r, "open", True)
            return r

        s.haven2.step = step

        async def fake(intent, token, **kw):
            eid = storage.log_decision(
                intent=intent, decision="ALLOW", result="ops", verification_score=0.9,
                risk_signal=0.0, anomaly_signal=0.0, policy_reasons=[], enqueue_review=False,
            )
            return {"status": "ok", "result": {"decision": "ALLOW", "policy_reasons": [],
                                               "entry_id": eid, "risk_signal": 0.0, "logged": True}}

        s.engine.execute_governed_action = fake
        real_qp = s._run_qp_step

        def degraded(intent):
            out = real_qp(intent)
            out.update(status="cbf_only_after:unknown", feasible=True, fail_closed=False)
            return out

        s._run_qp_step = degraded
        return s

    def fail_calls(self, s, which, message=None):
        """Make log_decision raise on the given call numbers (1 = ops row)."""
        storage = s.engine.storage
        real = storage.log_decision
        calls = {"n": 0}

        def flaky(*a, **kw):
            calls["n"] += 1
            if calls["n"] in which:
                raise RuntimeError(message or f"disk full #{calls['n']}")
            return real(*a, **kw)

        storage.log_decision = flaky

    def rows(self, s):
        cur = s.engine.storage.conn.cursor()
        cur.execute("SELECT id, decision, result, intent_envelope FROM audit_log ORDER BY timestamp, rowid")
        return cur.fetchall()

    @staticmethod
    def signed_intent(row):
        return json.loads(base64.b64decode(json.loads(row[3])["data"]))

    async def test_primary_fails_trace_row_in_chain(self):
        s = self.make_stack()
        self.fail_calls(s, {2})
        env = await s.govern(dict(CONTROL), s.issue_token("alice", "user"))
        self.assertEqual(env["decision"], "BLOCK")
        self.assertIn("audit_final_write_failed", env["reasons"])
        self.assertNotIn("final_entry_id", env["audit"])
        trace = env["audit"]["final_write_trace"]
        self.assertEqual(trace["kind"], "audit_row")
        rows = self.rows(s)
        self.assertEqual([(r[1], r[2]) for r in rows], [("ALLOW", "ops"), ("BLOCK", "stack_final_write_failed")])
        self.assertEqual(rows[1][0], trace["entry_id"])
        facts = self.signed_intent(rows[1])
        self.assertEqual(facts["stack_final_write_failed_for"], env["entry_id"])
        self.assertEqual((facts["from"], facts["to"]), ("ALLOW", "BLOCK"))
        self.assertEqual(facts["error_class"], "RuntimeError")
        self.assertEqual(facts["overrides_entry_hash"], s.engine.storage.entry_hash_of(env["entry_id"]))
        self.assertTrue(s.engine.storage.verify_chain()["valid"])
        cur = s.engine.storage.conn.cursor()
        cur.execute("SELECT COUNT(*) FROM review_queue WHERE entry_id = ?", (trace["entry_id"],))
        self.assertEqual(cur.fetchone()[0], 0)
        self.assertFalse(os.path.exists(s._fallback_path()))

    async def test_failed_commit_does_not_corrupt_chain(self):
        # The primary write gets as far as INSERT, then commit fails: the
        # half-written row must be rolled back, not committed with the trace.
        s = self.make_stack()
        storage = s.engine.storage
        real = storage.log_decision
        calls = {"n": 0}

        class BadCommitConn:
            def __init__(self, c):
                self._c = c

            def commit(self):
                raise RuntimeError("commit failed")

            def __getattr__(self, name):
                return getattr(self._c, name)

        def flaky(*a, **kw):
            calls["n"] += 1
            if calls["n"] == 2:
                real_conn = storage._local.conn
                storage._local.conn = BadCommitConn(real_conn)
                try:
                    return real(*a, **kw)
                finally:
                    storage._local.conn = real_conn
            return real(*a, **kw)

        storage.log_decision = flaky
        env = await s.govern(dict(CONTROL), s.issue_token("alice", "user"))
        self.assertEqual(env["decision"], "BLOCK")
        self.assertEqual(env["audit"]["final_write_trace"]["kind"], "audit_row")
        self.assertEqual([r[2] for r in self.rows(s)], ["ops", "stack_final_write_failed"])
        self.assertTrue(storage.verify_chain()["valid"])

    async def test_both_fail_fallback_record_still_block(self):
        s = self.make_stack()
        tip_before = None
        self.fail_calls(s, {2, 3})
        env = await s.govern(dict(CONTROL), s.issue_token("alice", "user"))
        self.assertEqual(env["decision"], "BLOCK")
        self.assertIn("audit_final_write_failed", env["reasons"])
        trace = env["audit"]["final_write_trace"]
        self.assertEqual(trace["kind"], "fallback_file")
        self.assertIn("trace_row_error", trace)
        path = s._fallback_path()
        self.assertEqual(trace["path"], path)
        self.assertEqual(len(self.rows(s)), 1)
        tip_before = s.engine.storage.entry_hash_of(env["entry_id"])
        with open(path, encoding="utf-8") as fh:
            lines = [json.loads(x) for x in fh if x.strip()]
        self.assertEqual(len(lines), 1)
        rec = lines[0]["record"]
        self.assertEqual(lines[0]["sha256"], trace["sha256"])
        self.assertEqual(rec["stack_final_write_failed_for"], env["entry_id"])
        self.assertEqual(rec["chain_tip"], tip_before)
        self.assertEqual(rec["prev_sha256"], GovernedStack.FALLBACK_GENESIS)
        self.assertEqual((rec["from"], rec["to"]), ("ALLOW", "BLOCK"))
        self.assertTrue(GovernedStack.verify_fallback_file(path)["valid"])
        self.assertTrue(s.engine.storage.verify_chain()["valid"])

    async def test_fallback_lines_link_and_detect_tamper(self):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "fb.jsonl")
        s = self.make_stack({"audit_fallback_path": path})
        self.fail_calls(s, {2, 3, 5, 6})
        e1 = await s.govern(dict(CONTROL), s.issue_token("alice", "user"))
        e2 = await s.govern(dict(CONTROL), s.issue_token("alice", "user"))
        self.assertEqual((e1["decision"], e2["decision"]), ("BLOCK", "BLOCK"))
        self.assertEqual(e2["audit"]["final_write_trace"]["path"], path)
        v = GovernedStack.verify_fallback_file(path)
        self.assertEqual(v, {"valid": True, "records": 2})
        with open(path, encoding="utf-8") as fh:
            lines = fh.readlines()
        doc = json.loads(lines[0])
        doc["record"]["to"] = "ALLOW"
        lines[0] = json.dumps(doc, sort_keys=True) + "\n"
        with open(path, "w", encoding="utf-8") as fh:
            fh.writelines(lines)
        self.assertFalse(GovernedStack.verify_fallback_file(path)["valid"])

    async def test_fallback_unavailable_never_raises(self):
        s = self.make_stack({"audit_fallback_path": os.path.join(tempfile.mkdtemp(), "no", "such", "dir.jsonl")})
        self.fail_calls(s, {2, 3})
        env = await s.govern(dict(CONTROL), s.issue_token("alice", "user"))
        self.assertEqual(env["decision"], "BLOCK")
        trace = env["audit"]["final_write_trace"]
        self.assertEqual(trace["kind"], "lost")
        self.assertIn("fallback_error", trace)
        self.assertEqual(trace["record"]["stack_final_write_failed_for"], env["entry_id"])
        self.assertTrue(any(n.startswith("audit_final_trace_lost:") for n in env["notes"]))

    async def test_trace_row_tamper_breaks_chain(self):
        s = self.make_stack()
        self.fail_calls(s, {2})
        env = await s.govern(dict(CONTROL), s.issue_token("alice", "user"))
        tid = env["audit"]["final_write_trace"]["entry_id"]
        row = [r for r in self.rows(s) if r[0] == tid][0]
        facts = self.signed_intent(row)
        facts["to"] = "ALLOW"
        forged = json.dumps({"data": base64.b64encode(json.dumps(facts).encode()).decode()})
        st = s.engine.storage
        st.conn.execute("UPDATE audit_log SET intent_envelope = ? WHERE id = ?", (forged, tid))
        st.conn.commit()
        self.assertFalse(st.verify_chain()["valid"])

    async def test_trace_row_decision_tamper_breaks_chain(self):
        s = self.make_stack()
        self.fail_calls(s, {2})
        env = await s.govern(dict(CONTROL), s.issue_token("alice", "user"))
        tid = env["audit"]["final_write_trace"]["entry_id"]
        st = s.engine.storage
        st.conn.execute("UPDATE audit_log SET decision = 'ALLOW' WHERE id = ?", (tid,))
        st.conn.commit()
        self.assertFalse(st.verify_chain()["valid"])

    async def test_healthy_path_unchanged(self):
        s = self.make_stack()
        env = await s.govern(dict(CONTROL), s.issue_token("alice", "user"))
        self.assertEqual(env["decision"], "BLOCK")
        self.assertIn("final_entry_id", env["audit"])
        self.assertNotIn("final_write_trace", env["audit"])
        self.assertNotIn("final_write_error", env["audit"])
        self.assertNotIn("audit_final_write_failed", env["reasons"])
        self.assertEqual([r[2] for r in self.rows(s)], ["ops", "stack_tightened"])
        self.assertFalse(os.path.exists(s._fallback_path()))
        self.assertTrue(s.engine.storage.verify_chain()["valid"])

    # ---- review follow-up (Damien, t188u) ---------------------------------

    def assert_fallback_unforked(self, path, expected):
        v = GovernedStack.verify_fallback_file(path)
        self.assertEqual(v, {"valid": True, "records": expected})
        with open(path, encoding="utf-8") as fh:
            recs = [json.loads(x)["record"] for x in fh if x.strip()]
        prevs = [r["prev_sha256"] for r in recs]
        self.assertEqual(len(prevs), len(set(prevs)), "two records share a prev_sha256 (fork)")

    async def test_tier3_read_only_dir_is_lost_never_raises(self):
        d = tempfile.mkdtemp()
        ro = os.path.join(d, "ro")
        os.mkdir(ro)
        os.chmod(ro, 0o500)
        self.addCleanup(os.chmod, ro, 0o700)
        if os.access(ro, os.W_OK):  # e.g. running as root
            self.skipTest("read-only directory is writable for this user")
        s = self.make_stack({"audit_fallback_path": os.path.join(ro, "fb.jsonl")})
        self.fail_calls(s, {2, 3})
        env = await s.govern(dict(CONTROL), s.issue_token("alice", "user"))
        self.assertEqual(env["decision"], "BLOCK")
        trace = env["audit"]["final_write_trace"]
        self.assertEqual(trace["kind"], "lost")
        self.assertEqual(trace["fallback_error"]["error_class"], "PermissionError")
        self.assertEqual(trace["record"]["stack_final_write_failed_for"], env["entry_id"])
        self.assertFalse(os.path.exists(os.path.join(ro, "fb.jsonl")))
        self.assertTrue(any(n.startswith("audit_final_trace_lost:PermissionError:") for n in env["notes"]))

    async def test_nothing_escapes_govern_on_every_tier(self):
        # Each tier fails in turn, including a non-RuntimeError and an
        # exception from the fallback path itself; govern() returns BLOCK.
        cases = [({2}, None, "audit_row"), ({2, 3}, None, "fallback_file"), ({2, 3}, "ro", "lost")]
        for which, mode, kind in cases:
            with self.subTest(which=which, kind=kind):
                d = tempfile.mkdtemp()
                path = os.path.join(d, "fb.jsonl")
                s = self.make_stack({"audit_fallback_path": path})
                if mode == "ro":
                    os.chmod(d, 0o500)
                    self.addCleanup(os.chmod, d, 0o700)
                    if os.access(d, os.W_OK):
                        self.skipTest("read-only directory is writable for this user")
                self.fail_calls(s, which)
                try:
                    env = await s.govern(dict(CONTROL), s.issue_token("alice", "user"))
                except BaseException as e:  # pragma: no cover - the failure we guard against
                    self.fail(f"govern() raised {type(e).__name__}")
                self.assertEqual(env["decision"], "BLOCK")
                self.assertIn("audit_final_write_failed", env["reasons"])
                self.assertEqual(env["audit"]["final_write_trace"]["kind"], kind)

    async def test_fallback_file_mode_0600_and_configured_path(self):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "custom", "fb.jsonl")
        os.mkdir(os.path.dirname(path))
        s = self.make_stack({"audit_fallback_path": path})
        self.assertEqual(s._fallback_path(), path)
        self.fail_calls(s, {2, 3})
        old = os.umask(0)  # even with a permissive umask the file is 0600
        try:
            env = await s.govern(dict(CONTROL), s.issue_token("alice", "user"))
        finally:
            os.umask(old)
        self.assertEqual(env["audit"]["final_write_trace"]["path"], path)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        # A pre-existing looser file is tightened on the next append.
        os.chmod(path, 0o644)
        GovernedStack._append_fallback_record(path, {"event": "x"})
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assert_fallback_unforked(path, 2)

    async def test_concurrent_failures_threads_do_not_fork_chain(self):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "fb.jsonl")
        n_threads, per = 8, 25
        barrier = threading.Barrier(n_threads)

        def worker(t):
            barrier.wait()
            for i in range(per):
                GovernedStack._append_fallback_record(path, {"event": "stack_final_write_failed", "t": t, "i": i})

        ts = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assert_fallback_unforked(path, n_threads * per)

    async def test_concurrent_failures_threads_via_govern(self):
        # Several stacks (own DBs) share one fallback file; both chain
        # writes fail in each, concurrently from threads.
        import asyncio
        d = tempfile.mkdtemp()
        path = os.path.join(d, "fb.jsonl")
        stacks = []
        for _ in range(4):
            s = self.make_stack({"audit_fallback_path": path})
            self.fail_calls(s, {2, 3, 5, 6, 8, 9})
            stacks.append(s)
        envs = []

        def run(s):
            for _ in range(3):
                envs.append(asyncio.run(s.govern(dict(CONTROL), s.issue_token("alice", "user"))))

        ts = [threading.Thread(target=run, args=(s,)) for s in stacks]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(len(envs), 12)
        self.assertTrue(all(e["decision"] == "BLOCK" for e in envs))
        self.assertTrue(all(e["audit"]["final_write_trace"]["kind"] == "fallback_file" for e in envs))
        self.assert_fallback_unforked(path, 12)

    async def test_concurrent_failures_processes_do_not_fork_chain(self):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "fb.jsonl")
        ctx = multiprocessing.get_context("spawn")
        n_procs, per = 4, 30
        procs = [ctx.Process(target=_mp_append, args=(path, per, k)) for k in range(n_procs)]
        for p in procs:
            p.start()
        for p in procs:
            p.join(120)
        self.assertEqual([p.exitcode for p in procs], [0] * n_procs)
        self.assert_fallback_unforked(path, n_procs * per)

    async def test_raw_exception_text_never_copied(self):
        # The injected storage exception carries a sentinel; it must not
        # appear in the trace, the fallback file, any DB row or the logs.
        logger = logging.getLogger("governed_stack.decisions")
        buf = io.StringIO()
        handler = logging.StreamHandler(buf)
        handler.setLevel(logging.DEBUG)
        old_level, old_disabled = logger.level, logger.disabled
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        logger.disabled = False
        self.addCleanup(logger.removeHandler, handler)
        self.addCleanup(logger.setLevel, old_level)
        self.addCleanup(setattr, logger, "disabled", old_disabled)
        expected = GovernedStack._error_digest(RuntimeError(SENTINEL))

        for which, kind in (({2}, "audit_row"), ({2, 3}, "fallback_file"), ({2, 3}, "lost")):
            with self.subTest(kind=kind):
                d = tempfile.mkdtemp()
                path = os.path.join(d, "fb.jsonl") if kind != "lost" else os.path.join(d, "no", "dir", "fb.jsonl")
                s = self.make_stack({"audit_fallback_path": path})
                self.fail_calls(s, which, message=SENTINEL)
                env = await s.govern(dict(CONTROL), s.issue_token("alice", "user"))
                self.assertEqual(env["decision"], "BLOCK")
                trace = env["audit"]["final_write_trace"]
                self.assertEqual(trace["kind"], kind)
                self.assertNotIn(SENTINEL, json.dumps(trace, default=str))
                # Envelope, minus the two pre-existing #36 fields that still
                # carry str(exc) (audit.final_write_error and the
                # "audit_final_write_failed:<exc>" note): follow-up on #36.
                env_scrubbed = json.loads(json.dumps(env, default=str))
                env_scrubbed["audit"].pop("final_write_error", None)
                env_scrubbed["notes"] = [n for n in env_scrubbed.get("notes") or []
                                         if not n.startswith("audit_final_write_failed:")]
                self.assertNotIn(SENTINEL, json.dumps(env_scrubbed, default=str))
                if kind != "audit_row":
                    self.assertEqual(trace["trace_row_error"], expected)
                if kind == "fallback_file":
                    with open(path, encoding="utf-8") as fh:
                        body = fh.read()
                    self.assertNotIn(SENTINEL, body)
                    rec = json.loads(body.splitlines()[0])["record"]
                    self.assertEqual(rec["primary_error"], expected)
                    self.assertEqual(rec["trace_row_error"], expected)
                if kind == "lost":
                    self.assertEqual(trace["record"]["primary_error"], expected)
                cur = s.engine.storage.conn.cursor()
                cur.execute("SELECT * FROM audit_log")
                for row in cur.fetchall():
                    blob = " ".join(str(c) for c in row)
                    self.assertNotIn(SENTINEL, blob)
                    for c in row:  # base64-wrapped signed intent
                        if isinstance(c, str) and '"data"' in c:
                            inner = base64.b64decode(json.loads(c)["data"]).decode()
                            self.assertNotIn(SENTINEL, inner)
                if kind == "audit_row":
                    facts = self.signed_intent([r for r in self.rows(s) if r[0] == trace["entry_id"]][0])
                    self.assertEqual(facts["error_sha256"], expected["error_sha256"])
        handler.flush()
        logs = buf.getvalue()
        self.assertIn("audit_final_write_failed", logs)
        self.assertNotIn(SENTINEL, logs)

    async def test_first_audit_write_failure_current_behaviour(self):
        # Documents (does not change) what happens when the engine's OWN
        # first log_decision fails: certified_governance_unified.py fails
        # closed to BLOCK with policy reason audit_log_unavailable,
        # entry_id=None, logged=False. No audit row and no fallback line is
        # written for it; the #38 trace only covers the linked final write.
        d = tempfile.mkdtemp()
        cfg = {"db_path": os.path.join(d, "a.db"), "signing_key_path": os.path.join(d, "k.pem"), "log_level": 40,
               "audit_fallback_path": os.path.join(d, "fb.jsonl")}
        s = GovernedStack(config=cfg, crypto=self.crypto)
        self.addCleanup(s.engine.storage.close)

        def boom(*a, **kw):
            raise RuntimeError(SENTINEL)

        s.engine.storage.log_decision = boom
        env = await s.govern({"action": "read", "payload": {"text": "hello"}}, s.issue_token("alice", "user"))
        self.assertEqual(env["decision"], "BLOCK")
        self.assertIn("audit_log_unavailable", env["reasons"])
        self.assertIsNone(env["entry_id"])
        self.assertFalse(env["audit"]["logged"])
        self.assertNotIn("final_write_trace", env["audit"])
        self.assertEqual(self.rows(s), [])
        self.assertFalse(os.path.exists(cfg["audit_fallback_path"]))


if __name__ == "__main__":
    unittest.main()
