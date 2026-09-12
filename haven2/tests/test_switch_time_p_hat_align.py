"""Regression: switch_times use 0-based step index aligned with history_p_hat."""

from __future__ import annotations

from haven2.engine import Haven2Engine
from haven2.realms import Realm


def test_forced_switch_time_matches_history_p_hat_index():
    """Same-step switch must record τ equal to history_p_hat's list index.

    Dropping engine's t=t_before+1 leaves TransistorLatch._t at 0 for the first
    dynamical step; history_p_hat must not carry a t=0 seed or the indices drift.
    """

    def always_defensive(_v: float) -> Realm:
        return Realm.DEFENSIVE

    eng = Haven2Engine(
        rho=0.9,
        e0=0.0,
        e_star=0.0,
        epsilon_switch=10.0,  # always open
        initial_realm=Realm.CALM,
        target_fn=always_defensive,
    )
    assert eng.energy.history_p_hat == []
    assert eng.latch.switch_times == []

    rec = eng.step(c_t=0.01, v_t=0.05)
    assert rec.switched is True
    assert eng.latch.switch_times == [0]
    assert len(eng.energy.history_p_hat) == 1
    tau = eng.latch.switch_times[0]
    assert tau == 0
    assert abs(eng.energy.history_p_hat[tau] - rec.p_hat) < 1e-12
    assert abs(eng.c_scores[tau] - rec.c_score) < 1e-12

    # Second step, no realm change — times stay aligned for any future switch.
    rec2 = eng.step(c_t=0.01, v_t=0.05)
    assert rec2.switched is False
    assert eng.latch.switch_times == [0]
    assert len(eng.energy.history_p_hat) == 2
    assert abs(eng.energy.history_p_hat[0] - rec.p_hat) < 1e-12
    assert abs(eng.energy.history_p_hat[1] - rec2.p_hat) < 1e-12


def test_zeta_weights_align_for_same_step_switch():
    """Z_E / Z_R / Z_C must use the same (t+1)^σ weight for step 0."""
    from haven2.zeta import DEFAULT_SIGMA

    def always_defensive(_v: float) -> Realm:
        return Realm.DEFENSIVE

    eng = Haven2Engine(
        rho=0.9,
        e0=0.0,
        e_star=0.0,
        epsilon_switch=10.0,
        initial_realm=Realm.CALM,
        target_fn=always_defensive,
    )
    eng.step(0.01, 0.05)
    z = eng.zeta_summaries(sigma=DEFAULT_SIGMA)
    # Manual: one step at t=0 → weights all 1^σ
    p0 = eng.energy.history_p_hat[0]
    c0 = eng.c_scores[0]
    assert abs(z["Z_E"] - p0 / (1.0**DEFAULT_SIGMA)) < 1e-12
    assert abs(z["Z_R"] - 1.0 / (1.0**DEFAULT_SIGMA)) < 1e-12
    assert abs(z["Z_C"] - c0 / (1.0**DEFAULT_SIGMA)) < 1e-12
