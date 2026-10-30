"""Maths of the live CLF/CBF building blocks (src/governance_engine/{plant,clf,cbf}.py).

Equations verified numerically (independent computation in each test):

  Plant      xdot = A x + B u,  A = [[0, 1], [0, -0.1]],  B = [0, 1]^T
             eig(A) = {0, -0.1}  (damped 1-D mass:  pdot = v,  vdot = -0.1 v + u)
  Euler      x_{k+1} = x_k + dt (A x_k + B u_k)
             vs exact zero-order-hold discretisation  expm(dt [[A, B], [0, 0]])
             (Van Loan block exponential)  ->  one-step error <= (dt^2 / 2) |M|^2 e^{dt |M|} |[x; u]|
  CLF        V(x) = e^T P e,  e = x - x_c,  P = [[2, .5], [.5, 1]]  (P > 0)
             grad V = 2 P e,   Lf V = grad V . A x,   Lg V = grad V . B
             exact Euler identity (V is quadratic):
                 V(x + dt xdot) = V + dt grad V . xdot + dt^2 xdot^T P xdot
             CLF row:  Lg V u <= -k V - Lf V
  CBF        h(x) = p_max - p,   Lf h = -v,  Lg h = 0   (relative degree 2)
             classic row:   -Lg h u <= Lf h + k h
  HOCBF      psi0 = h,  psi1 = hdot + k h = -v + k (p_max - p)
             psi1dot + a1 psi1 >= 0   <=>   u <= 0.1 v - k v + a1 psi1
             (hand-derived here from pddot = -0.1 v + u, not copied from the code)

Seeded numpy randomisation only (no hypothesis dependency in this repo).
"""
from __future__ import annotations

import numpy as np
import pytest
from scipy.linalg import expm

from governance_engine import (
    A,
    B,
    ControlBarrierFunction,
    ControlLyapunovFunction,
    Plant,
)
from governance_engine.clf import default_P

RNG_SEED = 20261025


@pytest.fixture()
def rng() -> np.random.Generator:
    return np.random.default_rng(RNG_SEED)


# --------------------------------------------------------------------------
# Plant
# --------------------------------------------------------------------------


def test_plant_matrices_are_the_damped_mass_literals() -> None:
    np.testing.assert_array_equal(A, np.array([[0.0, 1.0], [0.0, -0.1]]))
    np.testing.assert_array_equal(B, np.array([[0.0], [1.0]]))


def test_plant_spectrum_is_zero_and_minus_point_one() -> None:
    # A is upper-triangular -> eigenvalues are the diagonal entries.
    eig = np.sort(np.linalg.eigvals(A).real)
    np.testing.assert_allclose(eig, [-0.1, 0.0], atol=1e-14)


def test_plant_dynamics_matches_scalar_equations(rng) -> None:
    plant = Plant()
    for _ in range(200):
        p, v = rng.uniform(-5, 5, size=2)
        u = rng.uniform(-50, 50)
        xdot = plant.dynamics(np.array([p, v]), u)
        np.testing.assert_allclose(xdot, [v, -0.1 * v + u], rtol=0, atol=1e-13)


def test_plant_f_g_decomposition_is_affine_in_u(rng) -> None:
    plant = Plant()
    for _ in range(100):
        x = rng.uniform(-3, 3, size=2)
        u = rng.uniform(-9, 9)
        np.testing.assert_allclose(
            plant.dynamics(x, u), plant.f(x) + plant.g(x).reshape(2) * u, atol=1e-13
        )


def test_plant_rejects_bad_shapes() -> None:
    with pytest.raises(ValueError):
        Plant(A_mat=np.zeros((3, 3)))
    with pytest.raises(ValueError):
        Plant(B_mat=np.zeros((2, 2)))


def test_euler_step_closed_form(rng) -> None:
    """x+ = x + dt (A x + B u):  p+ = p + dt v,  v+ = v + dt(-0.1 v + u) = (1 - 0.1 dt) v + dt u."""
    plant = Plant()
    for _ in range(200):
        p, v = rng.uniform(-5, 5, size=2)
        u = rng.uniform(-50, 50)
        dt = rng.uniform(1e-4, 5e-2)
        got = plant.euler_step(np.array([p, v]), u, dt)
        np.testing.assert_allclose(got, [p + dt * v, (1 - 0.1 * dt) * v + dt * u], atol=1e-12)


