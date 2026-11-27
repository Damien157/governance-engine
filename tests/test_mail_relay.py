"""Draft-only mailbox relay: sealed spool -> Gmail / Outlook Drafts (fakes only).

No network and no real credentials: providers are exercised through injected
fake transports (the real Gmail/Graph client code runs, the HTTP layer is a
recorder), or through a fake DraftProvider.
"""

from __future__ import annotations

import base64
import email
import importlib.util
import json
import logging
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
from email.policy import default as default_policy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in reversed((ROOT / "src", ROOT, ROOT / "hais", ROOT / "imprint" / "src", ROOT / "haven2" / "src")):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from certified_governance_unified import CryptoEngine  # noqa: E402

from governed_stack import GovernedMail, GovernedStack  # noqa: E402
from governed_stack import mail_relay as relay_mod  # noqa: E402
from governed_stack.mail_relay import (  # noqa: E402
    ALREADY_UPLOADED,
    NOT_ATTEMPTED,
    OUTCOME_UNKNOWN,
    REFUSED,
    REJECTED,
    RETRY_LATER,
    SKIPPED,
    STATE_FILE,
    UPLOADED,
    MailRelay,
    PartnerConfig,
)
from governed_stack.mail_relay_providers import (  # noqa: E402
    GMAIL_DRAFTS_CREATE_URL,
    GRAPH_CREATE_MESSAGE_URL,
    GmailDraftsClient,
    GraphDraftsClient,
    ProviderError,
    RelayDraft,
    urllib_transport,
)
from governed_stack.sealed_mail import SealedMailAdapter, content_digest, spool_backend  # noqa: E402
from tests.test_governed_mail import _close_stack_storage, tmp_db  # noqa: E402

ME = "me@example.com"
YOU = "you@example.com"
TOKEN = "ya29.SECRET-TOKEN-MARKER-7f3a"
ERR_MARKER = "SECRET-ERROR-TEXT-MARKER-91bd"
BODY = "Are you free for lunch on Thursday? BODY-MARKER-55e1"
SUBJECT = "Lunch"


class FakeTransport:
    """Records requests; replies with a scripted (status, bytes) or raises."""

    def __init__(self, replies=None, *, delay=0.0):
        self.calls = []
        self.replies = list(replies or [])
        self.delay = delay
        self.lock = threading.Lock()

    def __call__(self, method, url, headers, body, timeout):
        with self.lock:
            self.calls.append({"method": method, "url": url, "headers": dict(headers), "body": body})
            n = len(self.calls)
            reply = self.replies.pop(0) if self.replies else (200, json.dumps({"id": f"d{n}"}).encode())
        if self.delay:
            time.sleep(self.delay)
        if isinstance(reply, BaseException):
            raise reply
        return reply


def _jwt(claims):
    seg = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()  # noqa: E731
    return f"{seg({'alg': 'none'})}.{seg(claims)}.sig"


class RelayCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.crypto = CryptoEngine(private_key_path=None)

    def setUp(self):
        db = tmp_db()
        self.addCleanup(lambda: os.path.exists(db) and os.remove(db))
        self.stack = GovernedStack(
            config={"db_path": db, "signing_key_path": db + ".pem", "log_level": 40},
            crypto=self.crypto,
        )
        self.addCleanup(lambda: _close_stack_storage(self.stack))
        self.storage = self.stack.engine.storage
        self.spool = Path(tempfile.mkdtemp(prefix="relay_spool_"))
        self.addCleanup(lambda: shutil.rmtree(self.spool, ignore_errors=True))
        self.adapter = SealedMailAdapter(spool_backend(self.spool), mail=GovernedMail(stack=self.stack))

    # helpers
    def spool_draft(self, to=ME, subject=SUBJECT, body=BODY, cc=None) -> Path:
        out = self.adapter.propose_draft_sync(to=to, subject=subject, body=body, cc=cc)
        self.assertEqual(out["decision"], "ALLOW", out)
        return Path(out["backend_result"]["spooled"])

    def relay(self, provider, allow=(ME, YOU), **kw) -> MailRelay:
        cfg = PartnerConfig.build(
            partner_id="pilot-1", spool_dir=self.spool, recipient_allowlist=list(allow), **kw
        )
        return MailRelay(config=cfg, provider=provider, storage=self.storage)

    def gmail(self, transport, token=TOKEN):
        return GmailDraftsClient(lambda: token, transport=transport)

    def graph(self, transport, token=TOKEN):
        return GraphDraftsClient(lambda: token, transport=transport)

    def relay_rows(self):
        rows = self.storage.conn.execute(
            "SELECT * FROM audit_log WHERE result LIKE 'mail_relay:%' ORDER BY rowid"
        ).fetchall()
        out = []
        for r in rows:
            d = {k: r[k] for k in r.keys()}
            d["intent"] = json.loads(base64.b64decode(json.loads(r["intent_envelope"])["data"]))
            d["meta"] = json.loads(r["metadata"])
            d["reasons"] = json.loads(r["policy_reasons"])
            out.append(d)
        return out

    def all_audit_text(self) -> str:
        rows = self.storage.conn.execute("SELECT * FROM audit_log WHERE result LIKE 'mail_relay:%'").fetchall()
        blob = json.dumps([{k: r[k] for k in r.keys()} for r in rows])
        decoded = " ".join(json.dumps(x["intent"]) for x in self.relay_rows())
        return blob + decoded

    def state(self):
        return json.loads((self.spool / STATE_FILE).read_text())


class TestHappyPaths(RelayCase):
    def test_gmail_happy_path_creates_one_draft_via_drafts_create(self):
        self.spool_draft(cc=[YOU])
        t = FakeTransport()
        out = self.relay(self.gmail(t)).run()
        self.assertEqual(out["status"], "ok", out)
        self.assertEqual([r["status"] for r in out["results"]], [UPLOADED])
        self.assertEqual(len(t.calls), 1)
        call = t.calls[0]
        self.assertEqual(call["method"], "POST")
        self.assertEqual(call["url"], GMAIL_DRAFTS_CREATE_URL)
        self.assertEqual(call["headers"]["Authorization"], f"Bearer {TOKEN}")
        raw = json.loads(call["body"])["message"]["raw"]
        msg = email.message_from_bytes(base64.urlsafe_b64decode(raw), policy=default_policy)
        self.assertEqual(msg["To"], ME)
        self.assertEqual(msg["Cc"], YOU)
        self.assertEqual(msg["Subject"], SUBJECT)
        self.assertEqual(msg.get_content().strip(), BODY)
        rows = self.relay_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["decision"], "ALLOW")
        self.assertEqual(rows[0]["meta"]["status"], UPLOADED)
        self.assertEqual(rows[0]["meta"]["provider"], "gmail")
        self.assertEqual(rows[0]["meta"]["recipient_count"], 2)
        self.assertTrue(self.storage.verify_chain()["valid"])

    def test_outlook_happy_path_posts_me_messages(self):
        self.spool_draft(cc=[YOU])
        t = FakeTransport([(201, b'{"id": "AAMkAD-msg"}')])
        tok = _jwt({"scp": "Mail.ReadWrite User.Read"})
        out = self.relay(self.graph(t, token=tok)).run()
        self.assertEqual([r["status"] for r in out["results"]], [UPLOADED], out)
        self.assertEqual(out["results"][0]["provider_ref"], "AAMkAD-msg")
        call = t.calls[0]
        self.assertEqual((call["method"], call["url"]), ("POST", GRAPH_CREATE_MESSAGE_URL))
        payload = json.loads(call["body"])
        self.assertEqual(payload["subject"], SUBJECT)
        self.assertEqual(payload["body"], {"contentType": "Text", "content": BODY})
        self.assertEqual(payload["toRecipients"], [{"emailAddress": {"address": ME}}])
        self.assertEqual(payload["ccRecipients"], [{"emailAddress": {"address": YOU}}])
        self.assertEqual(self.relay_rows()[0]["meta"]["provider"], "outlook")

    def test_graph_token_with_transmit_permission_refused_before_request(self):
        self.spool_draft()
        t = FakeTransport()
        tok = _jwt({"scp": "Mail.ReadWrite Mail." + "Send"})
        out = self.relay(self.graph(t, token=tok)).run()
        self.assertEqual(t.calls, [])
        self.assertEqual(out["results"][0]["status"], RETRY_LATER)
        self.assertIn("mail_relay:provider_credential_scope_refused", out["results"][0]["reasons"])

    def test_transport_refuses_any_other_endpoint(self):
        with self.assertRaises(ProviderError) as cm:
            urllib_transport("POST", GMAIL_DRAFTS_CREATE_URL + "/x", {}, b"", 1.0)
        self.assertEqual(cm.exception.kind, "refused_endpoint")
        with self.assertRaises(ProviderError):
            urllib_transport("GET", GMAIL_DRAFTS_CREATE_URL, {}, b"", 1.0)


