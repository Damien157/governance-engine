# Audit trail — reviewed gate changes

Living log of **source-reviewed** changes to the live governance path.
Not a runtime ledger (`artifacts/*/audit.db`); not mythos.

Verification labels match [UNIFIED_USEFUL.md](UNIFIED_USEFUL.md):
**Verified** / **Reported** / **Accepted residual**.

---

## Chronology note (order vs bar)

Ideal bar: seal gate-to-wire **before** shipping constitution/bus claims.

Actual trunk order:

1. **PR #13** — BioGovernance OS (check-only) — on `main` first  
2. **PR #14** — bio REVIEW queue + shared voucher honor — on `main`  
3. **PR #16** — full governance 0.6.0 (connectors, no-bypass lint, bio on bus, AGENT_MANDATES) — on `main`  
4. **PR #17** — content-binding seal 0.6.1 — on `main` **after** #13–#16  

So #13–#16 landed **before** the content-swap seal. That was the wrong order relative to the review bar; **#17 closes the hole** those PRs' claims needed. Treat #17 as **Verified** (source-reviewed). Treat #13 / #14 / #16 as on `main` with review status as listed below — constitution claims are only honest **with** #17 on the same trunk.

---

## PR #17 — content-binding seal 0.6.1 (**Verified**)

| Field | Value |
|-------|--------|
| Branch | `fix/content-binding-0.6.1` |
| URL | https://github.com/Damien157/governance-engine/pull/17 |
| Merge | **MERGED** to `main` after #13–#16 (see chronology) |
| Label | **Verified** (source-reviewed; TypeError on closed-over body; envelope bind proven) |
| Depends on | PR #16 on `main` (0.6.0) |
| Local suite | `tests/test_connectors.py` + mail/calendar/social/bus envelope probes **PASS** |

### What this adds

- Mail `check` / `require_allow` echo **`body`** (and `cc`) on the ALLOW envelope
- Calendar / social envelopes already carried gated fields; factories now **require** them
- `bus_mail_side_effect` / `bus_calendar_side_effect` / `bus_social_side_effect` take **only** the connector — no closed-over `body=` / `text=` / `summary=` kwargs (`TypeError` if passed)
- `ContentBindingError` + `assert_bound_content` — refuse send when envelope lacks approved content
- Constitution rule **3a** in [AGENT_MANDATES.md](AGENT_MANDATES.md)

### Threat closed (why this was gate-critical)

Gate could ALLOW body A while a factory closed over body B on the wire. That made “side effects only after ALLOW” false for content. Closed as a live bug, not an accepted residual.

### Accepted residuals

- Mail `cc` still not scanned by policy (routing metadata; echoed for bind only)
- Custom side_effects outside `bus_*` factories must still call `assert_bound_content` (mandate + helper; not AST-enforced yet)
- Semantic bio free-text dodge (unchanged)

---

## feat/bio-review-resolve-auth — REVIEW resolve + authority probes (open)

| Field | Value |
|-------|--------|
| Branch | `feat/bio-review-resolve-auth` |
| Depends on | PR #13 on `main` |
| Local suite | `tests/test_bio_governance.py` **16/16 PASS** |

### What this adds

- `enqueue_pending_review(entry_id)` on audit storage/engine — bio overlay REVIEW can enter the pending queue when stack logged ALLOW
- `GovernedBio.list_pending_reviews` / `resolve_review`; `approval_voucher` on `check`
- Stack forwards `approval_voucher` into `execute_governed_action`
- Sidecar `POST /v1/review/resolve` + bio channel voucher/enqueue fields
- Decision **cache bypass** when `approval_voucher` present (otherwise voucher ALLOW was masked as empty-reason cache hit)
- Authority probes: `pathogen_work` stays BLOCK across `authority_role` + JWT `role` variants

### Caller contract for REVIEW

1. `check` → `REVIEW` + `entry_id` (+ `review_enqueued`)
2. Stop side effects; escalate to human (`require_allow` raises)
3. Human `resolve_review(approve=True|False)`
4. If approve: re-`check` **same intent** with `approval_voucher`
5. Never treat bare `REVIEW` as soft-ALLOW

### Accepted residuals (unchanged)

