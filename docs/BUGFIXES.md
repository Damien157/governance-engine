# Bug → solution log

Damien: “best solutions come from bug fixes — apply the same to our bug finds.”
This file records documented findings turned into real fixes (not honesty labels).

HAIS sigmoid **m=0.5** unchanged. Sketches stay off the live `govern()` decision path.
No real sends. No GitHub push required for local verification.

Full useful map: [UNIFIED_USEFUL.md](UNIFIED_USEFUL.md).

## Integrity — require_persisted_key missing PEM (sidecar / LocalPEM)

| Bug | Solution |
|-----|----------|
| **`require_persisted_key` stub in sidecar** — `_build_stack` did `pass` then built `CryptoEngine` **without** the flag; LocalPEM only refused `path=None`, so a missing PEM path still **auto-generated** under production `GOVERNANCE_REQUIRE_PERSISTED_KEY=1`. | LocalPEM raises `FileNotFoundError` when require is set and the path is missing; sidecar passes `require_persisted_key` into `CryptoEngine`; `rotate()` still intentionally creates after backup (`require=False`). |

## 0.4.4 — bug→fix pass

| Bug | Solution |
|-----|----------|
| **GOV_LATCH_CLOSED rarely set (LIVE)** — `control`/`3dm` with Haven2 transistor closed stayed ALLOW and only noted “solvers skipped”. | **BLOCK** with reason `transistor_closed` → `error_code=GOV_LATCH_CLOSED`. Query / mail / calendar / social unchanged (latch does not block those). |
| **Smuggled routing fields ignored (LIVE contracts)** — scan models used `extra="ignore"`, so `to`/`attendees`/… dropped silently. | `MailScanIntent` / `CalendarScanIntent` / `SocialScanIntent` (+ `parse_intent` / `validate_*_scan` / stack path) raise `IntentValidationError` / BLOCK `GOV_INTENT_INVALID` with `scan_intent_forbids:<key>`. `GovernIntent` general path stays tolerant. |
| **Adaptive Re overshoot (MATH / fluids)** — public `adaptive_re_update` was unclamped. | **Clamped is the default** (`adaptive_re_update` → clamped). Unclamped renamed/kept as `adaptive_re_update_unclamped` for regression only. |
| **OSQP PositionCBF not forward-invariant (SKETCH)** — demo surrogate barrier failed closed-loop invariance. | Replaced with a relative-degree-2 **HOCBF** so closed-loop tests `assertTrue(forward_invariant)`. Legacy surrogate kept as `SurrogatePositionCBFBroken` for regression. **Not** wired into live `govern()`. |

## 0.4.3 — concurrent audit hash-chain race

| Bug | Solution |
|-----|----------|
| Concurrent audit writers could race on hash-chain linking when timestamps were assigned outside the single-writer lock. | Assign monotonic timestamp **under** the audit write lock; soak test covers concurrent append + verify. |

## Residual known issues

- `adaptive_re_update_unclamped` remains available and can overshoot by design (opt-in regression).
- TG Dedalus fluids smoke: incomplete without Dedalus / `sym_grad` (harness documents skip).


## Integrity — multi-tenant PEM + auth compare (sidecar)

| Bug | Solution |
|-----|----------|
| **Shared default PEM fallback** under multi-tenant + `REQUIRE=1` — missing `tenants/<id>/signing_key.pem` silently reused the process default key. | `_resolve_tenant_signing_key` raises `FileNotFoundError` (refusing shared default); `TenantRegistry.from_env` passes require and **preflight_stacks()** so failure is at start, not first `/v1/check`. |
| API-key / master-key string `==` | `hmac.compare_digest` on master and single-tenant equality checks. |
| Lazy tenant build vs `/ready` under require | `preflight_stacks()` eager-builds all tenants when require is set. |

## Integrity — Haven2 switch_times vs history_p_hat (0-based)

| Bug | Solution |
|-----|----------|
| Engine passed `t=t_before+1` into the latch, so `switch_times` were 1-based while `history_p_hat` was seeded at t=0 and `c_scores` / zeta used 0-based `arange` — same physical step got different `(t+1)^σ` weights (`Z_C` vs `Z_E`/`Z_R`). | Leave `TransistorLatch._t` alone (0-based); do not seed `history_p_hat`; regression asserts `history_p_hat[τ] ==` switched step’s `p_hat`. |

Full map: [UNIFIED_USEFUL.md](UNIFIED_USEFUL.md).
