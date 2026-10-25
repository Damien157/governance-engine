"""Maths of the live CVXOPT CLF-CBF-QP (src/governance_engine/qp_controller.py) and its simulator.

Problem solved at each state x with nominal input u_nom, z = [u, delta]:

    min_z   (u - u_nom)^2 + w delta^2                    (H = diag(2, 2w), f = [-2 u_nom, 0])
    s.t.    Lg V u - delta <= -k V - Lf V                (CLF, relaxed by delta >= 0)
            a_cbf u        <= b_cbf                      (HOCBF, hard)
            u_min <= u <= u_max,   delta >= 0

Independent closed form used as the oracle.  Eliminate delta = max(0, Lg V u - c),
c := -k V - Lf V.  The reduced cost  phi(u) = (u - u_nom)^2 + w max(0, Lg V u - c)^2
is a convex, C^1, piecewise-quadratic function of the scalar u, so the constrained
minimiser is the clip of the unconstrained minimiser m to [lo, hi]:

    m  = u_nom                                  if Lg V u_nom <= c
       = (u_nom + w Lg V c) / (1 + w Lg V^2)    otherwise
    u* = clip(m, u_min, min(u_max, b_cbf / a_cbf))        (a_cbf = 1 for the HOCBF row)
    delta* = max(0, Lg V u* - c)

Closed-loop identities (V quadratic, Euler):
    V_{k+1} = V_k + dt (Lf V + Lg V u_k) + dt^2 xdot^T P xdot
    QP row  =>  Lf V + Lg V u_k <= -k V_k + delta_k
    hence   V_{k+1} <= V_k + dt (-k V_k + delta_k) + dt^2 xdot^T P xdot       (every step)

Forward invariance (HOCBF): for x_0 with psi1(x_0) = -v_0 + k h_0 >= 0 the QP keeps
h(x_k) >= 0 up to the Euler discretisation error, for ANY nominal input sequence.

Known defects are recorded as xfail(strict=True); see the two tests at the end.
"""
from __future__ import annotations

import numpy as np
import pytest

import governance_engine.qp_controller as qpmod
from governance_engine import (
    CLFCBFQPController,
    ControlBarrierFunction,
    ControlLyapunovFunction,
    Plant,
    Simulator,
    SupervisorParams,
)
from governance_engine.clf import default_P

SEED = 7_654_321


def make(k_lyap=1.0, k_cbf=2.0, p_max=1.0, x_c=(0.5, 0.0), P=None, w=50.0, umin=-50.0, umax=50.0,
         use_hocbf=True, plant=None):
    plant = plant or Plant()
    clf = ControlLyapunovFunction(plant, np.array(x_c), P=P, k_lyap=k_lyap)
    cbf = ControlBarrierFunction(plant, p_max=p_max, k_cbf=k_cbf)
    qp = CLFCBFQPController(clf, cbf, clf_relax_weight=w, u_min=umin, u_max=umax, use_hocbf=use_hocbf)
    return plant, clf, cbf, qp


def oracle(un, Lg, c, ub, umin, umax, w):
    """Closed-form optimum of the relaxed 1-D QP (see module docstring)."""
    hi = min(ub, umax)
    lo = umin
    if lo > hi:
        return None
    m = un if Lg * un <= c else (un + w * Lg * c) / (1.0 + w * Lg**2)
    u = min(max(m, lo), hi)
    return u, max(0.0, Lg * u - c)


def phi(u, un, Lg, c, w):
    return (u - un) ** 2 + w * max(0.0, Lg * u - c) ** 2


# ------------------------------------------------------------------ closed form


def test_qp_matches_closed_form_default_controller():
    rng = np.random.default_rng(SEED)
    plant, clf, cbf, qp = make()
    worst = 0.0
    for _ in range(300):
        x = np.array([rng.uniform(-2, 1.2), rng.uniform(-5, 5)])
        un = rng.uniform(-60, 60)
        sol = qp.solve(x, un)
        Lf, Lg = clf.lie_derivatives(x)
        c = -clf.k_lyap * clf.V(x) - Lf
        _, ub = cbf.extended_constraint_coeffs(x)
        u_star, d_star = oracle(un, Lg, c, ub, -50.0, 50.0, 50.0)
        assert sol["status"] == "optimal" and sol["feasible"]
        worst = max(worst, abs(sol["u"] - u_star))
        # interior-point tolerance (observed worst ~4e-4 at |Lg V| ~ 8, w = 50)
        assert sol["u"] == pytest.approx(u_star, abs=2e-3)
        assert sol["delta"] == pytest.approx(d_star, abs=2e-2)
        # the OBJECTIVE gap is the primary optimality criterion and is much tighter
        gap = phi(sol["u"], un, Lg, c, 50.0) - phi(u_star, un, Lg, c, 50.0)
        # CVXOPT's relative duality-gap stop is applied to the objective with the constant
        # u_nom^2 dropped (value ~ -u_nom^2), so the absolute gap scales with (1 + u_nom^2).
        assert gap <= 1e-5 * (1.0 + un**2)
    assert worst < 2e-3


