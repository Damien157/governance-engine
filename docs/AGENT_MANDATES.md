# Agent mandates / constitution (0.6.0)

Every bot, channel adapter, and automation that touches outbound side effects
must obey this constitution. **No bypass.**

## Hard rules (all channels)

1. **Name + job** — every agent/bot declares: short name, one-line job, what it
   may do alone, and what must escalate to a human.
2. **Bus or Governed\* adapters** — mail, calendar, social, bio, and tool
   mutations go through `GovernedActionBus.execute(...)` (with a `side_effect`)
   or the channel `GovernedMail` / `GovernedCalendar` / `GovernedPost` /
   `GovernedBio` / tool `require_allow` / `govern` path. Check-only sidecar
   (`POST /v1/check`) never becomes remote execute.
3. **Side effects only after ALLOW** — real connector SDKs / MCP write tools
   run **only** inside bus `side_effect` callbacks (see
   `governed_stack.connectors`). Never call them after a soft `ok` glance.
4. **BLOCK / REVIEW** — raise / refuse (`ActionDenied` / `SendBlocked`). Do not
   send, publish, write calendar, or pretend bio execution. Escalate REVIEW.
5. **No-bypass lint** — `scripts/lint_no_bypass.py` fails CI if forbidden
   connector patterns appear outside allowlisted paths (`tests/`,
   `connectors.py`, `action_bus.py`, docs).
6. **Policy only tightens** — overlays (including bio) never loosen a HARD
   BLOCK; vouchers cannot override hard-block classes.
7. **HAIS m=0.5** — sketches stay off `govern()`.

## Channel mandates

| Channel | Alone (after ALLOW) | Escalates |
|---------|---------------------|-----------|
| **mail** | Send via bus `side_effect` + `MailSender` (mock in tests) | BLOCK/REVIEW → human; never Gmail outside bus |
| **calendar** | Create/update via `CalendarWriter` side_effect | BLOCK/REVIEW → human |
| **social** | Publish via `SocialPublisher` side_effect | BLOCK/REVIEW → human |
| **tool** | Intent through bus `tool` channel; side_effect only on ALLOW | Invalid / BLOCK / REVIEW → refuse |
| **bio** | Metadata / ticket logging mock only — **no** wet-lab, sequences, protocols, `/v1/execute` | BLOCK → refuse; REVIEW → human `resolve_review` then re-check with voucher (HARD BLOCK still blocked) |

## Bio REVIEW resolve contract

1. `GovernedBio.check` / bus `bio` → `REVIEW` enqueues overlay review when needed.
2. Human calls `GovernedBio.resolve_review(entry_id, approve=..., ...)`.
3. On approve, caller **re-checks** the same intent with the voucher.
4. Voucher may honor REVIEW→ALLOW for reviewable classes only; **HARD BLOCK**
   classes (`pathogen_work`, `enhancement`, `gain_of_function`, …) stay blocked.
5. Bio `side_effect` on ALLOW is **metadata / ticket create mock only** — never
   protocols, sequences, or lab execution.

## Naming template (copy per bot)

```
Name: <bot>
Job: <one line>
Alone: <actions after ALLOW via bus>
Escalate: <BLOCK/REVIEW/human>
Bypass: forbidden — connectors only in side_effects
```

## Related

- Per-channel agent notes: `src/governed_stack/AGENT_MAIL.md`,
  `AGENT_CALENDAR.md`, `AGENT_SOCIAL.md`, `AGENT_ALGORITHM.md`
- Roadmap: [`FULL_GOVERNANCE_ROADMAP.md`](FULL_GOVERNANCE_ROADMAP.md)
- Bio OS: [`BIO_GOVERNANCE_OS.md`](BIO_GOVERNANCE_OS.md)
