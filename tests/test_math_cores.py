"""Bug-finding maths cores: HAIS, Haven2, live CVXOPT, OSQP sketch honesty, control_law, CDCL.

Pure maths / sketch honesty — does not put sketches on the live govern path.
Does not change HAIS sigmoid midpoint away from m=0.5.
"""
from __future__ import annotations

import math
import os
import subprocess
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "src", ROOT / "hais", ROOT / "haven2" / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)


# ---------------------------------------------------------------------------
# HAIS SovereignKernel (m=0.5)
# ---------------------------------------------------------------------------


class TestHAISSovereignKernel(unittest.TestCase):
    def setUp(self) -> None:
        from hais_unified_kernel import SovereignKernel

        self.Kernel = SovereignKernel
        self.kernel = SovereignKernel(beta=0.5)

    def test_m_default_is_half(self) -> None:
        """Sigmoid midpoint used by evaluate_state must stay at 0.5."""
        out = self.kernel.evaluate_state(
            0.5, {"stress": 0.5, "anomaly": 0.5, "drift": 0.5}
        )
        S_mid = self.Kernel.sigmoid(0.5, m=0.5)
        self.assertAlmostEqual(out["S"], S_mid, places=12)
        self.assertAlmostEqual(S_mid, 0.5, places=12)

    def test_low_risk_cap_greater_than_high_and_threshold(self) -> None:
        low = self.kernel.evaluate_state(
            0.0, {"stress": 0.0, "anomaly": 0.0, "drift": 0.0}
        )
        high = self.Kernel(beta=0.5).evaluate_state(
            1.0, {"stress": 1.0, "anomaly": 1.0, "drift": 1.0}
        )
        self.assertGreater(low["capability_cap"], high["capability_cap"])
        self.assertGreater(low["capability_cap"], 0.25)
        # Old m=0 jam: best-case cap ≈ 0.134 — must not reappear at m=0.5
        self.assertGreater(low["capability_cap"], 0.2)
        self.assertFalse(abs(low["capability_cap"] - 0.134) < 0.02)

    def test_stress_sweep_throttle_not_all_or_nothing(self) -> None:
        from hais_unified_kernel import production_governed_pipeline

        throttle = 0
        n = 0
        for stress_val in [i / 10 for i in range(0, 11)]:
            out = production_governed_pipeline(
                {"id": f"stress_{stress_val}", "difficulty": 0.5},
                {"stress": stress_val, "anomaly": 0.1, "drift": 0.05},
            )
            n += 1
            if str(out["status"]).startswith("throttled"):
                throttle += 1
        self.assertEqual(n, 11)
        self.assertGreater(throttle, 0)
        self.assertLess(throttle, 11)

    def test_formulas_match_evaluate_state(self) -> None:
        beta = 0.5
        k = self.Kernel(beta=beta)
        x = 0.4
        state = {"stress": 0.2, "anomaly": 0.1, "drift": 0.3}
        out = k.evaluate_state(x, state)
        risk = (x + state["stress"] + state["anomaly"] + state["drift"]) / 4.0
        S = 1.0 / (1.0 + math.exp(-4.0 * (risk - 0.5)))
        tau = 1.0 + beta * math.exp(S)
        r_prime = S * tau
        cap = math.exp(-2.2 * r_prime)
        self.assertAlmostEqual(out["risk_metric"], risk, delta=1e-9)
        self.assertAlmostEqual(out["S"], S, delta=1e-9)
        self.assertAlmostEqual(out["tau"], tau, delta=1e-9)
        self.assertAlmostEqual(out["r_prime"], r_prime, delta=1e-9)
        self.assertAlmostEqual(out["capability_cap"], cap, delta=1e-9)


# ---------------------------------------------------------------------------
# Haven2Engine
# ---------------------------------------------------------------------------


class TestHaven2Engine(unittest.TestCase):
    def test_energy_recurrence(self) -> None:
        from haven2.engine import Haven2Engine

        rho = 0.9
        c = 0.1
        eng = Haven2Engine(rho=rho, e0=0.0, e_star=1.0, epsilon_switch=0.05)
        e = 0.0
        for _ in range(15):
            rec = eng.step(c_t=c, v_t=0.02)
            e = rho * e + c
            self.assertAlmostEqual(rec.e, e, places=12)
            self.assertAlmostEqual(eng.energy.e, e, places=12)

    def test_latch_does_not_flip_wildly_on_tiny_noise(self) -> None:
        """Closed latch (large residual) ignores alternating TargetRealm noise."""
        from haven2.engine import Haven2Engine
        from haven2.realms import Realm

        eng = Haven2Engine(
            rho=0.9,
            epsilon_switch=0.05,
            e0=5.0,
            e_star=0.0,
            initial_realm=Realm.NORMAL,
        )
        switches = 0
        for i in range(40):
            v = 0.001 if (i % 2 == 0) else 0.05
            rec = eng.step(c_t=0.5, v_t=v)
            if rec.switched:
                switches += 1
            self.assertFalse(rec.open)
            self.assertEqual(rec.realm, Realm.NORMAL)
        self.assertEqual(switches, 0)


# ---------------------------------------------------------------------------
# Live CVXOPT QP (governance_engine)
# ---------------------------------------------------------------------------


