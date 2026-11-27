# Mail relay: sealed spool to a partner's Drafts folder (draft-only)

`governed_stack.mail_relay` moves drafts written by the sealed mail path
(`sealed_mail_mcp` -> `SealedMailAdapter` -> `spool_backend`) into a partner's
**Gmail or Outlook Drafts folder**. It never transmits mail and never reads the
inbox. A human opens the draft in their own mail client and decides whether to
press Send.

Modules: `src/governed_stack/mail_relay.py` (verification, allowlist, state,
audit) and `src/governed_stack/mail_relay_providers.py` (Gmail and Graph
clients, stdlib `urllib` only). Tests: `tests/test_mail_relay.py` (fakes only,
no network, no credentials).

## What one relay run does

Under an exclusive `flock` on `<spool>/.mail_relay.lock` (a second relay gets
`busy` and does nothing):

1. **Audit chain must verify.** `storage.verify_chain()["valid"]` must be true,
   or nothing is uploaded (`audit_chain_invalid`).
2. **Re-verify each spool file** (`verify_spool_record`). The spool format is
   `DraftPayload.as_dict()` from `sealed_mail.py`: `to, subject, body, cc,
   entry_id, content_sha256, decision, kind`. Refused unless:
   - the field set is exactly that (an extra `bcc` or anything else is refused);
   - `kind == "draft"` and `decision == "ALLOW"`;
   - `content_sha256` equals `sealed_mail.content_digest(to, subject, body, cc)`;
   - `connectors.assert_bound_content(..., "mail")` passes on the record;
   - the govern() row named by `entry_id` exists in the signed chain, has
     decision `ALLOW`, and its scanned intent is exactly this subject and body;
   - no sealed-mail denial row (for example a content-binding refusal) names
     that entry.
3. **Partner recipient allowlist.** Every to/cc/bcc must be on the partner's
   list (case-insensitive). An **empty allowlist refuses everything**.
   Subjects containing CR/LF are refused (header injection).
4. **Idempotency.** `<spool>/.mail_relay_state.json` keys uploads by
   `content_sha256`. The entry is written as `in_flight` and fsynced *before*
   the provider call, so a crash mid-upload is treated as "outcome unknown"
   and never retried blindly. A re-run never creates a duplicate.
5. **One provider call per draft per run.** No retries inside a run; the run
   stops at the first provider error (circuit breaker) and the rest are
   `not_attempted`.
6. **One signed audit row per attempt** (refusal, failure or upload) through
   `AuditStorage.log_decision`, `enqueue_review=False`, result
   `mail_relay:<status>`, decision `ALLOW` only for `uploaded`, else `BLOCK`.

`run()` never raises. It is not called from `govern()` and cannot change any
gate decision; it can only refuse.

## Provider outcomes

| Provider result | Status | Retried on a later run? |
|---|---|---|
| 2xx with id | `uploaded` | never (idempotent) |
| other 4xx | `rejected` | no |
| 408 / 429 | `retry_later` | yes, up to `max_attempts` (default 3), then `failed_permanent` |
| token missing, Graph token scope refused, connection refused / DNS failure | `retry_later` | yes (nothing reached the provider) |
| 5xx, timeout, reset, 2xx without id, unknown client exception | `outcome_unknown` | **no**: the draft may exist; an operator checks Drafts |

Errors are stored only as `{error_class, error_sha256, error_len}`.

## What is audited (and what is not)

Relay rows carry: partner id, provider name, `subject_sha256`, `body_sha256`,
recipient **count**, status, reason codes, attempt number, error digest,
SHA-256 of the provider's draft id, and the govern() `entry_id`.

They never carry: recipient addresses or a recipient hash, the body, the
subject, the bearer token, or raw provider/exception text. Logs carry status,
reason codes and hash prefixes only. `tests/test_mail_relay.py::TestSecrets`
checks this with marker strings.

## Scopes

| Provider | Call | Scope / permission the partner grants |
|---|---|---|
| Gmail | `POST https://gmail.googleapis.com/gmail/v1/users/me/drafts` (`users.drafts.create`) | `https://www.googleapis.com/auth/gmail.compose` (at most) |
| Outlook | `POST https://graph.microsoft.com/v1.0/me/messages` (created in Drafts) | `Mail.ReadWrite` (delegated). **Do not grant** the Graph transmit permission. |