class TestReverify(RelayCase):
    def _edit(self, path, **changes):
        d = json.loads(path.read_text())
        d.update(changes)
        path.write_text(json.dumps(d))

    def assert_refused(self, out, reason):
        self.assertEqual([r["status"] for r in out["results"]], [REFUSED], out)
        self.assertIn(reason, out["results"][0]["reasons"])

    def test_tampered_body_hash_mismatch_uploads_nothing(self):
        p = self.spool_draft()
        self._edit(p, body="Wire the money today")
        t = FakeTransport()
        out = self.relay(self.gmail(t)).run()
        self.assert_refused(out, "mail_relay:content_hash_mismatch")
        self.assertEqual(t.calls, [])
        rows = self.relay_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["decision"], "BLOCK")

    def test_tampered_body_with_recomputed_hash_fails_chain_binding(self):
        p = self.spool_draft()
        d = json.loads(p.read_text())
        d["body"] = "Wire the money today"
        d["content_sha256"] = content_digest(d["to"], d["subject"], d["body"], d["cc"])
        p.write_text(json.dumps(d))
        t = FakeTransport()
        out = self.relay(self.gmail(t)).run()
        self.assert_refused(out, "mail_relay:governed_content_mismatch")
        self.assertEqual(t.calls, [])

    def test_non_allow_decision_refused(self):
        p = self.spool_draft()
        self._edit(p, decision="REVIEW")
        t = FakeTransport()
        self.assert_refused(self.relay(self.gmail(t)).run(), "mail_relay:spool_decision_not_allow")
        self.assertEqual(t.calls, [])

    def test_governed_row_not_allow_refused(self):
        p = self.spool_draft()
        d = json.loads(p.read_text())
        # Forge a spool that points at a BLOCK row from the real gate.
        blocked = self.adapter.propose_draft_sync(to=ME, subject="Account", body="Here is your password")
        self.assertNotEqual(blocked["decision"], "ALLOW")
        d["entry_id"] = blocked["entry_id"]
        p.write_text(json.dumps(d))
        t = FakeTransport()
        self.assert_refused(self.relay(self.gmail(t)).run(), "mail_relay:governed_decision_not_allow")
        self.assertEqual(t.calls, [])

    def test_missing_governed_row_refused(self):
        p = self.spool_draft()
        self._edit(p, entry_id="00000000-0000-0000-0000-000000000000")
        t = FakeTransport()
        self.assert_refused(self.relay(self.gmail(t)).run(), "mail_relay:governed_row_missing")

    def test_extra_bcc_field_refused(self):
        p = self.spool_draft()
        self._edit(p, bcc=["attacker@evil.example"])
        t = FakeTransport()
        self.assert_refused(self.relay(self.gmail(t)).run(), "mail_relay:spool_unexpected_fields")
        self.assertEqual(t.calls, [])

    def test_unparseable_spool_refused_without_raw_error(self):
        (self.spool / "draft_1_0123456789ab.json").write_text("{not json " + ERR_MARKER)
        out = self.relay(self.gmail(FakeTransport())).run()
        self.assert_refused(out, "mail_relay:spool_unparseable")
        self.assertNotIn(ERR_MARKER, json.dumps(out))
        self.assertNotIn(ERR_MARKER, self.all_audit_text())

    def test_broken_audit_chain_uploads_nothing(self):
        self.spool_draft()
        self.storage.conn.execute("UPDATE audit_log SET decision='ALLOW', result='tampered'")
        self.storage.conn.commit()
        t = FakeTransport()
        out = self.relay(self.gmail(t)).run()
        self.assertEqual(out["status"], "audit_chain_invalid")
        self.assertEqual(t.calls, [])

    def test_refusal_is_terminal_no_repeat_rows(self):
        p = self.spool_draft()
        self._edit(p, body="edited")
        r = self.relay(self.gmail(FakeTransport()))
        r.run()
        out = r.run()
        self.assertEqual(out["results"][0]["status"], SKIPPED)
        self.assertEqual(len(self.relay_rows()), 1)


