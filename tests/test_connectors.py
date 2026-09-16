"""Tests for live-connector Protocols, mocks, and bus side_effect factories."""

from __future__ import annotations

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
    MockBioTicketLogger,
    MockCalendarWriter,
    MockMailSender,
    MockSocialPublisher,
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

    async def test_mock_calendar_via_bus_factory(self):
        bus = self.make_bus()
        writer = MockCalendarWriter()
        out = await bus.execute(
            "calendar",
            summary="Team sync",
            description="Weekly check-in",
            side_effect=bus_calendar_side_effect(writer),
        )
        self.assertEqual(out["decision"], "ALLOW")
        self.assertEqual(len(writer.created), 1)
        self.assertEqual(writer.created[0]["summary"], "Team sync")

    async def test_mock_social_via_bus_factory(self):
        bus = self.make_bus()
        pub = MockSocialPublisher()
        out = await bus.execute(
            "social",
            text="Hello world from governed stack",
            platform="x",
            side_effect=bus_social_side_effect(pub, text="Hello world from governed stack"),
        )
        self.assertEqual(out["decision"], "ALLOW")
        self.assertEqual(len(pub.published), 1)
        self.assertIn("Hello world", pub.published[0]["text"])

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

    def test_bio_ticket_logger_standalone(self):
        logger = MockBioTicketLogger()
        se = bus_bio_side_effect(logger)
        se({"decision": "ALLOW", "entry_id": "e1", "purpose": "p", "domain": "aging",
            "intervention_class": "literature"})
        self.assertEqual(len(logger.tickets), 1)
        self.assertEqual(logger.tickets[0]["kind"], "bio_metadata_ticket")


if __name__ == "__main__":
    unittest.main()