def test_qp_matches_closed_form_randomised_parameters():
    """Randomise k_lyap, k_cbf, p_max, x_c, P, w and the input box."""
    rng = np.random.default_rng(SEED + 1)
    for _ in range(200):
        M = rng.normal(size=(2, 2))
        P = M @ M.T + 0.2 * np.eye(2)
        k_l, k_c = rng.uniform(0.2, 3.0), rng.uniform(0.5, 4.0)
        p_max = rng.uniform(0.3, 3.0)
        x_c = (rng.uniform(-1, 1), rng.uniform(-0.5, 0.5))
        w = float(rng.choice([1.0, 10.0, 50.0, 200.0]))
        umin, umax = -rng.uniform(5, 60), rng.uniform(5, 60)
        plant, clf, cbf, qp = make(k_l, k_c, p_max, x_c, P, w, umin, umax)
        x = np.array([rng.uniform(-2, p_max + 0.3), rng.uniform(-3, 3)])
        un = rng.uniform(-70, 70)
        Lf, Lg = clf.lie_derivatives(x)
        c = -k_l * clf.V(x) - Lf
        _, ub = cbf.extended_constraint_coeffs(x)
        exp_ = oracle(un, Lg, c, ub, umin, umax, w)
        if exp_ is None:
            continue  # infeasible: covered by the xfail tests below
        sol = qp.solve(x, un)
        assert sol["status"] == "optimal"
        gap = phi(sol["u"], un, Lg, c, w) - phi(exp_[0], un, Lg, c, w)
        assert gap <= 1e-5 * (1.0 + un**2)  # see note on the dropped constant above
        assert sol["u"] == pytest.approx(exp_[0], abs=2e-3 * (1 + abs(exp_[0])))


def test_qp_optimality_certificate_against_random_feasible_points():
    """No randomly drawn feasible (u, delta) beats the QP value (convexity => global)."""
    rng = np.random.default_rng(SEED + 2)
    plant, clf, cbf, qp = make()
    for _ in range(40):
        x = np.array([rng.uniform(-1.5, 1.2), rng.uniform(-4, 4)])
        un = rng.uniform(-60, 60)
        sol = qp.solve(x, un)
        Lf, Lg = clf.lie_derivatives(x)
        c = -clf.V(x) - Lf
        _, ub = cbf.extended_constraint_coeffs(x)
        f_star = (sol["u"] - un) ** 2 + 50.0 * sol["delta"] ** 2
        us = rng.uniform(-50, min(50.0, ub), size=2000)
        ds = np.maximum(0.0, Lg * us - c) + rng.uniform(0, 1, size=2000) * rng.integers(0, 2, size=2000)
        f = (us - un) ** 2 + 50.0 * ds**2
        assert np.all(f >= f_star - 1e-5 * (1 + un**2))  # tolerance: see note in test_qp_matches_...


def test_qp_feasibility_constraints_hold_at_solution():
    rng = np.random.default_rng(SEED + 3)
    plant, clf, cbf, qp = make()
    for _ in range(200):
        x = np.array([rng.uniform(-2, 1.2), rng.uniform(-5, 5)])
        sol = qp.solve(x, rng.uniform(-80, 80))
        a, b = cbf.extended_constraint_coeffs(x)
        Lf, Lg = clf.lie_derivatives(x)
        assert a * sol["u"] <= b + 1e-5  # HOCBF (hard)
        assert -50.0 - 1e-5 <= sol["u"] <= 50.0 + 1e-5  # input limits
        assert sol["delta"] >= -1e-6  # slack non-negative
        assert Lg * sol["u"] - sol["delta"] <= -clf.V(x) - Lf + 1e-5  # relaxed CLF


def test_qp_returns_nominal_when_nominal_is_feasible():
    plant, clf, cbf, qp = make()
    x = np.array([0.5, 0.0])  # at the target, V = 0, Lf = Lg = 0 -> CLF row 0 <= 0, inactive
    sol = qp.solve(x, 0.3)
    assert sol["u"] == pytest.approx(0.3, abs=1e-5)
    assert sol["delta"] == pytest.approx(0.0, abs=1e-3)
    assert sol["V"] == 0.0
    assert sol["h"] == pytest.approx(0.5)