def test_euler_matches_exact_zoh_to_second_order(rng) -> None:
    """Compare against Van Loan exact ZOH:  [x+; u] = expm(dt [[A,B],[0,0]]) [x; u]."""
    plant = Plant()
    M = np.zeros((3, 3))
    M[:2, :2] = A
    M[:2, 2:] = B
    nM = np.linalg.norm(M, 2)
    for dt in (1e-1, 1e-2, 1e-3):
        for _ in range(50):
            x = rng.uniform(-3, 3, size=2)
            u = rng.uniform(-5, 5)
            exact = (expm(dt * M) @ np.append(x, u))[:2]
            euler = plant.euler_step(x, u, dt)
            z = np.append(x, u)
            # Taylor remainder bound for the matrix exponential.
            bound = 0.5 * (dt * nM) ** 2 * np.exp(dt * nM) * np.linalg.norm(z)
            assert np.linalg.norm(exact - euler) <= bound + 1e-14


def test_euler_error_is_order_dt_squared() -> None:
    """Halving dt divides the one-step error by ~4 (local truncation O(dt^2))."""
    plant = Plant()
    M = np.zeros((3, 3))
    M[:2, :2] = A
    M[:2, 2:] = B
    x, u = np.array([0.3, 1.7]), 4.0
    errs = []
    for dt in (0.08, 0.04, 0.02, 0.01):
        exact = (expm(dt * M) @ np.append(x, u))[:2]
        errs.append(np.linalg.norm(exact - plant.euler_step(x, u, dt)))
    ratios = [errs[i] / errs[i + 1] for i in range(len(errs) - 1)]
    for r in ratios:
        assert 3.5 < r < 4.5


def test_euler_marginal_stability_of_free_response() -> None:
    """I + dt A has eigenvalues {1, 1 - 0.1 dt}: never expands (spectral radius == 1)."""
    for dt in (1e-3, 1e-2, 1e-1):
        ev = np.linalg.eigvals(np.eye(2) + dt * A)
        np.testing.assert_allclose(sorted(ev.real), sorted([1.0, 1.0 - 0.1 * dt]), atol=1e-13)
        assert np.max(np.abs(ev)) <= 1.0 + 1e-14


# --------------------------------------------------------------------------
# CLF
# --------------------------------------------------------------------------


def _clf(x_c=(0.5, 0.0), k=1.0, P=None) -> ControlLyapunovFunction:
    return ControlLyapunovFunction(Plant(), x_c=np.array(x_c), P=P, k_lyap=k)


def test_default_P_is_symmetric_positive_definite() -> None:
    P = default_P()
    np.testing.assert_array_equal(P, P.T)
    # Sylvester: leading principal minors positive.
    assert P[0, 0] > 0 and np.linalg.det(P) > 0
    eig = np.linalg.eigvalsh(P)
    # closed-form eigenvalues of [[2, .5],[.5, 1]]: (3 +- sqrt(1 + 1)) / 2
    np.testing.assert_allclose(eig, [(3 - np.sqrt(2)) / 2, (3 + np.sqrt(2)) / 2], atol=1e-14)


def test_clf_rejects_non_positive_definite_P() -> None:
    with pytest.raises(ValueError):
        _clf(P=np.array([[1.0, 2.0], [2.0, 1.0]]))  # det < 0
    with pytest.raises(ValueError):
        _clf(P=np.zeros((2, 2)))


def test_V_value_and_gradient(rng) -> None:
    clf = _clf()
    P = default_P()
    for _ in range(200):
        x = rng.uniform(-3, 3, size=2)
        e = x - np.array([0.5, 0.0])
        # explicit expansion of e^T P e = 2 e0^2 + 2 * 0.5 e0 e1 + e1^2
        expect = 2 * e[0] ** 2 + e[0] * e[1] + e[1] ** 2
        assert clf.V(x) == pytest.approx(expect, rel=1e-13, abs=1e-13)
        np.testing.assert_allclose(clf.grad_V(x), 2 * P @ e, atol=1e-13)
        # finite-difference gradient (central) as an independent check
        eps = 1e-6
        fd = np.array(
            [(clf.V(x + eps * d) - clf.V(x - eps * d)) / (2 * eps) for d in np.eye(2)]
        )
        np.testing.assert_allclose(clf.grad_V(x), fd, atol=1e-6)


