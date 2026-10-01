# Sealed mail connector (prototype, drafts only)

Branch: `feat/sealed-mail-v2` (local prototype). It is `feat/sealed-mail-connector` @ `933fd05`
cherry-picked onto `feat/bio-semantic-judge-adapter` @ `a34f810`, plus the two v2 fixes below
and the v3 denial-audit follow-up (see "Denial audit").

## What it is

`governed_stack.sealed_mail.SealedMailAdapter` is a mail adapter whose **only**
operation is `propose_draft(to, subject, body, cc=None)` (plus a sync wrapper).
There is no send, reply or forward operation.

Every call goes through, in order:

1. **Recipient allowlist** (optional, set by the operator). Recipients outside it → `BLOCK`,
   audited (see "Denial audit").
2. **Bio seal** (runs before the mail gate, can only tighten):
   - A subject or body that is a JSON object is checked with `SidecarService._bio_shaped_probe`
     (the same structural probe that seals `channel=raw`) → `BLOCK`.
   - Free text is checked by the **same judge as `channel=bio`**: `bio_semantic_judge.judge_bio_request`
     on `BioRequest(purpose=subject, summary=body, domain="other", intervention_class="literature")`.
     That is structural `classify_bio` plus the overlay kernel, merged tighten-only, with the
     same default stub classifier and the same charter reason vocabulary. A judge error, timeout or
     malformed output gives `REVIEW`. Inject a judge with
     `SealedMailAdapter(..., bio_judge=BioSemanticJudge(...))`; the default is the stack default
     (`DEFAULT_JUDGE`). Mail reasons are codes only: `bio_policy:block_phrase:<phrase>` is cut
     to `bio_policy:block_phrase`.
   - A sealed `BLOCK` / `REVIEW` (JSON probe, judge, or seal exception) is **written to the
     signed audit chain before returning** (stage `bio_seal`, marker `SEALED_MAIL_BIO_SEAL`;
     see "Denial audit"). Metadata also holds `scorer`, `seal`
     (`raw_json_probe` | `semantic_judge`) and the judge's audit view (scores, `fail_reason`,
     text hash).
   - The plain mail gate ALLOWs text like "how to synthesize a toxin". The seal is what stops it.
3. **Mail gate**: `GovernedMail.check` → `GovernedStack.govern` (only subject and body are scanned).
4. `BLOCK` / `REVIEW` → the decision is returned and **nothing is written**. `govern()`'s own
   row is the audit record (`audit_entry_id == entry_id`). If `govern()` left no row
   (`entry_id` missing) or returned a non-BLOCK/REVIEW denial (mapped to `BLOCK`), the adapter
   writes a `mail_gate` row itself.
5. `ALLOW` → `assert_bound_content`, then a frozen `DraftPayload` is built **only from the ALLOW
   envelope** (`to` / `subject` / `body` / `cc`). If the envelope differs from the request or
   is malformed, a `content_binding` BLOCK row is audited, `ContentBindingError` is raised,
   and nothing is written.

## Denial audit

Every denial leaves one signed, hash-chained audit row (`storage.verify_chain()` stays valid):

| Path | Stage / marker (first `policy_reasons` entry) | Decision column | Writer |
|---|---|---|---|
| Recipient outside allowlist | `recipient_allowlist` / `SEALED_MAIL_ALLOWLIST_DENIAL` | `BLOCK` | adapter |
| JSON probe, judge BLOCK/REVIEW, seal exception | `bio_seal` / `SEALED_MAIL_BIO_SEAL` | `BLOCK` / `REVIEW` | adapter |
| Mail gate BLOCK / REVIEW | (normal `govern()` row) | `BLOCK` / `REVIEW` | `govern()` |
| Mail gate denial with no `govern()` row | `mail_gate` / `SEALED_MAIL_GATE_DENIAL` | `BLOCK` / `REVIEW` | adapter |
| Content-binding refusal after ALLOW | `content_binding` / `SEALED_MAIL_CONTENT_BINDING_REFUSAL` | `BLOCK` | adapter |

Adapter rows (`sealed_mail.record_denial`) have:

- `decision` = the real decision (`BLOCK` or `REVIEW`). `storage.stats()` counts them under
  `blocked`, and under the new additive `reviewed` count.
