# Audit trail — reviewed gate changes

Living log of **source-reviewed** changes to the live governance path.
Not a runtime ledger (`artifacts/*/audit.db`); not mythos.

Verification labels match [UNIFIED_USEFUL.md](UNIFIED_USEFUL.md):
**Verified** / **Reported** / **Accepted residual**.

---

## Bio semantic judge adapter (**Reported**, local branch, source review pending)

| Field | Value |
|-------|--------|
| Branch | `feat/bio-semantic-judge-adapter` (off `main` @ `bac092b`; local only, not pushed) |
| Kernel | `src/governed_stack/bio_semantic_overlay.py`: user-authored, **vendored byte-for-byte** (sha256 `aab42764…9c89`); its 53 tests are vendored verbatim as `tests/test_bio_semantic_overlay.py` |
| Adapter | `src/governed_stack/bio_semantic_judge.py` |
| Tests | `tests/test_bio_semantic_judge.py` (`test_i1_*` … `test_i6_*`), plus 2 live-path tests in `tests/test_bio_governance.py` |
| Label | **Reported** until Damien has reviewed the source and run pytest |

### What changed

- **Single bio pipeline.** `GovernedBio.check` and sidecar `channel=bio` both call `govern_bio_request`. The order is: structural `classify_bio` → kernel `evaluate_overlay` → merge (tighten-only) → `stack.govern` → `tighten_decision` → `apply_bio_voucher_honor` → judgement audit row → REVIEW enqueue.
- **Bridge.** ALLOW_CANDIDATE↔ALLOW, REVIEW↔REVIEW, BLOCK↔BLOCK. Any other structural state goes to the kernel as invalid, so it returns BLOCK. The kernel's ALLOW maps back to ALLOW_CANDIDATE: the bio layer never grants ALLOW. A structural BLOCK keeps its own code (`GOV_BIO_DUAL_USE`).
- **Judge before voucher.** The judge runs before `stack.govern`. A judged BLOCK withholds the voucher from the stack (`bio_semantic:voucher_withheld`), so the voucher is never presented or consumed.
- **Classifier.** The default is the stub (`stub_heuristic_classifier` over `score_bio_text`), which keeps the 0.6.2 actions. A model judge plugs in through `BioSemanticJudge(classifier, scorer_id, config)`, set with `GovernedBio(semantic_judge=…)` or `SidecarService(bio_semantic_judge=…)`. Multi-tenant setups use the registry `service_factory`.
- **Reasons.** The vocabulary is closed (`CHARTER_REASONS`: the charter §2 labels, merge markers, and `judge_failed:<category>`). Reasons are derived only from validated scores and the failure category. Classifier free-text reasons and the stub's cue phrases are never forwarded. Text is carried only as its SHA-256.
- **Fail closed.** Classifier output is snapshotted (each label read once) before the kernel validates it. A kernel-call exception (`adapter_error`) or a malformed/looser outcome (`malformed_outcome`) gives REVIEW (BLOCK if the structural state is unknown). If the judgement audit write fails, ALLOW is tightened to REVIEW.
- **Audit.** Every judgement, structural BLOCKs included, is written as its own signed, chained row: `decision=BIO_SEMANTIC_JUDGEMENT`, `result=bio_semantic:<final>`, and metadata with `judged_entry_id`, scores, fail_reason and scorer. The row is never enqueued.
- **Config only.** Narrow ruff (`I001`) / mypy (`operator`, `index`) exemptions for the vendored kernel file, so it can stay unmodified.

### Findings / accepted residuals

- **Raw-kernel TOCTOU (adapter closes it; kernel not modified).** `_score` validates a mapping and then re-reads it. If the re-read gives NaN or a negative number, `max()` turns it into 0.0, so a validated 0.9 can come out as ALLOW. Tracked by the strict-xfail test `test_i5_raw_kernel_rugpull_toctou_gap_documented`. Suggested kernel fix: snapshot `scores = dict(scores)` before `validate_scores`.
- The stub is still phrase-heuristic. A benign cue (e.g. "published paper") checked first can mask a later harm cue (PR #19 residual, unchanged).
- Judgement rows use a non-decision label (not counted by `storage.stats()`). Audit consumers must look for `BIO_SEMANTIC_JUDGEMENT`.
- Running classifier threads cannot be cancelled on timeout. The kernel's bounded pool fails closed (REVIEW) once it is wedged.

---

## PR #19 — semantic stub overlay 0.6.2 (**Verified** / Ready to merge)

| Field | Value |
|-------|--------|
| Branch | `feat/bio-semantic-overlay` |
| Commits | `08e89f0` (stub + charter) · `9091f45` (trail note) |
| URL | https://github.com/Damien157/governance-engine/pull/19 |
| Depends on | PR #17 content-binding on `main` |
| Module | `src/governed_stack/bio_semantic.py` |
| Charter | [BIO_SEMANTIC_CHARTER.md](BIO_SEMANTIC_CHARTER.md) |
| Review bar | Source packet: stub families, wire after `classify_bio` before voucher honor, envelope/`GOV_BIO_SEMANTIC_*`, paraphrase residual, `SemanticResult` handoff |
| Label | **Verified** / **Ready to merge** (2026-09-21 source pass) |

### What this adds

- Offline **stub** scorer (`score_bio_text`) — charter eval families; no LLM
- `classify_bio_with_semantic` → structural `classify_bio` then tighten-only semantic
- Hooked in `GovernedBio.check` + sidecar bio channel **before** voucher honor
- Envelope field `bio_semantic`; codes `GOV_BIO_SEMANTIC_REVIEW` / `GOV_BIO_SEMANTIC_BLOCK`
- Failures / injection cues → REVIEW (never ALLOW-by-scorer-failure)
- `apply_bio_voucher_honor` also refuses when `decision == "BLOCK"` (semantic harden stays closed)

### Verdict (source pass)

Merge-ready: stub knows its paraphrase limits; sits after structural / before voucher; tighten-only; HARD_BLOCK stays blocked; no parallel queue; handoff = swap `score_bio_text`, keep `SemanticResult`.

### Accepted residuals

- Stub is phrase-heuristic, not a model judge — paraphrase coverage limited by cue lists (+ `default_benign_stub` fallthrough)
- `synthesis_howto` vs mechanism relies on ordered cue lists (charter §2.1)
- Model-judge + scorer prompt hygiene (§1.1) + injectable `SCORER` hook still future

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
