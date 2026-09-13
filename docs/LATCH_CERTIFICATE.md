# Latch certificate vs search — local opening on P vs NP language

> **Not a Clay P vs NP proof.** This formalizes an *asymmetry already present*
> in Haven2 so we can talk about check-vs-find without collapsing into myth.
> See also [NVNP_CONJECTURES.md](NVNP_CONJECTURES.md), [SIDED_ASIDE.md](SIDED_ASIDE.md).

## Motivation (plain)

- **0 / 1** — discrete realm and latch open bit (`Realm`, `history_open`).
- **In between** — continuous residual \(\hat{p}_t = E_t - E^\star\) and drive
  \(c_t\), volatility \(v_t\) (EnergyState + TargetRealm).
- **Transistor → transistor** — `TransistorLatch`: switch allowed only when
  \(|\hat{p}_t| < \varepsilon_{\mathrm{switch}}\); otherwise stay put
  (intentional hysteresis).
- **“Verify all answers at once”** — NP-shaped *wish*; here we only claim that
  a *proposed* forcing sequence is cheap to check by replay. Finding one is a
  different problem. Quantum / ζ remain **audit**, not an NP oracle
  ([QUANTUM_LINE.md](QUANTUM_LINE.md)).

## Objects (implemented)

Fix engine parameters \(\rho, \varepsilon_{\mathrm{switch}}, e_0, E^\star\)
(or running-mean equilibrium) and the default TargetRealm table.

A **trace** of length \(T\) is a pair of sequences

$$
\mathbf{c} = (c_0,\ldots,c_{T-1}),\qquad
\mathbf{v} = (v_0,\ldots,v_{T-1}).
$$

Running `Haven2Engine.step` yields residual history \(\hat{p}\), open bits,
realms, and `switch_times` \(\subseteq \{0,\ldots,T-1\}\) (0-based; see PR #3).

## VERIFY (easy) — switch certificate

A **switch certificate** for a claimed switch set \(S\) is the triple
\((\mathbf{c}, \mathbf{v}, S)\).

**Check** (deterministic, \(O(T)\)):

1. Replay the engine from a declared initial realm on \((\mathbf{c},\mathbf{v})\).
2. Accept iff the produced `switch_times` equal \(S\) (and optionally final
   realm / open bit match claimed values).

**Honesty:** \(O(T)\) replay-check is **not** a Haven2-specific complexity result. For *any* deterministic system, re-running a claimed input trace and comparing outputs costs the same order as generating the trace — that is what determinism means. VERIFY is a **framing move**: it names the easy half so the interesting half (SEARCH) is well-posed. Same *shape* as NP verification (short witness + poly-time checker), not a complexity theorem about Haven2.

Regression: `haven2/tests/test_latch_certificate.py` (latch replay integrity, not a complexity finding).

## SEARCH (harder, open) — forcing drive

**Search problem (informal).** Given target realm \(g^\star\) (e.g. DEFENSIVE),
horizon bound \(T\), and initial realm \(g_0\), find \((\mathbf{c},\mathbf{v})\)
of length \(\le T\) such that after replay the engine’s realm is \(g^\star\)
(or such that \(S\) contains a required switch).

This is **not** claimed NP-complete. Reasons to stay humble:

- Continuous inputs (unless discretized / rational-encoded).
- Tiny discrete output alphabet (3 realms) — hardness needs a careful encoding
  of *instances* into \((c,v)\) constraints, which we do not have yet.
- Natural barriers for “complexity from continuous dynamics” still apply if one
  tries to lift this to unrestricted NP.

What we *do* claim operationally: **asymmetric cost** on this toy —
verification is replay; synthesis is a control / inverse problem on the energy
+ latch map.

## Local conjecture [Latch Asymmetry]

> Under the implemented Haven2 dynamics, deciding whether a proposed
> \((\mathbf{c},\mathbf{v},S)\) is valid is \(O(T)\) by replay, while producing
> a short forcing drive for an arbitrary target realm (from fixed params) is
> a search/control task with no known poly-time reduction to a single closed-form
> “verify-all-at-once” step inside this stack.

Falsifiable pieces:

- Exhibit a poly-time *finder* for forcing drives under a fixed discretization
  of \((c,v)\) → conjecture weakens for that fragment.
- (VERIFY superlinearity would be surprising for this deterministic replay
  checker — not the interesting falsification target.)

## What this is for

| Use | Yes / no |
|-----|----------|
| Language bridge: continuous residual ↔ discrete gate | **Yes** |
| Regression + honesty about check vs find | **Yes** |
| Buyer product / live `govern()` change | **No** |
| Clay submission / Collapse Universality | **No** — [SIDED_ASIDE.md](SIDED_ASIDE.md) |

## Next experiments (only if useful)

1. Discretize \((c,v)\) to a finite alphabet and study forcing DEFENSIVE from
   CALM. **Caveat:** measuring wall-clock or node-count growth under *naive
   brute force* over the product alphabet is exponential **by construction of
   the search method**, not evidence that the reachability problem is
   intrinsically hard. A meaningful experiment needs a competent search
   (heuristic-guided, branch-and-bound with pruning, or an ILP/SAT encoding
   handed to a real solver) and even then yields **suggestive empirics**, not
   a proof.
2. Keep VERIFY as a permanent *integrity* check for latch/spectrum tests
   (already the spirit of `test_switch_time_p_hat_align.py`) — not as a
   complexity claim.
3. Do **not** widen `solver_ok` or product SPECTRUM_KEYS for this — research
   lane only. Prefer the customer hosted-check slice for product value.