def test_qp_clamps_to_input_box_when_nominal_exceeds_limits():
    plant, clf, cbf, qp = make(umin=-3.0, umax=2.0)
    x = np.array([0.5, 0.0])
    assert qp.solve(x, 100.0)["u"] == pytest.approx(2.0, abs=1e-4)
    assert qp.solve(x, -100.0)["u"] == pytest.approx(-3.0, abs=1e-4)


def test_qp_hocbf_single_constraint_closed_form_u_equals_min_unom_ub():
    """With the CLF row inactive (x at target) the optimum is  u* = min(u_nom, b_cbf)."""
    plant, clf, cbf, qp = make(x_c=(0.9, 0.0))
    # x = target (V = 0).  Make the CBF bind:  x = [0.9, 0]  =>  b = 4 (1 - 0.9) = 0.4
    x = np.array([0.9, 0.0])
    _, b = cbf.extended_constraint_coeffs(x)
    assert b == pytest.approx(0.4, abs=1e-12)
    assert qp.solve(x, 5.0)["u"] == pytest.approx(0.4, abs=1e-4)
    assert qp.solve(x, 0.1)["u"] == pytest.approx(0.1, abs=1e-4)


def test_qp_clf_slack_activates_when_cbf_conflicts_with_clf():
    """Target beyond the barrier: CLF wants to push through p_max, HOCBF wins, delta > 0."""
    plant, clf, cbf, qp = make(x_c=(1.5, 0.0))
    # x = (0.95, 0): Lg V = -0.55 < 0 so the CLF row demands u >= 1.1 while HOCBF caps u <= 0.2
    x = np.array([0.95, 0.0])
    Lf, Lg = clf.lie_derivatives(x)
    c = -clf.V(x) - Lf
    _, ub = cbf.extended_constraint_coeffs(x)
    u_star, d_star = oracle(0.0, Lg, c, ub, -50, 50, 50.0)
    sol = qp.solve(x, 0.0)
    assert d_star == pytest.approx(0.495, abs=1e-3)  # hand value: 0.605 - 0.55 * 0.2 = 0.495
    assert sol["delta"] == pytest.approx(d_star, rel=2e-2)
    assert sol["u"] == pytest.approx(u_star, abs=2e-3)
    assert sol["u"] <= ub + 1e-5  # CBF (hard) is never relaxed


def test_classic_cbf_branch_used_when_use_hocbf_false_and_Lg_h_nonzero():
    """Plant with B = [1, 1]^T has relative-degree-1 barrier: hdot = -(v + u).

    Classic row  Lf h + Lg h u >= -k h  <=>  u <= k h - v  (derived by hand here).
    """
    plant = Plant(B_mat=np.array([[1.0], [1.0]]))
    _, clf, cbf, qp = make(plant=plant, use_hocbf=False, x_c=(0.9, 0.0))
    x = np.array([0.9, 0.3])
    ub = 2.0 * (1.0 - 0.9) - 0.3  # k h - v = -0.1
    Lf, Lg = clf.lie_derivatives(x)
    c = -clf.V(x) - Lf
    u_star, _ = oracle(10.0, Lg, c, ub, -50, 50, 50.0)
    sol = qp.solve(x, 10.0)
    assert sol["Lg_h"] == pytest.approx(-1.0)
    assert sol["u"] == pytest.approx(u_star, abs=2e-3)
    assert sol["u"] <= ub + 1e-5


# ------------------------------------------------------------------ solver fallbacks


def test_cbf_only_fallback_matches_closed_form(monkeypatch):
    """If the full QP is not 'optimal', _solve_cbf_only gives clip(u_nom, u_min, b/a) (CLF dropped)."""
    plant, clf, cbf, qp = make()
    real_qp = qpmod.solvers.qp
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"status": "unknown", "x": None}
        return real_qp(*a, **k)

    monkeypatch.setattr(qpmod.solvers, "qp", flaky)
    x = np.array([0.9, 0.0])
    _, ub = cbf.extended_constraint_coeffs(x)
    sol = qp.solve(x, 9.0)
    assert sol["status"] == "cbf_only_after:unknown"
    assert sol["feasible"] is True
    assert np.isnan(sol["delta"])
    assert sol["u"] == pytest.approx(min(max(9.0, -50.0), min(ub, 50.0)), abs=1e-4)


