"""Maths of the OSQP CLF-CBF filters in solvers/ (SKETCH-ONLY: off the live govern path).

Both modules are catalogued as `sketch`; the live controller is the CVXOPT one
(tests/test_math_control_qp.py).  Their maths is still real and is verified here.

solvers/governance_engine_osqp_qp.py  (hard CLF, no slack)
    min_u ||u - u_nom||^2   s.t.  A_cbf u <= b_cbf,  lg_v . u <= -lf_v - V,  u_min <= u <= u_max
    = Euclidean projection of u_nom onto a polytope.  Oracle: exact active-set enumeration
    (all candidate points = u_nom, projections onto each boundary hyperplane, pairwise
    vertices; take the feasible candidate nearest to u_nom) -- valid for dim_u <= 2.

solvers/hais_osqp_clf_cbf_engine.py  (soft CLF via slack delta, weight lambda)
    min (1/2)||u - u_nom||^2 + (1/2) lambda delta^2
    s.t. A_cbf u <= b_cbf,   LgV u - delta <= -LfV - alpha V
    For dim_u = 1 the optimum is  u* = clip(m, lo, hi),
        m = u_nom                                   if LgV u_nom <= c
          = (u_nom + lambda LgV c)/(1 + lambda LgV^2)   otherwise,       c = -LfV - alpha V
    lo/hi from the CBF rows (a u <= b -> hi = b/a for a > 0, lo = b/a for a < 0).

Closed-form HOCBF for the double integrator xdot = [v, u], h = p_max - |p|:
    p >= 0:   psi = hdot + a0 h = -v + a0 h,  psidot + a1 psi >= 0  <=>  u <= -(a0+a1) v + a0 a1 h
    p <  0:   psi =  v + a0 h               <=>  -u <= (a0+a1) v + a0 a1 h
QuadraticCLF V = (p^2 + v^2)/2:  LfV = p v,  LgV = v,  so  Vdot = v (p + u).
Discretisation: p+ = p + dt v,  v+ = v + dt u.
"""
from __future__ import annotations

import itertools
import logging

import numpy as np
import pytest
from solvers.governance_engine_osqp_qp import GovernanceEngine as HardEngine
from solvers.hais_osqp_clf_cbf_engine import (
    DoubleIntegratorDynamics,
    PositionCBF,
    QuadraticCLF,
    SurrogatePositionCBFBroken,
    forward_invariant,
    min_barrier_value,
    nominal_pd_controller,
    run_closed_loop,
)
from solvers.hais_osqp_clf_cbf_engine import (
    GovernanceEngine as SoftEngine,
)

pytestmark = pytest.mark.filterwarnings("ignore::PendingDeprecationWarning")  # osqp raise_error notice

SEED = 424242

# OSQP defaults eps_abs = eps_rel = 1e-3 (the hard engine does not polish); observed worst
# solution error ~1e-3 on these tiny QPs, so use 5e-3.
OSQP_TOL = 5e-3


@pytest.fixture(autouse=True)
def _quiet_logs():
    logging.disable(logging.CRITICAL)
    yield
    logging.disable(logging.NOTSET)


def project_polytope(u_nom, A, b):
    """Nearest point to u_nom in {u : A u <= b} by active-set enumeration (dim <= 2)."""
    u_nom = np.asarray(u_nom, float)
    A = np.asarray(A, float)
    b = np.asarray(b, float)
    n = A.shape[0]
    d = u_nom.size
    cands = [u_nom.copy()]
    for i in range(n):
        ai = A[i]
        cands.append(u_nom - ((ai @ u_nom - b[i]) / (ai @ ai)) * ai)
    if d == 2:
        for i, j in itertools.combinations(range(n), 2):
            M = np.array([A[i], A[j]])
            if abs(np.linalg.det(M)) > 1e-12:
                cands.append(np.linalg.solve(M, np.array([b[i], b[j]])))
    best, best_d = None, np.inf
    for c in cands:
        if np.all(A @ c <= b + 1e-9):
            dist = np.linalg.norm(c - u_nom)
            if dist < best_d:
                best, best_d = c, dist
    return best  # None if the polytope is empty


# ------------------------------------------------------------------ hard-CLF engine


