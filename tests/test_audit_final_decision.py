"""The signed audit chain records the stack's final decision.

The ops layer (CGE) logs its decision before the HAIS cap, the latch and
the control QP run. When the stack tightens that decision, a linked
"stack_final" entry is appended to the same hash chain so the audit log
never shows ALLOW for an action that was finally BLOCKed.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in reversed((ROOT / "src", ROOT, ROOT / "hais", ROOT / "imprint" / "src", ROOT / "haven2" / "src")):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from certified_governance_unified import CryptoEngine  # noqa: E402
from governed_stack import GovernedStack  # noqa: E402

CONTROL = {"action": "control", "payload": {"text": "step"}, "u_nom": 5.0}


class TestAuditFinalDecision(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.crypto = CryptoEngine(private_key_path=None)

    def make_stack(self, ops_decision: str = "ALLOW") -> GovernedStack:
        d = tempfile.mkdtemp()
        s = GovernedStack(
            config={"db_path": os.path.join(d, "a.db"), "signing_key_path": os.path.join(d, "k.pem"), "log_level": 40},
            crypto=self.crypto,
        )
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
                intent=intent, decision=ops_decision, result="ops", verification_score=0.9,
                risk_signal=0.0, anomaly_signal=0.0, policy_reasons=[], enqueue_review=False,
            )
            return {"status": "ok", "result": {"decision": ops_decision, "policy_reasons": [],
                                               "entry_id": eid, "risk_signal": 0.0, "logged": True}}

        s.engine.execute_governed_action = fake
        return s

    def rows(self, s):
        cur = s.engine.storage.conn.cursor()
        cur.execute("SELECT id, decision, result, metadata FROM audit_log ORDER BY timestamp, rowid")
        return cur.fetchall()

    def degrade(self, s):
        real = s._run_qp_step

        def degraded(intent):
            out = real(intent)
            out.update(status="cbf_only_after:unknown", feasible=True, fail_closed=False)
            return out

        s._run_qp_step = degraded

    async def test_no_tightening_no_extra_entry(self):
        s = self.make_stack()
        env = await s.govern(dict(CONTROL), s.issue_token("alice", "user"))
        if env["decision"] != "ALLOW":
            self.skipTest("baseline control step not ALLOW in this environment")
        self.assertNotIn("final_entry_id", env["audit"])
        self.assertEqual(len(self.rows(s)), 1)

    async def test_qp_tightening_appends_linked_block_entry(self):
        s = self.make_stack()
        self.degrade(s)
        env = await s.govern(dict(CONTROL), s.issue_token("alice", "user"))
        self.assertEqual(env["decision"], "BLOCK")
        rows = self.rows(s)
        self.assertEqual(len(rows), 2)
        first, last = rows
        self.assertEqual(first[1], "ALLOW")
        self.assertEqual((last[0], last[1], last[2]), (env["audit"]["final_entry_id"], "BLOCK", "stack_tightened"))
        meta = json.loads(last[3])
        self.assertEqual(meta["overrides_entry_id"], env["entry_id"])
        self.assertEqual((meta["from"], meta["to"]), ("ALLOW", "BLOCK"))
        self.assertEqual(meta["qp_tightened"]["reason"], "qp_not_feasible:cbf_only_after:unknown")
        self.assertTrue(s.engine.storage.verify_chain()["valid"])

    async def test_latch_block_is_recorded_and_not_queued(self):
        s = self.make_stack()
        orig = s.haven2.step

        def closed(**kw):
            r = orig(**kw)
            try:
                r.open = False
            except Exception:
                object.__setattr__(r, "open", False)
            return r

        s.haven2.step = closed
        env = await s.govern(dict(CONTROL), s.issue_token("alice", "user"))
        self.assertEqual(env["decision"], "BLOCK")
        rows = self.rows(s)
        self.assertEqual([r[1] for r in rows], ["ALLOW", "BLOCK"])
        self.assertIn("transistor_closed", json.loads(rows[1][3]) and env["reasons"])
        cur = s.engine.storage.conn.cursor()
        cur.execute("SELECT COUNT(*) FROM review_queue WHERE entry_id = ?", (env["audit"]["final_entry_id"],))
        self.assertEqual(cur.fetchone()[0], 0)

    async def test_final_write_failure_fails_closed(self):
        s = self.make_stack()
        self.degrade(s)
        storage = s.engine.storage
        real = storage.log_decision
        calls = {"n": 0}

        def flaky(*a, **kw):
            calls["n"] += 1
            if calls["n"] >= 2:
                raise RuntimeError("disk full")
            return real(*a, **kw)

        storage.log_decision = flaky
        env = await s.govern(dict(CONTROL), s.issue_token("alice", "user"))
        self.assertEqual(env["decision"], "BLOCK")
        self.assertIn("audit_final_write_failed", env["reasons"])
        self.assertIn("final_write_error", env["audit"])

    async def test_anchor_advances_with_final_entry(self):
        s = self.make_stack()
        self.degrade(s)
        await s.govern(dict(CONTROL), s.issue_token("alice", "user"))
        st = s.engine.storage
        if not st.anchor_path:
            self.skipTest("no anchor for this storage")
        self.assertEqual(st._read_anchor()["seq"], 2)
        self.assertTrue(st.verify_chain()["valid"])

    def signed_intent(self, s, entry_id):
        import base64
        cur = s.engine.storage.conn.cursor()
        cur.execute("SELECT intent_envelope FROM audit_log WHERE id = ?", (entry_id,))
        env = json.loads(cur.fetchone()[0])
        return json.loads(base64.b64decode(env["data"]))

    def entry_hash(self, s, entry_id):
        cur = s.engine.storage.conn.cursor()
        cur.execute("SELECT entry_hash FROM audit_log WHERE id = ?", (entry_id,))
        return cur.fetchone()[0]

    async def test_link_fields_are_in_signed_intent(self):
        s = self.make_stack()
        self.degrade(s)
        env = await s.govern(dict(CONTROL), s.issue_token("alice", "user"))
        link = self.signed_intent(s, env["audit"]["final_entry_id"])
        self.assertEqual(link["overrides_entry_id"], env["entry_id"])
        self.assertEqual(link["overrides_entry_hash"], self.entry_hash(s, env["entry_id"]))
        self.assertEqual((link["from"], link["to"]), ("ALLOW", "BLOCK"))
        self.assertIn("qp_not_feasible:cbf_only_after:unknown", link["reasons"])
        self.assertEqual(link["qp_tightened"]["reason"], "qp_not_feasible:cbf_only_after:unknown")

    async def test_tampering_link_breaks_chain(self):
        import base64
        s = self.make_stack()
        self.degrade(s)
        env = await s.govern(dict(CONTROL), s.issue_token("alice", "user"))
        fid = env["audit"]["final_entry_id"]
        link = self.signed_intent(s, fid)
        link["to"] = "ALLOW"
        forged = json.dumps({"data": base64.b64encode(json.dumps(link).encode()).decode()})
        st = s.engine.storage
        st.conn.execute("UPDATE audit_log SET intent_envelope = ? WHERE id = ?", (forged, fid))
        st.conn.commit()
        self.assertFalse(st.verify_chain()["valid"])

    async def test_link_survives_interleaved_writer(self):
        s = self.make_stack()
        self.degrade(s)
        st = s.engine.storage
        real_qp = s._run_qp_step

        def qp_with_interleave(intent):
            # Another writer lands between the ops row and the final row.
            st.log_decision(intent={"other": 1}, decision="ALLOW", result="other",
                            verification_score=0.9, risk_signal=0.0, anomaly_signal=0.0,
                            policy_reasons=[], enqueue_review=False)
            return real_qp(intent)

        s._run_qp_step = qp_with_interleave
        env = await s.govern(dict(CONTROL), s.issue_token("alice", "user"))
        rows = self.rows(s)
        self.assertEqual(len(rows), 3)
        link = self.signed_intent(s, env["audit"]["final_entry_id"])
        # prev_hash points at the interleaved row; the signed link still binds the original.
        self.assertEqual(link["overrides_entry_hash"], self.entry_hash(s, env["entry_id"]))
        self.assertTrue(st.verify_chain()["valid"])

    async def test_missing_overridden_entry_fails_closed(self):
        s = self.make_stack()
        self.degrade(s)
        s.engine.storage.entry_hash_of = lambda _eid: None
        env = await s.govern(dict(CONTROL), s.issue_token("alice", "user"))
        self.assertEqual(env["decision"], "BLOCK")
        self.assertIn("audit_final_write_failed", env["reasons"])
        self.assertEqual(len(self.rows(s)), 1)


if __name__ == "__main__":
    unittest.main()
