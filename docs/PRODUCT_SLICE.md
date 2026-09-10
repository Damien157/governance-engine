# Product slice — sealed ALLOW before side effects

One-page buyer view of the live governance product. No P vs NP claims, no
consciousness claims, no valuation hype, no quantum-computing marketing.

## What you get

No agent or algorithm **side effect** without a sealed **ALLOW** from the
live gate. Every decision is audited.

| Live API | Side effect it gates |
|----------|----------------------|
| `GovernedAlgorithm` | Algorithm **run / deploy** (does not execute the algo; decides only) |
| `GovernedMail` | Outbound mail send |
| `GovernedCalendar` | Calendar write / invite |
| `GovernedPost` / `GovernedOutboundText` | Social / outbound text post |
| `GovernedActionBus` | Mutation facade — `require_allow` before any `side_effect` |
| `GovernedStack.govern` | Shared Authority + Audit spine (HAIS + Haven2 + durable entry) |

Front door alias: `HavenUnified` / `GovernedUnified` → stack govern path.

## Audit on each check

A typical `GovernedAlgorithm.check` / `check_sync` result includes:

- **decision** — `ALLOW` / `BLOCK` / `REVIEW` (+ `ok`, `reasons`, `entry_id`)
- **QUANTUM state** — structured HAIS snapshot + fused `quantum_line` (audit
  label only; **not** a quantum computer)
- **optional spectrum** — Haven2 finite Dirichlet summaries (`Z_E`, `Z_R`,
  `Z_C`, `Z_H`) when the engine/envelope can supply them; otherwise explicit
  nulls + reason
- **haven2** — realm / residual / latch open flag from the live step

## Soft block vs hard

- **Hard gate (live):** non-ALLOW means do not run, send, post, or write.
  Prefer `require_allow` so a miss raises (`AlgorithmBlocked` / `SendBlocked`
  / …) and cannot be ignored via `ok`.
- **Soft controls (partial):** sidecar rate limits (`GOV_RATE_LIMIT`) throttle
  bursty callers. Full Soft “challenge” UX and Hard quarantine / write-freeze
  / recovery are charter hypotheses — see [ATOM_SAFEGUARD_II.md](ATOM_SAFEGUARD_II.md).
- Vocabulary today: **ALLOW** proceed · **BLOCK** refuse · **REVIEW** human
  escalate — not a Millennium proof and not a Soft/Hard lockdown product SKU.

## Explicitly not this product

- **Not** a Millennium Prize / P vs NP proof
- **Not** quantum computing hardware or a quantum supremacy claim
  (“QUANTUM” is Damien’s fused HAIS audit label)
- **Not** a consciousness detector (spectrum = finite Dirichlet sums over
  energy/latch/score traces)
- **Not** an automatic executor — gates decide; callers act only after ALLOW

## Where to read next

- [ALGORITHM_GOVERNANCE_MAIN.md](ALGORITHM_GOVERNANCE_MAIN.md) — algorithm charter
- [QUANTUM_LINE.md](QUANTUM_LINE.md) — HAIS fused audit line
- [SPECTRAL_AUDIT.md](SPECTRAL_AUDIT.md) — Haven2 zeta / spectrum honesty
- `src/governed_stack/AGENT_*.md` — hard agent rules per channel
