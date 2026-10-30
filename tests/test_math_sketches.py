"""Maths of the SKETCH / reference modules (all of these are off the live govern() path -- sketch-only):
3-SAT DPLL, invariant-topology diagnostics, Taylor-Green control law, HavenSovereignEngine, the CVXOPT unified
control sketch, and the HAIS runtime v0.2 SafetyConfig / MetricsMonitor / AuditTrail.

3-SAT (solvers/hais_3sat.py, DPLL + unit propagation)
    SAT/UNSAT verdict equals brute force over all 2^n assignments for seeded random k-CNF (n <= 8), and every
    returned assignment is a full model: for each clause C, exists l in C with assignment[|l|] == (l > 0).
Invariant topology (diagnostics/invariant_topology.py)
    beta_0 = #weak components, beta_1 = E - V + beta_0 (cyclomatic number, >= 0 always for a multigraph),
    "spectral_radius" is only the PROXY maxOutDeg / V -- tests pin the proxy and the Gershgorin bound
    rho(A) <= max out-degree, which is why it is a meaningful scale; self-loop ratio = #loops / E;
    barrier iff hint == barrier or ratio > .4 or (beta_1 > 0 and ratio > .2).
    Cycle finder: finds cycles on simple examples; a randomised comparison with a reachability oracle (once a
    strict xfail; the DFS missed cycles through finished nodes, now fixed) checks every on-cycle node is reported.
Taylor-Green / control law (fluids/control_law.py; fluids/*.py need Dedalus so only the analytics are tested)
    u = (sin x cos y cos z, -cos x sin y cos z, 0):  div u = 0,  <E> = <|u|^2>/2 = 1/8,
    omega = curl u = (-cos x sin y sin z, -sin x cos y sin z, 2 sin x sin y cos z),  <enstrophy> = <|omega|^2>/2 = 3/8,
    <2 nu S:S> = nu <|omega|^2> = 2 nu <enstrophy> for periodic incompressible flow (verified spectrally).
    CLF: V = (E-E*)^2, f = g (E*-E) u - c dV/du with dV/du = 2 (E-E*) u  =>  dV/dt = -4E (g + 2c)(E-E*)^2 <= 0
    (inviscid, forcing-only), checked by a finite-difference step.
    CBF: -k (m - h) omega below the margin, 0 above (continuous at h = m).  Adaptive Re: *1.01 / *0.99, clamped.
HavenSovereignEngine (haven/haven_sovereign_engine.py)
    I = sum dR_i / (T L),  Phi(z) = 1/(1+|z|) + int_0^inf e^{-g t} dt = 1/(1+|z|) + 1/g,
    zh(s) = aX |zX(s)| + aR zR(s) + aC zC(s),  d(zh_i, zh_j) = sqrt(int (zh_i - zh_j)^2 ds)  (closed forms).
Unified control sketch (solvers/unified_control_sketch.py): the QP has no constraints (placeholder), so u = u_nom and the
    Euler trajectory is v_k = 10(1 - q^k), q = 1 - 0.1 dt, p_k = dt sum_{j<k} v_j -- verified in closed form.
HAIS runtime v0.2: SafetyConfig bounds (NaN fails closed), MetricsMonitor = numpy mean/min/max/percentile, AuditTrail
    hash = SHA256(JSON(event, sort_keys))[:16] (per-entry, NOT chained).
"""
from __future__ import annotations

import hashlib
import itertools
import json
import math
import random

import hais_governance_unified_runtime_v02 as rt
import numpy as np
import pytest
from diagnostics import invariant_topology as it
from fluids import control_law as cl
from haven.haven_sovereign_engine import HavenSovereignEngine
from scipy.integrate import quad
from solvers.hais_3sat import HAIS3SATSolver
from solvers.unified_control_sketch import simulate_unified_control

# ====================================================================================== 3-SAT


def brute_force_sat(n, clauses):
    for bits in itertools.product([False, True], repeat=n):
        if all(any(bits[abs(l) - 1] == (l > 0) for l in c) for c in clauses):
            return True
    return False


def is_model(assign, n, clauses):
    return len(assign) == n and all(any(assign[abs(l)] == (l > 0) for l in c) for c in clauses)


