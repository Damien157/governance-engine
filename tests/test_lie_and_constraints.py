"""Unit tests for Lie derivatives and QP constraint construction."""

from __future__ import annotations

import numpy as np
import pytest

from governance_engine.cbf import ControlBarrierFunction
from governance_engine.clf import ControlLyapunovFunction, default_P
from governance_engine.plant import A, B, Plant
from governance_engine.qp_controller import CLFCBFQPController


@pytest.fixture
def plant() -> Plant:
    return Plant()


@pytest.fixture
def clf(plant: Plant) -> ControlLyapunovFunction:
    return ControlLyapunovFunction(
        plant, x_c=np.array([0.5, 0.0]), P=default_P(), k_lyap=1.0
    )


@pytest.fixture
def cbf(plant: Plant) -> ControlBarrierFunction:
    return ControlBarrierFunction(plant, p_max=1.0, k_cbf=2.0)


def test_plant_matrices():
    assert A.shape == (2, 2)
    assert B.shape == (2, 1)
    np.testing.assert_allclose(A, [[0, 1], [0, -0.1]])
    np.testing.assert_allclose(B, [[0], [1]])


def test_plant_dynamics(plant: Plant):
    x = np.array([0.2, 0.5])
    u = 1.3
    xdot = plant.dynamics(x, u)
    expected = A @ x + B.ravel() * u
    np.testing.assert_allclose(xdot, expected)


def test_euler_step(plant: Plant):
    x = np.array([0.0, 0.0])
    dt = 0.01
    x1 = plant.euler_step(x, u=1.0, dt=dt)
    np.testing.assert_allclose(x1, x + dt * plant.dynamics(x, 1.0))


def test_P_positive_definite():
    P = default_P()
    eig = np.linalg.eigvalsh(P)
    assert np.all(eig > 0)


def test_clf_V_and_gradient(clf: ControlLyapunovFunction):
    x = np.array([0.7, -0.2])
    e = x - clf.x_c
    V = clf.V(x)
    np.testing.assert_allclose(V, e @ clf.P @ e)
    grad = clf.grad_V(x)
    np.testing.assert_allclose(grad, 2 * clf.P @ e)


def test_clf_lie_derivatives(clf: ControlLyapunovFunction, plant: Plant):
    x = np.array([0.3, 0.4])
    Lf, Lg = clf.lie_derivatives(x)
    grad = clf.grad_V(x)
    np.testing.assert_allclose(Lf, grad @ plant.f(x))
    np.testing.assert_allclose(Lg, grad @ plant.g().ravel())


def test_clf_constraint_form(clf: ControlLyapunovFunction):
    x = np.array([0.6, 0.1])
    a, b = clf.constraint_coeffs(x)
    Lf, Lg = clf.lie_derivatives(x)
    # a u <= b  <=>  Lg u <= -k V - Lf
    assert a == pytest.approx(Lg)
    assert b == pytest.approx(-clf.k_lyap * clf.V(x) - Lf)


def test_cbf_h(cbf: ControlBarrierFunction):
    x = np.array([0.8, 0.0])
    assert cbf.h(x) == pytest.approx(1.0 - 0.8)
    x_bad = np.array([1.2, 0.0])
    assert cbf.h(x_bad) < 0


def test_cbf_lie_derivatives_relative_degree(cbf: ControlBarrierFunction, plant: Plant):
    """For h=p_max-p on this plant, Lg h = 0 (relative degree 2)."""
    x = np.array([0.5, 0.3])
    Lf, Lg = cbf.lie_derivatives(x)
    np.testing.assert_allclose(Lf, -0.3)  # -v
    np.testing.assert_allclose(Lg, 0.0)
    grad = cbf.grad_h()
    np.testing.assert_allclose(Lf, grad @ plant.f(x))
    np.testing.assert_allclose(Lg, grad @ plant.g().ravel())


def test_cbf_classic_constraint_coeffs(cbf: ControlBarrierFunction):
    x = np.array([0.5, 0.3])
    a, b = cbf.constraint_coeffs(x)
    Lf, Lg = cbf.lie_derivatives(x)
    assert a == pytest.approx(-Lg)
    assert b == pytest.approx(Lf + cbf.k_cbf * cbf.h(x))


def test_hocbf_involves_control(cbf: ControlBarrierFunction):
    """Extended CBF must put a nonzero coefficient on u."""
    x = np.array([0.9, 0.5])  # approaching barrier with positive velocity
    a, b = cbf.extended_constraint_coeffs(x)
    assert a == pytest.approx(1.0)
    # With positive v near p_max, upper bound on u should be finite and restrictive
    assert np.isfinite(b)


def test_qp_standard_form_matches_notes():
    """Cost (u - u_nom)^2 <=> 1/2 u H u + f u with H=2, f=-2 u_nom."""
    u_nom = 3.5
    H, f = 2.0, -2.0 * u_nom
    # expand: 1/2 * 2 u^2 - 2 u_nom u = u^2 - 2 u_nom u = (u - u_nom)^2 - u_nom^2
    for u in [-1.0, 0.0, 3.5, 10.0]:
        qp = 0.5 * H * u * u + f * u
        raw = (u - u_nom) ** 2 - u_nom**2
        assert qp == pytest.approx(raw)


def test_qp_prefers_nominal_when_safe(clf, cbf):
    ctrl = CLFCBFQPController(clf, cbf)
    # Far from barrier, near setpoint, small u_nom
    x = np.array([0.5, 0.0])
    sol = ctrl.solve(x, u_nom=0.1)
    assert sol["feasible"]
    assert abs(sol["u"] - 0.1) < 0.2


def test_qp_overrides_unsafe_nominal(clf, cbf):
    ctrl = CLFCBFQPController(clf, cbf)
    # Near p_max with positive velocity; large positive u_nom would push through
    x = np.array([0.95, 0.8])
    sol = ctrl.solve(x, u_nom=20.0)
    assert sol["feasible"]
    assert sol["u"] < u_nom_safe_bound(cbf, x) + 1e-6


def u_nom_safe_bound(cbf: ControlBarrierFunction, x: np.ndarray) -> float:
    a, b = cbf.extended_constraint_coeffs(x)
    return b / a
