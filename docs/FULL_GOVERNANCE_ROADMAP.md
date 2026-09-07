# Full governance system — roadmap

Milestone **0.5.0**: **Governed action bus — no bypass**.

HAIS SovereignKernel remains **m=0.5**. Sketches stay **off** `govern()`.

## Done

1. **Action bus (0.5.0)** — `GovernedActionBus.execute(channel, *, side_effect=...)`
   runs channel `require_allow` / tool `govern` first; **only ALLOW** invokes
   `side_effect`. BLOCK/REVIEW raise `SendBlocked` (`ActionDenied`). Channels:
   `mail` | `calendar` | `social` | `tool`. Sidecar stays **check-only**
   (`POST /v1/check`); `POST /v1/execute` is refused — no remote arbitrary
   side effects. Library bus only for mutations.

## Next (toward “full”)

2. **Live connector enforcement** — wire real Gmail / Calendar / social MCP
   or SDK calls *only* as `side_effect` callbacks registered through the bus;
   agent rules + CI lint that forbid direct connector sends.

3. **CI gates** — keep ruff/mypy on `src/governed_stack`; add bus coverage to
   the all-works suite; optional import-linter / AST check that outbound
   send symbols are only referenced from bus side_effects / tests.

4. **Tool / agent govern** — expand `tool` channel + agent runtimes so every
   tool call is an intent through the bus (no “check optional, act anyway”).

5. **Hard tenancy / KMS** — beyond multi-tenant lite: real KMS/HSM providers,
   stronger isolation, quotas, and operator SSO (not claimed today).

## Honest non-goals (still)

- Not a full SaaS product, SLA, or formal CA.
- Not sketches on the decision path.
- Sidecar never becomes a remote code-exec / send proxy.