def solve_quiet(n, clauses, capsys):
    res = HAIS3SATSolver(n, [list(c) for c in clauses]).solve()
    capsys.readouterr()  # swallow the banner print
    return res


def test_dpll_matches_bruteforce_on_random_3cnf(capsys):
    rng = random.Random(20261025)
    seen = {True: 0, False: 0}
    for _ in range(400):
        n = rng.randint(3, 8)
        m = rng.randint(1, int(5.5 * n))
        clauses = [[rng.choice([-1, 1]) * v for v in rng.sample(range(1, n + 1), 3)] for _ in range(m)]
        res = solve_quiet(n, clauses, capsys)
        truth = brute_force_sat(n, clauses)
        seen[truth] += 1
        assert (res is not None) == truth, (n, clauses)
        if res is not None:
            assert is_model(res, n, clauses)
    assert seen[True] > 50 and seen[False] > 20  # both outcomes exercised (threshold ratio ~4.26)


def test_dpll_hand_cases(capsys):
    sat = solve_quiet(4, [[1, -2, 3], [-1, 2, -3], [2, 3, -4], [-1, -3, 4]], capsys)  # the module's own example
    assert is_model(sat, 4, [[1, -2, 3], [-1, 2, -3], [2, 3, -4], [-1, -3, 4]])
    # complete 2-variable contradiction x & ~x
    assert solve_quiet(1, [[1], [-1]], capsys) is None
    # all 8 sign patterns over 3 vars is the canonical UNSAT 3-CNF
    unsat = [[a * 1, b * 2, c * 3] for a in (1, -1) for b in (1, -1) for c in (1, -1)]
    assert solve_quiet(3, unsat, capsys) is None
    assert brute_force_sat(3, unsat) is False
    assert solve_quiet(3, [], capsys) is not None  # empty formula: any full assignment
    assert solve_quiet(2, [[]], capsys) is None  # empty clause is unsatisfiable
    # unit propagation forces a chain: x1, (-x1 v x2), (-x2 v x3) => all True
    forced = solve_quiet(3, [[1], [-1, 2], [-2, 3]], capsys)
    assert forced == {1: True, 2: True, 3: True}
    assert solve_quiet(3, [[1, -1, 2]], capsys) is not None  # tautological clause


# ====================================================================================== topology


def uf_components(edges):
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for u, v in edges:
        parent[find(u)] = find(v)
    return len({find(x) for x in list(parent)})


def test_graph_topology_hand_values():
    tri_loops = [("B0", "B0"), ("B1", "B1"), ("B2", "B2"), ("B0", "B1"), ("B1", "B2"), ("B2", "B0")]
    comp, b1, spec, V, E = it.compute_graph_topology(tri_loops)
    assert (comp, b1, V, E) == (1, 6 - 3 + 1, 3, 6) and spec == pytest.approx(2 / 3)
    assert it.self_loop_ratio(tri_loops) == 0.5
    assert it.compute_graph_topology([]) == (0, 0, 0.0, 0, 0)
    assert it.self_loop_ratio([]) == 0.0
    path = [("a", "b"), ("b", "c"), ("c", "d")]  # tree: beta_1 = 3 - 4 + 1 = 0
    assert it.compute_graph_topology(path) == (1, 0, pytest.approx(1 / 4), 4, 3)
    two = [("a", "b"), ("c", "d"), ("c", "d")]  # two components, parallel edge: beta_1 = 3 - 4 + 2 = 1
    assert it.compute_graph_topology(two)[:2] == (2, 1)
    star = [("h", x) for x in "abcde"]
    assert it.compute_graph_topology(star)[2] == pytest.approx(5 / 6)  # max out-degree 5 over V = 6


