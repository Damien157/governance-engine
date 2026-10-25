"""Maths of the decision-support signals and the rate/flow limiters
(certified_governance_unified.py; hais/certified_governance.py carries byte-identical copies).

MathematicalSignals (heuristic, documented as such -- we verify the arithmetic, not statistical validity)
  risk_signal        R = a(1-V) + b A + c F + d V A F,   (a,b,c,d) = (.4,.3,.2,.1), inputs clipped to [0,1]
                     R is multilinear => its max over the cube is at a vertex: max = max(a+b+c, b+c+d) = 0.9
                     dR/dA = b + d V F > 0, dR/dF = c + d V A > 0, dR/dV = -a + d A F < 0
                     level: HIGH if R>0.7, MEDIUM if R>0.4, else LOW (strict inequalities)
  integrity_pressure P = min(1, breaks/total) * exp(-lam * hours/24)
  throughput         R_avg=(cpu+mem)/2, S = load/cap + (1-R_avg),  avail = cap (1-R_avg) e^{-S/2},
                     eff = clip((1-R_avg)(1 - 2|load/cap - 1/2|), 0, 1)
  anomaly_signal     z = |x_n - mean(x_1..x_{n-1})| / s  (sample std, ddof=1) over the last `window`,
                     signal = min(1, z/6); severity thresholds 2,3,5; flat baseline => z in {0, 10}
  risk_trend         OLS: slope = Sxy/Sxx, intercept = ybar - slope xbar, R^2 = 1 - SS_res/SS_tot,
                     compared with numpy.polyfit; prediction_k = slope (n+k) + intercept
FlowControl (token bucket)
  tokens(t+dt) = min(C, tokens(t) + rho dt) ; consume(a): tokens >= a ? tokens -= a, u=(C-tokens)/C : u = 1
  checked against an independent event-by-event reference model under a fake clock; mode thresholds .5/.7/.9
UserRateLimiter (sliding window, 3600 s)
  count_k = |{ t_j : t_j >= now_k - 3600 }| (inclusive boundary), exceeded = count > limit;
  checked against a brute-force list reference under a fake clock.
"""
from __future__ import annotations

import math
import statistics

import certified_governance as cg_hais  # hais/certified_governance.py
import certified_governance_unified as cg_unified
import numpy as np
import pytest

MODULES = [cg_unified, cg_hais]
IDS = ["unified", "hais_copy"]


@pytest.fixture(params=MODULES, ids=IDS)
def cg(request):
    return request.param


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def clock(monkeypatch, cg):
    c = Clock()
    monkeypatch.setattr(cg.time, "time", c)
    return c


def R_ref(V, A, F, a=0.4, b=0.3, c=0.2, d=0.1):
    V, A, F = (min(1.0, max(0.0, z)) for z in (V, A, F))
    return a * (1 - V) + b * A + c * F + d * V * A * F


# --------------------------------------------------------------------------------------- risk_signal


def test_risk_signal_hand_values(cg):
    ms = cg.MathematicalSignals
    assert ms.risk_signal(1, 0, 0)["risk_signal"] == 0.0
    assert ms.risk_signal(0, 1, 1)["risk_signal"] == pytest.approx(0.9)
    assert ms.risk_signal(1, 1, 1)["risk_signal"] == pytest.approx(0.6)
    assert ms.risk_signal(0, 0, 0)["risk_signal"] == pytest.approx(0.4)
    assert ms.risk_signal(0.5, 0.5, 0.5)["risk_signal"] == pytest.approx(0.2 + 0.15 + 0.1 + 0.1 * 0.125)


def test_risk_signal_matches_formula_on_random_inputs_and_components_sum(cg):
    rng = np.random.default_rng(20261025)
    for V, A, F in rng.uniform(-0.2, 1.2, size=(500, 3)):  # includes out-of-range -> clipping
        out = cg.MathematicalSignals.risk_signal(V, A, F)
        assert out["risk_signal"] == pytest.approx(R_ref(V, A, F), abs=1e-15)
        comp = out["components"]
        assert sum(comp.values()) == pytest.approx(out["risk_signal"], abs=1e-15)
        assert 0.0 <= out["risk_signal"] <= 0.9 + 1e-15  # vertex-max bound


def test_risk_signal_extremum_is_at_vertices_and_equals_0_9(cg):
    verts = [(v, a, f) for v in (0, 1) for a in (0, 1) for f in (0, 1)]
    vals = [cg.MathematicalSignals.risk_signal(*v)["risk_signal"] for v in verts]
    assert max(vals) == pytest.approx(0.9)
    assert min(vals) == 0.0


