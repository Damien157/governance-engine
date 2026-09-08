# AtomSafeguardII — governed risk / invariant / lockdown charter

Damien’s **AtomSafeguardII** sketch: score risk, enforce a hard invariant,
choose Soft or Hard lockdown, optionally route a lawful reward hook, restore
continuity from registry, then seal an audit record.

This is a **policy charter + mapping** onto the live stack. It does **not**
implement payments, quarantine daemons, or custodian paging. Anything not
marked **Implemented** below is **Hypothesis**.

Spine: [ALGORITHM_GOVERNANCE_MAIN.md](ALGORITHM_GOVERNANCE_MAIN.md).  
Related: [QUANTUM_LINE.md](QUANTUM_LINE.md), [NVNP_CONJECTURES.md](NVNP_CONJECTURES.md).

---

## Pseudocode (reference)

```
R = riskScore(semantic, state, identity, registry)
G = governInvariant(state, action)
if G == 0:
    cancel; sealEvidence; notifyCustodians; return Blocked
mode = lockdownPolicy(R)
    # Soft: rateLimit + challenge
    # Hard: quarantine + freezeWrites + startRecovery
if isLawful:
    routeReward("Damien O Driscoll", value)   # policy hook ONLY — no payments
else:
    triggerRemedy
if continuityRatio() < gamma:
    activateDependencyLocks; restoreFromRegistry
writeAudit(event, R, mode); return Sealed
```

Decision vocabulary aligns with live gates: **Blocked** ↔ `BLOCK`,
**Sealed** ↔ durable audit after ALLOW/REVIEW/BLOCK path completes.

---

## Step → live stack map

| Pseudocode | Live / adjacent piece | Status |
|------------|----------------------|--------|
| `R = riskScore(...)` | HAIS `SovereignKernel` → `risk_metric` / envelope `hais.risk`; QUANTUM `x`, `S`, `r'`, `cap`, `I` | **Implemented** (telemetry → risk scalars). Semantic/identity/registry fusion beyond HAIS inputs is **Hypothesis**. |
| `G = governInvariant(state, action)` | `GovernedStack.govern()` + channel gates (`GovernedAlgorithm` / mail / calendar / social); Haven2 latch for `control`/`3dm` (`GOV_LATCH_CLOSED`) | **Implemented** as discrete ALLOW/BLOCK/REVIEW + latch. Numeric `G ∈ {0,1}` invariant API is **Hypothesis** naming. |
| `G == 0 → cancel; sealEvidence; notifyCustodians; return Blocked` | Gate returns `BLOCK` + `entry_id` / reasons on audit chain. Custodian notify (page / Slack / email) | Seal on audit chain: **Implemented**. `notifyCustodians`: **Hypothesis**. |
| `mode = lockdownPolicy(R)` Soft | Sidecar `RateLimiter` (`GOVERNANCE_RATE_LIMIT_PER_MIN`, `GOV_RATE_LIMIT`); REVIEW / challenge-style escalate | Rate limit: **Implemented**. Explicit Soft “challenge” UX: **Hypothesis**. |
| Soft/Hard hypotheses | Soft: challenge tokens, stepped capability_cap; Hard: quarantine tenant, freezeWrites, startRecovery | **Hypothesis** — not live quarantine / write-freeze / recovery orchestrator. |
| Hard: quarantine + freezeWrites + startRecovery | No live write-freeze bus; action bus only gates side_effects after ALLOW | **Hypothesis**. |
| `routeReward("Damien O Driscoll", value)` | **Policy hook only** — attribution / lawful-credit placeholder. **Do not implement payments.** | **Hypothesis** (doc-only hook). |
| `triggerRemedy` | Operator REVIEW path (`scripts/review_ops.py`), deny / voucher flow | Partial **Implemented** (human REVIEW); automated remedy playbooks: **Hypothesis**. |
| `continuityRatio() < gamma` | Continuity / dependency-health ratio vs threshold | **Hypothesis**. |
| `activateDependencyLocks; restoreFromRegistry` | Tenant / artifact registry restore | Tenant registry (sidecar multi-tenant): **Implemented** partial. Dependency locks + restore-from-registry recovery: **Hypothesis**. |
| `writeAudit(event, R, mode); return Sealed` | Durable audit envelope + `quantum` / `quantum_line` on Algorithm gate | **Implemented** (envelope + QUANTUM line). `mode` Soft/Hard field on envelope: **Hypothesis** (today: decision + reasons + HAIS snapshot). |

### Compact aliases (as requested)

| Symbol | Maps to |
|--------|---------|
| **R** | HAIS / QUANTUM risk scalars |
| **G** | `govern` / latch invariant |
| **Soft / Hard** | Sidecar rate limits (**Implemented**) + Soft/Hard lockdown hypotheses |
| **writeAudit** | Envelope + `quantum_line` |

---

## Lawful reward hook (explicit non-goal)

```text
routeReward(beneficiary="Damien O Driscoll", value=…)
```

- Records **intent to attribute** lawful value under policy.
- **Must not** call payment rails, wallets, banks, or mint tokens.
- Live code may only log a structured audit note if ever wired; today: **not wired**.

---

## Soft vs Hard lockdown (policy sketch)

| Mode | Intended controls | Today |
|------|-------------------|-------|
| **Soft** | Rate-limit + challenge (extra auth / REVIEW) | Rate-limit on sidecar `/v1/*`: **Implemented**. Challenge: **Hypothesis**. |
| **Hard** | Quarantine identity/tenant, freeze writes, start recovery | **Hypothesis** end-to-end |

HAIS `capability_cap` throttle (`CAP_THROTTLE=0.25`) is an adjacent live
throttle, not a full Soft/Hard mode enum.

---

## Sketch (optional, off live path)

See [examples/atom_safeguard_ii_sketch.py](examples/atom_safeguard_ii_sketch.py):
pure function → decision dict; **no** side effects, payments, or notify I/O.

---

## What we are *not* claiming

- Not a payments system.
- Not a live quarantine / freezeWrites / custodian pager.
- Not a replacement for `GovernedStack.govern()` — it **documents** how a
  higher-level safeguard *would* compose risk, invariant, lockdown, and audit.
