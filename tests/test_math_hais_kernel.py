"""Maths of the HAIS SovereignKernel (hais/hais_unified_kernel.py) and its two copies.

Equations verified (README tier table: "sigmoid m=0.5, cap exp(-2.2 S tau)"):

    risk  r   = (x + stress + anomaly + drift) / 4
    S(r)      = 1 / (1 + exp(-k (r - m))),   k = 4,  m = 0.5
    tau(S)    = 1 + beta exp(S),             beta = 0.5
    r'        = S tau
    cap       = exp(-2.2 r')
    delta r'  = r'_now - r'_prev              (telescoping sum  =  r'_n - 0)
    I         = 4 S (1 - S) (1 + r)           (k S (1-S)(1+r) in hais_unified_all)
    BLOCK     iff  cap < 0.25                 (GovernedStack.CAP_THROTTLE, strict)

Independent computation: all reference values are recomputed here with `decimal`
(50 digits), not with the module's `math`-based code.

Derived facts checked:
  * S(0.5) = 1/2 exactly, S(r) + S(1 - r) = 1 (odd symmetry about m).
  * For r in [0, 1]:  S in [sigma(-2), sigma(2)] = [0.1192, 0.8808].
  * cap is strictly decreasing in r; cap in (0, 1] for every real r.
  * max cap on [0,1] is cap(0) = 0.6637 > 0.25, min is cap(1) = 0.0139 < 0.25, so the
    breaker is neither always-on nor always-off (the m = 0 regression made max cap = 0.1344).
  * BLOCK threshold r* solves cap(r*) = 1/4: r* = 0.362785028970789...  (bisection in Decimal).
"""
from __future__ import annotations

import math
import random
from decimal import Decimal, getcontext

import hais_production_governed_pipeline as prod
import hais_unified_all as uall
import hais_unified_kernel as ker
import numpy as np
import pytest

getcontext().prec = 50
D = Decimal


def dec_sigmoid(r, k=4, m=D("0.5")):
    return 1 / (1 + (-D(k) * (D(r) - D(m))).exp())


def dec_chain(r, beta=D("0.5")):
    S = dec_sigmoid(r)
    tau = 1 + beta * S.exp()
    rp = S * tau
    cap = (-D("2.2") * rp).exp()
    return S, tau, rp, cap


def kernel_for_risk(cls, r):
    """Feed risk r through the public API:  x = 4 r, no telemetry."""
    return cls().evaluate_state(4.0 * r, {})


KERNELS = [ker.SovereignKernel, uall.SovereignKernel, prod.SovereignKernel]
IDS = ["hais_unified_kernel", "hais_unified_all", "hais_production_governed_pipeline"]


@pytest.mark.parametrize("cls", KERNELS, ids=IDS)
def test_exact_values_against_decimal_reference(cls):
    for r in (0.0, 0.1, 0.25, 0.3627850289707891, 0.5, 0.75, 1.0, 1.5, -0.5):
        out = kernel_for_risk(cls, r)
        S, tau, rp, cap = dec_chain(D(r))
        assert out["risk_metric"] == pytest.approx(r, abs=1e-15)
        assert out["S"] == pytest.approx(float(S), rel=1e-14)
        assert out["tau"] == pytest.approx(float(tau), rel=1e-14)
        assert out["r_prime"] == pytest.approx(float(rp), rel=1e-14)
        assert out["capability_cap"] == pytest.approx(float(cap), rel=1e-13)
        inst = 4 * S * (1 - S) * (1 + D(r))
        assert out["instability"] == pytest.approx(float(inst), rel=1e-13, abs=1e-15)


def test_closed_form_anchor_points():
    # r = 0.5:  S = 1/2, tau = 1 + e^{1/2}/2, r' = (1 + e^{1/2}/2)/2, I = 4 * 1/4 * 1.5 = 1.5
    out = kernel_for_risk(ker.SovereignKernel, 0.5)
    assert out["S"] == 0.5
    assert out["tau"] == pytest.approx(1 + 0.5 * math.exp(0.5), abs=1e-15)
    assert out["r_prime"] == pytest.approx(0.5 * (1 + 0.5 * math.exp(0.5)), abs=1e-15)
    assert out["instability"] == pytest.approx(1.5, abs=1e-15)
    # r = 0:  S = sigma(-2) = 1/(1+e^2)
    out0 = kernel_for_risk(ker.SovereignKernel, 0.0)
    assert out0["S"] == pytest.approx(1 / (1 + math.exp(2)), rel=1e-14)
    assert out0["capability_cap"] == pytest.approx(0.66367039501914, abs=1e-12)  # Decimal-checked above
    # r = 1:  S = sigma(2)
    out1 = kernel_for_risk(ker.SovereignKernel, 1.0)
    assert out1["S"] == pytest.approx(1 / (1 + math.exp(-2)), rel=1e-14)
    assert out1["capability_cap"] == pytest.approx(0.013905359487582, abs=1e-12)