def test_graph_topology_matches_union_find_and_cyclomatic_identity_random():
    rng = random.Random(8)
    for _ in range(300):
        n = rng.randint(1, 9)
        edges = [(f"n{rng.randrange(n)}", f"n{rng.randrange(n)}") for _ in range(rng.randint(1, 15))]
        comp, b1, spec, V, E = it.compute_graph_topology(edges)
        nodes = {x for e in edges for x in e}
        assert V == len(nodes) and E == len(edges)
        assert comp == uf_components(edges)
        assert b1 == E - V + comp >= 0  # cyclomatic number of a multigraph is never negative
        out = {}
        for u, _ in edges:
            out[u] = out.get(u, 0) + 1
        assert spec == pytest.approx(max(out.values()) / V)
        # Gershgorin / row-sum bound: spectral radius of the (multi)adjacency <= max out-degree = proxy * V
        A = np.zeros((V, V))
        idx = {x: i for i, x in enumerate(sorted(nodes))}
        for u, v in edges:
            A[idx[u], idx[v]] += 1
        assert max(abs(np.linalg.eigvals(A))) <= spec * V + 1e-9
        assert it.self_loop_ratio(edges) == pytest.approx(sum(u == v for u, v in edges) / E)


def test_barrier_rule_truth_table():
    def t(edges, hint="local"):
        return it.detect_barrier_shape(it.Technique("t", edges, [], hint))[0]

    assert t([], "barrier") is True  # hint alone suffices
    assert t([("a", "a")]) is True  # ratio 1 > .4
    assert t([("a", "b"), ("b", "c")]) is False  # ratio 0, beta_1 = 2-3+1 = 0
    assert t([("a", "b"), ("b", "c"), ("c", "a")]) is False  # a pure cycle: beta_1 = 1 but ratio 0 <= .2
    # ratio exactly 1/5 = .2 is NOT > .2 (strict), even though beta_1 = 5-5+1 = 1 > 0
    assert t([("a", "a"), ("a", "b"), ("b", "c"), ("c", "d"), ("d", "e")]) is False
    # ratio 1/3 > .2 and beta_1 = 3-3+1 = 1 > 0  => barrier
    assert t([("a", "a"), ("a", "b"), ("b", "c")]) is True


def test_barrier_rule_is_exactly_the_documented_predicate():
    rng = random.Random(77)
    for _ in range(300):
        n = rng.randint(2, 6)
        edges = [(f"n{rng.randrange(n)}", f"n{rng.randrange(n)}") for _ in range(rng.randint(1, 10))]
        for hint in ("local", "global", "barrier", "bogus"):
            tech = it.Technique("t", edges, [], hint)
            got = it.detect_barrier_shape(tech)[0]
            nodes = {x for e in edges for x in e}
            ratio = sum(u == v for u, v in edges) / len(edges)
            b1 = len(edges) - len(nodes) + uf_components(edges)
            want = tech.shape_hint == "barrier" or ratio > 0.4 or (b1 > 0 and ratio > 0.2)
            assert got == want
    assert it.Technique("t", [], [], "bogus").shape_hint == "local"  # unknown hints are normalised


def test_cycle_finder_on_cases_it_handles():
    f = lambda g: sorted({x for c in it.find_cycles_in_invariants(g) for x in c})  # noqa: E731
    assert f({"a": ["b"], "b": ["c"], "c": ["a"]}) == ["a", "b", "c"]
    assert f({"a": ["b"], "b": ["c"], "c": []}) == []  # DAG
    assert f({"a": ["a"]}) == ["a"]  # self-loop
    assert f({"a": ["b"], "b": ["a"], "c": ["d"], "d": ["c"], "e": ["a"]}) == ["a", "b", "c", "d"]
    inv = [it.Invariant("I0", ["I1"]), it.Invariant("I1", ["I2"]), it.Invariant("I2", ["I0"]), it.Invariant("I3", ["I0"])]
    assert it.detect_circular_invariants(inv) == ["I0", "I1", "I2"]
    # expression hash is the documented SHA-256 of "name:sorted(deps)"
    assert inv[0].expression_hash == hashlib.sha256("I0:['I1']".encode()).hexdigest()


def _reach(g, s, t):
    seen, stack = set(), [s]
    while stack:
        u = stack.pop()
        for v in g.get(u, []):
            if v == t:
                return True
            if v not in seen:
                seen.add(v)
                stack.append(v)
    return False


# Former bug 6 (fixed): the single-pass DFS never re-entered finished nodes and missed cycles through them.
def test_cycle_finder_reports_every_node_that_lies_on_a_cycle_minimal_repro():
    g = {"a": ["b", "c"], "b": ["a"], "c": ["b"]}
    on_cycle = {x for x in g if _reach(g, x, x)}
    assert on_cycle == {"a", "b", "c"}
    reported = {x for c in it.find_cycles_in_invariants(g) for x in c}
    assert reported == on_cycle


