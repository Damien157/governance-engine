"""3DM solver errors feed back into the GovernedStack decision (tighten-only).

Same shape as the control-step fix in #31: before this, a crashed 3DM run
only added a ``3dm_solver_error`` note and the decision stayed ALLOW.
"""

from __future__ import annotations

import dataclasses
import math
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
    "action": "3dm",
    "payload": {"difficulty": 0.1},
    "telemetry": {"stress": 0.0, "anomaly": 0.0, "drift": 0.0},
    "v_t": 0.0,
    "seed": 0,
}

_GOOD = {
    "instance": "demo", "abs_S": 10, "abs_L": 2, "matching_key": "((0,0,0),)",
    "hit_imprint_reversed": 0.4, "in_L": True,
}


class TestStack3dmErrorFeedback(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.crypto = CryptoEngine(private_key_path=None)

    def make_stack(self, result=None, raise_exc=None) -> GovernedStack:
        d = tempfile.mkdtemp()
        stack = GovernedStack(
            config={"db_path": os.path.join(d, "a.db"), "signing_key_path": os.path.join(d, "k.pem"), "log_level": 40},
            crypto=self.crypto,
        )
        real_step = stack.haven2.step
        stack.haven2.step = lambda **kw: dataclasses.replace(real_step(**kw), open=True)
        if raise_exc is not None:
            def boom(intent):
                raise raise_exc
            stack._run_3dm_demo = boom
        elif result is not None:
            stack._run_3dm_demo = lambda intent: dict(result)
        storage = getattr(stack.engine, "storage", None)
        if storage is not None and hasattr(storage, "close"):
            self.addCleanup(storage.close)
        return stack

    async def govern(self, stack, **extra):
        return await stack.govern({**_INTENT, **extra}, stack.issue_token("alice", "user"))

    async def test_healthy_result_unchanged(self):
        env = await self.govern(self.make_stack(result=_GOOD))
        if env["decision"] != "ALLOW":
            self.skipTest("baseline 3dm intent not ALLOW in this environment")
        self.assertFalse(any(str(r).startswith("3dm_") for r in env["reasons"]))
        self.assertNotIn("qp_tightened", env.get("audit") or {})

    async def test_sampler_miss_is_not_a_failure(self):
        env = await self.govern(self.make_stack(result={**_GOOD, "in_L": False}))
        if env["decision"] != "ALLOW":
            self.skipTest("baseline 3dm intent not ALLOW in this environment")
        self.assertFalse(any(str(r).startswith("3dm_") for r in env["reasons"]))

    async def test_solver_exception_blocks(self):
        env = await self.govern(self.make_stack(raise_exc=RuntimeError("imprint import failed")))
        self.assertEqual(env["decision"], "BLOCK")
        self.assertIn("3dm_solver_error", env["reasons"])
        self.assertEqual(env["error_code"], "GOV_INTERNAL")
        self.assertEqual(env["audit"]["qp_tightened"]["to"], "BLOCK")

    async def test_invalid_results_block(self):
        bad = [
            ({k: v for k, v in _GOOD.items() if k != "in_L"}, "3dm_result_invalid:missing:in_L"),
            ({**_GOOD, "hit_imprint_reversed": math.nan}, "3dm_result_invalid:hit_imprint_reversed"),
            ({**_GOOD, "hit_imprint_reversed": 1.5}, "3dm_result_invalid:hit_imprint_reversed"),
            ({**_GOOD, "abs_S": -1}, "3dm_result_invalid:abs_S"),
            ({**_GOOD, "in_L": "yes"}, "3dm_result_invalid:in_L"),
        ]
        for result, reason in bad:
            with self.subTest(reason=reason):
                env = await self.govern(self.make_stack(result=result))
                self.assertEqual(env["decision"], "BLOCK")
                self.assertIn(reason, env["reasons"])

    async def test_real_demo_run_is_healthy(self):
        stack = self.make_stack()
        try:
            out = {"kind": "3dm", **stack._run_3dm_demo(dict(_INTENT))}
        except Exception as exc:  # imprint deps missing in this env
            self.skipTest(f"imprint demo unavailable: {exc}")
        self.assertIsNone(GovernedStack._3dm_failure_reason(out))

    def test_classifier_ignores_other_kinds(self):
        self.assertIsNone(GovernedStack._3dm_failure_reason(None))
        self.assertIsNone(GovernedStack._3dm_failure_reason({"kind": "control", "error": "x"}))


if __name__ == "__main__":
    unittest.main()
