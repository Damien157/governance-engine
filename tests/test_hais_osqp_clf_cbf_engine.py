"""Tests for sketch OSQP CLF–CBF engine (not live CVXOPT path)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from solvers.hais_osqp_clf_cbf_engine import (  # noqa: E402
    GovernanceEngine,
    DoubleIntegratorDynamics,
    PositionCBF,
    SurrogatePositionCBFBroken,
    QuadraticCLF,
    nominal_pd_controller,
    run_closed_loop,
    forward_invariant,
    min_barrier_value,
)


class TestHaisOsqpEngine(unittest.TestCase):
    def test_structural_cbf_failsafe(self):
        eng = GovernanceEngine(dim_u=1, max_cbf_constraints=4)
        u, status = eng.solve(
            u_nom=np.array([0.0]),
            A_cbf=np.array([[0.0]]),
            b_cbf=np.array([-1.0]),
            V_x=1.0,
            lf_v=0.0,
            lg_v=np.array([0.0]),
        )
        self.assertEqual(status, "FAIL_SAFE_TRIGGERED")
        np.testing.assert_array_equal(u, [0.0])

    def test_nan_cbf_failsafe(self):
        eng = GovernanceEngine(dim_u=1, max_cbf_constraints=4)
        _, status = eng.solve(
            u_nom=np.array([0.0]),
            A_cbf=np.array([[np.nan]]),
            b_cbf=np.array([1.0]),
            V_x=1.0,
            lf_v=0.0,
            lg_v=np.array([0.0]),
        )
        self.assertEqual(status, "FAIL_SAFE_TRIGGERED")

    def test_closed_loop_position_cbf_forward_invariant(self):
        """Fixed HOCBF PositionCBF keeps |p|<=p_max under closed-loop QP."""
        eng = GovernanceEngine(dim_u=1, max_cbf_constraints=4)
        cbf = PositionCBF(p_max=1.0, alpha0=2.0, alpha1=2.0)
        traj = run_closed_loop(
            engine=eng,
            dynamics=DoubleIntegratorDynamics(dt=0.01),
            cbf=cbf,
            clf=QuadraticCLF(),
            nominal_controller=nominal_pd_controller,
            x0=np.array([0.8, 0.0]),
            steps=200,
        )
        safe = forward_invariant(traj, cbf.h, tol=1e-3)
        self.assertTrue(safe)
        self.assertGreaterEqual(min_barrier_value(traj, cbf.h), -1e-3)

    def test_legacy_surrogate_not_forward_invariant(self):
        """Regression: pre-fix surrogate barrier does NOT keep the safe set."""
        eng = GovernanceEngine(dim_u=1, max_cbf_constraints=4)
        broken = SurrogatePositionCBFBroken(p_max=1.0)
        traj = run_closed_loop(
            engine=eng,
            dynamics=DoubleIntegratorDynamics(dt=0.01),
            cbf=broken,
            clf=QuadraticCLF(),
            nominal_controller=nominal_pd_controller,
            x0=np.array([0.8, 0.0]),
            steps=200,
        )
        safe = forward_invariant(traj, broken.h, tol=1e-3)
        self.assertFalse(safe)
        self.assertLess(min_barrier_value(traj, broken.h), 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
