# Agent rules — outbound social / post

Hard gate. Do not bypass.

1. **Never** call LinkedIn, X/Twitter, or other social `create_post`, `publish`, `share`, or similar write tools until `GovernedPost.check` (or `require_allow`) returns **ALLOW**.
2. On **BLOCK** — refuse. Do not publish, draft around the policy, or smuggle content via another channel. Tell the user the decision and reasons.
3. On **REVIEW** — escalate to a human. Do not publish. Use the review queue / human approval path.
4. Prefer `GovernedPost.require_allow(...)` in code paths so a non-ALLOW raises `PostBlocked` / `SendBlocked` (`PermissionError`) and cannot be ignored via `ok`.
5. Recipients and URLs are intentionally kept out of the scanned intent; optional platform tag (`subject`) + body (`text`) are what policy sees. Do not smuggle PII into the body to “hide” it in URL/recipient fields — routing metadata is not a loophole for publish.
6. Box CLI stub (`scripts/governed_post.py`) only gates / prints the decision. It does **not** call any social API.
7. Official **X** connector (`user-X`, `https://api.x.com/mcp`) is **read / search / bookmarks** oriented (scopes include `tweet.read`, not `tweet.write`). Gate post *content* with `GovernedPost` anyway; do not claim X MCP can publish tweets unless a write tool appears.
8. No official LinkedIn connector in the catalog; when one appears, publish stays with that connector **after** ALLOW.
9. Do not weaken PolicyEngine to force a publish. HAIS m=0.5 stays untouched.

CLI check:

```bash
.venv/bin/python scripts/governed_post.py \
  --platform linkedin \
  --body "Excited to share a short update on our governance stack."
```
