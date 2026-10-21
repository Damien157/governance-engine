"""CLF-CBF-QP controller branches: safety beats tracking, classic CBF, fallbacks."""

from __future__ import annotations

import math

import numpy as np
import pytest

from governance_engine.cbf import ControlBarrierFunction
from governance_engine.clf import ControlLyapunovFunction
from governance_engine.plant import Plant
from governance_engine.qp_controller import CLFCBFQPController
from governance_engine.simulator import Simulator
from governance_engine.supervisor import ConstantNominal, SupervisorParams, TrackingNominal


def _ctrl(x_c=(0.5, 0.0), **kw):
    plant = Plant()
    clf = ControlLyapunovFunction(plant, x_c=np.array(x_c))
    cbf = ControlBarrierFunction(plant, p_max=1.0, k_cbf=2.0)
    return CLFCBFQPController(clf, cbf, **kw), cbf


def test_cbf_wins_over_clf_when_target_unsafe():
    # Target beyond the barrier: CLF wants to push past p_max, CBF must cap u.
    ctrl, cbf = _ctrl(x_c=(2.0, 0.0))
    x = np.array([0.95, 0.5])
    out = ctrl.solve(x, u_nom=40.0)
    _, b = cbf.extended_constraint_coeffs(x)
    assert out["feasible"]
    assert out["u"] <= b + 1e-6
    assert out["delta"] > 0  # CLF relaxed


def test_inactive_constraints_track_nominal():
    ctrl, _ = _ctrl()
    out = ctrl.solve(np.array([0.5, 0.0]), u_nom=0.0)
    assert out["status"] == "optimal"
    assert abs(out["u"]) < 1e-4


def test_input_bounds_respected():
    ctrl, _ = _ctrl(u_min=-1.0, u_max=1.0)
    out = ctrl.solve(np.array([-3.0, 0.0]), u_nom=30.0)
    assert -1.0 - 1e-6 <= out["u"] <= 1.0 + 1e-6


def test_cbf_only_fallback_when_full_qp_fails(monkeypatch):
    import governance_engine.qp_controller as qp

    real = qp.solvers.qp
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"status": "unknown", "x": None}
        return real(*a, **k)

    monkeypatch.setattr(qp.solvers, "qp", flaky)
    ctrl, cbf = _ctrl()
    x = np.array([0.9, 0.3])
    out = ctrl.solve(x, u_nom=50.0)
    assert out["status"].startswith("cbf_only_after:")
    assert math.isnan(out["delta"])
    assert out["u"] <= cbf.extended_constraint_coeffs(x)[1] + 1e-6


def test_clamp_fallback_when_all_qps_fail(monkeypatch):
    import governance_engine.qp_controller as qp

    monkeypatch.setattr(qp.solvers, "qp", lambda *a, **k: {"status": "unknown", "x": None})
    ctrl, cbf = _ctrl()
    x = np.array([0.9, 0.3])
    out = ctrl.solve(x, u_nom=50.0)
    assert out["status"].startswith("fallback_after:")
    assert out["feasible"] is False
    assert out["u"] == pytest.approx(min(50.0, cbf.extended_constraint_coeffs(x)[1]))


def test_classic_cbf_branch_when_lg_h_nonzero():
    # Plant whose control acts on position directly => Lg h != 0.
    plant = Plant(B_mat=np.array([[1.0], [1.0]]))
    clf = ControlLyapunovFunction(plant, x_c=np.array([0.5, 0.0]))
    cbf = ControlBarrierFunction(plant, p_max=1.0)
    ctrl = CLFCBFQPController(clf, cbf, use_hocbf=False)
    x = np.array([0.9, 0.0])
    out = ctrl.solve(x, u_nom=20.0)
    a, b = cbf.constraint_coeffs(x)
    assert out["feasible"]
    assert a * out["u"] <= b + 1e-6


def test_plant_rejects_bad_shapes():
    with pytest.raises(ValueError):
        Plant(A_mat=np.eye(3))
    with pytest.raises(ValueError):
        Plant(B_mat=np.ones((2, 2)))


def test_clf_rejects_non_pd_P():
    with pytest.raises(ValueError):
        ControlLyapunovFunction(Plant(), x_c=np.zeros(2), P=-np.eye(2))


def test_simulator_aggressive_push_keeps_barrier():
    res = Simulator(SupervisorParams()).run(np.array([0.0, 0.0]), T=3.0, nominal=20.0)
    assert res.barrier_held
    assert len(res.status) == len(res.t) - 1


def test_simulator_accepts_callable_and_provider():
    sim = Simulator()
    r1 = sim.run(np.zeros(2), T=0.2, nominal=lambda t, x: 1.0)
    r2 = sim.run(np.zeros(2), T=0.2, nominal=ConstantNominal(1.0))
    np.testing.assert_allclose(r1.x, r2.x)
    r3 = sim.run(np.zeros(2), T=0.2, nominal=TrackingNominal(x_c=np.array([0.5, 0.0])))
    assert r3.x[-1, 0] > 0


def test_with_u_nom_copies():
    p = SupervisorParams()
    q = p.with_u_nom(3.0)
    assert q.u_nom == 3.0 and p.u_nom == 0.0
    q.x_c[0] = 9.0
    assert p.x_c[0] == 0.5
