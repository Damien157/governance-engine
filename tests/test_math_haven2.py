"""Maths of Haven2 (haven2/src/haven2): energy recurrence, reset time, latch, EVTE score, zeta sums.

Equations verified:

  Energy      E_{t+1} = rho E_t + c_t,   rho in (0,1)
              closed form  E_t = rho^t E_0 + sum_{s<t} rho^{t-1-s} c_s
              equilibrium  E* = E[c] / (1 - rho);   residual  P_t = E_t - E*
              if c_s = (1-rho) E* for all s then  P_t = rho^t P_0   (pure geometric decay)
              bounded drive |c| <= C  ==>  |E_t| <= rho^t |E_0| + C/(1-rho)
  T_reset     smallest integer t with  rho^t |E_0 - E*| < eps   =  ceil( ln(eps/gap) / ln(rho) )
  Latch       open_t  <=>  |P_t| < eps_switch   (strict);
              realm_{t+1} = TargetRealm(V_t) if open else realm_t   (discrete CBF / hysteresis)
  Shock trace (haven2.engine.run_shock_regime_trace): exact residual
              P_t = rho^{t+1}(E_0 - E*) + rho^{t-t_shock} (c_shock - mean_c)   for t >= t_shock
  EVTE        C_t = clip( .3 A + .2 SF + .2 DD + .2 FE + .1 E, 0, 1 );  weights sum to 1
              DD = 1 - max(0, (peak - eq)/peak),  FE = exp(-err/0.05),
              E  = 1 - |P|/(2 eps)  inside the band, exp(-(|P|-eps)/eps) outside
  Delta       delta = .25 P + .25 realm_code + .25 C + .15 V + .10 |err|   (linear functional)
  Zeta        Z_E = sum P_t/(t+1)^s,  Z_R = sum_k (tau_k+1)^-s,  Z_C = sum C_t/(t+1)^s
              Z_H = |Z_E| + Z_R + Z_C ;  S = (E_spec + R_spec + C_spec)/3 ;  S_avg = mean over sigma grid
              For a unit trace of length N:  Z = zeta(s) - zeta(s, N+1)   (Riemann / Hurwitz, scipy)

Former defects (t_reset boundary, sweet-spot discontinuity) are fixed; their regression tests are at the end of the file.
"""
from __future__ import annotations

import math

import numpy as np
import pytest
from haven2 import (
    EnergyState,
    Haven2Engine,
    Realm,
    TransistorLatch,
    VolRegime,
    energy_zeta,
    engine_zeta,
    equilibrium_from_mean_c,
    master_zeta,
    optimal_rho,
    realm_switch_zeta,
    s_avg,
    t_reset,
    target_realm,
)
from haven2.engine import run_shock_regime_trace
from haven2.evte import (
    EVTEWeights,
    metric_alignment,
    metric_drawdown,
    metric_energy_sweet_spot,
    metric_forecast_error,
    metric_switch_health,
    score_c_t,
)
from haven2.realms import classify_volatility
from haven2.zeta import spectral_specs
from scipy.special import zeta as hurwitz

SEED = 20260101


# ------------------------------------------------------------------ energy


def test_energy_closed_form_random_drive():
    rng = np.random.default_rng(SEED)
    for _ in range(50):
        rho = rng.uniform(0.05, 0.99)
        e0 = rng.normal()
        c = rng.normal(size=40)
        es = EnergyState(rho=rho, e0=e0, e_star=0.0)
        for t, ct in enumerate(c, start=1):
            es.step(ct)
            closed = rho**t * e0 + sum(rho ** (t - 1 - s) * c[s] for s in range(t))
            assert es.e == pytest.approx(closed, rel=1e-11, abs=1e-12)


def test_energy_recurrence_is_linear_superposition():
    rng = np.random.default_rng(SEED + 1)
    rho = 0.83
    c1, c2 = rng.normal(size=30), rng.normal(size=30)
    a, b = 1.7, -0.4

    def run(e0, c):
        es = EnergyState(rho=rho, e0=e0, e_star=0.0)
        for x in c:
            es.step(x)
        return es.e

    assert run(a * 1.0 + b * 2.0, a * c1 + b * c2) == pytest.approx(
        a * run(1.0, c1) + b * run(2.0, c2), rel=1e-12, abs=1e-12
    )