class TestAllowlist(RelayCase):
    def test_recipient_outside_allowlist_refused(self):
        self.spool_draft(to=ME, cc=["stranger@example.org"])
        t = FakeTransport()
        out = self.relay(self.gmail(t), allow=(ME,)).run()
        self.assertEqual(out["results"][0]["status"], REFUSED)
        self.assertIn("mail_relay:recipient_not_allowlisted:1", out["results"][0]["reasons"])
        self.assertEqual(t.calls, [])
        self.assertNotIn("stranger@example.org", self.all_audit_text())

    def test_empty_allowlist_refuses_all(self):
        self.spool_draft()
        t = FakeTransport()
        out = self.relay(self.gmail(t), allow=()).run()
        self.assertEqual(out["results"][0]["reasons"], ["mail_relay:allowlist_empty"])
        self.assertEqual(t.calls, [])

    def test_allowlist_case_insensitive(self):
        self.spool_draft(to="Me@Example.com")
        out = self.relay(self.gmail(FakeTransport()), allow=(ME,)).run()
        self.assertEqual(out["results"][0]["status"], UPLOADED)

    def test_bad_allowlist_entry_rejected_at_config(self):
        with self.assertRaises(ValueError):
            PartnerConfig.build(partner_id="p", spool_dir=self.spool, recipient_allowlist=["a@b.c\r\nBcc: x@y"])


class TestIdempotency(RelayCase):
    def test_rerun_never_duplicates(self):
        self.spool_draft()
        t = FakeTransport()
        r = self.relay(self.gmail(t))
        self.assertEqual(r.run()["results"][0]["status"], UPLOADED)
        out2 = r.run()
        self.assertEqual(out2["results"][0]["status"], ALREADY_UPLOADED)
        # A fresh relay object (new process) reads the state beside the spool.
        out3 = self.relay(self.gmail(t)).run()
        self.assertEqual(out3["results"][0]["status"], ALREADY_UPLOADED)
        self.assertEqual(len(t.calls), 1)
        self.assertEqual(len(self.relay_rows()), 1)
        st = self.state()
        self.assertEqual(len(st["drafts"]), 1)
        self.assertEqual(next(iter(st["drafts"].values()))["status"], UPLOADED)

    def test_same_content_spooled_twice_uploads_once(self):
        self.spool_draft()
        time.sleep(0.002)
        self.spool_draft()
        self.assertEqual(len(list(self.spool.glob("draft_*.json"))), 2)
        t = FakeTransport()
        out = self.relay(self.gmail(t)).run()
        self.assertEqual(sorted(r["status"] for r in out["results"]), [ALREADY_UPLOADED, UPLOADED])
        self.assertEqual(len(t.calls), 1)

    def test_crash_mid_upload_is_not_retried(self):
        p = self.spool_draft()
        ch = json.loads(p.read_text())["content_sha256"]
        (self.spool / STATE_FILE).write_text(
            json.dumps({"version": 1, "files": {}, "drafts": {ch: {"status": "in_flight", "attempts": 1}}})
        )
        t = FakeTransport()
        out = self.relay(self.gmail(t)).run()
        self.assertEqual(out["results"][0]["status"], SKIPPED)
        self.assertEqual(out["results"][0]["prior"], OUTCOME_UNKNOWN)
        self.assertEqual(t.calls, [])

    def test_corrupt_state_fails_closed(self):
        self.spool_draft()
        (self.spool / STATE_FILE).write_text("{garbage")
        t = FakeTransport()
        out = self.relay(self.gmail(t)).run()
        self.assertEqual(out["status"], "state_unreadable")
        self.assertEqual(t.calls, [])