def test_cycle_finder_matches_reachability_oracle_on_random_digraphs():
    bad = 0
    for seed in range(3000):
        r = random.Random(seed)
        n = r.randint(2, 6)
        names = [chr(97 + i) for i in range(n)]
        g = {x: [y for y in names if r.random() < 0.3] for x in names}
        truth = {x for x in names if _reach(g, x, x)}
        got = {x for c in it.find_cycles_in_invariants(g) for x in c}
        bad += got != truth
    assert bad == 0


def test_cycle_finder_enumerates_each_simple_cycle_exactly_once():
    """Regression for bug 6: full simple-cycle enumeration, canonical rotation, no duplicates."""
    g = {"a": ["b", "c"], "b": ["a"], "c": ["b"]}
    assert sorted(it.find_cycles_in_invariants(g)) == [["a", "b"], ["a", "c", "b"]]
    assert it.find_cycles_in_invariants({"x": ["x", "x"]}) == [["x"]]
    # complete digraph on 4 nodes: sum_{k=2..4} C(4,k)(k-1)! = 6 + 8 + 6 = 20 simple cycles
    k4 = {u: [v for v in "abcd" if v != u] for u in "abcd"}
    cyc = it.find_cycles_in_invariants(k4)
    assert len(cyc) == 20 == len({tuple(c) for c in cyc})
    assert all(c[0] == min(c) and len(set(c)) == len(c) for c in cyc)
    for c in cyc:  # every reported cycle is a real closed walk
        assert all(c[(i + 1) % len(c)] in k4[c[i]] for i in range(len(c)))
    inv = [it.Invariant("I0", ["I2"]), it.Invariant("I1", ["I0"]), it.Invariant("I2", ["I1", "I0"])]
    assert it.detect_circular_invariants(inv) == ["I0", "I1", "I2"]


# ====================================================================================== Taylor-Green / control law


def _tg_fields(N=16):
    L = 2 * np.pi
    g = np.arange(N) * L / N
    x, y, z = np.meshgrid(g, g, g, indexing="ij")
    u = np.stack([np.sin(x) * np.cos(y) * np.cos(z), -np.cos(x) * np.sin(y) * np.cos(z), 0 * x])
    k = np.fft.fftfreq(N, d=1.0 / N)  # integer wavenumbers (domain 2 pi)
    K = np.meshgrid(k, k, k, indexing="ij")

    def d(f, axis):
        return np.real(np.fft.ifftn(1j * K[axis] * np.fft.fftn(f)))

    return u, d


def test_taylor_green_ic_divergence_energy_enstrophy_dissipation():
    u, d = _tg_fields()
    div = d(u[0], 0) + d(u[1], 1) + d(u[2], 2)
    assert np.max(np.abs(div)) < 1e-13
    E = np.mean(0.5 * np.sum(u * u, axis=0))
    assert E == pytest.approx(1 / 8, abs=1e-14)
    om = np.stack([d(u[2], 1) - d(u[1], 2), d(u[0], 2) - d(u[2], 0), d(u[1], 0) - d(u[0], 1)])
    x = np.arange(16) * 2 * np.pi / 16
    X, Y, Z = np.meshgrid(x, x, x, indexing="ij")
    om_exact = np.stack([-np.cos(X) * np.sin(Y) * np.sin(Z), -np.sin(X) * np.cos(Y) * np.sin(Z),
                         2 * np.sin(X) * np.sin(Y) * np.cos(Z)])
    assert np.max(np.abs(om - om_exact)) < 1e-12
    Ens = np.mean(0.5 * np.sum(om * om, axis=0))
    assert Ens == pytest.approx(3 / 8, abs=1e-13)
    # 2 nu <S:S> = nu <|omega|^2>  (periodic + incompressible), nu = 1/Re
    nu = 1 / 800
    S = np.stack([np.stack([0.5 * (d(u[i], j) + d(u[j], i)) for j in range(3)]) for i in range(3)])
    diss = np.mean(2 * nu * np.sum(S * S, axis=(0, 1)))
    assert diss == pytest.approx(2 * nu * Ens, rel=1e-12) == pytest.approx(0.75 * nu, rel=1e-12)
    # sketch parameters vs analytics: initial enstrophy margin h = limit - Ens = 0.5 - 0.375 > 0 (CBF inactive at t=0)
    assert 0.5 - Ens == pytest.approx(0.125)
    assert cl.cbf_correction(0.5 - Ens, om, 0.05, 0.1).max() == 0.0