def test_equilibrium_formula_and_pure_decay_of_residual():
    rng = np.random.default_rng(SEED + 2)
    for _ in range(50):
        rho = rng.uniform(0.1, 0.98)
        mean_c = rng.uniform(-1, 1)
        e_star = equilibrium_from_mean_c(mean_c, rho)
        assert e_star == pytest.approx(mean_c / (1 - rho), rel=1e-14)
        # drive exactly at equilibrium: E* is a fixed point, residual decays as rho^t
        e0 = e_star + rng.normal()
        es = EnergyState(rho=rho, e0=e0, e_star=e_star)
        gap0 = e0 - e_star
        for t in range(1, 25):
            es.step(mean_c)
            assert es.p_hat == pytest.approx(rho**t * gap0, rel=1e-9, abs=1e-12)


def test_fixed_point_property_of_equilibrium():
    for rho in (0.2, 0.5, 0.9):
        for mean_c in (-0.3, 0.0, 0.7):
            es = EnergyState(rho=rho, e0=mean_c / (1 - rho), e_star=None)
            es.step(mean_c)
            assert es.e == pytest.approx(mean_c / (1 - rho), rel=1e-13, abs=1e-15)
            assert es.p_hat == pytest.approx(0.0, abs=1e-13)


def test_running_mean_equilibrium_used_when_e_star_unset():
    rng = np.random.default_rng(SEED + 3)
    rho = 0.7
    es = EnergyState(rho=rho, e0=0.0)
    cs = rng.uniform(0, 1, size=20)
    for i, c in enumerate(cs, start=1):
        es.step(c)
        assert es.equilibrium == pytest.approx(cs[:i].mean() / (1 - rho), rel=1e-13)
        assert es.p_hat == pytest.approx(es.e - cs[:i].mean() / (1 - rho), rel=1e-13, abs=1e-14)


def test_bounded_drive_gives_bounded_energy():
    rng = np.random.default_rng(SEED + 4)
    for rho in (0.5, 0.9, 0.99):
        C = 1.0
        es = EnergyState(rho=rho, e0=5.0, e_star=0.0)
        for t in range(1, 400):
            es.step(rng.uniform(-C, C))
            assert abs(es.e) <= rho**t * 5.0 + C / (1 - rho) + 1e-12


def test_energy_rejects_rho_outside_open_unit_interval():
    for bad in (0.0, 1.0, -0.1, 1.5):
        with pytest.raises(ValueError):
            EnergyState(rho=bad)
        with pytest.raises(ValueError):
            equilibrium_from_mean_c(0.1, bad)


# ------------------------------------------------------------------ T_reset


def _brute_t_reset(eps, e0, e_star, rho):
    """Smallest t >= 0 with |E_t - E*| < eps under pure decay (the latch criterion)."""
    es = EnergyState(rho=rho, e0=e0, e_star=e_star)
    t = 0
    while not abs(es.p_hat) < eps:
        es.step((1 - rho) * e_star)  # drive at equilibrium
        t += 1
        assert t < 100000
    return t


def test_t_reset_equals_brute_force_simulation_generic_parameters():
    rng = np.random.default_rng(SEED + 5)
    for _ in range(300):
        rho = rng.uniform(0.2, 0.97)
        e_star = rng.normal()
        e0 = e_star + rng.choice([-1, 1]) * rng.uniform(0.01, 5)
        eps = rng.uniform(1e-3, 0.5)
        assert t_reset(eps, e0, e_star, rho) == _brute_t_reset(eps, e0, e_star, rho)


def test_t_reset_hand_values_and_monotonicity():
    assert t_reset(0.1, 1.0, 0.0, 0.5) == 4  # 0.5^4 = .0625 < .1 <= .125 = 0.5^3
    rng = np.random.default_rng(SEED + 6)
    for _ in range(100):
        rho = rng.uniform(0.3, 0.95)
        eps = rng.uniform(0.01, 0.2)
        g1, g2 = sorted(rng.uniform(0.5, 10, size=2))
        assert t_reset(eps, g1, 0.0, rho) <= t_reset(eps, g2, 0.0, rho)  # larger gap -> longer
        assert t_reset(eps * 2, g2, 0.0, rho) <= t_reset(eps, g2, 0.0, rho)  # larger eps -> shorter
        assert t_reset(eps, g2, 0.0, rho) <= t_reset(eps, g2, 0.0, min(0.99, rho + 0.03))  # slower decay -> longer


