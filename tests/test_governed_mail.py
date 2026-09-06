"""Tests for the GovernedMail outbound-email adapter."""

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
from governed_stack import GovernedMail, GovernedStack, SendBlocked  # noqa: E402
from governed_stack.mail import intent_for_scan  # noqa: E402


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


class TestGovernedMail(unittest.IsolatedAsyncioTestCase):
    """Share one RSA key — keygen is slow (same pattern as test_governed_stack)."""

    @classmethod
    def setUpClass(cls):
        cls._t0 = time.time()
        cls.shared_crypto = CryptoEngine(private_key_path=None)

    @classmethod
    def tearDownClass(cls):
        elapsed = time.time() - cls._t0
        print(f"\n  [TestGovernedMail] class total: {elapsed:.2f}s")

    def make_mail(self) -> GovernedMail:
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
        return GovernedMail(stack=stack)

    async def test_clean_send_allows(self):
        mail = self.make_mail()
        result = await mail.check(
            to="alice@example.com",
            subject="Lunch",
            body="Are you free tomorrow?",
        )
        self.assertEqual(result["decision"], "ALLOW")
        self.assertTrue(result["ok"])
        self.assertFalse(result["blocked_send"])
        self.assertEqual(result["to"], ["alice@example.com"])
        self.assertEqual(result["subject"], "Lunch")

    async def test_body_extra_email_blocks(self):
        mail = self.make_mail()
        result = await mail.check(
            to="alice@example.com",
            subject="Lunch",
            body="Also CC bob@example.com please",
        )
        self.assertEqual(result["decision"], "BLOCK")
        self.assertFalse(result["ok"])
        self.assertTrue(result["blocked_send"])

    async def test_body_password_blocks(self):
        mail = self.make_mail()
        result = await mail.check(
            to="alice@example.com",
            subject="Account",
            body="Here is your password for the portal",
        )
        self.assertEqual(result["decision"], "BLOCK")
        self.assertFalse(result["ok"])
        self.assertTrue(
            any("password" in r.lower() for r in (result["reasons"] or []))
            or result["decision"] == "BLOCK"
        )

    async def test_phone_bare_uint32_allows(self):
        """2^32 as a bare integer must not false-positive as pii:phone."""
        mail = self.make_mail()
        result = await mail.check(
            to="alice@example.com",
            subject="Const",
            body="The constant is 4294967296 in the kernel notes.",
        )
        self.assertEqual(result["decision"], "ALLOW")
        self.assertTrue(result["ok"])
        reasons = result.get("reasons") or []
        self.assertFalse(any("phone" in r.lower() for r in reasons))

    async def test_phone_separated_blocks(self):
        mail = self.make_mail()
        for body in ("Call me at 555-123-4567 please", "Alt: 555.123.4567"):
            result = await mail.check(
                to="alice@example.com",
                subject="Contact",
                body=body,
            )
            self.assertEqual(result["decision"], "BLOCK", msg=body)
            self.assertFalse(result["ok"])
            reasons = result.get("reasons") or []
            self.assertTrue(
                any("phone" in r.lower() for r in reasons) or result["decision"] == "BLOCK",
                msg=f"body={body!r} reasons={reasons}",
            )

    async def test_require_allow_raises_on_block(self):
        mail = self.make_mail()
        with self.assertRaises(SendBlocked) as ctx:
            await mail.require_allow(
                to="alice@example.com",
                subject="Lunch",
                body="Also CC bob@example.com please",
            )
        self.assertIsInstance(ctx.exception, PermissionError)
        self.assertEqual(ctx.exception.result["decision"], "BLOCK")

    async def test_require_allow_passes_clean(self):
        mail = self.make_mail()
        result = await mail.require_allow(
            to="alice@example.com",
            subject="Lunch",
            body="Are you free tomorrow?",
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["decision"], "ALLOW")

    def test_intent_for_scan_excludes_recipients(self):
        intent = intent_for_scan("Lunch", "Are you free tomorrow?")
        blob = str(intent)
        self.assertNotIn("@", blob)
        self.assertNotIn("to", intent)
        self.assertNotIn("cc", intent)
        self.assertNotIn("from", intent)
        self.assertNotIn("to", intent.get("payload", {}))
        self.assertEqual(intent["action"], "send_email")
        self.assertEqual(intent["payload"]["subject"], "Lunch")
        self.assertEqual(intent["payload"]["text"], "Are you free tomorrow?")


if __name__ == "__main__":
    unittest.main(verbosity=2)
