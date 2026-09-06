# Agent rules — calendar write

Hard gate. Do not bypass.

1. **Never** call Google Calendar `create_event`, `update_event`, `insert`, or similar write tools until `GovernedCalendar.check` (or `require_allow`) returns **ALLOW**.
2. On **BLOCK** — refuse. Do not create, update, or rewrite around the policy. Tell the user the decision and reasons.
3. On **REVIEW** — escalate to a human. Do not write. Use the review queue / human approval path.
4. Prefer `GovernedCalendar.require_allow(...)` in code paths so a non-ALLOW raises `SendBlocked` / `CalendarBlocked` (`PermissionError`) and cannot be ignored via `ok`.
5. Attendees, start, and end are intentionally kept out of the scanned intent; summary + description + location are what policy sees. Do not smuggle PII into the description to “hide” it in attendee fields — routing/scheduling metadata is not a loophole for write.
6. Box CLI stub (`scripts/governed_calendar.py`) only gates / prints the decision. It does **not** call the Calendar API or any connector. Actual create stays with the agent’s Calendar MCP **after** ALLOW.
7. Do not weaken PolicyEngine (e.g. allowing emails through) to force a write. HAIS m=0.5 stays untouched.

CLI check:

```bash
.venv/bin/python scripts/governed_calendar.py \
  --summary "Team sync" \
  --description "Weekly project status update" \
  --attendee alice@example.com \
  --start 2026-09-08T10:00:00 \
  --end 2026-09-08T11:00:00
```