class TestProviderFailures(RelayCase):
    def _two_drafts(self):
        self.spool_draft(body="first draft")
        time.sleep(0.002)
        self.spool_draft(body="second draft")

    def test_4xx_rejected_terminal_and_run_stops(self):
        self._two_drafts()
        t = FakeTransport([(400, ERR_MARKER.encode())])
        r = self.relay(self.gmail(t))
        out = r.run()
        self.assertEqual(out["status"], "stopped")
        self.assertEqual([x["status"] for x in out["results"]], [REJECTED, NOT_ATTEMPTED])
        self.assertEqual(len(t.calls), 1)
        err = out["results"][0]["error"]
        self.assertEqual(set(err), {"error_class", "error_sha256", "error_len"})
        self.assertEqual(err["error_len"], len(ERR_MARKER))
        out2 = r.run()  # rejected draft not retried; second one goes through
        self.assertEqual([x["status"] for x in out2["results"]], [SKIPPED, UPLOADED])
        self.assertEqual(len(t.calls), 2)

    def test_5xx_is_outcome_unknown_never_retried(self):
        self.spool_draft()
        t = FakeTransport([(503, ERR_MARKER.encode())])
        r = self.relay(self.graph(t))
        self.assertEqual(r.run()["results"][0]["status"], OUTCOME_UNKNOWN)
        out2 = r.run()
        self.assertEqual(out2["results"][0]["status"], SKIPPED)
        self.assertEqual(len(t.calls), 1)

    def test_timeout_is_outcome_unknown_no_retry_storm(self):
        self._two_drafts()
        t = FakeTransport([ProviderError("timeout", detail=ERR_MARKER)])
        r = self.relay(self.gmail(t))
        out = r.run()
        self.assertEqual([x["status"] for x in out["results"]], [OUTCOME_UNKNOWN, NOT_ATTEMPTED])
        self.assertEqual(len(t.calls), 1)

    def test_real_urllib_timeout_maps_to_outcome_unknown(self):
        self.spool_draft()

        class Hang:
            def __call__(self, method, url, headers, body, timeout):
                try:
                    raise socket.timeout("timed out " + ERR_MARKER)
                except socket.timeout as exc:
                    raise ProviderError("timeout", detail=str(exc)) from None

        out = self.relay(self.gmail(Hang())).run()
        self.assertEqual(out["results"][0]["status"], OUTCOME_UNKNOWN)

    def test_429_retry_later_then_budget_exhausted(self):
        self.spool_draft()
        t = FakeTransport([(429, b"slow down")] * 5)
        r = self.relay(self.gmail(t), max_attempts=2)
        self.assertEqual(r.run()["results"][0]["status"], RETRY_LATER)
        self.assertEqual(r.run()["results"][0]["status"], "failed_permanent")
        self.assertEqual(r.run()["results"][0]["status"], SKIPPED)
        self.assertEqual(len(t.calls), 2)

    def test_unexpected_client_exception_contained(self):
        self.spool_draft()

        class Boom:
            name = "gmail"

            def upload_draft(self, draft):
                raise RuntimeError(ERR_MARKER + TOKEN)

        out = self.relay(Boom()).run()
        self.assertEqual(out["results"][0]["status"], OUTCOME_UNKNOWN)
        self.assertNotIn(ERR_MARKER, json.dumps(out))
        self.assertNotIn(ERR_MARKER, self.all_audit_text())

    def test_missing_credential_nothing_sent(self):
        self.spool_draft()
        os.environ.pop("HAVEN_RELAY_GMAIL_TOKEN", None)
        t = FakeTransport()
        from governed_stack.mail_relay_providers import env_token

        out = self.relay(GmailDraftsClient(env_token("HAVEN_RELAY_GMAIL_TOKEN"), transport=t)).run()
        self.assertEqual(out["results"][0]["status"], RETRY_LATER)
        self.assertEqual(t.calls, [])

    def test_audit_write_failure_stops_run(self):
        self._two_drafts()
        t = FakeTransport()
        r = self.relay(self.gmail(t))
        real = self.storage.log_decision

        def failing(**kw):
            if str(kw.get("result", "")).startswith("mail_relay:"):
                raise RuntimeError("disk full " + ERR_MARKER)
            return real(**kw)

        self.storage.log_decision = failing
        try:
            out = r.run()
        finally:
            del self.storage.log_decision
        self.assertEqual(out["status"], "stopped")
        self.assertEqual(out["stopped_on"], "audit_failed")
        self.assertEqual(len(t.calls), 1)
        self.assertIn("mail_relay:audit_write_failed", out["results"][0]["reasons"])
        self.assertNotIn(ERR_MARKER, json.dumps(out))

    def test_run_never_raises(self):
        r = self.relay(self.gmail(FakeTransport()))
        r.storage = object()  # broken storage after construction
        self.spool_draft()
        out = r.run()
        self.assertIn(out["status"], ("audit_chain_invalid", "relay_error"))


