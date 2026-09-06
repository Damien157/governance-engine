"""Tests for the GovernedPost outbound-social adapter."""

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
from governed_stack import GovernedPost, GovernedStack, PostBlocked  # noqa: E402
from governed_stack.social import intent_for_scan  # noqa: E402


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


class TestGovernedPost(unittest.IsolatedAsyncioTestCase):
    """Share one RSA key — keygen is slow (same pattern as mail tests)."""

    @classmethod
    def setUpClass(cls):
        cls._t0 = time.time()
        cls.shared_crypto = CryptoEngine(private_key_path=None)

    @classmethod
    def tearDownClass(cls):
        elapsed = time.time() - cls._t0
        print(f"\n  [TestGovernedPost] class total: {elapsed:.2f}s")

    def make_post(self) -> GovernedPost:
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
        return GovernedPost(stack=stack)

    async def test_clean_post_allows(self):
        post = self.make_post()
        result = await post.check(
            text="Excited to share a short update on our governance stack.",
            platform="linkedin",
        )
        self.assertEqual(result["decision"], "ALLOW")
        self.assertTrue(result["ok"])
        self.assertFalse(result["blocked_publish"])
        self.assertEqual(result["platform"], "linkedin")

    async def test_body_email_blocks(self):
        post = self.make_post()
        result = await post.check(
            text="Ping me at bob@example.com for details",
            platform="x",
        )
        self.assertEqual(result["decision"], "BLOCK")
        self.assertFalse(result["ok"])
        self.assertTrue(result["blocked_publish"])

    async def test_body_password_blocks(self):
        post = self.make_post()
        result = await post.check(
            text="Never share your password in a post",
            platform="linkedin",
        )
        self.assertEqual(result["decision"], "BLOCK")
        self.assertFalse(result["ok"])

    async def test_phone_bare_uint32_allows(self):
        post = self.make_post()
        result = await post.check(
            text="The constant is 4294967296 in the kernel notes.",
            platform="",
        )
        self.assertEqual(result["decision"], "ALLOW")
        reasons = result.get("reasons") or []
        self.assertFalse(any("phone" in r.lower() for r in reasons))

    async def test_phone_separated_blocks(self):
        post = self.make_post()
        result = await post.check(
            text="Call me at 555-123-4567 please",
            platform="linkedin",
        )
        self.assertEqual(result["decision"], "BLOCK")
        self.assertFalse(result["ok"])

    async def test_require_allow_raises_on_block(self):
        post = self.make_post()
        with self.assertRaises(PostBlocked) as ctx:
            await post.require_allow(
                text="Ping me at bob@example.com",
                platform="linkedin",
            )
        self.assertIsInstance(ctx.exception, PermissionError)
        self.assertEqual(ctx.exception.result["decision"], "BLOCK")

    async def test_require_allow_passes_clean(self):
        post = self.make_post()
        result = await post.require_allow(
            text="Sharing a clean product update.",
            platform="linkedin",
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["decision"], "ALLOW")

    def test_intent_for_scan_excludes_recipients_and_urls(self):
        intent = intent_for_scan(text="Hello world", platform="linkedin")
        blob = str(intent)
        self.assertNotIn("http", blob.lower())
        self.assertNotIn("recipients", intent)
        self.assertNotIn("urls", intent)
        self.assertNotIn("to", intent.get("payload", {}))
        self.assertEqual(intent["action"], "publish_post")
        self.assertEqual(intent["payload"]["subject"], "linkedin")
        self.assertEqual(intent["payload"]["text"], "Hello world")

    def test_out_of_band_recipients_not_scanned(self):
        """Recipients/URLs must not enter the scanned intent even if passed."""
        post = self.make_post()
        # Sync path: recipients with emails stay out of band → clean body ALLOW
        result = post.check_sync(
            text="Clean announce with no PII in body.",
            platform="linkedin",
            recipients=["alice@example.com"],
            urls=["https://example.com/secret"],
        )
        self.assertEqual(result["decision"], "ALLOW")
        self.assertEqual(result["recipients"], ["alice@example.com"])
        self.assertEqual(result["urls"], ["https://example.com/secret"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
