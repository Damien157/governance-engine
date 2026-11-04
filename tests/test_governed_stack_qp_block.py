"""Real-CVXOPT control QP step through GovernedStack.govern (carried over from #32).

The monkeypatched failure modes (non-ok status, solver exception, voucher,
REVIEW/BLOCK never loosened, classifier) live in
``tests/test_stack_qp_status_feedback.py``; this file only adds end-to-end
checks against the real CVXOPT-backed controller.
"""
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
from governed_stack.contracts import GOV_INTERNAL  # noqa: E402

try:
    import cvxopt  # noqa: F401
    HAVE_CVXOPT = True
except ImportError:  # pragma: no cover
    HAVE_CVXOPT = False

CONTROL = {"action": "control", "payload": {"text": "step"}, "u_nom": 5.0}


@unittest.skipUnless(HAVE_CVXOPT, "cvxopt not installed")
class TestRealCvxoptQPGovernance(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.crypto = CryptoEngine(private_key_path=None)

    def make_stack(self) -> GovernedStack:
        d = tempfile.mkdtemp()
        s = GovernedStack(
            config={"db_path": os.path.join(d, "a.db"), "signing_key_path": os.path.join(d, "k.pem"), "log_level": 40},
            crypto=self.crypto,
        )
        storage = getattr(s.engine, "storage", None)
        if storage is not None and hasattr(storage, "close"):
            self.addCleanup(storage.close)
        # Force the latch open so control reaches the real solver.
        orig = s.haven2.step

        def step(**kw):
            r = orig(**kw)
            try:
                r.open = True
            except Exception:
                object.__setattr__(r, "open", True)
            return r

        s.haven2.step = step

        # Force ops ALLOW so only the QP step can tighten the decision.
        async def fake(intent, token, **kw):
            return {"status": "ok", "result": {"decision": "ALLOW", "policy_reasons": [],
                                               "entry_id": "e1", "risk_signal": 0.0, "logged": True}}

        s.engine.execute_governed_action = fake
        return s

    async def test_real_cvxopt_feasible_allows(self):
        s = self.make_stack()
        env = await s.govern(dict(CONTROL), s.issue_token("alice", "user"))
        self.assertEqual(env["solver"]["kind"], "control")
        self.assertEqual((env["solver"]["status"], env["solver"]["feasible"]), ("optimal", True))
        self.assertEqual(env["decision"], "ALLOW")
        self.assertFalse(any(str(r).startswith("qp_") for r in env["reasons"]))
        self.assertNotIn("qp_tightened", env["audit"])

    async def test_real_cvxopt_infeasible_blocks(self):
        # Far past the barrier at high speed: controller fails closed.
        s = self.make_stack()
        env = await s.govern({**CONTROL, "x": [5.0, 50.0]}, s.issue_token("alice", "user"))
        sol = env["solver"]
        self.assertIs(sol["feasible"], False)
        self.assertEqual(env["decision"], "BLOCK")
        reason = f"qp_not_feasible:{sol['status']}"
        self.assertIn(reason, env["reasons"])
        self.assertEqual(env["error_code"], GOV_INTERNAL)
        self.assertEqual(env["audit"]["ops_decision"], "ALLOW")
        self.assertEqual(env["audit"]["qp_tightened"], {"from": "ALLOW", "to": "BLOCK", "reason": reason})


if __name__ == "__main__":
    unittest.main()