The HTTP layer issues only `POST`, only to those two URLs
(`ALLOWED_ENDPOINTS`), and refuses redirects so the token is never forwarded.
For Graph, if the token is a readable JWT, any permission outside
`Mail.ReadWrite, User.Read, openid, profile, email, offline_access` (or any
app-only role) is refused before a request is made
(`GraphDraftsClient(require_readable_scopes=True)` also refuses opaque tokens).

**Send ban lint.** `scripts/lint_no_bypass.py` scans `mail_relay*.py` in full
(comments included) and fails CI on transmit endpoints (`/send`, `.send`,
the Graph transmit endpoint), transmit scopes, Gmail scopes other than
`gmail.compose`, read-only mail scopes, non-POST methods, reply/forward
endpoints, and any name like `send`/`send_draft` in the AST.

## Threat model

Protects against:
- **Tampered spool**: body/subject/recipient edits break `content_sha256`; a
  re-hashed edit fails the binding to the signed govern() row.
- **Forged spool** pointing at a BLOCK/REVIEW or missing row, or at an entry
  the adapter refused after ALLOW.
- **Recipient drift**: anything outside the partner's allowlist, including an
  injected `bcc` field.
- **Duplicate drafts** from re-runs, concurrent relays or a crash mid-upload.
- **Leaks** of token, body, recipients or error text into audit or logs.
- **Code drift toward sending**, via the lint.

Does **not** protect against:
- **An attacker with the audit signing key and DB write access**: they can
  forge an ALLOW row and a matching spool. The relay trusts the chain.
- **The token itself.** Google has no draft-only scope: `gmail.compose` also
  lets its holder transmit mail. `Mail.ReadWrite` lets its holder read and
  delete the mailbox. The relay code never does either, but anyone who
  steals the token can. Store it like a password.
- **An agent that still holds a raw Gmail/Outlook tool**: same limit as the
  sealed adapter; the partner's agent must only have `sealed_mail_mcp`.
- **The human pressing Send** on a draft. That is the intended last step.

## What it does NOT do

- No transmit, reply, forward, or draft-transmit call. No delete.
- No inbox reads, searches or listing (only `POST` to the create endpoints).
- No token minting, refresh, storage or OAuth flow: the partner supplies a
  short-lived access token.
- Does not delete or move spool files; the state file records what was done.

## Partner setup

1. Run the sealed path as today (`sealed_mail_mcp` with
   `SEALED_MAIL_OUTBOX` and `SEALED_MAIL_ALLOWLIST`).
2. Create an OAuth client in the partner's own Google Cloud project (scope
   `gmail.compose` only) or Entra app registration (delegated
   `Mail.ReadWrite` only). The partner owns the client and consent.
3. Obtain an access token by the partner's own means and expose it to the
   relay process only: `HAVEN_RELAY_GMAIL_TOKEN` or `HAVEN_RELAY_GRAPH_TOKEN`.
   A different source can be injected as any `() -> str` token provider.
4. Set the partner recipient allowlist, e.g.
   `HAVEN_RELAY_ALLOWLIST=alice@partner.example,bob@partner.example`.
5. Run, in the same process as the stack that owns the audit DB (preferred:
   construct `MailRelay(config=..., provider=..., storage=stack.engine.storage)`)
   or, with the MCP server stopped:

   ```
   python -m governed_stack.mail_relay --spool artifacts/sealed_mail/outbox \
       --partner pilot-1 --provider gmail
   ```

6. Check the JSON summary. `outcome_unknown` means look in Drafts before
   clearing that entry from `.mail_relay_state.json`.

## Limits (honest)

- **Single audit writer.** `AuditStorage` keeps the chain tip in memory per
  process. Two processes writing the same audit DB fork the chain (and
  `verify_chain` then fails, which also stops the relay). Run the relay in the
  stack's process, or while the MCP server is stopped.
- **On main, the govern() row records the CGE decision**, not the stack's
  final decision (that is PR #36). A stack-level tightening after a CGE ALLOW
  is visible to the relay only once #36 is merged; until then the relay
  relies on the sealed adapter not spooling non-ALLOW drafts.
- `verify_chain()` runs over the full chain each run (one RSA verify per row).
- Refusals are terminal per spool file: changing the allowlist does not
  re-try an earlier refusal until its entry is removed from the state file.
- POSIX only (`fcntl.flock`); on other platforms `run()` returns
  `relay_error` and uploads nothing.
- One audit row is written *after* each provider call. If that write fails
  after a successful upload, the draft exists unaudited: the run stops, the
  state records `uploaded` and the summary reports `audit_write_failed`.
- Tested only against fakes. No live Gmail or Graph call has been made.