class TestSecrets(RelayCase):
    def test_token_body_recipients_errors_never_in_audit_or_logs(self):
        self.spool_draft(to=ME, cc=[YOU])
        time.sleep(0.002)
        self.spool_draft(to=ME, body="another " + BODY)
        t = FakeTransport([(201, b'{"id":"ok-1"}'), (500, (ERR_MARKER + TOKEN).encode())])
        with self.assertLogs("governed_stack.mail_relay", level=logging.DEBUG) as cm:
            out = self.relay(self.gmail(t)).run()
        self.assertEqual([x["status"] for x in out["results"]], [UPLOADED, OUTCOME_UNKNOWN])
        logs = "\n".join(cm.output)
        audit = self.all_audit_text()
        for marker in (TOKEN, ERR_MARKER, BODY, ME, YOU, "SECRET"):
            self.assertNotIn(marker, logs, marker)
            self.assertNotIn(marker, audit, marker)
        self.assertNotIn(TOKEN, json.dumps(out))
        self.assertNotIn(ERR_MARKER, json.dumps(out))
        self.assertNotIn(TOKEN, json.dumps(self.state()))
        self.assertNotIn(ERR_MARKER, json.dumps(self.state()))
        # Only subject/body hashes; no recipient hash in relay rows.
        for row in self.relay_rows():
            self.assertNotIn("recipients_sha256", row["meta"])
            self.assertEqual(set(row["intent"]) - {"action", "channel", "partner_id", "provider"},
                             {"subject_sha256", "body_sha256"})

    def test_provider_error_str_has_no_detail(self):
        e = ProviderError("rejected", status=400, detail=ERR_MARKER)
        self.assertNotIn(ERR_MARKER, str(e))
        self.assertNotIn(ERR_MARKER, repr(e))
        self.assertEqual(e.digest["error_len"], len(ERR_MARKER))


class TestConcurrency(RelayCase):
    def test_concurrent_relays_do_not_double_upload(self):
        for i in range(3):
            self.spool_draft(body=f"draft {i}")
            time.sleep(0.002)
        t = FakeTransport(delay=0.05)
        relays = [self.relay(self.gmail(t)) for _ in range(4)]
        results = []
        start = threading.Barrier(len(relays))

        def go(r):
            start.wait()
            results.append(r.run())

        threads = [threading.Thread(target=go, args=(r,)) for r in relays]
        for th in threads:
            th.start()
        for th in threads:
            th.join(30)
        # Run again until every draft is done; still exactly one call each.
        for r in relays:
            r.run()
        self.assertEqual(len(t.calls), 3)
        self.assertTrue(any(o["status"] == "busy" for o in results))
        self.assertEqual(len([x for x in self.relay_rows() if x["meta"]["status"] == UPLOADED]), 3)
        self.assertTrue(self.storage.verify_chain()["valid"])

    def test_lock_excludes_other_processes(self):
        import subprocess

        self.spool_draft()
        code = (
            "import fcntl,os,sys,time;fd=os.open(sys.argv[1],os.O_RDWR|os.O_CREAT,0o600);"
            "fcntl.flock(fd,fcntl.LOCK_EX);print('locked',flush=True);time.sleep(3)"
        )
        proc = subprocess.Popen(
            [sys.executable, "-c", code, str(self.spool / relay_mod.LOCK_FILE)], stdout=subprocess.PIPE, text=True
        )
        try:
            self.assertEqual(proc.stdout.readline().strip(), "locked")
            t = FakeTransport()
            self.assertEqual(self.relay(self.gmail(t)).run()["status"], "busy")
            self.assertEqual(t.calls, [])
        finally:
            proc.kill()
            proc.wait()