def test_engine_reset_time_uses_equilibrium_and_epsilon():
    eng = Haven2Engine(rho=0.5, epsilon_switch=0.1, e0=1.0, e_star=0.0)
    assert eng.reset_time() == 4
    assert eng.reset_time(epsilon=0.3) == t_reset(0.3, 1.0, 0.0, 0.5)


# ------------------------------------------------------------------ latch / realms


def test_classify_volatility_boundaries():
    assert classify_volatility(0.0099999) is VolRegime.LOW
    assert classify_volatility(0.01) is VolRegime.MID  # boundary belongs to MID
    assert classify_volatility(0.03) is VolRegime.MID
    assert classify_volatility(0.0300001) is VolRegime.HIGH
    assert target_realm(0.005) is Realm.CALM
    assert target_realm(0.02) is Realm.NORMAL
    assert target_realm(0.05) is Realm.DEFENSIVE


def test_latch_is_open_iff_abs_p_hat_strictly_below_epsilon():
    latch = TransistorLatch(epsilon_switch=0.05)
    for p in (-0.0499999, 0.0, 0.0499999):
        assert latch.is_open(p)
    for p in (-0.05, 0.05, 0.0500001, -3.0):
        assert not latch.is_open(p)


def test_latch_invariants_on_random_streams():
    rng = np.random.default_rng(SEED + 7)
    eps = 0.05
    latch = TransistorLatch(epsilon_switch=eps, realm=Realm.NORMAL)
    prev_realm = latch.realm
    for t in range(2000):
        p = rng.normal(scale=0.08)
        v = rng.choice([0.005, 0.02, 0.06])
        realm = latch.step(p, v)
        open_ = latch.history_open[-1]
        sw = latch.history_switched[-1]
        assert open_ == (abs(p) < eps)
        if open_:
            assert realm is target_realm(v)  # open => follows target
        else:
            assert realm is prev_realm  # closed => holds (hysteresis)
        assert sw == (realm is not prev_realm)
        if sw:
            assert open_  # a switch is only possible through an open latch
        prev_realm = realm
    assert latch.switch_times == sorted(set(latch.switch_times))
    assert len(latch.switch_times) == sum(latch.history_switched)
    assert len(latch.history_realm) == 1 + len(latch.history_open)


def test_closed_latch_ignores_adversarial_target_flapping():
    latch = TransistorLatch(epsilon_switch=0.05, realm=Realm.CALM)
    for t in range(500):
        v = 0.06 if t % 2 else 0.005  # target alternates DEFENSIVE / CALM every step
        assert latch.step(0.5, v) is Realm.CALM  # |P| = 0.5 >> eps: closed
    assert latch.switch_times == []


def test_open_latch_is_idempotent_for_constant_target():
    latch = TransistorLatch(epsilon_switch=0.05, realm=Realm.NORMAL)
    latch.step(0.0, 0.06)  # NORMAL -> DEFENSIVE
    assert latch.history_switched == [True]
    for _ in range(20):
        latch.step(0.0, 0.06)
    assert latch.history_switched == [True] + [False] * 20
    assert latch.switch_times == [0]


def test_latch_rejects_non_positive_epsilon():
    with pytest.raises(ValueError):
        TransistorLatch(epsilon_switch=0.0)


# ------------------------------------------------------------------ shock / regime scenario (analytic)


@pytest.mark.parametrize("rho", [0.5, 0.8, 0.9, 0.95])
def test_shock_trace_matches_exact_residual_formula_and_switch_time(rho):
    eps, mean_c, shock_c = 0.05, 0.02, 1.0
    t_shock, t_reg, horizon = 5, 15, 120
    p_hat, switches, c_scores, eng = run_shock_regime_trace(
        rho, epsilon_switch=eps, mean_c=mean_c, shock_c=shock_c, shock_t=t_shock,
        regime_change_t=t_reg, horizon=horizon,
    )
    e_star = mean_c / (1 - rho)
    gap0 = 0.0 - e_star
    expected = []
    for t in range(horizon):
        val = rho ** (t + 1) * gap0
        if t >= t_shock:
            val += rho ** (t - t_shock) * (shock_c - mean_c)
        expected.append(val)
    np.testing.assert_allclose(p_hat, expected, rtol=1e-10, atol=1e-12)
    # first latch opening at/after the regime change predicts the unique switch (CALM -> DEFENSIVE)
    first_open = next(t for t in range(t_reg, horizon) if abs(expected[t]) < eps)
    assert switches == [first_open]
    assert eng.realm is Realm.DEFENSIVE
    # and the latch was provably CLOSED (blocking) at the regime change if the residual was still big
    if abs(expected[t_reg]) >= eps:
        assert first_open > t_reg
        assert all(eng.history[t].realm is Realm.CALM for t in range(t_reg, first_open))


