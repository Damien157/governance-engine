# Mathematics test suite

Numerical verification of the equations the repository actually implements. Every file states the equation it
checks in its module docstring. Expected values are derived analytically or by an **independent computation inside
the test** (Python `decimal` at 50 digits, `scipy.linalg.expm` / `solve_ivp` / `special.zeta`, closed-form QP oracle,
active-set enumeration, brute-force simulation / enumeration, `numpy.polyfit`, `hashlib`, integer Machin-formula
for pi) — never copied from the implementation's output. Randomised checks use fixed seeds. Stochastic checks
use a seeded chi-square or a 5-sigma envelope.

Run only the math tests:

    python -m pytest tests/test_math_*.py -q          # includes the older tests/test_math_cores.py

or the whole suite through the runner (the new files are listed in `PYTEST_FILES`):

    python scripts/run_all_works_tests.py

Genuine defects are encoded as `xfail(strict=True)` with the reason and a minimal repro in the marker. If someone
fixes one, strict-xfail turns the test red ("XPASS") so the marker gets removed. All seven defects found so far
(bugs 1-7) are fixed; their tests are now ordinary passing regression tests and no strict xfails remain.

## Equation → test file

### Control: CLF + CBF + QP on the damped 1-D mass — `test_math_control_clf_cbf.py`, `test_math_control_qp.py`
| Equation / property | File |
|---|---|
| A=[[0,1],[0,-0.1]], B=[0,1]ᵀ, spectrum {0,−0.1}, ẋ=Ax+Bu, f/g split | clf_cbf |
| Euler step x⁺=x+dt(Ax+Bu) vs exact ZOH (Van Loan block-expm): error O(dt²) (ratio ≈ 4 on halving dt) | clf_cbf |
| V=eᵀPe, P=[[2,.5],[.5,1]] SPD (closed-form eigenvalues), ∇V vs finite differences, LfV, LgV | clf_cbf |
| V̇ via the true flow; CLF row a·u−b = V̇+kV exactly; exact Euler identity V(x+dtẋ)=V+dt∇V·ẋ+dt²ẋᵀPẋ; discrete exponential envelope | clf_cbf |
| CBF h=p_max−p, Lf h=−v, Lg h=0; classic row; HOCBF row u ≤ 0.1v−kv+α₁(−v+kh) hand-derived; ψ̇₁+α₁ψ₁=b−u; forward invariance (solve_ivp) | clf_cbf |
| Relaxed QP min (u−u_nom)²+wδ² closed form: m=u_nom if Lg·u_nom≤c else (u_nom+w·Lg·c)/(1+w·Lg²); u*=clip(m,u_min,min(u_max,b_cbf)); optimality certificate against random feasible points | qp |
| Nominal passthrough, box clamp, single-constraint min(u_nom,b); slack activation (hand δ=0.495 at x=(0.95,0)); classic-CBF branch; fallbacks (cbf-only, last-resort clamp) | qp |
| Closed-loop invariance h≥0 under bang-bang / constant / adversarial u_nom; per-step CLF inequality V₊≤V+dt(−kV+δ)+dt²ẋᵀPẋ; convergence; target beyond the barrier; zero-input equilibrium p*=p_c+∛(u_nom/2w) or p_max | qp |

### Solvers (sketch-only) — `test_math_osqp_sketches.py`
Hard-CLF engine = Euclidean projection onto a polytope (1-D clip, 2-D active-set enumeration oracle); soft-CLF engine closed
form with λ=1e3 slack; slack feasibility; escalation/reset; non-finite rejection; PositionCBF rows, QuadraticCLF
(LfV=pv, LgV=v), double-integrator discretisation, PD formula, random-adversary forward invariance, and the
broken-surrogate counterexample. OSQP tolerance ≈ 5e-3.

### HAIS SovereignKernel — `test_math_hais_kernel.py`
S=σ(4(r−0.5)), τ=1+0.5e^S, r′=Sτ, cap=e^{−2.2r′}, I=4S(1−S)(1+r) against a 50-digit `decimal` reference for all three
kernel copies (bit-for-bit agreement); cap(0)=0.66367, cap(1)=0.013905; symmetry S(r)+S(1−r)=1; monotonicity; bounds;
the BLOCK threshold cap<0.25 ⇔ r<r\*=0.3627850289707891 (root found by Decimal bisection, 4r\*=1.45114011588);
effective-score pipeline formula; Δr′ telescoping; regression for the old m=0 bug (max cap 0.134).

