"""Tests for HavenUnified facade + catalog (sketches stay off decision path)."""

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
    GovernedUnified,
    TIERS,
    describe,
    import_check,
    live_ok,
)
from governed_stack import catalog  # noqa: E402
from governed_stack.stack import GovernedStack  # noqa: E402


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


class TestUnified(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls._t0 = time.time()
        cls.shared_crypto = CryptoEngine(private_key_path=None)

    @classmethod
    def tearDownClass(cls):
        elapsed = time.time() - cls._t0
        print(f"\n  [TestUnified] class total: {elapsed:.2f}s")

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

    def test_import_haven_unified_and_catalog(self):
        self.assertTrue(callable(HavenUnified))
        self.assertIs(GovernedUnified, HavenUnified)
        self.assertIn("live", TIERS)
        self.assertIn("core", TIERS)
        self.assertIn("sketch", TIERS)
        self.assertIn("describe", dir(catalog))
        text = describe()
        self.assertIn("live", text)
        self.assertIn("sketch", text)

    def test_manifest_has_tier_keys(self):
        hu = self.make_unified()
        man = hu.manifest()
        self.assertIn("live", man["tiers"])
        self.assertIn("core", man["tiers"])
        self.assertIn("sketch", man["tiers"])
        self.assertTrue(man["live_ok"])
        # production pipeline is sketch, not live
        self.assertIn(
            "hais_production_governed_pipeline",
            man["tiers"]["sketch"],
        )
        self.assertNotIn(
            "hais_production_governed_pipeline",
            man["tiers"]["live"],
        )

    def test_import_check_live_all_true(self):
        check = import_check()
        for entry in TIERS["live"]:
            self.assertTrue(
                check.get(entry["module"]),
                f"live module failed import: {entry['module']}",
            )
        self.assertTrue(live_ok(check))

    def test_mail_check_sync_clean_allow(self):
        hu = self.make_unified()
        result = hu.mail.check_sync(
            to="alice@example.com",
            subject="Lunch",
            body="Are you free tomorrow?",
        )
        self.assertEqual(result["decision"], "ALLOW")
        self.assertTrue(result["ok"])

    async def test_sketch_listed_but_govern_path_unchanged(self):
        """Sketches are catalogued; benign query still ALLOW via GovernedStack."""
        hu = self.make_unified()
        sketch_mods = [e["module"] for e in TIERS["sketch"]]
        self.assertIn("solvers.hais_3sat", sketch_mods)
        self.assertIn("diagnostics.invariant_topology", sketch_mods)
        self.assertIn("hais_production_governed_pipeline", sketch_mods)

        token = hu.issue_token("alice", "user")
        env = await hu.govern(
            {"action": "query", "payload": {"text": "hello there"}},
            token,
        )
        self.assertEqual(env["decision"], "ALLOW")
        self.assertNotIn("3sat", str(env.get("solver")))
        self.assertIsNone(env.get("solver"))

        # Sketch helpers must not go through govern decisioning
        sat = hu.run_3sat_demo()
        self.assertFalse(sat["on_decision_path"])
        pipe = hu.run_production_pipeline_sketch()
        self.assertFalse(pipe["on_decision_path"])
        self.assertIn("not real", pipe["note"].lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
