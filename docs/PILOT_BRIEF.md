# Design-partner pilot brief — governance-engine sealed connectors

**Pitch.** Governance-engine is a check gate for agent actions: purpose → cost → risk → authority → audit, with content-bound ALLOW envelopes so a later swap cannot ride an earlier approval. The highest-leverage next step is not more features — it is one design partner running real traffic through a sealed connector path.

---

## Who this is for

One operator (founder, lab ops lead, or small-team COO) who already uses an AI assistant for **outbound email or calendar**, is tired of soft “please don’t do bad things” prompts, and can spare two weeks of real use plus a short weekly call. Not a security theatre buyer; someone who will notice when a gate blocks or asks for REVIEW.

## Two-week scope

**Primary channel: outbound mail** (recommended).

Mail is the fastest way to hit content-binding, intent hashing, and human REVIEW in production-shaped traffic. Calendar is the clean alternate if mail access is politically harder; tool/agent govern (0.7) is better as week-2 watch-only once mail is green.

**Mail path the partner runs (name it in the kickoff checklist)**

- **Path:** the sealed-mail connector only — `governed_stack.sealed_mail_mcp`
  (stdio MCP server, one tool, `propose_draft`) → `SealedMailAdapter`
  (`src/governed_stack/sealed_mail.py`: recipient allowlist → bio seal →
  `GovernedMail` gate → draft built only from the ALLOW envelope). See
  [`SEALED_MAIL.md`](SEALED_MAIL.md).
- **Not** plain `GovernedMail` (`src/governed_stack/mail.py`) on its own: that
  gate runs no bio check on mail text. The bio seal exists only on the sealed path.
- **Only mail tool:** the partner's assistant must have `sealed_mail_mcp` as its
  only mail tool. If it also holds a raw Gmail/Outlook tool (`create_draft`,
  `send_message`, ...), it can skip the gate; the seal is not enforced in that
  setup.
- **Draft backend:** the repo ships a spool backend (`spool_backend`, local JSON
  handoff files with `content_sha256`, no credentials). Turning a spooled draft
  into a draft in the partner's mailbox (Gmail, Outlook or other) is done by a
  host process that holds the partner's credentials; that piece is not in this
  repo and must be named and agreed per partner before kickoff, along with the
  mail provider and the recipient allowlist (`SEALED_MAIL_ALLOWLIST`).

**In scope**

- Partner’s assistant creates mail drafts only through the governed sealed-mail connector (no side-door SMTP, and no send tool on that path).
- A person presses Send on each draft in the mail client; the connector does not send.
- ALLOW envelopes bind approved body/subject; mismatches refuse the draft write.
- Bio-shaped content hits the bio gate (structural + semantic stub on main); REVIEW stays human-resolvable where vouchers apply.
- Audit trail entries for ALLOW / REVIEW / BLOCK the partner can actually read.

**Out of scope for this pilot**

- Multi-tenant hard isolation / KMS.
- Live model-judge semantic overlay as default (stub stays default unless separately agreed).
- Social posting and unconstrained tool invoke as primary paths.

**Optional week-2 watch:** calendar creates/updates through the same pattern, read-only report — no requirement to “pass.”

## Success in two weeks (observable)

1. **At least one live loop:** partner-originated mail left the sealed path as an ALLOW’d draft, then a person pressed Send, with a matching audit row for the draft.
2. **At least one hard refusal or REVIEW:** a blocked or REVIEW’d attempt the partner agrees was correct (wrong recipient pattern, bio-shaped ask, content mismatch, or similar) — logged, not waved through.
3. **No known bypass used:** partner confirms in-scope assistant mail was drafted only through the sealed path (no side-door drafts or sends).
4. **One written note** from the partner: what felt right, what was annoying, what would block adoption.
5. **Grant-ready sentence:** Damien can name the partner role (or anonymised sector) and the channel in a pitch without inventing metrics.

## What we will not do

- Bypass the gate “just this once.”
- Reopen a **tool** BLOCK with a bio/mail voucher (tool path has no voucher reopen).
- Treat bio as execute/wet-lab: check-only; no sequences, protocols, or runbooks that enable harm.
- Honour an ALLOW if body/subject/intent no longer match the envelope (content-swap refuse).
- Invent factories that are not connector-shaped.
- Rubber-stamp merges or claim unverified test counts in the pilot write-up.

## What Damien needs from the partner

- Connector access to the mail account (or a dedicated pilot inbox) under the partner’s control.
- Willingness to route **in-scope** assistant mail only through the sealed path for two weeks.
- Two short syncs (kickoff + end) and async notes when something blocks oddly.
- Permission to cite “a design partner in [sector]” for grant materials; named logo optional and separate.

Volume: enough real messages that the gate sees routine work - not a synthetic flood and not a single demo draft.

## What the partner gets

- A sealed outbound path with an audit trail they can inspect.
- Clear refuse/REVIEW behaviour instead of silent model drift.
- Exit any time: turn off the connector; no lock-in clause for the pilot.
- First refusal rights on whether their name appears in external materials.

## First three outreach messages

**1 — cold / warm intro**

> I’m piloting a sealed connector for AI outbound mail: the assistant only drafts what a gate has ALLOW’d, with the approved text bound into the approval so it can’t be swapped later, and a person presses Send on each draft. Looking for one design partner for a two-week real-inbox run. If you already lean on an assistant for email and care when it goes wrong, are you open to a 20-minute call?

**2 — after interest**

> Scope is narrow on purpose: your (or a pilot) inbox, governed drafts only (you press Send), two weeks, one kickoff and one wrap call. Success is a real ALLOW’d draft that you then send, at least one correct BLOCK/REVIEW, and your honest notes - not a feature checklist. We will not bypass the gate or draft around the connector for in-scope mail.

**3 — scheduling close**

> If you’re in, I’ll send a one-pager (this brief), a connector setup checklist, and three proposed kickoff times. You keep the account keys; I need a yes on routing in-scope assistant drafts through the sealed path for fourteen days.

---

*Status note (27 Nov 2026, checked against git): main is `43712f9`, package version 0.7.0. Sealed drafts-only mail (#23), calendar, social, bio check-only and tool/agent (#20) are merged. The audit PRs #36, #38 and #39 are open and not part of the pilot baseline. The mail pilot does not depend on them.*