class TestSendBanLint(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("lint_no_bypass_relay", ROOT / "scripts" / "lint_no_bypass.py")
        cls.lint = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.lint)

    def test_relay_modules_are_clean(self):
        paths = [ROOT / "src/governed_stack/mail_relay.py", ROOT / "src/governed_stack/mail_relay_providers.py"]
        for p in paths:
            self.assertTrue(p.exists(), p)
        self.assertEqual(self.lint.lint_paths(paths), [])

    def test_lint_fails_on_any_transmit_endpoint_or_scope(self):
        bad = [
            "u = 'https://gmail.googleapis.com/gmail/v1/users/me/drafts/send'",
            "u = 'https://gmail.googleapis.com/gmail/v1/users/me/messages/send'",
            "svc.users().drafts().send(userId='me', body=b)",
            "u = 'https://graph.microsoft.com/v1.0/me/sendMail'",
            "u = 'https://graph.microsoft.com/v1.0/me/messages/abc/send'",
            "SCOPE = 'Mail.Send'",
            "# permission Mail.Send in a comment still fails",
            "SCOPE = 'https://www.googleapis.com/auth/gmail.send'",
            "SCOPE = 'https://mail.google.com/'",
            "SCOPE = 'https://www.googleapis.com/auth/gmail.readonly'",
            "P = 'Mail.Read'",
            "method = 'GET'",
            "def send_draft(x): pass",
            "client.send(x)",
        ]
        for line in bad:
            with self.subTest(line=line):
                self.assertTrue(self.lint.scan_relay("src/governed_stack/mail_relay.py", line + "\n"))

    def test_lint_detects_injected_send_in_real_module_copy(self):
        with tempfile.TemporaryDirectory() as d:
            src = (ROOT / "src/governed_stack/mail_relay_providers.py").read_text()
            injected = src + "\nX = 'https://graph.microsoft.com/v1.0/me/sendMail'\n"
            self.assertTrue(self.lint.scan_relay("src/governed_stack/mail_relay_providers.py", injected))
            self.assertEqual(self.lint.scan_relay("src/governed_stack/mail_relay_providers.py", src), [])
            del d

    def test_allowed_scopes_pass(self):
        ok = "A = 'https://www.googleapis.com/auth/gmail.compose'\nB = 'Mail.ReadWrite'\nM = 'POST'\n"
        self.assertEqual(self.lint.scan_relay("src/governed_stack/mail_relay.py", ok), [])

    def test_relay_modules_expose_no_transmit_attribute(self):
        import governed_stack.mail_relay as a
        import governed_stack.mail_relay_providers as b

        for mod in (a, b):
            for name in dir(mod):
                self.assertNotIn("send", name.lower(), f"{mod.__name__}.{name}")
        for cls in (GmailDraftsClient, GraphDraftsClient, MailRelay):
            self.assertFalse([n for n in dir(cls) if "send" in n.lower()], cls)


class TestDraftModel(unittest.TestCase):
    def test_subject_newline_refused_by_allowlist_check(self):
        d = RelayDraft(to=(ME,), cc=(), subject="hi\r\nBcc: x@evil.example", body="b", content_sha256="0" * 64)
        self.assertEqual(relay_mod.check_allowlist(d, frozenset({ME})), ["mail_relay:subject_header_unsafe"])

    def test_bcc_outside_allowlist_refused(self):
        d = RelayDraft(to=(ME,), cc=(), subject="s", body="b", content_sha256="0" * 64)
        self.assertEqual(
            relay_mod.check_allowlist(d, frozenset({ME}), bcc=["x@evil.example"]),
            ["mail_relay:recipient_not_allowlisted:1"],
        )


if __name__ == "__main__":
    unittest.main()
