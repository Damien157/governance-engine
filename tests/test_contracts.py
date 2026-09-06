"""P1 hard contracts: typed intents, schema validation, stable error codes."""

from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path

from certified_governance_unified import CryptoEngine

from governed_stack.contracts import (
    GOV_HAIS_CAP,
    GOV_INTENT_INVALID,
    GOV_POLICY_BLOCK,
    CalendarScanIntent,
    GovernIntent,
    IntentValidationError,
    MailScanIntent,
    SocialScanIntent,
    map_error_code,
    parse_intent,
    validate_calendar_scan,
    validate_mail_scan,
    validate_social_scan,
)
from governed_stack.mail import intent_for_scan as mail_intent_for_scan
from governed_stack.calendar import intent_for_scan as calendar_intent_for_scan
from governed_stack.social import intent_for_scan as social_intent_for_scan
from governed_stack.stack import GovernedStack


class TestIntentParsing(unittest.TestCase):
    def test_mail_scan_parses(self):
        m = validate_mail_scan(subject="Hello", body="World")
        self.assertIsInstance(m, MailScanIntent)
        d = m.dump_for_govern()
        self.assertEqual(d["action"], "send_email")
        self.assertEqual(d["payload"]["subject"], "Hello")
        self.assertEqual(d["payload"]["text"], "World")
        self.assertNotIn("to", d)
        self.assertNotIn("cc", d)
        self.assertNotIn("from", d)
        self.assertNotIn("to", d.get("payload", {}))

    def test_calendar_scan_parses(self):
        c = validate_calendar_scan(
            summary="Sync", description="Weekly", location="Room A"
        )
        self.assertIsInstance(c, CalendarScanIntent)
        d = c.dump_for_govern()
        self.assertEqual(d["action"], "calendar_write")
        self.assertEqual(d["payload"]["subject"], "Sync")
        self.assertIn("Weekly", d["payload"]["text"])
        self.assertIn("Room A", d["payload"]["text"])
        for forbidden in ("attendees", "start", "end"):
            self.assertNotIn(forbidden, d)
            self.assertNotIn(forbidden, d.get("payload", {}))

    def test_social_scan_parses(self):
        s = validate_social_scan(text="Hello feed", platform="linkedin")
        self.assertIsInstance(s, SocialScanIntent)
        d = s.dump_for_govern()
        self.assertEqual(d["action"], "publish_post")
        self.assertEqual(d["payload"]["subject"], "linkedin")
        self.assertEqual(d["payload"]["text"], "Hello feed")
        for forbidden in ("recipients", "urls", "handles"):
            self.assertNotIn(forbidden, d)
            self.assertNotIn(forbidden, d.get("payload", {}))

    def test_parse_intent_query(self):
        g = parse_intent({"action": "query", "payload": {"difficulty": 0.2}})
        self.assertIsInstance(g, GovernIntent)
        self.assertEqual(g.action, "query")

    def test_mail_scan_extra_to_ignored_on_model(self):
        # extra=ignore: smuggled `to` is not a field; dump has no recipients.
        m = MailScanIntent.model_validate(
            {
                "action": "send_email",
                "payload": {"subject": "x", "text": "y", "to": "secret@x.com"},
                "to": "also@x.com",
                "cc": ["c@x.com"],
            }
        )
        d = m.dump_for_govern()
        self.assertNotIn("to", d)
        self.assertNotIn("cc", d)
        self.assertNotIn("to", d["payload"])
        self.assertEqual(d["payload"]["subject"], "x")

    def test_adapters_intent_for_scan_no_routing(self):
        mail_d = mail_intent_for_scan("Subj", "Body")
        self.assertNotIn("to", mail_d)
        self.assertNotIn("cc", mail_d)
        self.assertEqual(set(mail_d["payload"].keys()), {"subject", "text"})

        cal_d = calendar_intent_for_scan(
            summary="S", description="D", location="L"
        )
        self.assertNotIn("attendees", cal_d)
        self.assertNotIn("start", cal_d)
        self.assertNotIn("end", cal_d)

        soc_d = social_intent_for_scan(text="T", platform="x")
        self.assertNotIn("recipients", soc_d)
        self.assertNotIn("urls", soc_d)

    def test_invalid_types_raise_intent_invalid(self):
        with self.assertRaises(IntentValidationError) as ctx:
            parse_intent({"action": {"nested": True}})
        self.assertEqual(ctx.exception.code, GOV_INTENT_INVALID)
        self.assertTrue(ctx.exception.errors)

        with self.assertRaises(IntentValidationError) as ctx2:
            parse_intent({"action": "query", "stress": ["not", "a", "float"]})
        self.assertEqual(ctx2.exception.code, GOV_INTENT_INVALID)

        with self.assertRaises(IntentValidationError):
            parse_intent("not-a-dict")  # type: ignore[arg-type]

    def test_map_error_code(self):
        self.assertEqual(
            map_error_code(decision="BLOCK", reasons=["hais_capability_cap"]),
            GOV_HAIS_CAP,
        )
        self.assertEqual(
            map_error_code(decision="BLOCK", reasons=["pii:email"]),
            GOV_POLICY_BLOCK,
        )
        self.assertEqual(
            map_error_code(decision="REVIEW", reasons=["risk_high"]),
            "GOV_POLICY_REVIEW",
        )
        self.assertIsNone(map_error_code(decision="ALLOW", reasons=[]))


class TestGovernContractsSmoke(unittest.TestCase):
    def _stack(self) -> GovernedStack:
        td = tempfile.mkdtemp()
        db = str(Path(td) / "c.db")
        key = str(Path(td) / "k.pem")
        crypto = CryptoEngine(private_key_path=key)
        stack = GovernedStack(
            config={"db_path": db, "signing_key_path": key, "log_level": 50},
            crypto=crypto,
        )
        self.addCleanup(lambda: stack.engine.storage.close() if hasattr(stack.engine, "storage") else None)
        return stack

    def test_valid_intent_not_intent_invalid(self):
        stack = self._stack()
        token = stack.issue_token("tester", "operator")
        intent = {"action": "query", "payload": {"difficulty": 0.2}, "telemetry": {}}
        env = asyncio.run(stack.govern(intent, token))
        self.assertNotEqual(env.get("error_code"), GOV_INTENT_INVALID)
        self.assertIn(env.get("decision"), ("ALLOW", "REVIEW", "BLOCK"))
        # Clean query should ALLOW under default policy.
        self.assertEqual(env.get("decision"), "ALLOW")
        self.assertIn("latency_ms", env)

    def test_bad_intent_block_intent_invalid(self):
        stack = self._stack()
        token = stack.issue_token("tester", "operator")
        env = asyncio.run(
            stack.govern({"action": None, "payload": "nope"}, token)  # type: ignore[arg-type]
        )
        self.assertEqual(env.get("decision"), "BLOCK")
        self.assertEqual(env.get("error_code"), GOV_INTENT_INVALID)
        self.assertTrue(
            any("intent_invalid" in str(r) for r in (env.get("reasons") or []))
        )

    def test_basemodel_intent_accepted(self):
        stack = self._stack()
        token = stack.issue_token("tester", "operator")
        intent = MailScanIntent.from_scan(subject="Lunch", body="Free tomorrow?")
        env = asyncio.run(stack.govern(intent, token))
        self.assertNotEqual(env.get("error_code"), GOV_INTENT_INVALID)
        self.assertIn(env.get("decision"), ("ALLOW", "REVIEW", "BLOCK"))


if __name__ == "__main__":
    unittest.main()