def test_larger_rho_delays_the_switch():
    times = {}
    for rho in (0.5, 0.8, 0.95):
        _, switches, _, _ = run_shock_regime_trace(rho, horizon=200)
        times[rho] = switches[0]
    assert times[0.5] <= times[0.8] <= times[0.95]


# ------------------------------------------------------------------ EVTE score


def test_weights_sum_to_one_and_dict_view():
    w = EVTEWeights()
    assert w.w_A + w.w_SF + w.w_DD + w.w_FE + w.w_E == pytest.approx(1.0, abs=1e-15)
    assert w.delta_p + w.delta_r + w.delta_c + w.delta_v + w.delta_f == pytest.approx(1.0, abs=1e-15)
    d = w.as_dict()
    assert d["A"] == 0.3 and d["E"] == 0.1


def test_metric_alignment_truth_table():
    assert metric_alignment(Realm.CALM, VolRegime.LOW) == 1.0
    assert metric_alignment(Realm.NORMAL, VolRegime.MID) == 1.0
    assert metric_alignment(Realm.DEFENSIVE, VolRegime.HIGH) == 1.0
    assert metric_alignment(Realm.CALM, VolRegime.HIGH) == 0.0


def test_metric_switch_health_table():
    assert metric_switch_health(switched=False, open_=False, steps_since_switch=0) == 1.0
    assert metric_switch_health(switched=True, open_=False, steps_since_switch=0) == 0.0
    assert metric_switch_health(switched=True, open_=True, steps_since_switch=5) == 1.0
    assert metric_switch_health(switched=True, open_=True, steps_since_switch=4) == 0.3
    assert metric_switch_health(switched=False, open_=True, steps_since_switch=99) == 0.8


def test_metric_drawdown_and_forecast_formulas():
    rng = np.random.default_rng(SEED + 8)
    for _ in range(100):
        peak = rng.uniform(1, 3)
        eq = rng.uniform(0.1, peak)
        assert metric_drawdown(peak, eq) == pytest.approx(1 - (peak - eq) / peak, abs=1e-14)
        assert 0.0 <= metric_drawdown(peak, eq) <= 1.0
        err = rng.uniform(0, 0.5)
        assert metric_forecast_error(err) == pytest.approx(math.exp(-err / 0.05), rel=1e-13)
    assert metric_drawdown(1.0, 1.2) == 1.0  # equity above peak -> no drawdown
    assert metric_drawdown(0.0, 0.5) == 1.0  # degenerate peak
    assert metric_forecast_error(0.0) == 1.0


def test_metric_energy_sweet_spot_inside_band_and_decay_outside():
    eps = 0.05
    assert metric_energy_sweet_spot(0.0, eps) == 1.0
    assert metric_energy_sweet_spot(0.025, eps) == pytest.approx(0.75, abs=1e-14)  # 1 - .5 * .5
    for mag in (0.06, 0.1, 0.5):
        assert metric_energy_sweet_spot(mag, eps) == pytest.approx(0.5 * math.exp(-(mag - eps) / eps), rel=1e-13)
        assert metric_energy_sweet_spot(-mag, eps) == metric_energy_sweet_spot(mag, eps)  # even in P