def test_cbf_correction_closed_form_and_continuity():
    rng = np.random.default_rng(6)
    w = rng.normal(size=(3, 5))
    for h, m, g in [(-0.2, 0.1, 0.05), (0.0, 0.1, 0.05), (0.09, 0.1, 0.3), (0.1, 0.1, 0.05), (0.5, 0.1, 0.05)]:
        out = cl.cbf_correction(h, w, g, m)
        assert np.allclose(out, -g * max(0.0, m - h) * w, atol=1e-15)
    left, right = cl.cbf_correction(0.1 - 1e-9, w, 0.05, 0.1), cl.cbf_correction(0.1, w, 0.05, 0.1)
    assert np.max(np.abs(left - right)) < 1e-9 * 0.05 * np.max(np.abs(w)) * 1.0001  # continuous at margin


def test_clf_energy_terms_first_variation_and_lyapunov_rate():
    rng = np.random.default_rng(10)
    u = rng.normal(size=(3, 6, 6, 6))
    Es = 0.4
    E = float(np.mean(0.5 * np.sum(u * u, axis=0)))
    g_, c_ = 0.5, 0.1
    V, f_base, f_clf = cl.energy_clf_terms(E, Es, u, g_, c_)
    assert V == pytest.approx((E - Es) ** 2)
    assert np.allclose(f_base, g_ * (Es - E) * u)
    dV_du = -f_clf / c_
    assert np.allclose(dV_du, 2 * (E - Es) * u)
    # first variation: d/de V(u + e phi) at e=0 equals mean(dV_du . phi)   (mean-energy functional)
    phi = rng.normal(size=u.shape)
    eps = 1e-6

    def Vof(uu):
        return (float(np.mean(0.5 * np.sum(uu * uu, axis=0))) - Es) ** 2

    fd = (Vof(u + eps * phi) - Vof(u - eps * phi)) / (2 * eps)
    assert fd == pytest.approx(float(np.mean(np.sum(dV_du * phi, axis=0))), rel=1e-6)
    # dV/dt along u' = f_base + f_clf equals -4E (g + 2c)(E-E*)^2 <= 0
    f = f_base + f_clf
    fd_t = (Vof(u + eps * f) - Vof(u - eps * f)) / (2 * eps)
    assert fd_t == pytest.approx(-4 * E * (g_ + 2 * c_) * (E - Es) ** 2, rel=1e-6)
    assert fd_t <= 0.0
    # at the target the forcing vanishes
    u0 = u * math.sqrt(Es / E)
    _, fb0, fc0 = cl.energy_clf_terms(float(np.mean(0.5 * np.sum(u0 * u0, axis=0))), Es, u0, g_, c_)
    assert np.max(np.abs(fb0 + fc0)) < 1e-12


def test_adaptive_re_geometric_growth_and_bounds():
    Re = 800.0
    for _ in range(10):
        Re = cl.adaptive_re_update(Ens=0.1, Reynolds=Re, Re_target_enstrophy=0.6, Re_min=400, Re_max=2000)
    assert Re == pytest.approx(800 * 1.01 ** 10)
    Re = 800.0
    for _ in range(10):
        Re = cl.adaptive_re_update(Ens=0.9, Reynolds=Re, Re_target_enstrophy=0.6, Re_min=400, Re_max=2000)
    assert Re == pytest.approx(800 * 0.99 ** 10)
    assert cl.adaptive_re_update(0.6, 800.0, 0.6, 400, 2000) == 800.0  # at target: unchanged
    rng = np.random.default_rng(13)
    Re = 1000.0
    for _ in range(5000):  # clamped map never leaves [min, max] for any enstrophy sequence
        Re = cl.adaptive_re_update_clamped(float(rng.uniform(0, 1.2)), Re, 0.6, 400, 2000)
        assert 400 <= Re <= 2000
    assert cl.adaptive_re_update_clamped(0.0, 1995.0, 0.6, 400, 2000) == 2000
    assert cl.adaptive_re_update_clamped(9.0, 401.0, 0.6, 400, 2000) == 400
    assert cl.adaptive_re_update_unclamped(0.0, 1999.0, 0.6, 400, 2000) == pytest.approx(1999 * 1.01)  # overshoot is by design


