"""QP controller status feeds back into the GovernedStack decision (tighten-only)."""

from __future__ import annotations

import dataclasses
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in reversed((ROOT / "src", ROOT, ROOT / "hais", ROOT / "imprint" / "src", ROOT / "haven2" / "src")):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from certified_governance_unified import CryptoEngine  # noqa: E402

from governed_stack import GovernedStack  # noqa: E402

_INTENT = {
    "action": "control",
    "payload": {"difficulty": 0.1},
    "telemetry": {"stress": 0.0, "anomaly": 0.0, "drift": 0.0},
    "v_t": 0.0,
    "u_nom": 5.0,
}


class TestStackQpStatusFeedback(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.crypto = CryptoEngine(private_key_path=None)

    def make_stack(self) -> GovernedStack:
        d = tempfile.mkdtemp()
        stack = GovernedStack(
            config={"db_path": os.path.join(d, "a.db"), "signing_key_path": os.path.join(d, "k.pem"), "log_level": 40},
            crypto=self.crypto,
        )
        # Force the Haven2 latch open so the control step actually runs.
        real_step = stack.haven2.step
        stack.haven2.step = lambda **kw: dataclasses.replace(real_step(**kw), open=True)
        storage = getattr(stack.engine, "storage", None)
        if storage is not None and hasattr(storage, "close"):
            self.addCleanup(storage.close)
        return stack

    async def govern(self, stack, **extra):
        return await stack.govern({**_INTENT, **extra}, stack.issue_token("alice", "user"))

    async def test_healthy_optimal_solve_unchanged(self):
        env = await self.govern(self.make_stack())
        self.assertEqual(env["solver"]["status"], "optimal")
        self.assertTrue(env["solver"]["feasible"])
        self.assertFalse(env["solver"]["fail_closed"])
        self.assertEqual(env["decision"], "ALLOW")
        self.assertFalse(any(str(r).startswith("qp_") for r in env["reasons"]))
        self.assertNotIn("qp_tightened", env.get("audit") or {})
        self.assertIsNone(env.get("error_code"))

    def force_ops_decision(self, stack, decision):
        real = stack.engine.execute_governed_action
        seen = {}

        async def wrapped(intent, token, **kw):
            seen.update(kw)
            ops = await real(intent, token, **{k: v for k, v in kw.items() if k != "approval_voucher"})
            ops.setdefault("result", {})["decision"] = decision
            return ops

        stack.engine.execute_governed_action = wrapped
        return seen

    def assert_qp_block(self, env, reason):
        self.assertEqual(env["decision"], "BLOCK")
        self.assertIn(reason, env["reasons"])
        self.assertEqual(env["audit"]["qp_tightened"], {"from": "ALLOW", "to": "BLOCK", "reason": reason})
        self.assertTrue(any(reason in n for n in env["notes"]))
        self.assertEqual(env["error_code"], "GOV_INTERNAL")

    async def test_infeasible_step_allow_becomes_block(self):
        env = await self.govern(self.make_stack(), x=[0.5, 20.0])
        s = env["solver"]
        self.assertFalse(s["feasible"])
        self.assertTrue(s["fail_closed"])
        self.assertEqual(s["status"], "infeasible_barrier")
        self.assert_qp_block(env, "qp_not_feasible:infeasible_barrier")

    async def test_non_ok_status_allow_becomes_block(self):
        stack = self.make_stack()
        real = stack._run_qp_step

        def degraded(intent):
            out = real(intent)
            out.update(status="cbf_only_after:unknown", feasible=True, fail_closed=False)
            return out

        stack._run_qp_step = degraded
        env = await self.govern(stack)
        self.assert_qp_block(env, "qp_not_feasible:cbf_only_after:unknown")

    async def test_solver_exception_allow_becomes_block(self):
        stack = self.make_stack()

        def boom(intent):
            raise RuntimeError("kaboom")

        stack._run_qp_step = boom
        env = await self.govern(stack)
        self.assert_qp_block(env, "qp_solver_error")
        self.assertTrue(any("qp_solver_error:kaboom" in n for n in env["notes"]))

    async def test_voucher_allow_cannot_reopen_qp_block(self):
        stack = self.make_stack()
        seen = self.force_ops_decision(stack, "ALLOW")  # as if voucher honored by ops
        env = await stack.govern(
            {**_INTENT, "x": [0.5, 20.0]}, stack.issue_token("alice", "user"),
            approval_voucher="voucher-xyz",
        )
        self.assertEqual(seen.get("approval_voucher"), "voucher-xyz")
        self.assert_qp_block(env, "qp_not_feasible:infeasible_barrier")

    async def test_review_and_block_never_loosened(self):
        for dec in ("REVIEW", "BLOCK"):
            self.assertEqual(GovernedStack._tighten(dec, "ALLOW"), dec)
            self.assertEqual(GovernedStack._tighten(dec, "BLOCK"), "BLOCK")
            stack = self.make_stack()
            self.force_ops_decision(stack, dec)
            env = await self.govern(stack)  # healthy feasible start
            self.assertEqual(env["decision"], dec)
            self.assertNotIn("qp_tightened", env["audit"])
        self.assertEqual(GovernedStack._tighten("weird", "ALLOW"), "BLOCK")
        # Latch closed -> BLOCK, solver skipped, stays BLOCK.
        d = tempfile.mkdtemp()
        stack = GovernedStack(
            config={"db_path": os.path.join(d, "a.db"), "signing_key_path": os.path.join(d, "k.pem"), "log_level": 40},
            crypto=self.crypto,
        )
        env = await self.govern(stack, x=[0.5, 20.0])
        self.assertEqual(env["decision"], "BLOCK")

    def test_failure_reason_classifier(self):
        f = GovernedStack._qp_failure_reason
        self.assertIsNone(f(None))
        self.assertIsNone(f({"kind": "control", "status": "optimal", "feasible": True, "fail_closed": False}))
        self.assertEqual(f({"kind": "control", "error": "x"}), "qp_solver_error")
        self.assertEqual(
            f({"kind": "control", "status": "error:foo", "feasible": False, "fail_closed": True}),
            "qp_not_feasible:error:foo",
        )
        self.assertEqual(
            f({"kind": "control", "status": "cbf_only_after:unknown", "feasible": True, "fail_closed": False}),
            "qp_not_feasible:cbf_only_after:unknown",
        )
        self.assertIsNone(f({"kind": "3dm", "error": "x"}))


if __name__ == "__main__":
    unittest.main()