def test_last_resort_clamp_formula(monkeypatch):
    """If both QPs report non-optimal, u = clip(min(u_nom, b/a), u_min, u_max) with a = 1."""
    plant, clf, cbf, qp = make()
    monkeypatch.setattr(qpmod.solvers, "qp", lambda *a, **k: {"status": "unknown", "x": None})
    x = np.array([0.9, 1.0])
    _, ub = cbf.extended_constraint_coeffs(x)
    sol = qp.solve(x, 30.0)
    assert sol["status"] == "fallback_after:unknown"
    assert sol["feasible"] is False
    assert sol["u"] == pytest.approx(float(np.clip(min(30.0, ub), -50.0, 50.0)), abs=1e-12)
    assert sol["u"] <= ub + 1e-12


# ------------------------------------------------------------------ closed loop


def _hocbf_safe_start(rng, p_max=1.0, k=2.0):
    """x0 with h0 >= 0 and psi1(x0) = -v0 + k h0 >= 0."""
    p0 = rng.uniform(-2.0, p_max)
    v0 = rng.uniform(-3.0, k * (p_max - p0))
    return np.array([p0, v0])


def test_closed_loop_invariance_under_adversarial_nominal_bang_bang():
    rng = np.random.default_rng(SEED + 4)
    for trial in range(10):
        x0 = _hocbf_safe_start(rng)
        sim = Simulator(SupervisorParams(x_c=np.array([1.5, 0.0]), dt=0.01))
        signs = rng.choice([-50.0, 50.0], size=60)
        res = sim.run(x0, T=3.0, nominal=lambda t, x, s=signs: float(s[int(t / 0.05) % 60]))
        assert res.min_h >= -1e-6, (trial, x0, res.min_h)
        assert np.all(np.abs(res.u[:-1]) <= 50.0 + 1e-6)
        assert set(res.status) == {"optimal"}
        assert np.all(res.delta >= -1e-9)


@pytest.mark.parametrize("u_adv", [50.0, 1.0e3])
def test_closed_loop_invariance_constant_adversary_and_equilibrium_at_barrier(u_adv):
    sim = Simulator(SupervisorParams(x_c=np.array([0.5, 0.0]), dt=0.01))
    res = sim.run(np.array([0.9, 0.0]), T=20.0, nominal=u_adv)
    assert res.min_h >= -1e-6
    # Analytic resting point: pushed against the barrier, p -> p_max, v -> 0.
    assert res.x[-1, 0] == pytest.approx(1.0, abs=1e-6)
    assert res.x[-1, 1] == pytest.approx(0.0, abs=1e-6)
    # V at that point = e^T P e with e = (p_max - p_c, 0) = (0.5, 0):  P00 * 0.25 = 0.5
    assert res.V[-1] == pytest.approx(default_P()[0, 0] * 0.25, abs=1e-5)


@pytest.mark.parametrize("u_nom", [0.5, 5.0, 12.0])
def test_closed_loop_equilibrium_without_barrier_matches_cubic_balance(u_nom):
    """Constant u_nom, CBF inactive: resting point (p*, 0) solves  u_QP(p*) = 0  (needs Ax = 0 => u = 0).

    At x = (p, 0):  e0 = p - p_c,  V = 2 e0^2,  Lf V = 0,  Lg V = e0,  c = -V.
    Zero-input balance of the relaxed QP:  (u_nom + w Lg c) / (1 + w Lg^2) = 0
        =>  u_nom = w e0 * 2 e0^2  =>  e0 = (u_nom / (2 w))^(1/3).
    """
    w = 50.0
    p_star = 0.5 + np.cbrt(u_nom / (2 * w))
    assert p_star < 1.0  # barrier inactive for these cases
    sim = Simulator(SupervisorParams(x_c=np.array([0.5, 0.0]), dt=0.01))
    res = sim.run(np.array([0.5, 0.0]), T=40.0, nominal=u_nom)
    assert res.x[-1, 0] == pytest.approx(p_star, abs=2e-3)
    assert res.x[-1, 1] == pytest.approx(0.0, abs=2e-3)


def test_closed_loop_invariance_holds_across_step_sizes():
    """Euler discretisation: min h >= -1e-6 for each of three step sizes (dt = 0.02, 0.01, 0.005)."""
    rng = np.random.default_rng(SEED + 5)
    x0s = [_hocbf_safe_start(rng) for _ in range(4)]
    for dt in (0.02, 0.01, 0.005):
        for x0 in x0s:
            sim = Simulator(SupervisorParams(x_c=np.array([1.5, 0.0]), dt=dt))
            res = sim.run(x0, T=2.0, nominal=50.0)
            assert res.min_h >= -1e-6


