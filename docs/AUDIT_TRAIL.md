# Audit trail — reviewed gate changes

Living log of **source-reviewed** changes to the live governance path.
Not a runtime ledger (`artifacts/*/audit.db`); not mythos.

Verification labels match [UNIFIED_USEFUL.md](UNIFIED_USEFUL.md):
**Verified** / **Reported** / **Accepted residual**.

---

## test/all-works — one-command runner + default tool-gate hardening (**Reported**, PR open)

| Field | Value |
|-------|--------|
| Branch | `test/all-works` (rebased on `main` @ `a804a7e`, PR #25) |
| Runner | `scripts/run_all_works_tests.py`: auto-discovers `tests/test_*.py` (pytest, per-area summary); sub-project folders kept; `coverage_problems()` fails on any test file matching no area (satisfies `tests/test_runner_coverage.py`, unchanged) |
| Policy | `certified_governance_unified.py` default `PolicySpec`: destructive/irreversible + CBRN rules (below); `PolicyRule.exclude_actions`; `action_rules` now honour `pattern` |
| Contracts | `contracts.parse_intent`: `run_algorithm` scan fields under `payload` are folded in instead of silently dropped |
| Label | **Reported** until Damien reviews and merges |

### What changed

- **Destructive actions.** BLOCK: `rm -rf` on root/home/system paths or `--no-preserve-root`, `DROP DATABASE/SCHEMA`, format/mkfs/`dd of=/dev/sd*`/fork bomb, wipe/erase/destroy production data, wipe/erase the database/disk/server/backups; action names `drop_database`, `wipe_disk|database|production|all|everything…`, `format_disk|drive…`, `delete_everything`, `delete_production…`. REVIEW: other `rm -rf`, `DROP/TRUNCATE TABLE`, "delete all/every", action names `delete_all*`, `truncate_table`, `wipe_*`, `bulk_delete*`. Reasons `banned_term:destructive*` / `policy:destructive_action:*` map to `GOV_POLICY_BLOCK` / `GOV_POLICY_REVIEW`.
- **CBRN.** BLOCK: synthesis/acquisition/weaponisation verbs near named agents (nerve agents, sarin, VX, ricin, anthrax, ...), "build/make" of weapons/devices, enrich uranium for a weapon. REVIEW: a bare agent mention (not on mail/calendar/social/bio channels).
- **Scoping.** Destructive content rules skip `send_email`, `calendar_write`, `publish_post` (prose, not an executable tool call). CBRN BLOCK applies everywhere; bare-mention REVIEW skips prose and `bio_govern`.
- **Silent drop fixed.** `{"action": "run_algorithm", "payload": {"purpose": ...}}` reached policy with an empty payload, so nothing was scanned.

### Accepted residuals

- Regex markers: paraphrase, obfuscation and non-English requests are not caught; this is a floor, not a classifier.
- ~~Mail/calendar/social intents passed through `GovernedTool` with non-contract payload keys (e.g. `payload.body`) still lose those keys before policy; use the channel adapters (`from_scan` forms).~~ **Closed** by `fix/tool-gate-content-fields` (see below).

---

## fix/tool-gate-content-fields — prose-channel content reaches the policy scan (**Reported**, PR open)

| Field | Value |
|-------|--------|
| Branch | `fix/tool-gate-content-fields` (stacked on `test/all-works`, PR #26) |
| Code | `src/governed_stack/contracts.py` (`parse_intent` + `_fold_prose_scan_fields`); no policy-engine change |
| Tests | `tests/test_governed_tool_prose_fields.py` (area `tool_agent_0.7`) |
| Label | **Reported** until Damien reviews and merges |

### What changed

- **Gap closed.** `send_email` / `calendar_write` / `publish_post` intents given to `GovernedTool` with content under non-contract keys (`payload.body`, `payload.message`, top-level `description`/`body`/`text`, ...) were reduced to the contract's `payload.subject`/`payload.text` by models with `extra="ignore"`, so the policy scanned an empty payload and ALLOWed. `parse_intent` now folds every content-bearing field into the contract's scanned `payload.subject` / `payload.text` (the existing `from_scan` mechanism): the channel's top-level aliases (mail `subject`/`body`/`text`/`message`/`content`/`html`; calendar `summary`/`title`/`description`/`notes`/`location`/`body`/`text`/`message`/`content`; social `text`/`body`/`message`/`content`/`title`/`platform`) and **every other free-text key under `payload`** (strings, and string leaves of nested lists/dicts; numbers/bools/None are not prose and are skipped).
- **Secret keys still rejected.** `private_key`/`password`/`token`/`secret` (case-insensitive) at the top level or directly under `payload` of these three actions are now rejected with `scan_intent_forbids:*` → `GOV_INTENT_INVALID` (previously silently dropped), as are the channel routing keys (`to/cc/bcc/from`, `attendees/start/end`, `handles/urls/recipients`) and any of those keys nested inside a folded free-text value. Nothing is folded from a forbidden key.
- **Exemptions unchanged.** The destructive content rules still skip `send_email`/`calendar_write`/`publish_post` (`exclude_actions`); CBRN BLOCK applies to these actions and now fires on content in any of the fields above. Destructive text in a mail/post/event is therefore still ALLOWed by the default policy (prose), but it is now scanned: a policy without the exemption BLOCKs it (tested).
- **Hash behaviour.** Contract-conforming shapes (`payload.subject/text`, `from_scan` top-level forms) produce byte-identical govern dumps and `intent_sha256` (hashes pinned from pre-fix code in the tests). Identical fragments are de-duplicated. Intents that used the dropped keys now hash differently per content, i.e. the approval binds the body.

### Accepted residuals

- Mail/calendar/social content now also reaches the **PII** rules (email address / phone / SSN / "password" / "credit card" terms are not exempt), so a body that contains an email address or phone number is BLOCKed. Previously the same text sent under `payload.text` was already blocked; sending it under `payload.body` was the unscanned path.
- Unknown top-level keys outside the alias lists (and non-dict `payload`) are still ignored/rejected by the model as before; only the aliases above and `payload` extras are folded.
- Regex markers: paraphrase/obfuscation is not caught (unchanged).

---

## fix/bio-ci-kernel-snapshot — kernel score snapshot + bio tests in CI (**Reported**, PR open)

| Field | Value |
|-------|--------|
| Branch | `fix/bio-ci-kernel-snapshot` (off `main` @ `495b294`) |
| Kernel | `src/governed_stack/bio_semantic_overlay.py`: no longer byte-for-byte (was sha256 `aab42764…9c89`). Only change: `snapshot_scores` + `_score` merges from the snapshot |
| CI | `scripts/run_all_works_tests.py` (the `ci.yml` suite step) now also runs pytest over the 11 `tests/` files its unittest list never ran; `ci.yml` itself unchanged (historical: the runner was later rewritten to auto-discover tests, see [TESTING.md](../TESTING.md)) |
| Label | **Reported** until Damien reviews and merges |

### What changed

- **Raw-kernel TOCTOU closed.** `_score` used to call `validate_scores(scores)` (which itself read each label twice: `label in scores`, then `scores[label]`) and then re-read `scores[label]` to merge. A mapping returning valid values on the checked reads and NaN/negative on the merge read was `max()`-ed to 0.0, so a validated 0.9 came out ALLOW. A mapping returning NaN on the *first* read only also came out ALLOW (the `in` check consumed it). Now each label is read exactly once into a plain dict of floats, that snapshot is validated (finite, in [0, 1], all labels present, same error strings as before), and the merge uses only the snapshot. `validate_scores` delegates to the same snapshot. The raw kernel now matches the adapter.
- **Tests.** Strict xfail `test_i5_raw_kernel_rugpull_toctou_gap_documented` flipped to `test_i5_raw_kernel_closes_rugpull_toctou` (passing). New raw-kernel regressions: read-at-most-once counting, bad-first-read (NaN / -0.1 / 1.5 / inf) fails closed, flip-after-first-read decides on the snapshot, thresholds/tighten-only unchanged.
- **CI.** Runner phase 2 (pytest) for `test_audit_projection`, `test_bio_authority_jwt`, `test_bio_governance`, `test_bio_semantic`, `test_bio_semantic_judge`, `test_bio_semantic_overlay`, `test_governed_algorithm`, `test_lie_and_constraints`, `test_quantum_line`, `test_safety_sim`, `test_spectral_audit`.

### Accepted residuals

- GitHub Actions still blocked remotely by the account billing lock; green is local only.
- `ruff` / `mypy` are unpinned in the dev extras.
- `decide()` is still a pure function over an already-validated mapping; external callers that validate and then call `decide` on a live mapping must snapshot first (use `snapshot_scores`).
- `hais/hais_tests`, `haven2/tests`, `imprint/tests` are not in CI (out of scope; `hais/hais_tests/test_zk_proofs.py` has a broken import). **Superseded:** #25 fixed the import and the runner now runs all three folders (`EXTRA_PYTEST_DIRS`).

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

- **Raw-kernel TOCTOU (adapter closes it; kernel not modified). Closed in the kernel by `fix/bio-ci-kernel-snapshot`.** `_score` validates a mapping and then re-reads it. If the re-read gives NaN or a negative number, `max()` turns it into 0.0, so a validated 0.9 can come out as ALLOW. Tracked by the strict-xfail test `test_i5_raw_kernel_rugpull_toctou_gap_documented`. Suggested kernel fix: snapshot `scores = dict(scores)` before `validate_scores`.
- The stub is still phrase-heuristic. A benign cue (e.g. "published paper") checked first can mask a later harm cue (PR #19 residual, unchanged).
- Judgement rows use a non-decision label (not counted by `storage.stats()`). Audit consumers must look for `BIO_SEMANTIC_JUDGEMENT`.
- Running classifier threads cannot be cancelled on timeout. The kernel's bounded pool fails closed (REVIEW) once it is wedged.

---

## feat/tool-agent-govern-0.7 — tool/agent govern 0.7.0 (open — source pass)

| Field | Value |
|-------|--------|
| Branch | `feat/tool-agent-govern-0.7` |
| Depends on | #19 semantic stub on `main` |
| Synced with `main` | merge of `origin/main` @ `495b294` (#21 ruff I001, #22 bio semantic judge); bio channel still routes through `govern_bio_request` |
| Module | `src/governed_stack/tool.py`, connectors `bus_tool_side_effect` |

### What this adds

- `GovernedTool` — `check` / `require_allow`; envelope echoes `action`, `intent`, `intent_sha256`
- Bus `tool` channel delegates to `GovernedTool` (same envelope)
- `ToolInvoker` + `MockToolInvoker` + `bus_tool_side_effect(invoker)` — connector only; sha mismatch → `ContentBindingError`
- `AGENT_TOOL.md` + mandates 0.7

### Accepted residuals

- AST cannot prove every external agent binary uses the bus (mandate residual)
- No live MCP tool SDK in-repo (mocks + Protocol, same as 0.6.0 connectors)

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