# ====================================================================================== HavenSovereignEngine


def test_haven_sovereign_density_phi_fingerprint_distance():
    eng = HavenSovereignEngine(["NA"], {})
    gains = [0.12, 0.15, 0.18]
    T, Lc = 0.5, 2.85e-21
    assert eng.compute_intelligence_density(gains) == pytest.approx(sum(gains) / (T * Lc), rel=1e-12)
    assert eng.compute_intelligence_density([]) == 0.0
    for z, g in [(1.0, 0.5), (0.0, 2.0), (-3.0, 0.25), (1 + 1j, 4.0)]:
        want = 1 / (1 + abs(z)) + 1 / g
        assert eng.symmetry_convergence_phi(z, g) == pytest.approx(want, rel=1e-8)
    zh = eng.spectral_fingerprint(lambda s: -2 * s, lambda s: s ** 2, lambda s: 1.0, sigma=3.0, alpha_vals=(0.5, 0.25, 2.0))
    assert zh == pytest.approx(0.5 * 6 + 0.25 * 9 + 2.0)  # note |zeta_X| is used, zeta_R / zeta_C are not
    # L2 distance between sigma and 0 on [0,3]: sqrt(int s^2) = 3 ; between sin and cos on [0, 2 pi]: sqrt(2 pi)
    assert eng.manifold_distance(lambda s: s, lambda s: 0.0, 0.0, 3.0) == pytest.approx(3.0)
    assert eng.manifold_distance(np.sin, np.cos, 0.0, 2 * np.pi) == pytest.approx(math.sqrt(2 * math.pi), rel=1e-9)
    f, g2 = (lambda s: math.exp(-s)), (lambda s: 0.5 * s)
    want = math.sqrt(quad(lambda s: (f(s) - g2(s)) ** 2, 0, 2)[0])
    assert eng.manifold_distance(f, g2, 0, 2) == pytest.approx(want, rel=1e-12)
    assert eng.manifold_distance(f, f, 0, 2) == 0.0  # d(x, x) = 0
    assert eng.manifold_distance(f, g2, 0, 2) == pytest.approx(eng.manifold_distance(g2, f, 0, 2))  # symmetry
    assert eng.evaluate_global_governance({"any": 1}) == "approved"  # invariant checks are stubs returning True


# ====================================================================================== unified control sketch


def test_unified_control_sketch_closed_form(capsys):
    simulate_unified_control()
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("Step")]
    assert len(lines) == 10
    dt, q = 0.01, 1 - 0.1 * 0.01
    for k, ln in enumerate(lines):
        # after k+1 Euler steps with u = 1 (the unconstrained QP optimum is u_nom: min (u-1)^2)
        v = [10 * (1 - q ** j) for j in range(k + 2)]
        p = dt * sum(v[:-1])
        got_p = float(ln.split("Position=")[1].split(",")[0])
        got_u = float(ln.split("u)=")[1])
        assert abs(got_p - p) <= 5.001e-4  # printed with 3 decimals
        assert got_u == pytest.approx(1.0, abs=1e-2)  # CVXOPT default tolerance


# ====================================================================================== HAIS runtime v0.2


def test_safety_config_bounds_are_inclusive_and_nan_fails_closed():
    c = rt.SafetyConfig()
    ok = dict(tau=0.5, fatigue=0.3, risk_score=0.1, improvement=0.01)
    assert c.within_bounds(**ok)
    edges = [("tau", 0.3), ("tau", 0.9), ("fatigue", 0.0), ("fatigue", 0.7), ("risk_score", 0.0), ("risk_score", 0.2),
             ("improvement", 0.001), ("improvement", 0.05)]
    for k, v in edges:
        assert c.within_bounds(**{**ok, k: v}), (k, v)  # closed intervals
    outs = [("tau", 0.2999), ("tau", 0.9001), ("fatigue", -0.001), ("fatigue", 0.7001), ("risk_score", -0.001),
            ("risk_score", 0.2001), ("improvement", 0.0009), ("improvement", 0.0501)]
    for k, v in outs:
        assert not c.within_bounds(**{**ok, k: v}), (k, v)
    for k in ok:
        assert not c.within_bounds(**{**ok, k: float("nan")})  # NaN compares False -> rejected
        assert not c.within_bounds(**{**ok, k: float("inf")})


