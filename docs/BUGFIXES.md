# Bug → solution log

Damien: “best solutions come from bug fixes — apply the same to our bug finds.”
This file records documented findings turned into real fixes (not honesty labels).

HAIS sigmoid **m=0.5** unchanged. Sketches stay off the live `govern()` decision path.
No real sends. No GitHub push required for local verification.

Full useful map: [UNIFIED_USEFUL.md](UNIFIED_USEFUL.md).

## 0.6.1 — content-swap seal (envelope binding)

| Bug | Solution |
|-----|----------|
| **Content-swap after ALLOW** — `GovernedMail.check` returned `to`/`subject` but **omitted `body`**; `bus_mail_side_effect(..., body=...)` could close over different text than gated. Same class of gap for calendar description/location and social text. | Gate envelopes include exact approved content (`body`; `summary`/`description`/`location`; `text`). Factories are `bus_*_side_effect(connector)` only — closed-over content kwargs raise `TypeError`. Side effects read only from envelope; missing keys → `ContentBindingError`. |


## Integrity — require_persisted_key missing PEM (sidecar / LocalPEM)

| Bug | Solution |
|-----|----------|
| **`require_persisted_key` stub in sidecar** — `_build_stack` did `pass` then built `CryptoEngine` **without** the flag; LocalPEM only refused `path=None`, so a missing PEM path still **auto-generated** under production `GOVERNANCE_REQUIRE_PERSISTED_KEY=1`. | LocalPEM raises `FileNotFoundError` when require is set and the path is missing; sidecar passes `require_persisted_key` into `CryptoEngine`; `rotate()` still intentionally creates after backup (`require=False`). |

## Control QP result ignored by the decision

| Bug | Solution |
|-----|----------|
| **QP outcome never gated `govern()`** — the `control` step stored `status`/`feasible` in `solver` but the decision never read them; a solver exception only added note `qp_solver_error:…`. An infeasible or crashed safety solve could return **ALLOW**. | Fixed in #31 (tighten-only, via `GovernedStack._qp_failure_reason` + `_tighten`): solver exception → reason `qp_solver_error`; `feasible` not true, `fail_closed`, or status outside the OK set (incl. `cbf_only_after:*`) → `qp_not_feasible:<status>`; any of these → **BLOCK** (`error_code=GOV_INTERNAL`). Never loosens REVIEW/BLOCK; runs after the ops layer, so an approval voucher cannot reopen it. The CGE audit row keeps the pre-solver decision (`audit.ops_decision`, no second audit write); the override is recorded as `audit.qp_tightened = {from, to, reason}` plus a note. Tests: `tests/test_stack_qp_status_feedback.py` (failure modes, classifier) and `tests/test_governed_stack_qp_block.py` (real CVXOPT ALLOW + infeasible BLOCK, carried over from #32). |

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

## Audit-log truncation guard (fail closed)

| Bug | Solution |
|-----|----------|
| The signed SHA-256 hash chain could not detect a dropped **tail**: deleting the last N rows, deleting the per-key `audit.db`, or restoring an older copy left a chain that still verified, so decisions carried on as if nothing happened (pinned as a known limitation in `test_math_audit_hashchain.py`). | `AuditStorage` now keeps a **signed head anchor** `{seq, head_hash}` in a sidecar file `<db_path>.anchor` (outside the DB). After each committed append it is atomically advanced (temp file + `os.replace`), and only forward, and only when the previously anchored head is still at the same position in the chain. `verify_chain()` (run before every decision) returns `anchor_ok`/`anchor_reason`. A chain shorter than `seq`, a different hash at position `seq`, an anchor with a bad signature or unreadable contents, or an anchor deleted while the process is running sets `valid=False`, and `execute_governed_action` returns a terminal **BLOCK** with reason `audit_truncation:<reason>`. That BLOCK is never REVIEW, is never served from the decision cache, and no approval voucher can reopen it. A legacy DB with no anchor gets one on first open, but only if its chain verifies. Tests: `tests/test_audit_truncation_guard.py`. |

**Limits (by design, documented):**
- An attacker who controls both the log *and* the anchor file (for example, deletes both, or restores an old DB together with its old anchor) is not detected. The anchor is in a separate file, not on separate trust infrastructure. Stronger options would be to pin `seq/head_hash` externally (a WORM bucket, a transparency log, or a remote witness) or to hold the anchor where the DB writer cannot write.
- Deleting the anchor *and* truncating while the engine is stopped looks like a legacy DB on the next open, so it is re-bootstrapped and accepted. While the process is running, deleting the anchor is caught (`anchor_missing`).
- Anyone holding the signing key can forge an anchor. The anchor gives no more protection than the key.
- A crash between the DB commit and the anchor write leaves the anchor one entry behind. That is still sound, because it only guards a shorter prefix, so it is not flagged.
- The anchor binds the DB file's basename (`audit.db`), not its full path. Anchors for different keys are told apart by living next to their own DB. Copying another key's anchor that uses the same signing key and has a matching prefix is not detected.
- Recovery after a real incident is a deliberate operator step: restore the correct DB, or remove the anchor after investigating. Until then the guard keeps blocking.

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

## Integrity — tool gate fail-closed (whole-intent scan, non-dict payload, rm flags)

| Bug | Solution |
|-----|----------|
| **Tool args unscanned** — generic intents dropped extra top-level keys (`cmd`, `argv`) before policy; JSON escaping hid `\t`/`\n` from `\s`, so `rm\t-rf /`, a second line, or an argv list ALLOWed. | `intent_to_govern_dict` keeps extra top-level keys for generic `GovernIntent`; `PolicyEngine.evaluate` also scans a normalised view of every string leaf (whitespace collapsed, per line + whole, string lists joined as argv). |
| **Split rm flags** — `rm -r -f /`, `--recursive --force` missed `tool_rm_rf_root`. | `_RM_RF_ANY`: any order/mix of `-r/-R/--recursive` + `-f/--force`, separate or combined. `rm file.txt` / `rm -f build.log` still ALLOW. |
| **#27 regression** — non-dict `payload` + prose alias (`body`/`summary`/`text`) was silently dropped → ALLOW. | `_fold_prose_scan_fields` raises `IntentValidationError` → BLOCK `GOV_INTENT_INVALID`. |
| String `payload` crashed `stack._govern_inner` (`AttributeError` on `.get`). | `govern()` BLOCKs any non-dict payload with `GOV_INTENT_INVALID`; difficulty lookup made type-safe. |
| **Review gaps** — `rm -r /` / `rm -r -I /` ALLOWed; `rm -rf -- /`, `'/'`, `"/"`, `/.` only REVIEWed; zero-width / fullwidth / quote / backslash tricks (`r\u200bm`, `ｒｍ`, `r''m`, `r\\m`) ALLOWed; tuple dict key raised `TypeError`; `rm -rf /home/me/proj/dist` false-positive BLOCK. | Root rule needs only recursive (`_RM_R_ANY`), accepts `--`/`/.`/`..`; scan text is NFKC-folded, Cf-stripped, plus a quote/backslash-stripped shell view (dict keys scanned too); `GovernedTool.check` BLOCKs non-str keys with `GOV_INTENT_INVALID`; root = system dir or one level below (deeper → REVIEW). Also BLOCK `find / … -delete`/`-exec rm -r` and `base64 -d … \| sh`. |