def test_score_c_t_is_the_clipped_weighted_sum_and_in_unit_interval():
    rng = np.random.default_rng(SEED + 9)
    w = EVTEWeights()
    for _ in range(300):
        realm = rng.choice(list(Realm))
        v = rng.uniform(0, 0.06)
        switched, open_ = bool(rng.integers(0, 2)), bool(rng.integers(0, 2))
        ss = int(rng.integers(0, 12))
        peak = rng.uniform(1, 2)
        eq = rng.uniform(0.5, peak)
        err = rng.uniform(0, 0.2)
        p = rng.normal(scale=0.1)
        eps = 0.05
        got = score_c_t(realm=realm, v_t=v, switched=switched, open_=open_, steps_since_switch=ss,
                        equity=eq, equity_peak=peak, forecast_abs_err=err, p_hat=p, epsilon_switch=eps)
        expect = (
            w.w_A * metric_alignment(realm, classify_volatility(v))
            + w.w_SF * metric_switch_health(switched, open_, ss)
            + w.w_DD * metric_drawdown(peak, eq)
            + w.w_FE * metric_forecast_error(err)
            + w.w_E * metric_energy_sweet_spot(p, eps)
        )
        assert got == pytest.approx(min(1.0, max(0.0, expect)), abs=1e-14)
        assert 0.0 <= got <= 1.0


def test_governed_delta_linear_functional():
    eng = Haven2Engine()
    w = eng.weights
    for realm, code in ((Realm.CALM, 0.0), (Realm.NORMAL, 1.0), (Realm.DEFENSIVE, 2.0)):
        d = eng.governed_delta(p_hat=0.3, realm=realm, c_score=0.8, v_t=0.02, abs_err=0.04)
        assert d == pytest.approx(0.25 * 0.3 + 0.25 * code + 0.25 * 0.8 + 0.15 * 0.02 + 0.10 * 0.04, abs=1e-15)
    # linearity: delta(a p) - delta(0) = a * delta_p * p
    d0 = eng.governed_delta(0.0, Realm.NORMAL, 0.5, 0.02, 0.0)
    d2 = eng.governed_delta(2.0, Realm.NORMAL, 0.5, 0.02, 0.0)
    assert d2 - d0 == pytest.approx(2.0 * w.delta_p, abs=1e-14)


def test_engine_step_bookkeeping_equity_and_forecast_error():
    eng = Haven2Engine(rho=0.9, epsilon_switch=0.05)
    r1 = eng.step(0.02, 0.02, equity_return=0.10, forecast=0.025)
    assert eng._equity == pytest.approx(1.10)
    r2 = eng.step(0.02, 0.03, equity_return=-0.20)
    # abs_err uses the PREVIOUS forecast: |v_2 - forecast_1| = |0.03 - 0.025|
    assert eng._equity == pytest.approx(1.10 * 0.80)
    assert eng._equity_peak == pytest.approx(1.10)
    assert r2.delta == pytest.approx(
        eng.governed_delta(r2.p_hat, r2.realm, r2.c_score, 0.03, 0.005), abs=1e-14
    )
    assert r1.t == 1 and r2.t == 2


# ------------------------------------------------------------------ zeta


def test_zeta_finite_sums_against_direct_loops():
    rng = np.random.default_rng(SEED + 10)
    for sigma in (1.5, 2.0, 3.7):
        p = rng.normal(size=25)
        c = rng.uniform(0, 1, size=25)
        tau = sorted(rng.choice(60, size=7, replace=False).tolist())
        assert energy_zeta(p, sigma) == pytest.approx(
            math.fsum(p[t] / (t + 1) ** sigma for t in range(25)), rel=1e-12, abs=1e-14)
        assert engine_zeta(c, sigma) == pytest.approx(
            math.fsum(c[t] / (t + 1) ** sigma for t in range(25)), rel=1e-12)
        assert realm_switch_zeta(tau, sigma) == pytest.approx(
            math.fsum(1.0 / (k + 1) ** sigma for k in tau), rel=1e-12)
        zh = master_zeta(p, tau, c, sigma)
        assert zh == pytest.approx(
            abs(energy_zeta(p, sigma)) + realm_switch_zeta(tau, sigma) + engine_zeta(c, sigma), rel=1e-13)


@pytest.mark.parametrize("sigma", [2.0, 3.0, 4.0])
def test_unit_trace_zeta_equals_riemann_minus_hurwitz_tail(sigma):
    N = 500
    expect = float(hurwitz(sigma, 1) - hurwitz(sigma, N + 1))
    assert energy_zeta([1.0] * N, sigma) == pytest.approx(expect, rel=1e-12)
    assert engine_zeta([1.0] * N, sigma) == pytest.approx(expect, rel=1e-12)
    assert realm_switch_zeta(list(range(N)), sigma) == pytest.approx(expect, rel=1e-12)
    if sigma == 2.0:  # Basel: zeta(2) = pi^2/6, truncation error < 1/N
        assert abs(energy_zeta([1.0] * N, 2.0) - math.pi**2 / 6) < 1.0 / N


