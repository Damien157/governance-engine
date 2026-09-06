# Agent rules — outbound Gmail / mail

Hard gate. Do not bypass.

1. **Never** call Gmail `send_message`, `reply`, `forward`, or `create_draft` until `GovernedMail.check` (or `require_allow`) returns **ALLOW**.
2. On **BLOCK** — refuse. Do not send, draft, or rewrite around the policy. Tell the user the decision and reasons.
3. On **REVIEW** — escalate to a human. Do not send. Use the review queue / human approval path.
4. Prefer `GovernedMail.require_allow(...)` in code paths so a non-ALLOW raises `SendBlocked` (`PermissionError`) and cannot be ignored via `ok`.
5. Recipients (`To`/`Cc`) are intentionally kept out of the scanned intent; subject + body are what policy sees. Do not smuggle PII into the body to “hide” it in routing fields — routing is not a loophole for send.
6. Box CLI stubs (`scripts/governed_mail.py`, `scripts/send_governed_mail.py`) only gate / print “would send”. They do **not** call the Gmail API. Actual send stays with the agent’s Gmail MCP **after** ALLOW.
7. Do not weaken PolicyEngine (e.g. allowing emails through) to force a send.

CLI check:

```bash
.venv/bin/python scripts/governed_mail.py --to alice@example.com --subject "Lunch" --body "Are you free tomorrow?"
```

Stub that only prints after ALLOW:

```bash
.venv/bin/python scripts/send_governed_mail.py --to alice@example.com --subject "Lunch" --body "Are you free tomorrow?"
```