- Semantic free-text dodge on raw
- Full JWT/stack gaming matrix still thin (role probes added; not exhaustive)
- No REVIEW timeout / SLA


## PR #16 — Full governance 0.6.0 (**Verified** for bus/lint/mandates; see chronology)

| Field | Value |
|-------|--------|
| URL | https://github.com/Damien157/governance-engine/pull/16 |
| Merge | Before PR #17 content-binding seal |
| Label | **Verified** (source packet reviewed in chat: connectors, lint_no_bypass, bio bus call site, AGENT_MANDATES) |
| Note | Gate-to-wire honesty for mail/social/calendar content requires **#17** on the same trunk |

### What landed

- `connectors.py` + `bus_*_side_effect` factories; no-bypass AST lint in CI
- Bio channel on `GovernedActionBus`; AGENT_MANDATES constitution
- Structural-marker honesty line (bio / mandates)

---

## PR #14 — Bio REVIEW queue + voucher honor (**Reported** → treat as on `main`)

| Field | Value |
|-------|--------|
| URL | https://github.com/Damien157/governance-engine/pull/14 |
| Label | **Reported** / source-adjacent (voucher honor + enqueue path; full JWT matrix still thin) |
| Merge | Before PR #17 |

---
## PR #13 — BioGovernance OS (check-only)

| Field | Value |
|-------|--------|
| Branch | `feat/bio-governance-os` |
| Commits | `b64b25d` (channel + policy + contracts) · `2d3dfc5` (bypass seal + raw probe + gaps) |
| URL | https://github.com/Damien157/governance-engine/pull/13 |
| Local suite | `tests/test_bio_governance.py` **13/13 PASS** (+ smoke script) |
| GitHub CI | Red ~3s (repo-wide Actions pattern; not treated as bio-specific proof) |
| Review bar | Source review of BLOCK / probe / contracts — not description-only endorsement |

### What landed

- `GovernedBio` + `bio_policy` (tighten-only) on Purpose→Cost→Risk→Authority→Audit
- `BioScanIntent` / `BIO_SCAN_FORBIDDEN` — no sequences/protocols in scan
- `BIO_BYPASS_FORBIDDEN` + `BIO_SCAN_REJECT_KEYS` — `force_allow` / `bypass` / … rejected
- Sidecar `channel: "bio"` on `POST /v1/check`; **no** `/v1/execute` for bio
- `_bio_shaped_probe` on `channel=raw` after JSON→dict, **before** `stack.govern`
  - Uses `keys & BIO_SCAN_REJECT_KEYS` (`dict_keys` supports `&` with frozenset natively)
  - One-level nested `payload` mirrored (raw never hits Pydantic)
  - Structural fingerprint `purpose`+`domain`+`intervention_class` on both layers
- Docs: gravity model + **Known gaps** in [BIO_GOVERNANCE_OS.md](BIO_GOVERNANCE_OS.md)

### Accepted residuals (tracked, not closed in #13)

1. **Semantic free-text dodge** — key-based probe cannot catch natural-language bio without structured keys; needs classifier later  
2. **Thin authority test** — today: `classify_bio(..., pathogen_work, authority_role="pi") → BLOCK`; not full JWT/role/stack unblock matrix  
3. **No REVIEW timeout** — bio `REVIEW` is per-call/stateless; unresolved items do not expire or auto-ALLOW (operational queue risk if human resolve stalls)

### Deliberate non-goals

- Not a biosafety officer / IRB / medical authority  
- Not wet-lab execution or recipe generation  
- Not semantic dual-use NLP in this PR  

### Follow-ups after merge

- Point HAIS-AI README bio doc link at `main`  
- Add bio row to [UNIFIED_USEFUL.md](UNIFIED_USEFUL.md) integrity / live-modules tables  
- Stronger authority/unblock + optional SidecarService.check integration test for raw nested smuggle  
- Land [HAIS_UNIFIED_ARCHITECTURE.md](HAIS_UNIFIED_ARCHITECTURE.md) when ready (local draft)

---

## Earlier integrity stack

See UNIFIED_USEFUL §3 (FIX 1–10, latch, hosted-check, audit projection). Those entries predate this file; do not duplicate here unless re-reviewed.