def test_metrics_monitor_summary_matches_numpy():
    rng = np.random.default_rng(99)
    m = rt.MetricsMonitor()
    assert m.summary()["pass_rate"] == 1.0 and m.summary()["n_steps"] == 0
    recs = [{"tau": float(rng.uniform(.3, .9)), "fatigue": float(rng.uniform(0, .7)), "risk_score": float(rng.uniform(0, .2)),
             "passed": bool(rng.integers(0, 2)), "false_assoc": float(rng.uniform(0, 1))} for _ in range(101)]
    for r in recs:
        m.record(r)
    s = m.summary()
    assert s["tau_mean"] == pytest.approx(np.mean([r["tau"] for r in recs]))
    assert s["tau_min"] == min(r["tau"] for r in recs) and s["tau_max"] == max(r["tau"] for r in recs)
    assert s["fatigue_max"] == max(r["fatigue"] for r in recs)
    assert s["risk_mean"] == pytest.approx(sum(r["risk_score"] for r in recs) / 101)
    assert s["pass_rate"] == pytest.approx(sum(r["passed"] for r in recs) / 101)
    fa = sorted(r["false_assoc"] for r in recs)
    assert s["false_assoc_p90"] == pytest.approx(fa[90])  # N=101: index 0.9*(N-1) = 90 exactly
    m.reset()
    assert m.summary()["n_steps"] == 0


def test_audit_trail_hash_is_truncated_sha256_of_sorted_event_json():
    a = rt.AuditTrail()
    ev = {"b": 2, "a": [1, 2], "c": {"y": 1, "x": 0}}
    a.append(ev)
    a.append({"c": {"x": 0, "y": 1}, "a": [1, 2], "b": 2})  # same event, different key order
    h0, h1 = a.entries[0]["hash"], a.entries[1]["hash"]
    assert h0 == h1 == hashlib.sha256(json.dumps(ev, sort_keys=True).encode()).hexdigest()[:16]
    assert len(h0) == 16 and [e["index"] for e in a.entries] == [0, 1]
    a.append({**ev, "b": 3})
    assert a.entries[2]["hash"] != h0  # event change -> different hash
    # tamper detection per entry: recompute and compare
    a.entries[0]["event"]["b"] = 99
    recomputed = hashlib.sha256(json.dumps(a.entries[0]["event"], sort_keys=True, default=str).encode()).hexdigest()[:16]
    assert recomputed != a.entries[0]["hash"]


def test_governance_kernel_pass_logic_and_halt():
    def step(**over):
        base = {"tau": 0.5, "fatigue": 0.1, "risk_score": 0.05, "improvement": 0.01, "action": "ok"}
        return {**base, **over}

    for over, passed in [({}, True), ({"tau": 0.95}, False), ({"action": "harm_human"}, False),
                         ({"risk_score": float("nan")}, False), ({"improvement": 0.0}, False)]:
        k = rt.GovernanceKernel(lambda o=over: step(**o))
        assert k.step()["passed"] is passed
    k = rt.GovernanceKernel(step, supervisor=lambda r: {"reject": r["risk_score"] > 0.04})
    assert k.step()["passed"] is False  # supervisor can only tighten
    k = rt.GovernanceKernel(lambda: step(tau=0.95), supervisor=lambda r: {"reject": False})
    assert k.step()["passed"] is False  # ...and never loosens a failed bound
    assert k.override.halt("SUPERVISOR") is False and k.override.halt("AGENT") is False  # only HUMAN halts
    assert k.halt()["executed"] is True
    with pytest.raises(RuntimeError):
        k.step()
    k.reset()
    assert k.step()["passed"] is False
    assert k.monitor.summary()["n_steps"] == 1
