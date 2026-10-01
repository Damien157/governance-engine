# Sealed mail connector (prototype, drafts only)

Branch: `feat/sealed-mail-v2` (local prototype). It is `feat/sealed-mail-connector` @ `933fd05`
cherry-picked onto `feat/bio-semantic-judge-adapter` @ `a34f810`, plus the two v2 fixes below.

## What it is

`governed_stack.sealed_mail.SealedMailAdapter` is a mail adapter whose **only**
operation is `propose_draft(to, subject, body, cc=None)` (plus a sync wrapper).
There is no send, reply or forward operation.

Every call goes through, in order:

1. **Recipient allowlist** (optional, set by the operator). Recipients outside it → `BLOCK`.
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
   - A sealed `BLOCK` / `REVIEW` (JSON probe or judge) is **written to the signed audit chain
     before returning**, as its own row. Fields: `decision=SEALED_MAIL_BIO_SEAL`,
     `result=sealed_mail:<BLOCK|REVIEW>`; intent holds `channel=mail`, `subject_sha256`,
     `body_sha256`, `recipient_count`; metadata holds the decision, reason codes, `scorer`,
     `seal` (`raw_json_probe` | `semantic_judge`) and the judge's audit view (scores,
     `fail_reason`, text hash). Raw subject, body and recipients are never stored. If the
     audit write fails, the result stays `BLOCK` / `REVIEW` (reason
     `sealed_mail:audit_write_failed`) and nothing is written.
   - The plain mail gate ALLOWs text like "how to synthesize a toxin". The seal is what stops it.
3. **Mail gate**: `GovernedMail.check` → `GovernedStack.govern` (only subject and body are scanned).
4. `BLOCK` / `REVIEW` → the decision is returned and **nothing is written**.
5. `ALLOW` → `assert_bound_content`, then a frozen `DraftPayload` is built **only from the ALLOW
   envelope** (`to` / `subject` / `body` / `cc`). If the envelope differs from the request, it raises
   `ContentBindingError` and nothing is written.

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
  Fixed in v2: sealed decisions get their own signed audit row. It is still not a `govern()` row,
  so it carries no HAIS / Haven2 telemetry, and it does **not** enter the human review queue: a
  bio-sealed REVIEW is a stop, not a reviewable item. Audit consumers must also query
  `decision=SEALED_MAIL_BIO_SEAL` (`storage.stats()` does not count it).
- SHA-256 of a short or guessable subject/body can be confirmed by dictionary guessing. The
  hashes support correlation, not confidentiality.
- The `recipient_allowlist` BLOCK still returns before `govern()` and is **not** audited.
- If the audit write fails, the decision stays non-ALLOW but leaves no durable trace (the
  reason is in the returned result only).
- The mail gate does not scan recipients. Use `recipient_allowlist` to limit routing.