def test_zeta_linearity_and_sigma_monotonicity():
    rng = np.random.default_rng(SEED + 11)
    a, b = rng.uniform(0, 1, size=15), rng.uniform(0, 1, size=15)
    assert engine_zeta(2 * a - 3 * b, 2.0) == pytest.approx(
        2 * engine_zeta(a, 2.0) - 3 * engine_zeta(b, 2.0), rel=1e-12, abs=1e-14)
    z = [engine_zeta(a, s) for s in (1.2, 1.8, 2.5, 4.0)]
    assert all(z[i] > z[i + 1] for i in range(3))  # non-negative trace: decreasing in sigma
    assert engine_zeta(a, 2.0) <= a.max() * float(hurwitz(2.0, 1))  # <= max(c) * zeta(sigma)


def test_zeta_empty_inputs_and_signed_energy():
    assert energy_zeta([], 2.0) == 0.0 and engine_zeta([], 2.0) == 0.0 and realm_switch_zeta([], 2.0) == 0.0
    assert energy_zeta([-1.0, -0.5], 2.0) < 0  # signed
    assert master_zeta([-1.0, -0.5], [], [0.0, 0.0], 2.0) == pytest.approx(abs(energy_zeta([-1.0, -0.5], 2.0)))


def test_s_avg_is_the_sigma_grid_mean_of_the_combined_scalar():
    rng = np.random.default_rng(SEED + 12)
    p, c = rng.normal(size=20), rng.uniform(0, 1, size=20)
    tau = [3, 9]
    grid = (1.5, 2.0, 2.5)
    manual = np.mean([
        (abs(energy_zeta(p, s)) + realm_switch_zeta(tau, s) + engine_zeta(c, s)) / 3.0 for s in grid])
    assert s_avg(p, tau, c, grid) == pytest.approx(manual, rel=1e-13)
    sp = spectral_specs(p, tau, c, 2.0)
    assert sp.combined() == pytest.approx((sp.e_spec + sp.r_spec + sp.c_spec) / 3.0, rel=1e-14)
    assert sp.z_h == pytest.approx(sp.e_spec + sp.r_spec + sp.c_spec, rel=1e-14)
    with pytest.raises(ValueError):
        s_avg(p, tau, c, [])


def test_optimal_rho_is_argmax_of_s_avg_on_the_analytic_decay_family():
    """Trace family P_t = rho^t, C_t = (1+rho)/2, one switch at tau=5: ranking is reproducible by hand."""
    def run(rho):
        T = 40
        return [rho**t for t in range(T)], [5], [0.5 + 0.5 * rho] * T

    grid = [0.3, 0.5, 0.7, 0.9]
    res = optimal_rho(run, grid)
    manual = [s_avg(*run(r)) for r in grid]
    np.testing.assert_allclose(res.s_avg_curve, manual, rtol=1e-13)
    assert res.rho_star == grid[int(np.argmax(manual))]
    # E_spec = |sum rho^t/(t+1)^s| increases with rho and so does C_t -> S_avg increasing: argmax = largest rho
    assert res.rho_star == 0.9
    with pytest.raises(ValueError):
        optimal_rho(run, [])


def test_engine_zeta_summaries_consistent_with_free_functions():
    p_hat, switches, c_scores, eng = run_shock_regime_trace(0.9, horizon=60)
    z = eng.zeta_summaries(sigma=2.0)
    assert z["Z_E"] == pytest.approx(energy_zeta(p_hat, 2.0), rel=1e-13)
    assert z["Z_R"] == pytest.approx(realm_switch_zeta(switches, 2.0), rel=1e-13)
    assert z["Z_C"] == pytest.approx(engine_zeta(c_scores, 2.0), rel=1e-13)
    assert z["Z_H"] == pytest.approx(master_zeta(p_hat, switches, c_scores, 2.0), rel=1e-13)
    assert z["Z_D"] == pytest.approx(engine_zeta(eng.history_delta, 2.0), rel=1e-13)
    # a switch at tau contributes 1/(tau+1)^2 to Z_R
    assert z["Z_R"] == pytest.approx(sum(1.0 / (t + 1) ** 2 for t in switches))