### Haven2 — `test_math_haven2.py`
Energy recurrence closed form, superposition, equilibrium and decay ρᵗ, running-mean equilibrium; t_reset=⌈ln(ε/gap)/lnρ⌉
vs brute-force simulation; discrete latch invariants (2000 random steps, adversarial flapping, idempotence); exact
shock-trace residual P_t=ρ^{t+1}(E₀−E\*)+ρ^{t−t_shock}(c_shock−c̄); EVTE tables/formulas; zeta Dirichlet sums vs
`scipy.special.zeta` (Hurwitz tail, Basel); s_avg, optimal_rho; minimal spectral-audit snapshot.

### Imprint 3DM — `test_math_imprint_3dm.py`
|S|=C(|T|,k); pairwise-validity oracle; hand-solved greedy prior (2/3); exact permutation enumeration; Born rule
p=w/Σw and amplitudes²; probabilities sum to 1; seeded chi-square + 5σ envelopes (n=30000) for empirical frequencies;
peel score; geometric-mean baseline; fitness; DNA codec lengths/motifs/round-trip; mutation Hamming distance.

### Zero-knowledge primitives — `test_math_zk_pedersen.py`
RFC 3526 group-14 prime reproduced from π (integer Machin) ; P and Q=(P−1)/2 prime (Miller–Rabin); G,H in the order-Q
subgroup; Pedersen commit Gᵛ Hʳ homomorphism and exponent reduction mod Q; CDS94 OR-proof sigma equations
(e₀+e₁=e, Hᶻ=a·Cᵉ per branch), completeness for both bits, rejection of non-bits/wrong commitments, tamper
rejection of every proof field; 18-bit range proof recomposition ∏Cᵢ^{2ⁱ}=C, rejection of wrong targets/out-of-range
values, boundary values 0 and 2¹⁸−1; risk-threshold algebra d=(T−1)−r, C_d=G^{T−1}C_r⁻¹=commit(d,−s_r).

### Signals, flow control, rate limiting — `test_math_signals_flow.py` (both copies: root and `hais/`)
Risk R=a(1−V)+bA+cF+dVAF (vertex max 0.9, partials, strict level thresholds); integrity pressure
(b/n)e^{−λt}; throughput efficiency/available capacity; rolling z-score anomaly signal (sample std; flat-baseline
branches); OLS trend slope/intercept/R² vs `numpy.polyfit`, shift/scale invariances; token bucket vs an independent
reference model (fake clock) and throughput ≤ C+ρT; sliding-window limiter (inclusive boundary) vs brute force.

### Audit chain, hashing, projection, QUANTUM line — `test_math_audit_hashchain.py`
SHA-256 hash chain recomputed independently, genesis link, signature check; tamper matrix with hand-derived broken-sets
(payload edit → 1 row, entry_hash rewrite → 2 rows, re-hash without re-sign → row + successor, middle/first row
deletion, timestamp swap); integrity_pressure=|broken|/N; SHA-256 avalanche (Hamming ≈128±8); intent hash / cache key /
approval-voucher binding; `audit_projection` hand values + bounds/monotonicity on 500 random envelopes; QUANTUM line
width 150, round-trip error ≤5e-9, reconstruction identities, kernel agreement.

### Bio semantic channel — `test_math_bio_scoring.py`
Decision map (BLOCK if p_max≥0.70 or enhancement/synthesis ≥0.50; REVIEW if p_max≥0.15 or uncertain≥0.50) against an
oracle on an 8⁴ boundary grid + 20k random vectors; monotonicity; `tighten` is a max-semilattice; overlay is
tighten-only; score snapshot reads each label exactly once (TOCTOU) and rejects non-finite/out-of-range/bool/str;
chunking arithmetic and overlap property; chunk max-aggregation; stub-scorer action table.

