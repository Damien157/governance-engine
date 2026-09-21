# Agent rules — tool / agent runtime calls (0.7)

Hard gate. Do not bypass.

1. **Never** invoke a real tool / MCP write / agent side effect until
   `GovernedTool.require_allow` (or `GovernedActionBus.execute("tool", ...)`)
   returns **ALLOW**.
2. On **BLOCK** — refuse. Do not call the tool, rewrite the intent to dodge, or
   soft-ignore `ok`.
3. On **REVIEW** — escalate. Do not invoke. Use human review / voucher path
   where applicable.
4. Prefer `GovernedActionBus.execute("tool", intent=..., side_effect=bus_tool_side_effect(invoker))`
   so non-ALLOW raises `SendBlocked` / `ActionDenied` and the invoker only sees
   the **approved** envelope (`action`, `intent`, `intent_sha256`).
5. Do not close over a different intent in the side_effect — factories take the
   connector only; sha mismatch refuses the call.
6. Sidecar stays **check-only** — no remote `/v1/execute` for arbitrary tools.
7. Name + job: every agent declares what tools it may run alone vs escalate
   (see `docs/AGENT_MANDATES.md`).

Accepted residual: AST lint cannot prove every external agent host is wired;
mandate + bus path is the enforceable contract inside this package.