def test_closed_loop_clf_step_inequality_holds_every_step():
    """V_{k+1} <= V_k + dt(-k V_k + delta_k) + dt^2 xdot^T P xdot  (exact consequence of the QP row)."""
    P = default_P()
    plant = Plant()
    for x0, nom, xc in (
        (np.array([-1.0, 0.0]), 0.0, (0.5, 0.0)),
        (np.array([0.9, 0.3]), 50.0, (0.5, 0.0)),
        (np.array([0.0, 0.0]), 0.0, (1.5, 0.0)),
    ):
        dt = 0.01
        sim = Simulator(SupervisorParams(x_c=np.array(xc), dt=dt))
        res = sim.run(x0, T=6.0, nominal=nom)
        for k in range(len(res.t) - 2):
            xdot = plant.dynamics(res.x[k], res.u[k])
            bound = (
                res.V[k]
                + dt * (-1.0 * res.V[k] + res.delta[k])
                + dt**2 * float(xdot @ P @ xdot)
            )
            assert res.V[k + 1] <= bound + 1e-6 * (1 + res.V[k])


def test_closed_loop_converges_to_target_when_barrier_inactive():
    sim = Simulator(SupervisorParams(x_c=np.array([0.5, 0.0]), dt=0.01))
    res = sim.run(np.array([-1.0, 0.0]), T=20.0, nominal=0.0)
    assert res.V[-1] < 1e-3 * res.V[0]  # (observed 1.2e-4; CLF slack occurs where Lg V = 0)
    assert np.linalg.norm(res.x[-1] - np.array([0.5, 0.0])) < 0.05
    assert res.min_h >= 0.2  # barrier never even approached (observed 0.298)


def test_closed_loop_target_beyond_barrier_settles_at_barrier():
    sim = Simulator(SupervisorParams(x_c=np.array([1.5, 0.0]), dt=0.01))
    res = sim.run(np.array([0.0, 0.0]), T=20.0, nominal=0.0)
    assert res.x[-1, 0] == pytest.approx(1.0, abs=1e-5)
    assert res.min_h >= -1e-6


def test_simulator_time_grid_and_shapes():
    sim = Simulator(SupervisorParams(dt=0.01))
    res = sim.run(np.array([0.0, 0.0]), T=0.5, nominal=0.0)
    assert res.x.shape == (51, 2) and res.t.shape == (51,)
    np.testing.assert_allclose(res.t, 0.01 * np.arange(51), atol=1e-12)
    # recorded h and V are the barrier / Lyapunov values of the recorded states
    np.testing.assert_allclose(res.h, 1.0 - res.x[:, 0], atol=1e-14)
    np.testing.assert_allclose(res.V, [sim.clf.V(x) for x in res.x], atol=1e-12)


# ------------------------------------------------------------------ genuine defects (strict xfail)

_INFEASIBLE_STATE = np.array([0.5, 20.0])  # HOCBF bound = -3.9 v + 4 h = -76 < u_min = -50


@pytest.mark.xfail(
    strict=True,
    reason=(
        "BUG: when the HOCBF row demands u < u_min (e.g. x=[0.5, 20]), CLFCBFQPController.solve "
        "calls solvers.qp a second time in _solve_cbf_only WITHOUT try/except; CVXOPT raises "
        "ValueError('domain error') and the exception escapes solve(), so the documented "
        "last-resort clamp (u_min) is unreachable. Expected: return u = u_min, feasible=False."
    ),
)
def test_infeasible_hocbf_returns_max_braking_instead_of_raising():
    _, _, cbf, qp = make()
    _, ub = cbf.extended_constraint_coeffs(_INFEASIBLE_STATE)
    assert ub < -50.0  # scenario really is infeasible within the input box
    sol = qp.solve(_INFEASIBLE_STATE, 7.0)  # raises ValueError today
    assert sol["feasible"] is False
    assert sol["u"] == pytest.approx(-50.0)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "BUG: for x=[0.9, 15] (HOCBF bound -58.1 < u_min) CVXOPT raises inside the first solve, "
        "the generic `except Exception` branch returns u = u_nom UNFILTERED (+7: accelerating "
        "into the barrier, outside the CBF row) with status 'error:domain error'. A safety "
        "filter must never hand back the raw nominal input when the solver fails; expected "
        "the clamped/ braking input (<= HOCBF bound or u_min)."
    ),
)
def test_solver_exception_must_not_return_unfiltered_nominal():
    _, _, cbf, qp = make()
    x = np.array([0.9, 15.0])
    _, ub = cbf.extended_constraint_coeffs(x)
    sol = qp.solve(x, 7.0)
    assert sol["u"] <= max(ub, -50.0) + 1e-9, f"returned raw u_nom={sol['u']} (status {sol['status']})"
