# Testing

One command runs every test file (`tests/test_*.py`, auto-discovered) and prints a per-area summary:

```bash
python -m venv .venv && .venv/bin/pip install -e '.[dev,mcp]'
.venv/bin/python scripts/run_all_works_tests.py
```

No network and no real sends: stacks use temp audit DBs, mail goes to fake/spool backends.
`mcp` is optional; without it the MCP server tests skip (the rest still run).

## Per area

```bash
.venv/bin/python scripts/run_all_works_tests.py --list            # file -> area map
.venv/bin/python scripts/run_all_works_tests.py --area sealed_mail
.venv/bin/python scripts/run_all_works_tests.py --area mail --area calendar
.venv/bin/python scripts/run_all_works_tests.py -- -x -k review   # extra pytest args after --
```

| area | covers |
|---|---|
| governance_gate | GovernedStack, contracts/intents, key providers, sidecar, runtime bridge, algorithm gate |
| mail | GovernedMail |
| sealed_mail | drafts-only SealedMailAdapter (#23) + `sealed_mail_mcp` server |
| calendar | GovernedCalendar |
| social_posts | GovernedSocial |
| tool_agent_0.7 | GovernedTool, action bus, connectors, no-bypass lint (#20) |
| bio_judge_kernel_overlay | bio policy, semantic judge, overlay kernel, bio authority JWT |
| audit | audit projection/soak, HAIS review flag, observability, review ops/path, spectral audit |
| control_loop_qp | CLF/CBF/QP controller, plant, simulator, OSQP engines, invariants |
| unified_all_works | unified catalog, all-works smoke, quantum line |

Areas are derived from file names (`AREAS` in the script); a new file that matches nothing shows up as `other`, never silently skipped.
Plain `pytest` from the repo root also runs everything (paths come from `pyproject.toml`).