def test_hard_engine_1d_equals_interval_clip():
    rng = np.random.default_rng(SEED)
    for _ in range(100):
        un = rng.uniform(-3, 3)
        lg, lf, V = rng.uniform(0.3, 2) * rng.choice([-1, 1]), rng.normal(), rng.uniform(0, 2)
        cb = rng.uniform(0.2, 2) * rng.choice([-1, 1])
        bb = rng.uniform(0.5, 3)
        eng = HardEngine(1, 2, u_min=np.array([-2.0]), u_max=np.array([2.0]))
        u, st = eng.solve(np.array([un]), np.array([[cb]]), np.array([bb]), V, lf, np.array([lg]))
        A = np.array([[cb], [lg], [1.0], [-1.0]])
        b = np.array([bb, -lf - V, 2.0, 2.0])
        expect = project_polytope([un], A, b)
        if expect is None:
            assert st == "FAIL_SAFE_TRIGGERED"
            continue
        assert st == "OPTIMAL_SAFE"
        assert u[0] == pytest.approx(expect[0], abs=OSQP_TOL)


def test_hard_engine_2d_is_euclidean_projection():
    rng = np.random.default_rng(SEED + 1)
    n_ok = 0
    for _ in range(80):
        un = rng.uniform(-3, 3, size=2)
        A_cbf = rng.normal(size=(1, 2))
        b_cbf = rng.uniform(0.2, 2.0, size=1)
        lg = rng.normal(size=2)
        lf, V = rng.normal(), rng.uniform(0.0, 1.0)
        eng = HardEngine(2, 1, u_min=np.array([-2.0, -2.0]), u_max=np.array([2.0, 2.0]))
        u, st = eng.solve(un, A_cbf, b_cbf, V, lf, lg)
        A = np.vstack([A_cbf, lg[None, :], np.eye(2), -np.eye(2)])
        b = np.concatenate([b_cbf, [-lf - V], [2.0] * 4])
        expect = project_polytope(un, A, b)
        if expect is None:
            assert st == "FAIL_SAFE_TRIGGERED"
            continue
        n_ok += 1
        assert st == "OPTIMAL_SAFE"
        assert np.linalg.norm(u - expect) < 3 * OSQP_TOL
        # primal feasibility within OSQP tolerance
        assert np.all(A @ u <= b + 5e-3)
    assert n_ok > 40


def test_hard_engine_input_validation_and_failsafe_accounting():
    with pytest.raises(ValueError):
        HardEngine(1, 1, u_min=np.array([1.0]), u_max=np.array([-1.0]))
    eng = HardEngine(1, 1, u_min=np.array([-1.0]), u_max=np.array([1.0]))
    # structurally impossible row 0 * u <= -1  -> failsafe (u = 0), counter increments
    for k in (1, 2, 3):
        u, st = eng.solve(np.array([0.5]), np.array([[0.0]]), np.array([-1.0]), 1.0, 0.0, np.array([0.0]))
        assert st == "FAIL_SAFE_TRIGGERED" and u[0] == 0.0
        assert eng.failsafe_active and eng.failsafe_steps == k
    # a valid solve clears the episode
    u, st = eng.solve(np.array([0.5]), np.array([[1.0]]), np.array([2.0]), 1.0, 0.0, np.array([0.0]))
    assert st == "OPTIMAL_SAFE" and u[0] == pytest.approx(0.5, abs=OSQP_TOL)
    assert not eng.failsafe_active and eng.failsafe_steps == 0
    # shape / count errors and NaNs
    assert eng.solve(np.array([0.5, 0.1]), np.array([[1.0]]), np.array([1.0]), 1.0, 0, np.array([0.0]))[1] \
        == "FAIL_SAFE_TRIGGERED"
    assert eng.solve(np.array([0.5]), np.array([[1.0], [1.0]]), np.array([1.0, 1.0]), 1.0, 0, np.array([0.0]))[1] \
        == "FAIL_SAFE_TRIGGERED"  # more than max_cbf_constraints rows
    assert eng.solve(np.array([0.5]), np.array([[np.nan]]), np.array([1.0]), 1.0, 0, np.array([0.0]))[1] \
        == "FAIL_SAFE_TRIGGERED"


def test_hard_engine_infeasible_intersection_triggers_failsafe():
    """CBF demands u <= -1, actuator box demands u >= 0: empty set -> OSQP infeasible -> failsafe."""
    eng = HardEngine(1, 1, u_min=np.array([0.0]), u_max=np.array([1.0]))
    u, st = eng.solve(np.array([0.5]), np.array([[1.0]]), np.array([-1.0]), 1.0, 0.0, np.array([0.0]))
    assert st == "FAIL_SAFE_TRIGGERED" and u[0] == 0.0


# ------------------------------------------------------------------ soft-CLF (HAIS) engine


