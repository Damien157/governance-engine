# Sealed mail connector (prototype, drafts only)

Branch: `feat/sealed-mail-connector` (local prototype off `main` 0.6.2).

## What it is

`governed_stack.sealed_mail.SealedMailAdapter` is a mail adapter whose **only**
operation is `propose_draft(to, subject, body, cc=None)` (plus a sync wrapper).
There is no send, reply or forward operation.

Every call goes through, in order:

1. **Recipient allowlist** (optional, set by the operator). Recipients outside it → `BLOCK`.
2. **Bio seal** (runs before the mail gate, can only tighten):
   - A subject or body that is a JSON object is checked with `SidecarService._bio_shaped_probe`
     (the same structural probe that seals `channel=raw`) → `BLOCK`.
   - Free text is checked with `classify_bio_with_semantic` (structural phrases + the 0.6.2
     semantic stub) → `BLOCK` / `REVIEW`.
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

## Residuals

- The bio seal inherits the limits of the stub scorer: it matches patterns, not meaning.
- JSON probing only looks at a subject or body that is *entirely* a JSON object.
- A bio-sealed decision short-circuits before `govern()`, so it writes no mail audit entry.
- The mail gate does not scan recipients. Use `recipient_allowlist` to limit routing.
