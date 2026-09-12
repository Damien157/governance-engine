"""Governed delta step field + Z_D spectral summary."""

from __future__ import annotations

from haven2.engine import Haven2Engine
from haven2.realms import Realm
from haven2.zeta import engine_zeta, DEFAULT_SIGMA


def test_step_records_delta_and_history():
    eng = Haven2Engine(rho=0.9, e0=0.0, e_star=0.0, epsilon_switch=0.05)
    rec = eng.step(0.02, 0.015)
    assert isinstance(rec.delta, float)
    assert eng.history_delta == [rec.delta]
    # Recompute expected
    expected = eng.governed_delta(
        p_hat=rec.p_hat,
        realm=rec.realm,
        c_score=rec.c_score,
        v_t=rec.v,
        abs_err=0.0,  # first step
    )
    assert abs(rec.delta - expected) < 1e-12


def test_zeta_summaries_includes_z_d():
    eng = Haven2Engine(rho=0.9, e0=0.0, e_star=0.0, epsilon_switch=0.05)
    eng.step(0.02, 0.015)
    eng.step(0.01, 0.02, forecast=0.015)
    z = eng.zeta_summaries()
    assert "Z_D" in z
    assert abs(z["Z_D"] - engine_zeta(eng.history_delta, DEFAULT_SIGMA)) < 1e-12
    # Completeness quartet still present (product spectrum keys)
    for k in ("Z_E", "Z_R", "Z_C", "Z_H"):
        assert k in z


def test_delta_weights_are_tunable():
    eng = Haven2Engine(rho=0.9, e0=0.0, e_star=0.0, epsilon_switch=0.05)
    eng.weights.delta_p = 1.0
    eng.weights.delta_r = 0.0
    eng.weights.delta_c = 0.0
    eng.weights.delta_v = 0.0
    eng.weights.delta_f = 0.0
    rec = eng.step(0.5, 0.01)
    assert abs(rec.delta - rec.p_hat) < 1e-12