def test_risk_signal_monotonicity(cg):
    rng = np.random.default_rng(7)
    ms = cg.MathematicalSignals
    for V, A, F in rng.uniform(0, 1, size=(200, 3)):
        h = 1e-3
        r0 = ms.risk_signal(V, A, F)["risk_signal"]
        if A + h <= 1:
            assert ms.risk_signal(V, A + h, F)["risk_signal"] > r0  # dR/dA = b + dVF > 0
        if F + h <= 1:
            assert ms.risk_signal(V, A, F + h)["risk_signal"] > r0
        if V + h <= 1:
            assert ms.risk_signal(V + h, A, F)["risk_signal"] < r0  # dR/dV = -a + dAF < -0.3


def test_risk_signal_custom_weights_and_level_boundaries(cg):
    ms = cg.MathematicalSignals
    w = {"a": 0.5, "b": 0.25, "c": 0.125, "d": 0.0625}
    out = ms.risk_signal(0.25, 0.5, 0.75, weights=w)
    assert out["risk_signal"] == pytest.approx(0.5 * 0.75 + 0.25 * 0.5 + 0.125 * 0.75 + 0.0625 * 0.25 * 0.5 * 0.75)
    # strict thresholds: a risk landing exactly ON 0.7 / 0.4 belongs to the lower band
    exact = {"a": 1.0, "b": 0.0, "c": 0.0, "d": 0.0}
    on_hi = ms.risk_signal(0.3, 0, 0, exact)
    assert on_hi["risk_signal"] == 0.7 and on_hi["qualitative_level"] == "MEDIUM"
    on_lo = ms.risk_signal(0.6, 0, 0, exact)
    assert on_lo["risk_signal"] == 0.4 and on_lo["qualitative_level"] == "LOW"
    assert ms.risk_signal(0.25, 0, 0, exact)["qualitative_level"] == "HIGH"  # 0.75 > 0.7
    assert ms.risk_signal(0.5, 0, 0, exact)["qualitative_level"] == "MEDIUM"  # 0.5 > 0.4
    assert ms.risk_signal(1.0, 0.0, 0.0)["qualitative_level"] == "LOW"
    assert ms.risk_signal(0.0, 1.0, 1.0)["qualitative_level"] == "HIGH"  # 0.9
    assert ms.risk_signal(0.5, 0.5, 0.0)["qualitative_level"] == "LOW"  # 0.2+0.15 = 0.35
    assert ms.risk_signal(0.5, 1.0, 0.5)["qualitative_level"] == "MEDIUM"  # 0.2+0.3+0.1+0.025 = 0.625


def test_risk_signal_level_is_consistent_with_value(cg):
    rng = np.random.default_rng(99)
    for V, A, F in rng.uniform(0, 1, size=(300, 3)):
        o = cg.MathematicalSignals.risk_signal(V, A, F)
        r = o["risk_signal"]
        exp = "HIGH" if r > 0.7 else "MEDIUM" if r > 0.4 else "LOW"
        assert o["qualitative_level"] == exp


# --------------------------------------------------------------------------------------- integrity pressure


def test_integrity_pressure_formula(cg):
    f = cg.MathematicalSignals.integrity_pressure_signal
    assert f(0, 0, 24) == {"integrity_pressure": 0.0}
    assert f(0, 100, 24)["integrity_pressure"] == 0.0
    assert f(5, 10, 0)["integrity_pressure"] == pytest.approx(0.5)  # no decay at t = 0
    assert f(5, 10, 240, lam=0.1)["integrity_pressure"] == pytest.approx(0.5 * math.exp(-0.1 * 10))
    rng = np.random.default_rng(5)
    for _ in range(100):
        total = int(rng.integers(1, 1000))
        breaks = int(rng.integers(0, total + 1))
        hrs = float(rng.uniform(0, 500))
        lam = float(rng.uniform(0, 2))
        p = f(breaks, total, hrs, lam)["integrity_pressure"]
        assert p == pytest.approx(min(1.0, breaks / total * math.exp(-lam * hrs / 24)), rel=1e-12)
        assert 0.0 <= p <= 1.0


def test_integrity_pressure_monotone_in_time_and_breaks(cg):
    f = cg.MathematicalSignals.integrity_pressure_signal
    ts = [f(3, 10, h)["integrity_pressure"] for h in (0, 12, 24, 48, 240)]
    assert all(a > b for a, b in zip(ts, ts[1:]))
    bs = [f(k, 10, 24)["integrity_pressure"] for k in range(11)]
    assert all(a < b for a, b in zip(bs, bs[1:]))
    # over-count is clipped at 1
    assert f(50, 10, 0)["integrity_pressure"] == 1.0