class TestLiveCVXOPTQP(unittest.TestCase):
    def test_plant_AB_shapes(self) -> None:
        from governance_engine.plant import A, B

        self.assertEqual(A.shape, (2, 2))
        self.assertEqual(B.shape, (2, 1))
        np.testing.assert_allclose(A, [[0, 1], [0, -0.1]])
        np.testing.assert_allclose(B, [[0], [1]])

    def test_hocbf_overrides_unsafe_u_nom(self) -> None:
        from governance_engine.cbf import ControlBarrierFunction
        from governance_engine.clf import ControlLyapunovFunction, default_P
        from governance_engine.plant import Plant
        from governance_engine.qp_controller import CLFCBFQPController

        plant = Plant()
        clf = ControlLyapunovFunction(
            plant, x_c=np.array([0.5, 0.0]), P=default_P(), k_lyap=1.0
        )
        cbf = ControlBarrierFunction(plant, p_max=1.0, k_cbf=2.0)
        ctrl = CLFCBFQPController(clf, cbf)
        x = np.array([0.95, 0.8])
        u_nom = 20.0
        sol = ctrl.solve(x, u_nom=u_nom)
        self.assertTrue(sol["feasible"])
        a, b = cbf.extended_constraint_coeffs(x)
        bound = b / a
        self.assertLess(sol["u"], bound + 1e-6)
        self.assertLess(sol["u"], u_nom)


# ---------------------------------------------------------------------------
# OSQP sketch (NOT on govern path; PositionCBF HOCBF forward-invariant)
# ---------------------------------------------------------------------------


class TestOSQPSketchHonesty(unittest.TestCase):
    def test_structural_failsafe(self) -> None:
        from solvers.hais_osqp_clf_cbf_engine import GovernanceEngine

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

    def test_position_cbf_closed_loop_forward_invariant(self) -> None:
        """Fixed: HOCBF PositionCBF keeps the safe set under closed-loop QP."""
        from solvers.hais_osqp_clf_cbf_engine import (
            DoubleIntegratorDynamics,
            GovernanceEngine,
            PositionCBF,
            QuadraticCLF,
            forward_invariant,
            min_barrier_value,
            nominal_pd_controller,
            run_closed_loop,
        )

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

    def test_legacy_surrogate_not_forward_invariant(self) -> None:
        """Regression: old surrogate barrier fails forward-invariance."""
        from solvers.hais_osqp_clf_cbf_engine import (
            DoubleIntegratorDynamics,
            GovernanceEngine,
            QuadraticCLF,
            SurrogatePositionCBFBroken,
            forward_invariant,
            min_barrier_value,
            nominal_pd_controller,
            run_closed_loop,
        )

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


# ---------------------------------------------------------------------------
# control_law adaptive Re (default clamped; unclamped opt-in)
# ---------------------------------------------------------------------------


class TestControlLawAdaptiveRe(unittest.TestCase):
    def test_default_adaptive_re_update_is_clamped(self) -> None:
        from fluids.control_law import adaptive_re_update, adaptive_re_update_clamped

        Re = 1999.0
        for _ in range(50):
            Re = adaptive_re_update(0.1, Re, 0.6, 400.0, 2000.0)
        self.assertLessEqual(Re, 2000.0)
        self.assertGreaterEqual(Re, 400.0)
        # Alias identity: public adaptive_re_update == clamped behaviour.
        self.assertEqual(
            adaptive_re_update(0.1, 1999.0, 0.6, 400.0, 2000.0),
            adaptive_re_update_clamped(0.1, 1999.0, 0.6, 400.0, 2000.0),
        )

    def test_unclamped_opt_in_can_overshoot(self) -> None:
        """Regression: unclamped is opt-in only and can leave [Re_min, Re_max]."""
        from fluids.control_law import adaptive_re_update_unclamped

        Re_max = 2000.0
        Re = adaptive_re_update_unclamped(0.1, 1999.0, 0.6, 400.0, Re_max)
        self.assertGreater(Re, Re_max)
        Re_min = 400.0
        Re2 = adaptive_re_update_unclamped(1.0, 401.0, 0.6, Re_min, 2000.0)
        self.assertLess(Re2, Re_min)


# ---------------------------------------------------------------------------
# CDCL external binary (optional)
# ---------------------------------------------------------------------------


class TestCDCL(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cdcl = ROOT / "solvers" / "cdcl"
        cls.sat_cnf = ROOT / "solvers" / "cnf" / "smoke_sat.cnf"
        cls.unsat_cnf = ROOT / "solvers" / "cnf" / "smoke_unsat.cnf"

    def _have_binary(self) -> bool:
        return self.cdcl.is_file() and os.access(self.cdcl, os.X_OK)

    def _run(self, cnf: Path) -> str:
        if not self._have_binary():
            self.skipTest("CDCL binary missing or not executable")
        proc = subprocess.run(
            [str(self.cdcl), str(cnf)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        return (proc.stdout or "") + (proc.stderr or "")

    def test_smoke_sat(self) -> None:
        out = self._run(self.sat_cnf)
        # Solver prints status line "SAT" (not "UNSAT")
        lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
        self.assertTrue(any(ln == "SAT" for ln in lines), msg=out)

    def test_smoke_unsat(self) -> None:
        out = self._run(self.unsat_cnf)
        lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
        self.assertTrue(any(ln == "UNSAT" for ln in lines), msg=out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
