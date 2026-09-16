# Full governance system — roadmap

Milestone **0.6.1**: **Content-binding seal** (approved content on envelope;
factories reject closed-over gated kwargs). Builds on **0.6.0** live-connector
enforcement + no-bypass lint + bio on action bus.

HAIS SovereignKernel remains **m=0.5**. Sketches stay **off** `govern()`.

## Done

1. **Action bus (0.5.0)** — `GovernedActionBus.execute(channel, *, side_effect=...)`
   runs channel `require_allow` / tool `govern` first; **only ALLOW** invokes
   `side_effect`. BLOCK/REVIEW raise `SendBlocked` (`ActionDenied`). Channels:
   `mail` | `calendar` | `social` | `tool`. Sidecar stays **check-only**
   (`POST /v1/check`); `POST /v1/execute` is refused — no remote arbitrary
   side effects. Library bus only for mutations.

2. **Live connector enforcement (0.6.0 — done)** — Protocols + mocks +
   `bus_*_side_effect` factories in `governed_stack.connectors`; real SDK/MCP
   writes allowed **only** inside bus side_effects. Agent constitution:
   [`AGENT_MANDATES.md`](AGENT_MANDATES.md). CI: `scripts/lint_no_bypass.py`
   (AST/scan) wired in `ci_local.sh` and `.github/workflows/ci.yml`. Bio channel
   on the bus (`bio`) via `GovernedBio.require_allow`; side_effect =
   metadata/ticket mock only (no wet-lab / sequences / protocols).

2a. **Content-binding seal (0.6.1 — done)** — Gate envelopes echo exact approved
   content; `bus_*_side_effect(connector)` only (no `body=`/`text=` close-over);
   `ContentBindingError` / `assert_bound_content` for unbound envelopes.

## Remaining (toward “full”)

3. **CI gates (partially done)** — ruff/mypy + no-bypass lint + bus/bio coverage
   in the all-works suite. Optional: import-linter / stronger symbol graph that
   outbound send symbols are only reachable from bus side_effects.

4. **Tool / agent govern** — expand `tool` channel + agent runtimes so every
   tool call is an intent through the bus (no “check optional, act anyway”);
   wire agent hosts to `AGENT_MANDATES.md` by default.

5. **Hard tenancy / KMS** — beyond multi-tenant lite: real KMS/HSM providers,
   stronger isolation, quotas, and operator SSO (not claimed today).

## Honest non-goals (still)

- Not a full SaaS product, SLA, or formal CA.
- Not sketches on the decision path.
- Sidecar never becomes a remote code-exec / send proxy.
- Bio never executes wet-lab work or returns protocols/sequences.