# --------------------------------------------------------------------------------------- throughput


def test_throughput_signal_formulas(cg):
    f = cg.MathematicalSignals.throughput_signal
    rng = np.random.default_rng(11)
    for _ in range(200):
        load, cap = float(rng.uniform(0, 200)), float(rng.uniform(1, 200))
        cpu, mem = float(rng.uniform(-0.1, 1.1)), float(rng.uniform(-0.1, 1.1))
        out = f(load, cap, cpu, mem)
        Ravg = (min(1, max(0, cpu)) + min(1, max(0, mem))) / 2
        ratio = load / cap
        S = ratio + 1 - Ravg
        assert out["available_capacity"] == pytest.approx(max(0.0, cap * (1 - Ravg) * math.exp(-0.5 * S)), rel=1e-12)
        eff = max(0.0, min(1.0, (1 - Ravg) * (1 - abs(ratio - 0.5) * 2)))
        assert out["efficiency_score"] == pytest.approx(eff, abs=1e-12)
        assert out["utilization_percentage"] == pytest.approx(100 * Ravg)
        assert out["status"] == ("OPTIMAL" if eff > 0.7 else "STRAINED" if eff > 0.4 else "CRITICAL")


def test_throughput_hand_values_and_sweet_spot(cg):
    f = cg.MathematicalSignals.throughput_signal
    idle_half = f(50, 100, 0.0, 0.0)  # ratio .5, R_avg 0 -> eff = 1
    assert idle_half["efficiency_score"] == 1.0 and idle_half["status"] == "OPTIMAL"
    assert idle_half["available_capacity"] == pytest.approx(100 * math.exp(-0.5 * 1.5))
    saturated = f(100, 100, 1.0, 1.0)  # R_avg = 1 -> eff = 0, avail = 0
    assert saturated["efficiency_score"] == 0.0 and saturated["available_capacity"] == 0.0
    assert saturated["status"] == "CRITICAL"
    assert f(10, 0, 0.2, 0.2)["available_capacity"] == 0.0  # zero capacity -> ratio 0, cap 0
    # efficiency is unimodal in load with peak at ratio .5 (|ratio - .5| is minimised)
    effs = [f(r * 100, 100, 0.1, 0.1)["efficiency_score"] for r in (0.0, 0.25, 0.5, 0.75, 1.0)]
    assert effs.index(max(effs)) == 2 and effs[0] == pytest.approx(effs[4]) and effs[1] == pytest.approx(effs[3])


# --------------------------------------------------------------------------------------- anomaly


def test_anomaly_signal_matches_statistics_reference(cg):
    f = cg.MathematicalSignals.anomaly_signal
    rng = np.random.default_rng(31)
    for _ in range(100):
        window = int(rng.integers(4, 15))
        data = list(rng.normal(10, 2, size=window + int(rng.integers(0, 10))))
        data[-1] += float(rng.normal(0, 8))
        out = f(data, window=window)
        recent = data[-window:]
        base = recent[:-1]
        mean, std = np.mean(base), np.std(base, ddof=1)
        z = abs(recent[-1] - mean) / std
        assert out["z_score"] == pytest.approx(z, rel=1e-9)
        assert out["anomaly_signal"] == pytest.approx(min(1.0, z / 6), rel=1e-9)
        assert out["mean"] == pytest.approx(mean) and out["std"] == pytest.approx(std)
        assert out["severity"] == ("CRITICAL" if z > 5 else "HIGH" if z > 3 else "MEDIUM" if z > 2 else "LOW")


def test_anomaly_signal_hand_example_and_edge_cases(cg):
    f = cg.MathematicalSignals.anomaly_signal
    # baseline 1..9: mean 5, sample std = sqrt(60/8) ; current 5 + 3 s
    s = statistics.stdev(range(1, 10))
    assert s == pytest.approx(math.sqrt(7.5))
    out = f(list(range(1, 10)) + [5 + 3 * s + 1e-9], window=10)
    assert out["z_score"] == pytest.approx(3.0, abs=1e-6) and out["severity"] == "HIGH"
    assert out["anomaly_signal"] == pytest.approx(0.5, abs=1e-6)
    assert f([1.0] * 5, window=10) == {
        "anomaly_signal": 0.0, "z_score": 0.0, "severity": "LOW", "reason": "insufficient_data"}
    flat_same = f([2.0] * 10, window=10)
    assert flat_same["z_score"] == 0.0 and flat_same["anomaly_signal"] == 0.0
    flat_jump = f([2.0] * 9 + [2.5], window=10)
    assert flat_jump["z_score"] == 10.0 and flat_jump["anomaly_signal"] == 1.0
    assert flat_jump["severity"] == "CRITICAL"
    one_pt = f([1.0, 3.0], window=2)  # single-point baseline -> std = 0 branch -> z = 10
    assert one_pt["std"] == 0.0 and one_pt["z_score"] == 10.0
    assert f([5.0, 5.0], window=2)["z_score"] == 0.0


