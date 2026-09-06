"""
Unified end-to-end suite: prove the live governed stack works together.

Covers catalog → GovernedStack → mail/calendar/post → decide bridge →
audit chain → HAIS m=0.5 → sketches stay off path.

Run:
  .venv/bin/python -m unittest tests.test_all_works -v
  .venv/bin/python scripts/run_all_works_tests.py
"""

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
from governed_stack import (  # noqa: E402
    HavenUnified,
    GovernedDecisionEngine,
    SendBlocked,
    PostBlocked,
    TIERS,
    describe,
    import_check,
    live_ok,
)
from governed_stack.stack import GovernedStack  # noqa: E402
from hais_unified_kernel import SovereignKernel  # noqa: E402


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


class TestAllWorks(unittest.IsolatedAsyncioTestCase):
    """Single shared stack: everything that must work on the live path."""

    @classmethod
    def setUpClass(cls):
        cls._t0 = time.time()
        cls.shared_crypto = CryptoEngine(private_key_path=None)

    @classmethod
    def tearDownClass(cls):
        print(f"\n  [TestAllWorks] class total: {time.time() - cls._t0:.2f}s")

    def make_unified(self) -> HavenUnified:
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
        return HavenUnified(stack=stack)

    # ------------------------------------------------------------------
    # Inventory / imports
    # ------------------------------------------------------------------

    def test_01_catalog_and_live_imports(self):
        self.assertIn("live", TIERS)
        self.assertIn("core", TIERS)
        self.assertIn("sketch", TIERS)
        live_names = {e["symbol"] for e in TIERS["live"]}
        for need in (
            "GovernedStack",
            "GovernedMail",
            "GovernedCalendar",
            "GovernedPost",
            "HavenUnified",
            "GovernedDecisionEngine",
        ):
            self.assertIn(need, live_names, f"missing live symbol: {need}")
        check = import_check()
        for entry in TIERS["live"]:
            self.assertTrue(
                check.get(entry["module"]),
                f"live import failed: {entry['module']}",
            )
        self.assertTrue(live_ok(check))
        text = describe()
        self.assertIn("GovernedDecisionEngine", text)
        self.assertIn("sketch", text)

    def test_02_hais_kernel_m_half_not_jammed(self):
        """m=0.5: low risk → higher cap; high risk → throttle (not always ~0.13)."""
        k = SovereignKernel()
        low = k.evaluate_state(0.2, {"stress": 0.1, "anomaly": 0.05, "drift": 0.0})
        high = k.evaluate_state(0.9, {"stress": 0.9, "anomaly": 0.8, "drift": 0.5})
        self.assertGreater(low["capability_cap"], 0.25)
        self.assertLess(high["capability_cap"], low["capability_cap"])
        # Broken m=0 paste jammed near ~0.134 for everything
        self.assertGreater(low["capability_cap"], 0.20)

    # ------------------------------------------------------------------
    # Shared facade: mail / calendar / post / decide
    # ------------------------------------------------------------------

    def test_03_mail_allow_and_phone_rules(self):
        hu = self.make_unified()
        clean = hu.mail.check_sync(
            to="alice@example.com",
            subject="Lunch",
            body="Are you free tomorrow?",
        )
        self.assertEqual(clean["decision"], "ALLOW")
        self.assertTrue(clean["ok"])

        bare = hu.mail.check_sync(
            to="bob@example.com",
            subject="Kernel note",
            body="The constant is 4294967296 in the kernel notes.",
        )
        self.assertEqual(bare["decision"], "ALLOW")
        self.assertFalse(any("phone" in r.lower() for r in bare.get("reasons") or []))

        phone = hu.mail.check_sync(
            to="bob@example.com",
            subject="Call",
            body="Call me at 555-123-4567 please",
        )
        self.assertEqual(phone["decision"], "BLOCK")
        self.assertTrue(any("phone" in r.lower() for r in phone.get("reasons") or []))

    def test_04_calendar_and_post_allow_block(self):
        hu = self.make_unified()
        cal_ok = hu.calendar.check_sync(
            summary="Team sync",
            description="Weekly status",
            location="Room A",
        )
        self.assertEqual(cal_ok["decision"], "ALLOW")

        cal_bad = hu.calendar.check_sync(
            summary="Call",
            description="Dial 555-123-4567",
            location="",
        )
        self.assertEqual(cal_bad["decision"], "BLOCK")

        post_ok = hu.post.check_sync(
            platform="x",
            text="Short update on our governance request gate.",
        )
        self.assertEqual(post_ok["decision"], "ALLOW")

        post_bad = hu.post.check_sync(
            platform="x",
            text="Email me at test@example.com for healthcare details",
        )
        self.assertEqual(post_bad["decision"], "BLOCK")
        self.assertTrue(
            any("email" in r.lower() or "pii" in r.lower() for r in post_bad.get("reasons") or [])
        )

    async def test_05_require_allow_raises(self):
        hu = self.make_unified()
        with self.assertRaises((SendBlocked, PermissionError)):
            await hu.mail.require_allow(
                to="x@y.com",
                subject="s",
                body="Call 555-123-4567",
            )
        with self.assertRaises((PostBlocked, PermissionError)):
            await hu.post.require_allow(
                platform="linkedin",
                text="Reach me at leak@example.com",
            )

    async def test_06_decide_bridge_and_halt(self):
        hu = self.make_unified()
        self.assertIsInstance(hu.runtime, GovernedDecisionEngine)
        allow = await hu.decide({"text": "hello there, status please"})
        self.assertEqual(allow["decision"], "ALLOW")
        self.assertEqual(allow["runtime"], "governed_stack")
        self.assertIsNotNone(allow.get("audit_id"))

        pii = await hu.decide(
            {"text": "contact me at test@example.com about healthcare"}
        )
        self.assertEqual(pii["decision"], "BLOCK")
        self.assertTrue(any("email" in r.lower() or "pii" in r.lower() for r in pii["policy_reasons"]))

        forbidden = await hu.decide({"action": "harm_human", "payload": {"text": "no"}})
        self.assertEqual(forbidden["decision"], "BLOCK")
        self.assertTrue(any("constitution" in r for r in forbidden["policy_reasons"]))

        hu.runtime.halt("test halt")
        halted = await hu.decide({"text": "hello"})
        self.assertEqual(halted["decision"], "BLOCK")
        self.assertTrue(hu.runtime.health()["halted"])
        hu.runtime.reset()
        self.assertFalse(hu.runtime.health()["halted"])

    async def test_07_audit_chain_and_ops_engine(self):
        hu = self.make_unified()
        await hu.decide({"text": "ping for audit"})
        health = hu.runtime.health()
        self.assertTrue(health.get("live_ok", True))
        self.assertIn("certified_governance", health.get("engine_module", ""))
        chain = hu.runtime.audit.verify_chain()
        self.assertTrue(chain.get("valid"))
        self.assertGreaterEqual(chain.get("entries_checked", 0), 1)

    async def test_08_govern_path_skips_sketches(self):
        hu = self.make_unified()
        sketch_mods = [e["module"] for e in TIERS["sketch"]]
        self.assertIn("hais_governance_unified_runtime_v02", sketch_mods)
        self.assertIn("hais_unified_all", sketch_mods)
        self.assertNotIn(
            "hais_governance_unified_runtime_v02",
            [e["module"] for e in TIERS["live"]],
        )

        token = hu.issue_token("alice", "user")
        env = await hu.govern(
            {"action": "query", "payload": {"text": "hello there"}},
            token,
        )
        self.assertEqual(env["decision"], "ALLOW")
        self.assertIsNone(env.get("solver"))

        sat = hu.run_3sat_demo()
        self.assertFalse(sat["on_decision_path"])
        pipe = hu.run_production_pipeline_sketch()
        self.assertFalse(pipe["on_decision_path"])

    def test_09_hais_os_reexport(self):
        from hais_os import GovernedDecisionEngine as GDE

        self.assertIs(GDE, GovernedDecisionEngine)


if __name__ == "__main__":
    unittest.main(verbosity=2)