def test_minimal_spectral_snapshot_is_length_one_dirichlet_sum():
    """spectral_audit tier 3: single (p_hat, c) point => Z_E = p, Z_C = c, Z_R = 0, Z_H = |p| + c for ANY sigma."""
    from governed_stack.spectral_audit import build_spectrum

    for sigma in (1.5, 2.0, 7.0):
        out = build_spectrum({"haven2": {"p_hat": -0.37, "c": 0.21}}, sigma=sigma)
        assert out["source"] == "minimal_residual_snapshot"
        assert out["Z_E"] == pytest.approx(-0.37, abs=1e-15)
        assert out["Z_C"] == pytest.approx(0.21, abs=1e-15)
        assert out["Z_R"] == 0.0
        assert out["Z_H"] == pytest.approx(0.37 + 0.21, abs=1e-15)


# ------------------------------------------------------------------ former defects (fixed) and regression tests


# Former bug 3 (fixed): ceil(...) returned the first t with rho^t*gap <= eps; t_reset now uses the strict
# latch criterion.
def test_t_reset_boundary_residual_equals_epsilon_is_still_closed():
    eps, e0, e_star, rho = 0.25, 1.0, 0.0, 0.5  # all exactly representable in binary
    t = t_reset(eps, e0, e_star, rho)
    es = EnergyState(rho=rho, e0=e0, e_star=e_star)
    for _ in range(t):
        es.step(0.0)
    assert TransistorLatch(epsilon_switch=eps).is_open(es.p_hat), (t, es.p_hat)


# Former bug 4 (fixed): t_reset returned 0 for |E_0 - E*| == eps although the strict latch is closed there.
def test_t_reset_zero_means_latch_already_open_at_gap_equal_epsilon():
    eps, e0, e_star, rho = 0.5, 1.0, 0.5, 0.5
    latch = TransistorLatch(epsilon_switch=eps)
    assert not latch.is_open(e0 - e_star)  # |P_0| == eps: closed (strict)
    assert t_reset(eps, e0, e_star, rho) == 1  # so not "already eligible"
    assert latch.is_open(rho * (e0 - e_star))
    # just inside the band really is 0
    assert t_reset(eps, 0.999, e_star, rho) == 0 and latch.is_open(0.999 - e_star)


def test_t_reset_agrees_with_latch_on_exact_power_of_two_boundaries():
    """Regression for bugs 3-4: whenever rho^k * gap == eps exactly, t_reset must be k + 1 (not k)."""
    for rho in (0.5, 0.25):
        for k in range(0, 8):
            for gap in (1.0, 2.0, 0.75):
                eps = gap * rho**k  # exact in binary
                assert t_reset(eps, gap, 0.0, rho) == k + 1 == _brute_t_reset(eps, gap, 0.0, rho)
                assert t_reset(eps, 0.0, gap, rho) == k + 1  # sign of the gap is irrelevant


# Former bug 5 (fixed): the outside branch restarted at 1.0 (jump 0.5 -> 1.0 at |P| = eps); it now starts at 0.5.
def test_energy_sweet_spot_is_non_increasing_in_abs_p_hat():
    eps = 0.05
    mags = np.linspace(0.0, 0.3, 601)
    vals = [metric_energy_sweet_spot(float(m), eps) for m in mags]
    assert all(vals[i] >= vals[i + 1] - 1e-12 for i in range(len(vals) - 1))


def test_energy_sweet_spot_is_continuous_at_band_edge_and_unchanged_inside():
    """Regression for bug 5: left and right limits at |P| = eps agree; the in-band formula is unchanged."""
    for eps in (1e-3, 0.05, 0.3, 2.0):
        left = metric_energy_sweet_spot(eps * (1 - 1e-12), eps)
        assert left == pytest.approx(0.5, abs=1e-9)
        assert metric_energy_sweet_spot(eps, eps) == 0.5
        assert metric_energy_sweet_spot(-eps, eps) == 0.5
        for frac in (0.0, 0.1, 0.5, 0.9):
            assert metric_energy_sweet_spot(frac * eps, eps) == pytest.approx(1.0 - 0.5 * frac, abs=1e-15)
        assert metric_energy_sweet_spot(3 * eps, eps) == pytest.approx(0.5 * math.exp(-2.0), rel=1e-13)