# --------------------------------------------------------------------------------------- OLS trend


def test_risk_trend_equals_numpy_polyfit_and_r2_definition(cg):
    f = cg.MathematicalSignals.risk_trend_signal
    rng = np.random.default_rng(404)
    for _ in range(100):
        n = int(rng.integers(3, 40))
        y = rng.uniform(0, 1, size=n)
        out = f(list(y), horizon=4)
        x = np.arange(n)
        slope, intercept = np.polyfit(x, y, 1)
        assert out["slope"] == pytest.approx(slope, abs=1e-10)
        assert out["intercept"] == pytest.approx(intercept, abs=1e-10)
        yhat = slope * x + intercept
        r2 = 1 - np.sum((y - yhat) ** 2) / np.sum((y - y.mean()) ** 2)
        assert out["r_squared"] == pytest.approx(r2, abs=1e-9)
        assert 0.0 <= out["r_squared"] <= 1.0 + 1e-12
        assert out["predictions"] == pytest.approx([slope * (n + k) + intercept for k in range(4)], abs=1e-9)


def test_risk_trend_exact_on_linear_data_and_labels(cg):
    f = cg.MathematicalSignals.risk_trend_signal
    up = f([0.1 + 0.05 * i for i in range(6)], horizon=2)
    assert up["slope"] == pytest.approx(0.05) and up["intercept"] == pytest.approx(0.1)
    assert up["r_squared"] == pytest.approx(1.0) and up["trend"] == "RISING"
    assert up["predictions"] == pytest.approx([0.1 + 0.05 * 6, 0.1 + 0.05 * 7])
    assert f([0.9 - 0.02 * i for i in range(5)])["trend"] == "FALLING"
    assert f([0.5, 0.505, 0.5, 0.505, 0.5])["trend"] == "STABLE"
    const = f([0.3, 0.3, 0.3, 0.3])  # SS_tot = 0 -> r2 defined as 1
    assert const["slope"] == 0.0 and const["r_squared"] == 1.0 and const["trend"] == "STABLE"
    short = f([0.1, 0.2])
    assert short["trend"] == "UNKNOWN" and short["r_squared"] is None and short["predictions"] == []
    # slope threshold is strict at +-0.01
    assert f([0.0, 0.0099, 0.0198])["trend"] == "STABLE"
    assert f([0.0, 0.0101, 0.0202])["trend"] == "RISING"


def test_risk_trend_slope_is_invariant_to_vertical_shift_and_scales_linearly(cg):
    f = cg.MathematicalSignals.risk_trend_signal
    rng = np.random.default_rng(8)
    y = list(rng.uniform(0, 1, 12))
    base = f(y)
    shifted = f([v + 3.0 for v in y])
    scaled = f([2.5 * v for v in y])
    assert shifted["slope"] == pytest.approx(base["slope"], abs=1e-12)
    assert shifted["intercept"] == pytest.approx(base["intercept"] + 3.0, abs=1e-12)
    assert shifted["r_squared"] == pytest.approx(base["r_squared"], abs=1e-12)
    assert scaled["slope"] == pytest.approx(2.5 * base["slope"], abs=1e-12)
    assert scaled["r_squared"] == pytest.approx(base["r_squared"], abs=1e-12)


# --------------------------------------------------------------------------------------- FlowControl


def test_flow_control_hand_trace(cg, clock):
    fc = cg.FlowControl(capacity=10, refill_per_sec=2.0)
    assert fc.consume(4) == pytest.approx(0.4)  # tokens 6
    clock.t += 1.0  # +2 -> 8
    assert fc.consume(3) == pytest.approx(0.5)  # tokens 5
    clock.t += 100.0  # capped at 10
    assert fc.consume(1) == pytest.approx(0.1)  # tokens 9
    assert fc.consume(9) == pytest.approx(1.0)  # tokens 0 -> utilization 1 (consumed)
    assert fc.consume(1) == 1.0  # rejected (also utilization 1.0)
    st = fc.stats()
    assert st["total_consumed"] == 17.0 and st["total_rejected"] == 1.0
    assert st["current_tokens"] == pytest.approx(0.0)
    assert st["utilization"] == pytest.approx(1.0)


