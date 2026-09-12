# N V NP conjectures — Collapse, Polynomial-Space, Irreversibility, Energy Discipline

> **This is NOT a Clay Millennium P vs NP proof.**  
> Nothing here claims $\mathrm{P}=\mathrm{NP}$ or $\mathrm{P}\neq\mathrm{NP}$.
> Damien’s “Basic Sentence Structure: **N V NP**” notes are operational
> conjectures about *governed search / collapse processes* in this stack.
> CDCL, imprint 3DM, and HAIS remain classical procedures with exponential
> worst cases where applicable.

Spine: [ALGORITHM_GOVERNANCE_MAIN.md](ALGORITHM_GOVERNANCE_MAIN.md).

Local latch check-vs-find opening (not Clay): [LATCH_CERTIFICATE.md](LATCH_CERTIFICATE.md).

---

## Notation

Let $\mathcal{C}_t$ be the live candidate set at step $t$, $\Phi_t$ a
non-negative potential (residual / free energy / soft score mass), and
$\mathcal{E}_t$ an eliminated (pruned) set. A *membrane certificate* is any
checkable witness that a candidate may never re-enter $\mathcal{C}$.

---

## Conjecture [Collapse]

> **Alias:** Copilot / slide “**Monotone Contraction**” is the same family as
> this section — $\Phi$ strictly decreasing along accepted steps. Prefer the
> name **Collapse** in this repo; treat Monotone Contraction as a synonym, not
> a second conjecture.

**Statement.** Under a governed collapse operator $V$ (the “verb”), the
potential $\Phi$ is **strictly decreasing** along accepted steps until a
stabilization predicate $\mathrm{Stab}$ holds:

$$
t \notin \mathrm{Stab}
\;\wedge\;
\text{step accepted}
\quad\Longrightarrow\quad
\Phi_{t+1} < \Phi_t - \varepsilon_t
\quad\text{for some}\quad \varepsilon_t > 0.
$$

Stabilization means a fixed point of the discrete dynamics (no further
productive prune / no open latch transition / solver halt), not a complexity
class collapse.

### Proof sketch (notes → formal outline)

1. **Decay.** Homogeneous residual decay (Haven2-style)
   $|E_t - E^\star| \approx \rho^t |E_0 - E^\star|$ with $\rho\in(0,1)$ drives
   a Lyapunov-like gap toward equilibrium when drive $c_t$ sits at mean.
2. **Vacuum pressure.** Soft verifiers / weighted samplers push probability
   mass off low-score matchings (“vacuum”), shrinking effective support of
   $\Phi$ measured as score mass outside $\mathcal{L}$ (valid set).
3. **Certified pruning.** Encoder thresholds (or soft-verifier cutoffs)
   remove candidates with checkable certificates; each removal drops $\Phi$
   by at least the removed mass.
4. **Halt.** When no certificate fires and residual $<\varepsilon$, declare
   $\mathrm{Stab}$.

### Stack mapping

| Piece | Role | Status |
|-------|------|--------|
| Haven2 `EnergyState` / `t_reset` | Discrete energy decay toward $E^\star$ | **Implemented** behavior |
| Haven2 transistor latch | Realm flip on residual magnitude | **Implemented** |
| Imprint `SuperpositionSampler.collapse` | Weighted classical draw (not quantum) | **Implemented** sketch-on-ALLOW only for `action=3dm` |
| HAIS `capability_cap` | Throttle when risk-elevated | **Implemented** |
| SoftVerifier thresholds as $\Phi$ certificates | | **Hypothesis** / partial (training-time soft scores) |
| Strict global $\Phi$ with $\varepsilon_t$ floor across *all* gates | | **Hypothesis** — not a single shared potential today |

---

## Conjecture [Polynomial-Space Collapse]

**Statement.** For an NP instance $I$ **with structural restriction $R$**
(bounded treewidth, bags / separators / neighborhoods, membrane quotas), the
collapse engine maintains a live-label / survivor set whose size stays
polynomial in the instance size at every stage:

