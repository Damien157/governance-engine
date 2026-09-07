"""Tests for GovernedActionBus — require_allow before side_effect (no bypass)."""

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

from certified_governance_unified import (  # noqa: E402
    CryptoEngine,
    PolicyEngine,
    PolicyRule,
    PolicySpec,
)
from governed_stack import (  # noqa: E402
    PUBLIC_EXECUTE_HELPERS,
    ActionDenied,
    GovernedActionBus,
    GovernedStack,
    SendBlocked,
    __version__,
)
import governed_stack as gs  # noqa: E402


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


class TestActionBus(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls._t0 = time.time()
        cls.shared_crypto = CryptoEngine(private_key_path=None)

    @classmethod
    def tearDownClass(cls):
        elapsed = time.time() - cls._t0
        print(f"\n  [TestActionBus] class total: {elapsed:.2f}s")

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

    async def test_allow_side_effect_called_once(self):
        bus = self.make_bus()
        calls: list[dict] = []

        def side_effect(result: dict) -> str:
            calls.append(result)
            return "sent-mock"

        out = await bus.execute(
            "mail",
            to="alice@example.com",
            subject="Lunch",
            body="Are you free tomorrow?",
            side_effect=side_effect,
        )
        self.assertEqual(out["decision"], "ALLOW")
        self.assertTrue(out.get("executed"))
        self.assertEqual(out.get("side_effect_result"), "sent-mock")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["decision"], "ALLOW")

    async def test_block_side_effect_never_called(self):
        bus = self.make_bus()
        calls: list[dict] = []

        def side_effect(result: dict) -> None:
            calls.append(result)

        with self.assertRaises(SendBlocked) as ctx:
            await bus.execute(
                "mail",
                to="alice@example.com",
                subject="Lunch",
                body="Also CC bob@example.com please",
                side_effect=side_effect,
            )
        self.assertEqual(calls, [])
        self.assertEqual(ctx.exception.result.get("decision"), "BLOCK")
        self.assertIs(ActionDenied, SendBlocked)

    async def test_review_side_effect_never_called(self):
        bus = self.make_bus()
        base = PolicyEngine._default_spec()
        probe = PolicyRule(
            id="review_probe_bus",
            type="banned",
            pattern=r"\bGOVERN_REVIEW_PROBE\b",
            action="REVIEW",
            reason="bus review probe",
        )
        bus.stack.engine.policy_engine = PolicyEngine(
            spec=PolicySpec(
                pii_rules=list(base.pii_rules),
                banned_terms=list(base.banned_terms) + [probe],
                action_rules=list(base.action_rules),
            )
        )
        calls: list[dict] = []

        def side_effect(result: dict) -> None:
            calls.append(result)

        with self.assertRaises(SendBlocked) as ctx:
            await bus.execute(
                "mail",
                to="alice@example.com",
                subject="Review me",
                body="token GOVERN_REVIEW_PROBE for human eyes",
                side_effect=side_effect,
            )
        self.assertEqual(calls, [])
        self.assertEqual(ctx.exception.result.get("decision"), "REVIEW")

    async def test_smuggled_to_on_mail_denied_before_side_effect(self):
        """Mail-shaped intent with smuggled `to` → deny; side_effect never runs."""
        bus = self.make_bus()
        calls: list[dict] = []

        def side_effect(result: dict) -> None:
            calls.append(result)

        with self.assertRaises(SendBlocked) as ctx:
            await bus.execute(
                "tool",
                intent={
                    "action": "send_email",
                    "payload": {"subject": "x", "text": "clean body no pii"},
                    "to": "smuggled@x.com",
                },
                side_effect=side_effect,
            )
        self.assertEqual(calls, [])
        result = ctx.exception.result
        self.assertEqual(result.get("decision"), "BLOCK")
        self.assertFalse(result.get("ok"))

    def test_bus_is_only_public_execute_helper(self):
        """Documentation + export surface: mutations go through GovernedActionBus."""
        self.assertIn("GovernedActionBus", gs.__all__)
        self.assertIn("ActionDenied", gs.__all__)
        self.assertTrue(hasattr(gs, "GovernedActionBus"))
        self.assertEqual(PUBLIC_EXECUTE_HELPERS, frozenset({"GovernedActionBus"}))
        # No free-function execute / send_now that bypasses the bus.
        self.assertFalse(hasattr(gs, "execute"))
        self.assertFalse(hasattr(gs, "send_now"))
        self.assertFalse(hasattr(gs, "mutate"))
        # Package has no free-function mutation entry — only the bus class.
        bypass_names = [n for n in gs.__all__ if n in ("execute", "send_now", "mutate")]
        self.assertEqual(bypass_names, [])
        self.assertIn("PUBLIC_EXECUTE_HELPERS", gs.__all__)
        self.assertTrue(callable(GovernedActionBus.execute))
        self.assertTrue(callable(GovernedActionBus.execute_sync))
        self.assertEqual(__version__, "0.5.0")

    def test_execute_sync_allow(self):
        bus = self.make_bus()
        calls: list[int] = []

        def side_effect(result: dict) -> int:
            calls.append(1)
            return 1

        out = bus.execute_sync(
            "mail",
            to="alice@example.com",
            subject="Hi",
            body="Hello there",
            side_effect=side_effect,
        )
        self.assertEqual(out["decision"], "ALLOW")
        self.assertEqual(calls, [1])

    async def test_unknown_channel_raises(self):
        bus = self.make_bus()
        with self.assertRaises(ValueError):
            await bus.execute(
                "fax",
                side_effect=lambda r: None,
            )


if __name__ == "__main__":
    unittest.main()