### Sketch-only modules — `test_math_sketches.py`
3-SAT DPLL vs brute force (400 random formulas); topology diagnostics (β₀, β₁=E−V+β₀, degree proxy and Gershgorin bound,
self-loop ratio, barrier predicate); Taylor–Green initial-condition analytics (div u=0, ⟨E⟩=1/8, ⟨enstrophy⟩=3/8,
dissipation = ν⟨|ω|²⟩, spectral); `control_law` CBF/CLF/adaptive-Re maths (V̇=−4E(g+2c)(E−E\*)²); HavenSovereignEngine
(density, Φ(z), fingerprint, L² distance); unified control sketch closed-form trajectory; runtime v0.2 SafetyConfig,
MetricsMonitor, AuditTrail, GovernanceKernel pass logic.

## Genuine defects found (all fixed: bugs 1-2 in PR #29, bugs 3-7 on `fix/math-bugs-3-6`)
| # | Where | Test | Defect |
|---|---|---|---|
| 1 | CLFCBFQPController.solve | `test_infeasible_hocbf_returns_max_braking_instead_of_raising` | **FIXED.** Infeasible HOCBF row (`x=[0.5, 20]`): the second `solvers.qp` in `_solve_cbf_only` had no try/except, so `ValueError('domain error')` escaped and the last-resort clamp was unreachable. Fix: infeasibility is detected up front (`_barrier_infeasible`), the second QP is wrapped, and every non-optimal path goes through `_fail_closed_command` (max brake `u_min` when the row is infeasible, status `infeasible_barrier`). |
| 2 | same | `test_solver_exception_must_not_return_unfiltered_nominal` | **FIXED.** `x=[0.9, 15]`: the first solve raised and the generic `except` returned the **raw u_nom** (+7, towards the barrier) with `error:…` (fail-open). Fix: the `except` now returns `clip(min(u_nom, b/a), u_min, u_max)` (the HOCBF-admissible input closest to u_nom, or max brake if none exists) with status `error:<exc>`, `feasible=False`, `fail_closed=True`. Non-finite solver output / NaN `u_nom` are handled the same way. |
| 3 | Haven2 `t_reset` | `test_t_reset_boundary_residual_equals_epsilon_is_still_closed` | **FIXED.** ⌈ln(ε/gap)/lnρ⌉ gave the smallest t with ρᵗ·gap ≤ ε, but the latch needs strict |P|<ε (`t_reset(0.25,1.0,0.0,0.5)` was 2, residual exactly 0.25). Fix: after the ceil, step while `rho**t * gap >= eps` (only moves exact-boundary cases; now 3). Regression: `test_t_reset_agrees_with_latch_on_exact_power_of_two_boundaries`. |
| 4 | Haven2 `t_reset` | `test_t_reset_zero_means_latch_already_open_at_gap_equal_epsilon` | **FIXED.** `t_reset(0.5,1.0,0.5,0.5)` was 0 while |P₀|=ε and the latch is closed. Fix: the "already eligible" shortcut is now `gap < eps` (strict); that case returns 1. The test was rewritten to assert the corrected contract (closed at t=0, `t_reset==1`, open at t=1); the latch itself stays strict. |
| 5 | Haven2 `metric_energy_sweet_spot` | `test_energy_sweet_spot_is_non_increasing_in_abs_p_hat` | **FIXED.** Score was discontinuous (`metric(0.0499,0.05)=0.505 < metric(0.05,0.05)=1.0`). Fix: the outside branch is `0.5·exp(-(|P|-ε)/ε)`, continuing from 0.5; in-band formula unchanged. Outside values are halved (EVTE C_t weight 0.1 ⇒ C_t drops by ≤0.05 when |P|≥ε). Regression: `test_energy_sweet_spot_is_continuous_at_band_edge_and_unchanged_inside`. |
| 6 | `diagnostics.find_cycles_in_invariants` | `test_cycle_finder_reports_every_node_that_lies_on_a_cycle_minimal_repro` (+ randomised twin) | **FIXED.** The single DFS never revisited finished nodes, missing on-cycle nodes (a→b, a→c, b→a, c→b returned {a,b}; 75/3000 random digraphs). Fix: full simple-cycle enumeration from each cycle's smallest node, searching only larger nodes; each cycle reported once, rotated to start at its minimum. Worst case exponential (inherent to listing all cycles). Regression: `test_cycle_finder_enumerates_each_simple_cycle_exactly_once`. |
| 7 | `bio_semantic.semantic_action` | `test_semantic_action_non_finite_p_must_fail_closed` | **FIXED.** NaN `p_harmful` fell through every `>=` → `("pass", None)` (fail-open). Fix: non-finite `p_harmful` or `confidence` → `("BLOCK", "GOV_BIO_SEMANTIC_BLOCK")` (an explicit `error` still maps to REVIEW first, as before). Regression: `test_semantic_action_non_finite_scores_block_for_every_label`. |

