"""Tests for the GovernedCalendar calendar-write adapter."""

from __future__ import annotations

import os
import sys
import tempfile
import time
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
from governed_stack import GovernedCalendar, GovernedStack, SendBlocked  # noqa: E402
from governed_stack.calendar import CalendarBlocked, intent_for_scan  # noqa: E402


def tmp_db() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return path

def _close_stack_storage(stack) -> None:
    """Best-effort close of AuditStorage on the ops engine."""
    eng = getattr(stack, "engine", None)
    if eng is None:
        return
    storage = getattr(eng, "storage", None)
    if storage is not None and hasattr(storage, "close"):
        try:
            storage.close()
        except Exception:
            pass


class TestGovernedCalendar(unittest.IsolatedAsyncioTestCase):
    """Share one RSA key — keygen is slow (same pattern as test_governed_mail)."""

    @classmethod
    def setUpClass(cls):
        cls._t0 = time.time()
        cls.shared_crypto = CryptoEngine(private_key_path=None)

    @classmethod
    def tearDownClass(cls):
        elapsed = time.time() - cls._t0
        print(f"\n  [TestGovernedCalendar] class total: {elapsed:.2f}s")

    def make_cal(self) -> GovernedCalendar:
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
        return GovernedCalendar(stack=stack)

    async def test_clean_summary_description_allows(self):
        cal = self.make_cal()
        result = await cal.check(
            summary="Team sync",
            description="Weekly project status update",
            location="Room A",
            start="2026-09-08T10:00:00",
            end="2026-09-08T11:00:00",
            attendees=["alice@example.com"],
        )
        self.assertEqual(result["decision"], "ALLOW")
        self.assertTrue(result["ok"])
        self.assertFalse(result["blocked_write"])
        self.assertEqual(result["summary"], "Team sync")
        self.assertEqual(result["attendees"], ["alice@example.com"])
        self.assertEqual(result["start"], "2026-09-08T10:00:00")
        self.assertEqual(result["end"], "2026-09-08T11:00:00")

    async def test_description_email_blocks(self):
        cal = self.make_cal()
        result = await cal.check(
            summary="Team sync",
            description="Also invite bob@example.com please",
        )
        self.assertEqual(result["decision"], "BLOCK")
        self.assertFalse(result["ok"])
        self.assertTrue(result["blocked_write"])
        reasons = result.get("reasons") or []
        self.assertTrue(
            any("email" in r.lower() or "pii" in r.lower() for r in reasons)
            or result["decision"] == "BLOCK",
            msg=f"reasons={reasons}",
        )

    async def test_description_password_blocks(self):
        cal = self.make_cal()
        result = await cal.check(
            summary="Onboarding",
            description="Here is your password for the portal",
        )
        self.assertEqual(result["decision"], "BLOCK")
        self.assertFalse(result["ok"])
        self.assertTrue(result["blocked_write"])
        self.assertTrue(
            any("password" in r.lower() for r in (result["reasons"] or []))
            or result["decision"] == "BLOCK"
        )

    def test_intent_for_scan_excludes_routing_metadata(self):
        intent = intent_for_scan(
            summary="Team sync",
            description="Weekly project status update",
            location="Room A",
        )
        blob = str(intent)
        self.assertNotIn("@", blob)
        self.assertNotIn("attendees", intent)
        self.assertNotIn("start", intent)
        self.assertNotIn("end", intent)
        self.assertNotIn("attendees", intent.get("payload", {}))
        self.assertNotIn("start", intent.get("payload", {}))
        self.assertNotIn("end", intent.get("payload", {}))
        self.assertEqual(intent["action"], "calendar_write")
        self.assertEqual(intent["payload"]["subject"], "Team sync")
        self.assertIn("Weekly project status update", intent["payload"]["text"])
        self.assertIn("Room A", intent["payload"]["text"])

    async def test_attendees_out_of_band_still_allow(self):
        """Attendee emails must not enter scanned intent — clean body still ALLOW."""
        cal = self.make_cal()
        result = await cal.check(
            summary="Planning",
            description="Quarterly roadmap review",
            attendees=["alice@example.com", "bob@example.com"],
            start="2026-09-10T14:00:00",
            end="2026-09-10T15:00:00",
        )
        self.assertEqual(result["decision"], "ALLOW")
        self.assertTrue(result["ok"])
        self.assertFalse(result["blocked_write"])
        self.assertEqual(
            result["attendees"],
            ["alice@example.com", "bob@example.com"],
        )

    async def test_require_allow_raises_on_block(self):
        cal = self.make_cal()
        with self.assertRaises(SendBlocked) as ctx:
            await cal.require_allow(
                summary="Team sync",
                description="Also invite bob@example.com please",
            )
        self.assertIsInstance(ctx.exception, PermissionError)
        self.assertIs(CalendarBlocked, SendBlocked)
        self.assertEqual(ctx.exception.result["decision"], "BLOCK")


if __name__ == "__main__":
    unittest.main(verbosity=2)
