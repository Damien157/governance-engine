# N V NP conjectures — Collapse, Irreversibility, Energy Discipline

> **This is NOT a Clay Millennium P vs NP proof.**  
> Nothing here claims $\mathrm{P}=\mathrm{NP}$ or $\mathrm{P}\neq\mathrm{NP}$.
> Damien’s “Basic Sentence Structure: **N V NP**” notes are operational
> conjectures about *governed search / collapse processes* in this stack.
> CDCL, imprint 3DM, and HAIS remain classical procedures with exponential
> worst cases where applicable.

Spine: [ALGORITHM_GOVERNANCE_MAIN.md](ALGORITHM_GOVERNANCE_MAIN.md).

---

## Notation

Let $\mathcal{C}_t$ be the live candidate set at step $t$, $\Phi_t$ a
non-negative potential (residual / free energy / soft score mass), and
$\mathcal{E}_t$ an eliminated (pruned) set. A *membrane certificate* is any
checkable witness that a candidate may never re-enter $\mathcal{C}$.

---

## Conjecture [Collapse]

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

### Stack mapping

| Piece | Role | Status |
|-------|------|--------|
| Haven2 energy + EVTE / zeta scores | Residual accounting | **Implemented** |
| HAIS `SovereignKernel` (m=0.5) | Capability cap from telemetry | **Implemented** |
| `GovernedAlgorithm` energy_cost field | Cost estimate on scan | **Implemented** (declarative) |
| Unified joule / carbon meter | | **Not implemented** — do not claim |
| Single $\Phi$ tying energy + prune mass + NN forward cost | | **Hypothesis** |

---

## Relation to Algorithm Governance Main

| Axis | How conjectures touch it |
|------|--------------------------|
| **Purpose** | Collapse/search must state why it runs before ALLOW |
| **Cost** | Energy Discipline ↔ `energy_cost` / speedup claims |
| **Risk** | Cap throttle, instability $I$, latch closed |
| **Authority** | Only token/role may revoke membrane certificates |
| **Audit** | QUANTUM line + `entry_id` witness the step |

See also: [QUANTUM_LINE.md](QUANTUM_LINE.md), [GOVERNED_CONTROLLER_NN.md](GOVERNED_CONTROLLER_NN.md).

---

## What we are *not* claiming

- No resolution of P vs NP.
- Imprint “superposition” is a **weighted classical draw**.
- Haven2 “transistor” is a **discrete latch** on energy residual.
- QUANTUM line is a **fused audit encoding**, not qubits.