def test_risk_metric_is_mean_of_four_inputs():
    rng = np.random.default_rng(11)
    for _ in range(200):
        x, s, a, d = rng.uniform(0, 1, size=4)
        out = ker.SovereignKernel().evaluate_state(x, {"stress": s, "anomaly": a, "drift": d})
        assert out["risk_metric"] == pytest.approx((x + s + a + d) / 4, abs=1e-15)
    # missing telemetry defaults to 0
    assert ker.SovereignKernel().evaluate_state(0.8, {})["risk_metric"] == pytest.approx(0.2)


def test_sigmoid_symmetry_and_midpoint():
    sig = ker.SovereignKernel.sigmoid
    assert sig(0.5, m=0.5) == 0.5
    rng = np.random.default_rng(12)
    for _ in range(200):
        r = rng.uniform(-3, 4)
        assert sig(r, m=0.5) + sig(1 - r, m=0.5) == pytest.approx(1.0, abs=1e-14)
    # default (legacy) midpoint is 0 -- evaluate_state must NOT use it
    assert sig(0.0) == 0.5
    assert ker.SovereignKernel().evaluate_state(0.0, {})["S"] != 0.5


def test_S_range_on_unit_risk_interval():
    lo, hi = 1 / (1 + math.exp(2)), 1 / (1 + math.exp(-2))
    for r in np.linspace(0, 1, 101):
        S = kernel_for_risk(ker.SovereignKernel, float(r))["S"]
        assert lo - 1e-15 <= S <= hi + 1e-15


def test_monotonicity_and_bounds_of_cap_and_r_prime():
    rs = np.linspace(-2.0, 3.0, 501)
    caps = np.array([kernel_for_risk(ker.SovereignKernel, float(r))["capability_cap"] for r in rs])
    rps = np.array([kernel_for_risk(ker.SovereignKernel, float(r))["r_prime"] for r in rs])
    assert np.all(np.diff(caps) < 0)  # strictly decreasing
    assert np.all(np.diff(rps) > 0)  # r' strictly increasing (S, tau both increase)
    assert np.all((caps > 0) & (caps <= 1.0))


def test_cap_is_decreasing_in_each_telemetry_channel():
    base = {"stress": 0.2, "anomaly": 0.2, "drift": 0.2}
    for ch in base:
        prev = 2.0
        for v in np.linspace(0, 1, 21):
            st = dict(base, **{ch: float(v)})
            cap = ker.SovereignKernel().evaluate_state(0.3, st)["capability_cap"]
            assert cap < prev
            prev = cap


def test_cap_range_makes_breaker_neither_always_on_nor_always_off():
    cap_best = kernel_for_risk(ker.SovereignKernel, 0.0)["capability_cap"]
    cap_worst = kernel_for_risk(ker.SovereignKernel, 1.0)["capability_cap"]
    assert cap_best > 0.25 > cap_worst


def test_m_equals_zero_regression_value_is_0p134():
    """The documented BUGFIX: with m=0 the best case cap is exp(-2.2 * 0.5 * (1 + e^{1/2}/2)) = 0.1344."""
    S = 0.5
    cap_m0 = math.exp(-2.2 * S * (1 + 0.5 * math.exp(S)))
    assert cap_m0 == pytest.approx(0.134, abs=5e-4)
    assert cap_m0 < 0.25 < kernel_for_risk(ker.SovereignKernel, 0.0)["capability_cap"]


def _dec_threshold():
    lo, hi = D(0), D(1)
    for _ in range(160):
        mid = (lo + hi) / 2
        if dec_chain(mid)[3] > D("0.25"):
            lo = mid
        else:
            hi = mid
    return lo


def test_block_threshold_risk_is_root_of_cap_equals_quarter():
    r_star = _dec_threshold()
    assert float(r_star) == pytest.approx(0.3627850289707891, abs=1e-12)
    eps = 1e-9
    below = kernel_for_risk(ker.SovereignKernel, float(r_star) - eps)["capability_cap"]
    above = kernel_for_risk(ker.SovereignKernel, float(r_star) + eps)["capability_cap"]
    assert below > 0.25 > above  # flips exactly there
    # in telemetry terms:  x + stress + anomaly + drift = 4 r* = 1.45114...
    assert 4 * float(r_star) == pytest.approx(1.4511401158831564, abs=1e-11)


