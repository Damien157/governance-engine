"""Tests for live-connector Protocols, mocks, and bus side_effect factories."""

from __future__ import annotations

import inspect
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_PATHS = (
    ROOT / "src",
    ROOT,
    ROOT / "hais",
    ROOT / "imprint" / "src",
    ROOT / "haven2" / "src",
)
for p in reversed(_PATHS):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from certified_governance_unified import CryptoEngine  # noqa: E402

from governed_stack import GovernedActionBus, GovernedStack  # noqa: E402
from governed_stack.connectors import (  # noqa: E402
    ContentBindingError,
    MockBioTicketLogger,
    MockCalendarWriter,
    MockMailSender,
    MockSocialPublisher,
    assert_bound_content,
    bus_bio_side_effect,
    bus_calendar_side_effect,
    bus_mail_side_effect,
    bus_social_side_effect,
)


def tmp_db() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return path


def _close_stack_storage(stack) -> None:
    eng = getattr(stack, "engine", None)
    if eng is None:
        return
    storage = getattr(eng, "storage", None)
    if storage is not None and hasattr(storage, "close"):
        try:
            storage.close()
        except Exception:
            pass


class TestConnectors(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.shared_crypto = CryptoEngine(private_key_path=None)

    def make_bus(self) -> GovernedActionBus:
        db_path = tmp_db()
        self.addCleanup(lambda: os.path.exists(db_path) and os.remove(db_path))
        key_path = db_path + ".pem"
        self.addCleanup(lambda: os.path.exists(key_path) and os.remove(key_path))
        stack = GovernedStack(
            config={
                "db_path": db_path,
                "signing_key_path": key_path,
                "log_level": 40,
            },
            crypto=self.shared_crypto,
        )
        self.addCleanup(lambda s=stack: _close_stack_storage(s))
        return GovernedActionBus(stack=stack)

    async def test_mock_mail_via_bus_factory(self):
        bus = self.make_bus()
        sender = MockMailSender()
        out = await bus.execute(
            "mail",
            to="alice@example.com",
            subject="Lunch",
            body="Are you free tomorrow?",
            side_effect=bus_mail_side_effect(sender),
        )
        self.assertEqual(out["decision"], "ALLOW")
        self.assertTrue(out.get("executed"))
        self.assertEqual(len(sender.sent), 1)
        self.assertTrue(sender.sent[0]["mock"])
        self.assertEqual(sender.sent[0]["to"], ["alice@example.com"])
        self.assertEqual(sender.sent[0]["subject"], "Lunch")
        self.assertEqual(sender.sent[0]["body"], "Are you free tomorrow?")
        self.assertEqual(out.get("body"), "Are you free tomorrow?")
        self.assertEqual(sender.sent[0]["body"], "Are you free tomorrow?")
        self.assertEqual(out["body"], "Are you free tomorrow?")

    async def test_envelope_includes_approved_mail_body(self):
        bus = self.make_bus()
        result = await bus.mail.check(
            to="alice@example.com",
            subject="Lunch",
            body="Are you free tomorrow?",
        )
        self.assertEqual(result["decision"], "ALLOW")
        self.assertEqual(result["body"], "Are you free tomorrow?")
        self.assertEqual(result["subject"], "Lunch")
        self.assertIn("cc", result)
        assert_bound_content(result, "mail")

    async def test_calendar_envelope_includes_gated_content(self):
        bus = self.make_bus()
        result = await bus.calendar.check(
            summary="Team sync",
            description="Weekly check-in",
            location="Room A",
        )
        self.assertEqual(result["decision"], "ALLOW")
        self.assertEqual(result["summary"], "Team sync")
        self.assertEqual(result["description"], "Weekly check-in")
        self.assertEqual(result["location"], "Room A")
        assert_bound_content(result, "calendar")

    async def test_social_envelope_includes_text(self):
        bus = self.make_bus()
        result = await bus.post.check(
            text="Hello world from governed stack",
            platform="x",
        )
        self.assertEqual(result["decision"], "ALLOW")
        self.assertEqual(result["text"], "Hello world from governed stack")
        self.assertEqual(result["platform"], "x")
        assert_bound_content(result, "social")

    async def test_mock_calendar_via_bus_factory(self):
        bus = self.make_bus()
        writer = MockCalendarWriter()
        out = await bus.execute(
            "calendar",
            summary="Team sync",
            description="Weekly check-in",
            location="Room A",
            side_effect=bus_calendar_side_effect(writer),
        )
        self.assertEqual(out["decision"], "ALLOW")
        self.assertEqual(len(writer.created), 1)
        self.assertEqual(writer.created[0]["summary"], "Team sync")
        self.assertEqual(writer.created[0]["description"], "Weekly check-in")
        self.assertEqual(writer.created[0]["location"], "Room A")

    async def test_mock_social_via_bus_factory(self):
        bus = self.make_bus()
        pub = MockSocialPublisher()
        out = await bus.execute(
            "social",
            text="Hello world from governed stack",
            platform="x",
            side_effect=bus_social_side_effect(pub),
        )
        self.assertEqual(out["decision"], "ALLOW")
        self.assertEqual(len(pub.published), 1)
        self.assertIn("Hello world", pub.published[0]["text"])
        self.assertEqual(pub.published[0]["text"], out["text"])

    async def test_block_never_calls_mock_sender(self):
        bus = self.make_bus()
        sender = MockMailSender()
        from governed_stack import SendBlocked

        with self.assertRaises(SendBlocked):
            await bus.execute(
                "mail",
                to="alice@example.com",
                subject="Lunch",
                body="Also CC bob@example.com please",
                side_effect=bus_mail_side_effect(sender),
            )
        self.assertEqual(sender.sent, [])

    def test_factory_rejects_closed_over_body_kwarg(self):
        """Old body=/subject=/text= overrides must be TypeError (removed)."""
        sender = MockMailSender()
        with self.assertRaises(TypeError):
            bus_mail_side_effect(sender, body="malicious")  # type: ignore[call-arg]
        with self.assertRaises(TypeError):
            bus_mail_side_effect(sender, subject="swap")  # type: ignore[call-arg]
        writer = MockCalendarWriter()
        with self.assertRaises(TypeError):
            bus_calendar_side_effect(writer, summary="swap")  # type: ignore[call-arg]
        pub = MockSocialPublisher()
        with self.assertRaises(TypeError):
            bus_social_side_effect(pub, text="malicious")  # type: ignore[call-arg]

        # Signatures accept only the connector positional/kw.
        for factory in (
            bus_mail_side_effect,
            bus_calendar_side_effect,
            bus_social_side_effect,
        ):
            params = list(inspect.signature(factory).parameters)
            self.assertEqual(params, [params[0]])
            self.assertNotIn("body", params)
            self.assertNotIn("subject", params)
            self.assertNotIn("text", params)
            self.assertNotIn("summary", params)

    async def test_content_swap_impossible_mock_gets_envelope_body(self):
        """Gate ALLOW on safe body; factory cannot close over malicious; mock gets envelope."""
        bus = self.make_bus()
        sender = MockMailSender()
        safe = "safe approved content for lunch"
        # Attempting content-swap at factory construction fails hard.
        with self.assertRaises(TypeError):
            bus_mail_side_effect(sender, body="malicious")  # type: ignore[call-arg]

        se = bus_mail_side_effect(sender)
        out = await bus.execute(
            "mail",
            to="alice@example.com",
            subject="Lunch",
            body=safe,
            side_effect=se,
        )
        self.assertEqual(out["decision"], "ALLOW")
        self.assertEqual(out["body"], safe)
        self.assertEqual(len(sender.sent), 1)
        self.assertEqual(sender.sent[0]["body"], safe)
        self.assertNotEqual(sender.sent[0]["body"], "malicious")

        # Even a hand-built ALLOW envelope without body refuses to send.
        with self.assertRaises(ContentBindingError):
            se(
                {
                    "ok": True,
                    "decision": "ALLOW",
                    "to": ["alice@example.com"],
                    "subject": "Lunch",
                    # body omitted — content-swap / unbound send blocked
                }
            )
        self.assertEqual(len(sender.sent), 1)  # unchanged

    def test_assert_bound_content_missing_raises(self):
        with self.assertRaises(ContentBindingError):
            assert_bound_content({"subject": "x", "to": ["a@b.c"]}, "mail")
        with self.assertRaises(ContentBindingError):
            assert_bound_content({"summary": "s"}, "calendar")
        with self.assertRaises(ContentBindingError):
            assert_bound_content({"platform": "x"}, "social")

    def test_bio_ticket_logger_standalone(self):
        logger = MockBioTicketLogger()
        se = bus_bio_side_effect(logger)
        se(
            {
                "decision": "ALLOW",
                "entry_id": "e1",
                "purpose": "p",
                "domain": "aging",
                "intervention_class": "literature",
            }
        )
        self.assertEqual(len(logger.tickets), 1)
        self.assertEqual(logger.tickets[0]["kind"], "bio_metadata_ticket")


if __name__ == "__main__":
    unittest.main()