def test_V_positive_definite_and_zero_only_at_target(rng) -> None:
    clf = _clf()
    assert clf.V(np.array([0.5, 0.0])) == 0.0
    lam_min = np.linalg.eigvalsh(default_P())[0]
    for _ in range(300):
        x = rng.uniform(-3, 3, size=2)
        e = np.linalg.norm(x - np.array([0.5, 0.0]))
        if e > 1e-9:
            assert clf.V(x) >= lam_min * e**2 - 1e-12 > 0


def test_clf_lie_derivatives_closed_form(rng) -> None:
    """Lf V = 2 e^T P A x ;  Lg V = 2 e^T P B = 2 (P e)[1]."""
    clf = _clf()
    P = default_P()
    xc = np.array([0.5, 0.0])
    for _ in range(200):
        x = rng.uniform(-3, 3, size=2)
        e = x - xc
        Lf, Lg = clf.lie_derivatives(x)
        assert Lf == pytest.approx(float(2 * e @ P @ (A @ x)), abs=1e-12)
        assert Lg == pytest.approx(float(2 * (P @ e)[1]), abs=1e-12)
        # Lg V = 2(0.5 e0 + e1)  for the default P
        assert Lg == pytest.approx(2 * (0.5 * e[0] + e[1]), abs=1e-12)


def test_Vdot_along_dynamics_equals_Lf_plus_Lg_u(rng) -> None:
    """d/dt V(x(t)) at t=0 via a tiny central difference of the true flow."""
    clf = _clf()
    M = np.zeros((3, 3))
    M[:2, :2] = A
    M[:2, 2:] = B
    h = 1e-5
    for _ in range(50):
        x = rng.uniform(-2, 2, size=2)
        u = rng.uniform(-5, 5)
        fwd = (expm(h * M) @ np.append(x, u))[:2]
        bwd = (expm(-h * M) @ np.append(x, u))[:2]
        vdot_fd = (clf.V(fwd) - clf.V(bwd)) / (2 * h)
        Lf, Lg = clf.lie_derivatives(x)
        assert vdot_fd == pytest.approx(Lf + Lg * u, rel=1e-6, abs=1e-6)


def test_clf_constraint_coeffs_encode_decrease_condition(rng) -> None:
    """a u <= b with a = Lg V, b = -k V - Lf V  <=>  Vdot <= -k V."""
    for k in (0.5, 1.0, 3.0):
        clf = _clf(k=k)
        for _ in range(100):
            x = rng.uniform(-3, 3, size=2)
            u = rng.uniform(-20, 20)
            a, b = clf.constraint_coeffs(x)
            Lf, Lg = clf.lie_derivatives(x)
            vdot = Lf + Lg * u
            # slack of the row equals  Vdot + k V  (so a u <= b  <=>  Vdot <= -k V)
            assert a * u - b == pytest.approx(vdot + k * clf.V(x), abs=1e-10)


def test_euler_V_exact_quadratic_identity(rng) -> None:
    """V(x + dt xdot) = V + dt gradV.xdot + dt^2 xdot^T P xdot  (exact for quadratic V)."""
    clf = _clf()
    plant = Plant()
    P = default_P()
    for _ in range(300):
        x = rng.uniform(-3, 3, size=2)
        u = rng.uniform(-50, 50)
        dt = rng.uniform(1e-4, 0.05)
        xdot = plant.dynamics(x, u)
        lhs = clf.V(plant.euler_step(x, u, dt))
        rhs = clf.V(x) + dt * float(clf.grad_V(x) @ xdot) + dt**2 * float(xdot @ P @ xdot)
        assert lhs == pytest.approx(rhs, rel=1e-11, abs=1e-11)


def test_clf_feasible_u_gives_exponential_envelope_in_discrete_time(rng) -> None:
    """If Lg u <= -kV - Lf V then V_{k+1} <= (1 - k dt) V_k + dt^2 xdot^T P xdot (exact bound)."""
    clf = _clf(k=1.0)
    plant = Plant()
    P = default_P()
    for _ in range(300):
        x = rng.uniform(-3, 3, size=2)
        a, b = clf.constraint_coeffs(x)
        if abs(a) < 1e-9:
            continue
        # a point on the constraint boundary, then push it inside by a random margin
        u = b / a - np.sign(a) * rng.uniform(0, 5)
        dt = 0.01
        xdot = plant.dynamics(x, u)
        v_next = clf.V(plant.euler_step(x, u, dt))
        bound = (1 - clf.k_lyap * dt) * clf.V(x) + dt**2 * float(xdot @ P @ xdot)
        assert v_next <= bound + 1e-10