def soft_oracle(un, A, b, V, lf, lg, alpha, lam):
    hi = min((bi / ai[0] for ai, bi in zip(A, b) if ai[0] > 0), default=np.inf)
    lo = max((bi / ai[0] for ai, bi in zip(A, b) if ai[0] < 0), default=-np.inf)
    if lo > hi:
        return None
    c = -lf - alpha * V
    m = un if lg * un <= c else (un + lam * lg * c) / (1 + lam * lg**2)
    u = min(max(m, lo), hi)
    return u, max(0.0, lg * u - c)


def test_soft_engine_matches_closed_form_1d():
    rng = np.random.default_rng(SEED + 2)
    eng = SoftEngine(1, 4, alpha=1.0, lambda_slack=1e3)
    n_checked = 0
    for _ in range(200):
        un = rng.uniform(-5, 5)
        lg = rng.normal()
        lf = rng.normal()
        V = rng.uniform(0, 2)
        rows = int(rng.integers(1, 4))
        A = rng.choice([-1.0, 1.0], size=(rows, 1)) * rng.uniform(0.3, 2, size=(rows, 1))
        b = rng.uniform(0.5, 3, size=rows)
        exp_ = soft_oracle(un, A, b, V, lf, np.array([lg])[0], 1.0, 1e3)
        u, st = eng.solve(np.array([un]), A, b, V, lf, np.array([lg]))
        if exp_ is None:
            assert st == "FAIL_SAFE_TRIGGERED"
            continue
        n_checked += 1
        assert st == "OPTIMAL_SAFE"
        assert u[0] == pytest.approx(exp_[0], abs=OSQP_TOL)
        assert np.all(A @ u <= b + 1e-3)  # CBF rows are hard
    assert n_checked > 100


def test_soft_engine_slack_is_what_makes_conflicts_feasible():
    """CLF wants u >= 5 (LgV = -1, c = -5) but CBF caps u <= 1: soft engine returns u = 1, not failsafe."""
    eng = SoftEngine(1, 2, alpha=1.0, lambda_slack=1e3)
    u, st = eng.solve(np.array([0.0]), np.array([[1.0]]), np.array([1.0]), 5.0, 0.0, np.array([-1.0]))
    assert st == "OPTIMAL_SAFE"
    # closed form: m = (0 + 1e3 * (-1) * (-5)) / (1 + 1e3) = 4.995... -> clipped to hi = 1
    assert u[0] == pytest.approx(1.0, abs=1e-3)


def test_soft_engine_failsafe_escalation_and_reset():
    eng = SoftEngine(1, 1)
    for _ in range(eng.failsafe_escalation_threshold):
        u, st = eng.solve(np.array([0.0]), np.array([[0.0]]), np.array([-1.0]), 1.0, 0.0, np.array([0.0]))
        assert st == "FAIL_SAFE_TRIGGERED" and u[0] == 0.0
    assert eng.failsafe_steps == 50 and eng.failsafe_escalated
    eng.reset_failsafe()
    assert (eng.failsafe_steps, eng.failsafe_active, eng.failsafe_escalated) == (0, False, False)


def test_soft_engine_rejects_non_finite_and_bad_shapes():
    eng = SoftEngine(1, 2)
    ok = dict(A_cbf=np.array([[1.0]]), b_cbf=np.array([1.0]), V_x=1.0, lf_v=0.0, lg_v=np.array([0.0]))
    assert eng.solve(np.array([0.0]), **ok)[1] == "OPTIMAL_SAFE"
    for bad in (
        dict(ok, V_x=np.inf),
        dict(ok, lf_v=np.nan),
        dict(ok, b_cbf=np.array([np.inf])),
        dict(ok, A_cbf=np.array([[1.0, 2.0]])),
        dict(ok, lg_v=np.array([0.0, 0.0])),
    ):
        assert eng.solve(np.array([0.0]), **bad)[1] == "FAIL_SAFE_TRIGGERED"
    assert eng.solve(np.array([np.nan]), **ok)[1] == "FAIL_SAFE_TRIGGERED"


# ------------------------------------------------------------------ PositionCBF / CLF / dynamics


def test_position_cbf_rows_match_hand_derivation():
    rng = np.random.default_rng(SEED + 3)
    for a0, a1 in ((2.0, 2.0), (1.0, 3.0), (0.5, 0.7)):
        cbf = PositionCBF(1.0, a0, a1)
        for _ in range(100):
            p, v = rng.uniform(-1.5, 1.5), rng.uniform(-3, 3)
            A, b = cbf.build(np.array([p, v]))
            if p >= 0:
                h = 1.0 - p
                assert A[0, 0] == 1.0
                assert b[0] == pytest.approx(-(a0 + a1) * v + a0 * a1 * h, abs=1e-12)
            else:
                h = 1.0 + p
                assert A[0, 0] == -1.0
                assert b[0] == pytest.approx((a0 + a1) * v + a0 * a1 * h, abs=1e-12)
            assert cbf.h(np.array([p, v])) == pytest.approx(1.0 - abs(p))


