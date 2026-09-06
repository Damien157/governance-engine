"""Tiny unittest suite for sketch OSQP GovernanceEngine (not live CVXOPT path)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from solvers.governance_engine_osqp_qp import GovernanceEngine  # noqa: E402


class TestOsqpGovernanceEngine(unittest.TestCase):
    def test_u_min_gt_u_max_raises(self):
        with self.assertRaises(ValueError):
            GovernanceEngine(
                dim_u=1,
                max_cbf_constraints=2,
                u_min=np.array([1.0]),
                u_max=np.array([-1.0]),
            )

    def test_feasible_optimal_safe(self):
        eng = GovernanceEngine(
            dim_u=1,
            max_cbf_constraints=2,
            u_min=np.array([-1.0]),
            u_max=np.array([1.0]),
        )
        u_opt, status = eng.solve(
            u_nom=0.5,
            A_cbf=np.array([[0.0]]),
            b_cbf=np.array([1.0]),
            V_x=1.0,
            lf_v=0.0,
            lg_v=np.array([0.0]),
        )
        self.assertEqual(status, "OPTIMAL_SAFE")
        self.assertEqual(u_opt.shape, (1,))
        np.testing.assert_allclose(u_opt, [0.5], atol=1e-4)

    def test_nan_cbf_failsafe(self):
        eng = GovernanceEngine(
            dim_u=1,
            max_cbf_constraints=2,
            u_min=np.array([-1.0]),
            u_max=np.array([1.0]),
        )
        _, status = eng.solve(
            u_nom=0.5,
            A_cbf=np.array([[np.nan]]),
            b_cbf=np.array([1.0]),
            V_x=1.0,
            lf_v=0.0,
            lg_v=np.array([0.0]),
        )
        self.assertEqual(status, "FAIL_SAFE_TRIGGERED")


if __name__ == "__main__":
    unittest.main(verbosity=2)