$$
|L(I)| \le \mathrm{poly}(n)
\quad\text{at every governed stage under } R.
$$

This is **FPT-style / restricted search hygiene**, not a claim about unrestricted NP.

### Proof sketch

1. **Route into structural regions.** Decompose $I$ under $R$ (treewidth bags,
   separators, motif neighborhoods) so search is confined to locally bounded
   pieces rather than the full exponential candidate cloud.
2. **Membrane quotas.** Cap survivors per region / membrane; excess is
   eliminated under checkable certificates (see Irreversibility).
3. **Neural decay.** Soft / neural scores down-weight weak candidates inside
   each region; scores alone never certify YES.
4. **Certified pruning + no re-entry.** Hard membrane witnesses remove
   candidates from $L(I)$; Irreversibility keeps them out unless $\pi$ is
   explicitly revoked under Purpose→Cost→Risk→Authority→Audit.

### Stack mapping

| Piece | Role | Status |
|-------|------|--------|
| Imprint region / motif filters (DNA clash, local enumerator bounds) | Confine / prune structural pieces | **Partial** — instance-local filters **Implemented**; full bag/separator routing **Hypothesis** |
| Global poly($n$) survivor quotas across all membranes | Enforce $|L(I)| \le \mathrm{poly}(n)$ at every stage | **Hypothesis** — not a shared quota meter today |
| SoftVerifier / neural down-weight inside regions | Decay weak mass | **Partial** (training-time soft scores) |
| Cross-run membrane registry + no re-entry | | **Hypothesis** (see Irreversibility) |

### Hard caveat

