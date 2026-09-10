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

## Hosted check API (HTTP sidecar)

Check-only — no remote execute. Stdlib ``http.server`` sidecar (``governed_stack.sidecar``).

Issue a JWT with the same signing key as the sidecar:

```bash
python - <<'PY'
from governed_stack.sidecar import SidecarService
svc = SidecarService()
print(svc.issue_token("customer", "operator"))
PY
```

```bash
curl -s -X POST http://127.0.0.1:8080/v1/check \
  -H 'Content-Type: application/json' \
  -d '{
    "channel": "algorithm",
    "token": "<JWT>",
    "purpose": "batch_dedupe",
    "summary": "Nightly anonymized id dedupe",
    "time_cost": "O(n log n)",
    "space_cost": "O(n)",
    "energy_cost": "low",
    "speedup": "~2x",
    "risk_notes": "read-only replica",
    "security_margin": "standard"
  }'
```

Algorithm responses include ``decision`` / ``ok`` / ``reasons`` / ``entry_id`` plus QUANTUM (``quantum``, ``quantum_line``) and ``spectrum`` when available. See [HOSTED_CHECK_API.md](HOSTED_CHECK_API.md) and [CUSTOMER_OPS.md](CUSTOMER_OPS.md).

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
