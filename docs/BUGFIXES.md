# Bug → solution log

## Integrity — require_persisted_key missing PEM (sidecar / LocalPEM)

| Bug | Solution |
|-----|----------|
| **`require_persisted_key` stub in sidecar** — `_build_stack` did `pass` then built `CryptoEngine` **without** the flag; LocalPEM only refused `path=None`, so a missing PEM path still **auto-generated** under production `GOVERNANCE_REQUIRE_PERSISTED_KEY=1`. | LocalPEM raises `FileNotFoundError` when require is set and the path is missing; sidecar passes `require_persisted_key` into `CryptoEngine`; `rotate()` still intentionally creates after backup (`require=False`). |


Damien: “best solutions come from bug fixes — apply the same to our bug finds.”
This file records documented findings turned into real fixes (not honesty labels).

HAIS sigmoid **m=0.5** unchanged. Sketches stay off the live `govern()` decision path.
No real sends. No GitHub push required for local verification.

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

## Residual known issues — integrity queue (post PR #1)

| Bug | Intended solution |
|-----|-------------------|
| API-key / master-key string `==` | Use `hmac.compare_digest` for equality checks in sidecar auth. |
| Multi-tenant shared default PEM fallback | Require per-tenant signing PEM when multi-tenant (esp. `REQUIRE=1`); no silent shared key. |
| Lazy tenant build under `REQUIRE=1` | Eager-build / preflight all tenants at serve start so `/ready` fail-fast matches runtime. |

Full map: [UNIFIED_USEFUL.md](UNIFIED_USEFUL.md).