- `result=sealed_mail:<stage>`. `policy_reasons` = [stage marker] + reason codes (charter codes
  for the bio seal; `sealed_mail:recipient_not_allowlisted:<n>`;
  `sealed_mail:gate_decision:<D>`; `sealed_mail:content_binding_refused` +
  `sealed_mail:content_mismatch:<field>`).
- An intent of `action=sealed_mail_denial`, `channel=mail`, `stage`, `subject_sha256`,
  `body_sha256`, `recipients_sha256` (SHA-256 over sorted, lower-cased, de-duplicated
  to+cc) and `recipient_count`. Raw subject, body and addresses are never stored.
- Metadata repeating the digests, plus `error_code` and `governed_entry_id` (the linked
  `govern()` row, if one exists).

If the audit write fails, the denial still stands (`BLOCK` / `REVIEW`, reason
`sealed_mail:audit_write_failed`, `audit_entry_id=None`) and nothing is written.

**Review queue.** Adapter denial rows are written with
`AuditStorage.log_decision(..., enqueue_review=False)`, so a bio-sealed `REVIEW` is **never**
put in `review_queue`. The queue INSERT is skipped inside the same `log_decision` call, so
there is never a PENDING window to race.
`resolve_review` on it raises `ValueError` ("no review found"), so no voucher can be
minted for it. A reviewer would see only hashes, and the seal is a stop, not a question.
As defense in depth: if someone force-enqueues and approves such a row anyway, the voucher
is bound to the hash-only intent. `SealedMailAdapter` / `GovernedMail.check` accept no
voucher, and the seal re-runs on every call, so the same mail stays sealed and no draft is
written. `enqueue_review` defaults to `True`, so all other callers behave as before.
Mail-gate `REVIEW` rows written by `govern()` are still enqueued (pre-existing behavior).

Input errors (non-string subject/body, no `to` recipient) raise `TypeError` before any
decision is made. They are caller errors and are not audited. A draft-backend failure after
`ALLOW` is not a denial; its `govern()` ALLOW row exists.

The draft write goes to an **injected backend** (`Callable[[DraftPayload], Any]`, sync or
async). The code holds no credentials. `spool_backend(dir)` writes JSON handoff files that
carry a `content_sha256`.

`governed_stack.sealed_mail_mcp` is an optional stdio MCP server (`pip install -e '.[mcp]'`,
mcp>=2) that exposes exactly one tool, `propose_draft`.

## Enforcement limit (read this)

This prototype **does not stop an agent from bypassing it**. An agent that still has a raw
Gmail tool (`create_draft`, `send_message`, ...) can skip the adapter completely. The seal is
only real when the agent's **only** mail tool is the gated one. For example, register
`sealed_mail_mcp` *instead of* the raw Gmail connector, with the Gmail credentials held by a
backend the agent cannot reach. Python privacy (`__slots__`, name mangling) is not a security
boundary either.

In the live demo on 2026-09-29, the gated payload was spooled first. The agent then passed it
unchanged to its Gmail connector. That handoff depended on the agent following the rules;
nothing enforced it.

## Known gaps / residuals

- The bio seal inherits the limits of the stub scorer: it matches patterns, not meaning. A
  benign cue checked first can mask a harm cue. Inject a model judge to improve this.
- JSON probing only looks at a subject or body that is *entirely* a JSON object.
- ~~A bio-sealed decision short-circuits before `govern()`, so it writes no mail audit entry.~~
  Fixed in v2/v3: every denial has a signed row with the real decision. Adapter rows are not
  `govern()` rows, so they carry no HAIS / Haven2 telemetry.
- `enqueue_review` was added to `certified_governance_unified.AuditStorage` only. The
  `hais/certified_governance.py` copy was not changed.
- SHA-256 of a short or guessable subject/body can be confirmed by dictionary guessing. The
  hashes support correlation, not confidentiality.
- ~~The `recipient_allowlist` BLOCK is not audited.~~ Fixed in v3.
- Rows from the `govern()` mail gate store the PII-redacted intent (subject/body), not hashes.
  This is existing `govern()` behavior.
- If the audit write fails, the decision stays non-ALLOW but leaves no durable trace (the
  reason is in the returned result only).
- The mail gate does not scan recipients. Use `recipient_allowlist` to limit routing.