def test_position_cbf_row_is_psi_dot_condition_for_double_integrator():
    """For p >= 0:  psidot + a1 psi = (b - u) for psi = -v + a0 (p_max - p) and pddot = u."""
    a0, a1 = 2.0, 3.0
    cbf = PositionCBF(1.0, a0, a1)
    rng = np.random.default_rng(SEED + 4)
    for _ in range(100):
        p, v, u = rng.uniform(0, 1), rng.uniform(-2, 2), rng.uniform(-9, 9)
        psi = -v + a0 * (1 - p)
        psidot = -u - a0 * v
        _, b = cbf.build(np.array([p, v]))
        assert psidot + a1 * psi == pytest.approx(b[0] - u, abs=1e-12)


def test_quadratic_clf_lie_derivatives():
    clf = QuadraticCLF()
    rng = np.random.default_rng(SEED + 5)
    for _ in range(100):
        p, v, u = rng.uniform(-2, 2, size=3)
        V, lf, lg = clf.build(np.array([p, v]))
        assert V == pytest.approx(0.5 * (p * p + v * v))
        # Vdot along xdot = [v, u] is  p v + v u
        assert lf + lg[0] * u == pytest.approx(p * v + v * u, abs=1e-13)


def test_double_integrator_discretisation():
    dyn = DoubleIntegratorDynamics(dt=0.02)
    x1 = dyn.step(np.array([1.0, 2.0]), np.array([3.0]))
    np.testing.assert_allclose(x1, [1.0 + 0.02 * 2.0, 2.0 + 0.02 * 3.0], atol=1e-15)
    # free flight (u = 0) for N steps is exactly p0 + N dt v0
    x = np.array([0.0, 1.5])
    for _ in range(100):
        x = dyn.step(x, np.array([0.0]))
    assert x[0] == pytest.approx(100 * 0.02 * 1.5, abs=1e-12)


def test_nominal_pd_controller_formula():
    u = nominal_pd_controller(np.array([0.4, -0.2]), kp=2.0, kd=1.0)
    assert u[0] == pytest.approx(-2.0 * 0.4 - 1.0 * -0.2)


def test_closed_loop_hocbf_forward_invariance_random_adversaries():
    rng = np.random.default_rng(SEED + 6)
    for _ in range(12):
        p0 = rng.uniform(-0.99, 0.99)
        h0 = 1 - abs(p0)
        # psi(x0) >= 0:   p >= 0:  v <= a0 h ;   p < 0:  v >= -a0 h   (a0 = 2)
        v0 = rng.uniform(-3, 2 * h0) if p0 >= 0 else rng.uniform(-2 * h0, 3)
        signs = rng.choice([-5.0, 5.0], size=40)
        counter = [0]

        def nominal(x):
            counter[0] += 1
            return np.array([signs[(counter[0] // 10) % 40]])

        tr = run_closed_loop(
            SoftEngine(1, 4), DoubleIntegratorDynamics(0.01), PositionCBF(1.0, 2.0, 2.0),
            QuadraticCLF(1.0), nominal, np.array([p0, v0]), steps=400,
        )
        assert set(tr.statuses) == {"OPTIMAL_SAFE"}
        assert forward_invariant(tr, lambda x: 1.0 - abs(x[0]), tol=1e-4)
        assert min_barrier_value(tr, lambda x: 1.0 - abs(x[0])) >= -1e-4


def test_broken_surrogate_row_does_not_forbid_acceleration_into_the_wall():
    """Documented pre-0.4.4 defect: the surrogate row is a LOWER bound on u only.

    At p = 0.99, v = +5 pushing u = +1e3 is admitted by the broken row but rejected by PositionCBF.
    """
    x = np.array([0.99, 5.0])
    A_b, b_b = SurrogatePositionCBFBroken(1.0).build(x)
    A_g, b_g = PositionCBF(1.0, 2.0, 2.0).build(x)
    u_attack = 1.0e3
    assert A_b[0, 0] * u_attack <= b_b[0]  # broken row lets it through
    assert A_g[0, 0] * u_attack > b_g[0]  # HOCBF row forbids it