def test_flow_control_matches_reference_model_and_invariants(cg, clock):
    rng = np.random.default_rng(1234)
    C, rho = 25, 3.5
    fc = cg.FlowControl(C, rho)
    tokens, consumed, rejected = float(C), 0.0, 0.0
    for _ in range(2000):
        dt = float(rng.exponential(0.5))
        amt = float(rng.choice([0.5, 1.0, 2.0, 5.0]))
        clock.t += dt
        tokens = min(C, tokens + rho * dt)  # independent reference
        if tokens >= amt:
            tokens -= amt
            consumed += amt
            exp_u = (C - tokens) / C
        else:
            rejected += amt
            exp_u = 1.0
        assert fc.consume(amt) == pytest.approx(exp_u, abs=1e-9)
        assert 0.0 <= fc.tokens <= C
        assert 0.0 <= exp_u <= 1.0
    assert fc._total_consumed == pytest.approx(consumed)
    assert fc._total_rejected == pytest.approx(rejected)
    # conservation: tokens_final = C + refill_credited - consumed  is bounded; consumed <= C + rho*T
    assert consumed <= C + rho * (clock.t - 1_000_000.0) + 1e-6


def test_flow_control_long_run_throughput_cannot_exceed_refill_rate(cg, clock):
    C, rho = 10, 5.0
    fc = cg.FlowControl(C, rho)
    T = 200.0
    steps = 20000
    for _ in range(steps):  # saturating demand
        clock.t += T / steps
        fc.consume(1.0)
    assert fc._total_consumed <= C + rho * T + 1e-6
    assert fc._total_consumed >= 0.95 * rho * T  # and it does use (nearly) all of the refill


def test_flow_control_mode_thresholds(cg):
    fc = cg.FlowControl(1, 1.0)
    cases = [(0.0, "NORMAL"), (0.4999, "NORMAL"), (0.5, "ELEVATED"), (0.6999, "ELEVATED"),
             (0.7, "HIGH_PRESSURE"), (0.8999, "HIGH_PRESSURE"), (0.9, "EMERGENCY"), (1.0, "EMERGENCY")]
    for u, m in cases:
        assert fc.mode(u) == m


# --------------------------------------------------------------------------------------- UserRateLimiter


def test_rate_limiter_boundary_semantics(cg, clock):
    rl = cg.UserRateLimiter(max_per_hour=3)
    assert rl.record_and_check("u")["count_last_hour"] == 1
    clock.t += 1800
    assert rl.record_and_check("u")["count_last_hour"] == 2
    clock.t += 1800  # first entry is exactly 3600 s old: cutoff = now-3600, kept because `< cutoff` is strict
    out = rl.record_and_check("u")
    assert out["count_last_hour"] == 3 and out["exceeded"] is False
    out = rl.record_and_check("u")
    assert out["count_last_hour"] == 4 and out["exceeded"] is True and out["limit"] == 3
    clock.t += 0.001  # first entry now 3600.001 s old -> evicted
    assert rl.record_and_check("u")["count_last_hour"] == 4  # entries at 1800, 3600, 3600, now
    assert rl.record_and_check("other")["count_last_hour"] == 1  # users are independent


def test_rate_limiter_matches_bruteforce_reference(cg, clock):
    rng = np.random.default_rng(777)
    rl = cg.UserRateLimiter(max_per_hour=20)
    hist: dict[str, list[float]] = {}
    for _ in range(3000):
        clock.t += float(rng.exponential(60.0))
        u = f"user{int(rng.integers(0, 4))}"
        hist.setdefault(u, []).append(clock.t)
        expected = sum(1 for t in hist[u] if t >= clock.t - 3600.0)
        out = rl.record_and_check(u)
        assert out["count_last_hour"] == expected
        assert out["exceeded"] == (expected > 20)


def test_rate_limiter_prune_never_changes_counts(cg, clock):
    rl = cg.UserRateLimiter(max_per_hour=5, max_tracked_users=3)
    for i in range(10):
        rl.record_and_check(f"old{i}")
    clock.t += 7200  # all old windows aged out
    for i in range(4):
        out = rl.record_and_check(f"new{i}")
        assert out["count_last_hour"] == 1
    assert rl.stats()["tracked_users"] <= 5  # stale windows pruned once above the tracking cap
    assert rl.record_and_check("new0")["count_last_hour"] == 2  # live state survived pruning