### QP fail-closed fix (bugs 1-2): what is and is not guaranteed
Identity (exact for the Euler plant, `psi1 = -v + k h`, `ub = 0.1v - kv + alpha1 psi1`):
`psi1(x+) = (1 - alpha1 dt) psi1(x) + dt (ub(x) - u)`. A command `u <= ub` keeps `{psi1 >= 0}` and so `{h >= 0}`
invariant (`dt <= 1/k`); the fallback always returns `u <= max(ub, u_min)`. Even with the solver dead at every step the
clamp alone keeps the safe set invariant from `psi1(x0) >= 0` (tested against +50 / bang-bang / random nominals).
Tests: `test_returned_command_never_moves_state_toward_unsafe_set_grid_and_random[natural|solver_raises|solver_nonoptimal|solver_nan]`,
`test_closed_loop_*dead_solver*`, `test_closed_loop_from_infeasible_starts_is_best_effort_brake_until_recovered`.
Residual risk (unchanged by the fix, documented rather than hidden):
* If `ub < u_min` **no safe command exists**; the controller applies the maximal brake `u_min`, which gives the pointwise
  best position trajectory of any admissible input, but an unrecoverable start (e.g. `[0.5, 20]`) still leaves `h >= 0`.
* For starts with `psi1 < 0` whose row is feasible, the HOCBF only restores `psi1` at rate `alpha1`; `h` can dip below 0
  although max braking would have avoided it (e.g. `[-0.121, 4.179]`, dt=0.02: controller min h = -0.117, brake min h = +0.90).
  This is a property of the HOCBF design on the "optimal" path, not of the error paths, and is not changed here.

Also noted but pinned rather than xfailed: the audit hash chain cannot detect truncation of the **tail**
(`test_tail_truncation_is_NOT_detectable_known_limitation`); the `quantum_line` module docstring says "four integer
digits" but `{:+015.8f}` yields five (the width 150 itself is correct).

## Deliberately not covered
* **Governance outer-loop wave dynamics and Meta-Supervisor C_L/C_E** — not present in this repository
  (`supervisor.py` explicitly has no wave ODE); nothing to test.
* **Dedalus Taylor–Green solvers** (`fluids/hais_taylor_green*.py`, `tg_vortex_control_fixed.py`, `smoke_taylor_green.py`):
  need Dedalus at import and are PDE time-steppers; only their analytic initial condition and the pure-numpy
  `control_law` are covered. The inline controller in those scripts (CBF engaged only when h<0; unclamped adaptive Re)
  differs from `control_law` and is not tested.
* **CDCL C solver** and CNF fixtures: exercised by the existing `tests/test_math_cores.py` only (SAT/UNSAT smoke).
* **Statistical validity** of the heuristic signals (risk/anomaly/trend): the arithmetic is verified, not whether the
  heuristics are good predictors.
* **Cryptographic security** of the ZK scheme / RSA-PSS (soundness, zero-knowledge): only algebraic completeness,
  binding checks on tampering and group structure are tested. Cryptographic hardness is not provable by tests.
* **Concurrency of the audit chain**: covered by the existing `tests/test_audit_soak.py`.
* **Live bio judge LLM scoring**: `classify_text_llm` is an unimplemented hook; only the deterministic kernel and stub are tested.
* `hais/hais_tests/` and `haven2/tests`, `imprint/tests` are existing suites and are untouched.