**$R$ is doing the work.** Polynomial survivors under bounded treewidth /
quotas / neighborhoods is **not** general NP. Do **not** read this conjecture
as Collapse Universality, $\mathrm{NP}\subseteq\mathrm{P}$, or poly-time for
all NP. Premises without structural restriction remain unproven; see
[What we are not claiming](#what-we-are-not-claiming) and
[SIDED_ASIDE.md](SIDED_ASIDE.md).

---

## Conjecture [Irreversibility]

**Statement.** Once a candidate $c$ is eliminated into $\mathcal{E}_t$ under a
membrane certificate $\pi$, it stays out for all future governed steps:

$$
c\in\mathcal{E}_t
\;\wedge\;
\mathrm{ValidMembrane}(\pi, c)
\quad\Longrightarrow\quad
\forall s\ge t.\; c\notin\mathcal{C}_s.
$$

No silent resurrection of pruned branches without a new, audited authority
decision that **revokes** $\pi$ (explicit reopen), which itself must pass
Purpose→Cost→Risk→Authority→Audit.

### Proof sketch

1. **Membrane certificates.** A prune writes an append-only audit fact
   (“eliminated under rule $R$ with witness $w$”).
2. **Set discipline.** Operator $V$ only draws from $\mathcal{C}_t$; membership
   tests exclude $\mathcal{E}_t$ by construction.
3. **No shadow reopen.** Bypass paths that reintroduce $c$ without revoking
   $\pi$ are policy-illegal (same spirit as mail/calendar/social/algorithm
   gates refusing rewrite-around).

### Stack mapping

| Piece | Role | Status |
|-------|------|--------|
| Audit chain / `entry_id` | Durable decision log | **Implemented** |
| Algorithm / mail / calendar / social BLOCK | Refuse rewrite-around | **Implemented** policy posture |
| Imprint: once matching left $S$ under enumerator | Instance-local, not global membrane | **Partial** — per-instance sets |
| Cross-run “eliminated forever” registry | | **Hypothesis** — not a global prune DB today |
| Haven2 latch closed → control/3dm BLOCK | Temporary irreversibility of actuator path | **Implemented** (latch can reopen when residual allows — *not* eternal) |

Honest label: latch reopen means Haven2 irreversibility is **conditional**, not
absolute. Absolute irreversibility is the conjecture for *certified candidate
elimination*, not for energy residual.

---

## Conjecture [Energy Discipline]

**Statement.** Every accepted governed step consumes or accounts for energy
(residual drive) within a declared budget, and must not increase unconstrained
capability when residual risk is high:

$$
\mathrm{Accept}(t)
\;\Longrightarrow\;
\mathrm{Account}(c_t, E_t, \mathrm{cap}_t)
\;\wedge\;
\big(\mathrm{cap}_t < \theta \Rightarrow \mathrm{decision}_t \neq \mathrm{ALLOW}_{\mathrm{unsafe}}\big).
$$

In this repo: $\theta = 0.25$ (`GovernedStack.CAP_THROTTLE`), and
$\mathrm{cap}_t = \exp(-2.2\, r'_t)$ from HAIS.

### Proof sketch

1. **Energy recurrence.** $E_{t+1} = \rho E_t + c_t$ (Haven2) accounts drive.
2. **Capability bound.** HAIS maps risk → $S$ → $\tau$ → $r'$ → $\mathrm{cap}$.
3. **Joint gate.** Ops policy ∧ HAIS cap ∧ (for control/3dm) latch open.
4. **Algorithm Cost axis.** Declared `energy_cost` is an **estimate** on the
   scan intent — complementary bookkeeping, not a physics meter.

### Optional form (hypothesis) — contraction per joule under structure

Under the same structural restriction $R$ as Polynomial-Space Collapse, an
optional strengthening asks for a **contraction-per-joule** lower bound:

$$
\frac{\Delta |L|}{\Delta E} \ge \frac{1}{\mathrm{poly}(n)}
\quad\text{(when } \Delta E > 0 \text{ and survivors shrink).}
$$

This is a **hypothesis** only. There is **no** implemented joule / carbon meter
in this stack; `energy_cost` and Haven2 residual are accounting / residual
drive, not physics. Do not treat the inequality as measured.

### Stack mapping

| Piece | Role | Status |
|-------|------|--------|
| Haven2 energy + EVTE / zeta scores | Residual accounting | **Implemented** |
| HAIS `SovereignKernel` (m=0.5) | Capability cap from telemetry | **Implemented** |
| `GovernedAlgorithm` energy_cost field | Cost estimate on scan | **Implemented** (declarative) |
| Unified joule / carbon meter | | **Not implemented** — do not claim |
| Contraction-per-joule $\Delta|L|/\Delta E \ge 1/\mathrm{poly}(n)$ under $R$ | Optional Energy Discipline form | **Hypothesis** — not a measured meter |
| Single $\Phi$ tying energy + prune mass + NN forward cost | | **Hypothesis** |

---

## DNA → Membrane → Neural prune → Superposition → Collapse

Operational pipeline for imprint **3DM** (classical NP-style witness search).
This is the concrete “N V NP” *process* shape — **not** a complexity-class proof.

| Stage | Meaning | Imprint / stack piece | Status |
|-------|---------|----------------------|--------|
| **DNA** | Encode candidate matchings as codon strings; clash motif (`TTTT`) flags non-disjoint reuse | `imprint.dna.DNACodec`, pool \(D\) | **Implemented** (Phase 0 sim) |
| **Membrane** | Sequence-level / certificate prune: motif scan removes invalid encodings from effective support; membrane certificates must not silently resurrect | Motif filter + Irreversibility conjecture | **Partial** — instance-local filter **Implemented**; global membrane registry **Hypothesis** |
| **Neural prune** | Soft / neural scores down-weight (or threshold) weak candidates; **never** admit without hard \(V\) | `SoftVerifier`, optional logistic mix | **Implemented** soft scores; aggressive certified neural prune thresholds **Hypothesis** / training-time |
| **Superposition** | Weighted classical amplitudes over remaining \(S\) (not qubits) | `SuperpositionSampler` weights \(w=\alpha s+\beta F+\gamma g\) | **Implemented** sketch (classical) |
| **Collapse** | Draw a witness candidate; hard gate decides membership in \(L\) | `SuperpositionSampler.collapse` + hard \(V\) | **Implemented** on ALLOW-gated `action=3dm` path only as solver sketch |

Tie-in: run under Algorithm Purpose→Cost→Risk→Authority→Audit before any
deploy; latch closed → `GOV_LATCH_CLOSED` for `control`/`3dm`.

### 3DM decision pseudocode (classical witness pipeline)

```
# Instance: sets X,Y,Z and triples T; seek matching of size k (disjoint cover).
S = generate_k_subsets(T, k)          # enumerate candidate matchings
for M in S:
    # Membrane / DNA prune (optional early reject)
    if has_clash_motif(encode_DNA(M)):
        eliminate(M); continue
    # Neural / soft prune — score only; does NOT certify YES
    if neural_score(M) < tau:
        eliminate_or_downweight(M); continue
# Superposition: build weights over surviving candidates
W = superposition_weights(survivors)
# Collapse: sample a witness candidate
M* = sample_witness(W)                # classical weighted draw
# Hard matching check — NP verifier
if hard_matching_check(M*, k):        # V(M*) == 1  →  M* ∈ L
    return YES with witness M*
else:
    return NO_or_continue_search
```

Honest labels:

- `generate_k_subsets` / hard \(V\) / imprint enumerator: **Implemented** for
  small enumerable instances (Phase 0).
- `neural_score ≥ tau` as a *sound* prune certificate: **Hypothesis** unless
  paired with a checkable membrane witness (soft scores alone are not proofs).
- `sample_witness`: **Implemented** as weighted classical collapse — **not**
  quantum sampling.

Reaffirm: this remains a **classical NP-style witness pipeline** (guess +
poly-time hard check). It does **not** show \(\mathrm{P}=\mathrm{NP}\) or
\(\mathrm{P}\neq\mathrm{NP}\).

Out-of-band analytic sketch (unrelated to 3DM ALLOW): [ANALYTIC_ZERO_SUITE.md](ANALYTIC_ZERO_SUITE.md).

## Relation to Algorithm Governance Main

| Axis | How conjectures touch it |
|------|--------------------------|
| **Purpose** | Collapse/search must state why it runs before ALLOW |
| **Cost** | Energy Discipline ↔ `energy_cost` / speedup claims |
| **Risk** | Cap throttle, instability $I$, latch closed |
| **Authority** | Only token/role may revoke membrane certificates |
| **Audit** | QUANTUM line + `entry_id` witness the step |

See also: [QUANTUM_LINE.md](QUANTUM_LINE.md), [GOVERNED_CONTROLLER_NN.md](GOVERNED_CONTROLLER_NN.md),
[ATOM_SAFEGUARD_II.md](ATOM_SAFEGUARD_II.md). Out-of-band: [ANALYTIC_ZERO_SUITE.md](ANALYTIC_ZERO_SUITE.md).
Parked (not product): [SIDED_ASIDE.md](SIDED_ASIDE.md).

---

## What we are *not* claiming

- No resolution of P vs NP.
- **Collapse Universality does NOT hold here** and must **not** be used to claim
  $\mathrm{NP}\subseteq\mathrm{P}$. That leap is parked in
  [SIDED_ASIDE.md](SIDED_ASIDE.md) — not product, not a live gate premise.
- Premises **without** structural restriction $R$ are **unproven**. Restricted
  poly($n$) survivors under Polynomial-Space Collapse $\neq$ poly-time for all NP.
- Imprint “superposition” is a **weighted classical draw**.
- Haven2 “transistor” is a **discrete latch** on energy residual.
- QUANTUM line is a **fused audit encoding**, not qubits.
- DNA→…→Collapse is a **classical** imprint 3DM witness pipeline, not a P vs NP proof.
- AnalyticZeroSuite is **out-of-band** research vocabulary — not a Riemann proof.

Parked naming / marketing / unproven leaps (not on the live gate or
[PRODUCT_SLICE.md](PRODUCT_SLICE.md)): [SIDED_ASIDE.md](SIDED_ASIDE.md).