def test_pipeline_threshold_behaviour_matches_cap_lt_quarter():
    """production_governed_pipeline throttles iff cap < 0.25 (or no valid candidate; seeded)."""
    for x, tel in ((0.2, {"stress": 0.1}), (0.9, {"stress": 0.9, "anomaly": 0.9, "drift": 0.9})):
        random.seed(1234)
        ker._GLOBAL_KERNEL.prev_r_prime = 0.0
        out = ker.production_governed_pipeline({"id": "m", "difficulty": x}, tel)
        cap = ker.SovereignKernel().evaluate_state(x, tel)["capability_cap"]
        assert out["capability_cap"] == pytest.approx(cap, rel=1e-13)
        if cap < 0.25:
            assert out["status"] == "throttled_circuit_breaker" and out["solution"] is None
        else:
            assert out["status"] in ("success", "throttled_circuit_breaker")


def test_pipeline_effective_score_formula():
    """effective = score * cap - 0.1 * instability  and the arg-max candidate is returned."""
    random.seed(99)
    ker._GLOBAL_KERNEL.prev_r_prime = 0.0
    x, tel = 0.1, {"stress": 0.05, "anomaly": 0.05, "drift": 0.05}
    out = ker.production_governed_pipeline({"id": "p", "difficulty": x}, tel)
    assert out["status"] == "success"
    # replay the identical RNG stream to recompute the candidate set independently
    random.seed(99)
    cands = ker.generate_bounded_candidates(ker.validate_and_normalize({"id": "p", "difficulty": x}))
    m = ker.SovereignKernel().evaluate_state(x, tel)
    eff = [(c["score"] * m["capability_cap"] - 0.1 * m["instability"], c["candidate_id"]) for c in cands]
    best = max(eff)
    assert out["solution"]["candidate_id"] == best[1]
    assert out["solution"]["effective_score"] == pytest.approx(best[0], rel=1e-13)


def test_validate_and_normalize_clamps_difficulty():
    for raw, expect in ((-3.0, 0.0), (0.4, 0.4), (7.0, 1.0)):
        assert ker.validate_and_normalize({"id": "a", "difficulty": raw})["difficulty_norm"] == expect
    with pytest.raises(ValueError):
        ker.validate_and_normalize({"difficulty": 0.5})


def test_delta_r_prime_telescopes():
    rng = np.random.default_rng(13)
    k = ker.SovereignKernel()
    total = 0.0
    last = None
    for _ in range(50):
        out = k.evaluate_state(rng.uniform(0, 1), {"stress": rng.uniform(0, 1)})
        total += out["delta_r_prime"]
        last = out["r_prime"]
    assert total == pytest.approx(last, abs=1e-12)  # prev starts at 0


def test_instability_properties():
    # I >= 0 for r >= -1;  max over S of 4 S (1 - S) is 1 at S = 1/2
    for r in np.linspace(-0.9, 2.0, 100):
        assert kernel_for_risk(ker.SovereignKernel, float(r))["instability"] >= 0.0
    # peak of S(1-S) is at r = m = 0.5
    rs = np.linspace(0, 1, 1001)
    S = np.array([kernel_for_risk(ker.SovereignKernel, float(r))["S"] for r in rs])
    assert rs[np.argmax(S * (1 - S))] == pytest.approx(0.5, abs=1e-3)


def test_three_copies_agree_bit_for_bit_on_random_inputs():
    rng = np.random.default_rng(14)
    for _ in range(200):
        x = float(rng.uniform(0, 1))
        tel = {k: float(rng.uniform(0, 1)) for k in ("stress", "anomaly", "drift")}
        outs = [cls(beta=0.5).evaluate_state(x, dict(tel)) for cls in KERNELS]
        for key in outs[0]:
            assert outs[1][key] == outs[0][key] == outs[2][key]


def test_k_sigmoid_parameter_in_full_form_kernel():
    """hais_unified_all exposes k: S(r; k) = sigma(k (r - 1/2));  I = k S (1-S)(1+r)."""
    for k in (1.0, 2.0, 4.0, 8.0):
        out = uall.SovereignKernel(k_sigmoid=k).evaluate_state(0.8, {})
        r = 0.2
        S = 1 / (1 + math.exp(-k * (r - 0.5)))
        assert out["S"] == pytest.approx(S, rel=1e-14)
        assert out["instability"] == pytest.approx(k * S * (1 - S) * (1 + r), rel=1e-13)


def test_beta_scales_tau_linearly():
    for beta in (0.0, 0.25, 0.5, 1.0):
        out = ker.SovereignKernel(beta=beta).evaluate_state(0.6, {})
        assert out["tau"] == pytest.approx(1 + beta * math.exp(out["S"]), rel=1e-14)
    # beta = 0 gives r' = S exactly
    o = ker.SovereignKernel(beta=0.0).evaluate_state(0.6, {})
    assert o["r_prime"] == pytest.approx(o["S"], rel=1e-14)


def test_stack_cap_threshold_constant():
    from governed_stack.stack import GovernedStack

    assert GovernedStack.CAP_THROTTLE == 0.25
