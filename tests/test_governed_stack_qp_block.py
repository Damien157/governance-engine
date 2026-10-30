"""QP control step must tighten the stack decision to BLOCK on failure (never loosen)."""
from __future__ import annotations

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
from governed_stack import stack as stack_mod  # noqa: E402
from governed_stack.contracts import GOV_INTERNAL  # noqa: E402

CONTROL = {"action": "control", "payload": {"text": "step"}, "u_nom": 5.0}


class _FakeQP:
    def __init__(self, result=None, exc=None):
        self.result, self.exc = result, exc

    def solve(self, x, u_nom):
        if self.exc:
            raise self.exc
        return dict(self.result)


def _ok(status="optimal"):
    return {"u": 0.1, "h": 0.2, "status": status, "feasible": True}


class TestQPGovernanceBlock(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.crypto = CryptoEngine(private_key_path=None)

    def make_stack(self):
        fd, db = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.addCleanup(lambda: os.path.exists(db) and os.remove(db))
        s = GovernedStack(config={"db_path": db, "signing_key_path": db + ".pem", "log_level": 40}, crypto=self.crypto)
        self.addCleanup(lambda: os.path.exists(db + ".pem") and os.remove(db + ".pem"))
        # Force the latch open so control reaches the solver.
        orig = s.haven2.step

        def step(**kw):
            r = orig(**kw)
            try:
                r.open = True
            except Exception:
                object.__setattr__(r, "open", True)
            return r

        s.haven2.step = step
        return s

    def force_ops(self, s, decision, reasons=()):
        async def fake(intent, token, **kw):
            fake.kwargs = kw
            return {"status": "ok", "result": {"decision": decision, "policy_reasons": list(reasons),
                                               "entry_id": "e1", "risk_signal": 0.0, "logged": True}}
        s.engine.execute_governed_action = fake
        return fake

    async def run_ctrl(self, s, qp, **opts):
        s._ensure_qp = lambda: qp
        logs = []
        orig = stack_mod.structured_log
        stack_mod.structured_log = lambda ev, **kw: logs.append((ev, kw))
        try:
            env = await s.govern(dict(CONTROL), s.issue_token("alice", "user"), **opts)
        finally:
            stack_mod.structured_log = orig
        return env, logs

    async def _assert_block(self, qp, reason):
        s = self.make_stack()
        self.force_ops(s, "ALLOW")
        env, logs = await self.run_ctrl(s, qp)
        self.assertEqual(env["decision"], "BLOCK")
        self.assertIn(reason, env["reasons"])
        self.assertEqual(env["error_code"], GOV_INTERNAL)
        self.assertEqual(env["audit"]["ops_decision"], "ALLOW")  # earlier CGE write kept honest via note
        self.assertTrue(any("qp override" in n for n in env["notes"]))
        final = [kw for ev, kw in logs if ev == "govern_decision"]
        self.assertEqual(final[-1]["decision"], "BLOCK")
        self.assertIn(reason, final[-1]["reasons"])

    # 1. ALLOW -> BLOCK
    async def test_infeasible_blocks(self):
        await self._assert_block(_FakeQP({"u": 0, "h": -1, "status": "optimal", "feasible": False}), "qp:infeasible")

    async def test_non_ok_status_blocks(self):
        await self._assert_block(_FakeQP({"u": 0, "h": 0, "status": "unknown", "feasible": True}), "qp:status:unknown")

    async def test_fail_closed_controller_shape_blocks(self):
        await self._assert_block(_FakeQP({"u": 0, "h": 0, "status": "infeasible_barrier", "feasible": False}),
                                 "qp:status:infeasible_barrier")

    async def test_solver_exception_blocks(self):
        await self._assert_block(_FakeQP(exc=RuntimeError("boom")), "qp:solver_error")

    # 2. REVIEW/BLOCK never loosened (ops-level REVIEW/BLOCK skips solver; must stay).
    async def test_review_not_loosened(self):
        for dec in ("REVIEW", "BLOCK"):
            s = self.make_stack()
            self.force_ops(s, dec, ["policy_x"])
            env, _ = await self.run_ctrl(s, _FakeQP(_ok()))
            self.assertEqual(env["decision"], dec)

    def test_helper_never_loosens(self):
        self.assertEqual(GovernedStack._qp_block_reasons({"kind": "control", **_ok()}), [])
        self.assertEqual(GovernedStack._qp_block_reasons({"kind": "control", **_ok("cbf_only_after:x")}), [])
        self.assertEqual(GovernedStack._qp_block_reasons(None), [])

    # 3. ok step leaves ALLOW
    async def test_ok_step_allows(self):
        for st in ("optimal", "cbf_only_after:unknown"):
            s = self.make_stack()
            self.force_ops(s, "ALLOW")
            env, _ = await self.run_ctrl(s, _FakeQP(_ok(st)))
            self.assertEqual(env["decision"], "ALLOW", st)
            self.assertFalse(any(r.startswith("qp:") for r in env["reasons"]))

    # Voucher: passed through to ops (which ALLOWs), QP check still BLOCKs afterwards.
    async def test_voucher_cannot_reopen(self):
        s = self.make_stack()
        fake = self.force_ops(s, "ALLOW")
        env, _ = await self.run_ctrl(s, _FakeQP(exc=RuntimeError("x")), approval_voucher="v-123")
        self.assertEqual(fake.kwargs.get("approval_voucher"), "v-123")
        self.assertEqual(env["decision"], "BLOCK")

    # Real CVXOPT: feasible point -> ALLOW; infeasible barrier -> BLOCK.
    async def test_real_cvxopt(self):
        try:
            import cvxopt  # noqa: F401
        except ImportError:
            self.skipTest("cvxopt not installed")
        s = self.make_stack()
        self.force_ops(s, "ALLOW")
        qp = s._ensure_qp()
        env, _ = await self.run_ctrl(s, qp)
        self.assertEqual(env["solver"]["kind"], "control")
        self.assertEqual((env["solver"]["status"], env["solver"]["feasible"]), ("optimal", True))
        self.assertEqual(env["decision"], "ALLOW")
        # Far past the barrier at high speed: should be fail-closed infeasible.
        s2 = self.make_stack()
        self.force_ops(s2, "ALLOW")
        s2._ensure_qp = lambda: qp
        env2 = await s2.govern({**CONTROL, "x": [5.0, 50.0]}, s2.issue_token("a", "user"))
        self.solver2 = env2["solver"]
        print("\n  real cvxopt:", env["solver"].get("status"), env["decision"], "|", env2["solver"].get("status"), env2["decision"])
        self.assertIs(env2["solver"]["feasible"], False)
        self.assertEqual(env2["decision"], "BLOCK")
        self.assertIn("qp:infeasible", env2["reasons"])


if __name__ == "__main__":
    unittest.main()