# --------------------------------------------------------------------------
# CBF / HOCBF
# --------------------------------------------------------------------------


def _cbf(p_max=1.0, k=2.0) -> ControlBarrierFunction:
    return ControlBarrierFunction(Plant(), p_max=p_max, k_cbf=k)


def test_cbf_h_and_gradient(rng) -> None:
    cbf = _cbf(p_max=1.3)
    for _ in range(100):
        p, v = rng.uniform(-3, 3, size=2)
        assert cbf.h(np.array([p, v])) == pytest.approx(1.3 - p)
    np.testing.assert_array_equal(cbf.grad_h(), [-1.0, 0.0])


def test_cbf_relative_degree_two(rng) -> None:
    """Lf h = -v, Lg h = 0 for every state (so u cannot enter the classic CBF row)."""
    cbf = _cbf()
    for _ in range(100):
        x = rng.uniform(-3, 3, size=2)
        Lf, Lg = cbf.lie_derivatives(x)
        assert Lf == pytest.approx(-x[1], abs=1e-14)
        assert Lg == 0.0


def test_cbf_classic_row_is_input_independent(rng) -> None:
    cbf = _cbf(k=2.0)
    for _ in range(100):
        x = rng.uniform(-3, 3, size=2)
        a, b = cbf.constraint_coeffs(x)
        assert a == 0.0  # -Lg h = 0  =>  0 * u <= Lf h + k h
        assert b == pytest.approx(-x[1] + 2.0 * (1.0 - x[0]), abs=1e-13)


def test_hocbf_row_matches_hand_derivation(rng) -> None:
    """u <= 0.1 v - k v + a1 (-v + k h), derived from pddot = -0.1 v + u."""
    for k, a1 in ((2.0, None), (2.0, 5.0), (1.0, 0.5), (3.0, 3.0)):
        cbf = _cbf(k=k)
        for _ in range(100):
            p, v = rng.uniform(-3, 3, size=2)
            a, b = cbf.extended_constraint_coeffs(np.array([p, v]), alpha1=a1)
            alpha1 = k if a1 is None else a1
            h = 1.0 - p
            psi1 = -v + k * h
            assert a == 1.0
            assert b == pytest.approx(0.1 * v - k * v + alpha1 * psi1, abs=1e-12)


def test_hocbf_row_is_exactly_psi1_dot_condition(rng) -> None:
    """psi1dot(x,u) + a1 psi1(x) = b - u  for the true flow  (psi1 = -v + k h)."""
    k = 2.0
    cbf = _cbf(k=k)
    plant = Plant()
    for _ in range(200):
        x = rng.uniform(-3, 3, size=2)
        u = rng.uniform(-30, 30)
        xdot = plant.dynamics(x, u)
        # psi1 = -v + k (p_max - p)  =>  psi1dot = -vdot - k pdot
        psi1 = -x[1] + k * (1.0 - x[0])
        psi1dot = -xdot[1] - k * xdot[0]
        _, b = cbf.extended_constraint_coeffs(x)  # alpha1 = k
        assert psi1dot + k * psi1 == pytest.approx(b - u, abs=1e-11)


def test_hocbf_forward_invariance_of_h_in_continuous_time() -> None:
    """Chain  psi1 >= 0 forever  =>  hdot >= -k h  =>  h(t) >= h(0) e^{-k t} > 0 (comparison lemma).

    Saturate the HOCBF row with equality (worst case u) and integrate the exact
    continuous flow with scipy; check psi1 stays >= -tol and h stays > 0.
    """
    from scipy.integrate import solve_ivp

    k = 2.0
    cbf = _cbf(k=k)
    plant = Plant()

    def rhs(_t, x):
        _, b = cbf.extended_constraint_coeffs(x)
        return plant.dynamics(x, b)  # u = upper bound  (binding)

    x0 = np.array([0.0, 1.5])  # psi1(0) = -1.5 + 2 = 0.5 >= 0
    sol = solve_ivp(rhs, (0.0, 5.0), x0, rtol=1e-10, atol=1e-12, dense_output=True)
    ts = np.linspace(0, 5, 501)
    xs = sol.sol(ts)
    h = 1.0 - xs[0]
    psi1 = -xs[1] + k * h
    assert np.all(psi1 >= -1e-8)
    assert np.all(h > 0)
    # comparison-lemma lower envelope for h with  psi1(0) >= 0:  hdot >= -k h
    assert np.all(h >= h[0] * np.exp(-k * ts) - 1e-8)
