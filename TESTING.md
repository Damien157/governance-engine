# Testing

One command runs every test file (`tests/test_*.py`, auto-discovered, via pytest) plus the sub-project folders `haven2/tests`, `imprint/tests` and `hais/hais_tests` (each its own pytest invocation), and prints a per-area summary:

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
| tool_agent_0.7 | GovernedTool (incl. default-policy hardening tests), action bus, connectors, no-bypass lint (#20) |
| bio_judge_kernel_overlay | bio policy, semantic judge, overlay kernel, bio authority JWT |
| audit | audit projection/soak, HAIS review flag, observability, review ops/path, spectral audit |
| control_loop_qp | CLF/CBF/QP controller, plant, simulator, OSQP engines, invariants |
| unified_all_works | unified catalog, all-works smoke, quantum line |
| haven2 / imprint / hais | sub-project test folders (`EXTRA_PYTEST_DIRS`); `hais_tests` ZK slow tests skip unless `ZK_RUN_SLOW=1` |

Areas are derived from file names (`AREAS` in the script). **Coverage guard:** a `tests/test_*.py` that matches no area is a coverage error: the runner exits 2 before running anything, and `tests/test_runner_coverage.py` (#25) fails with the same message. A test file therefore cannot be silently left out; add a pattern to `AREAS` when you add a file that does not fit an existing area.
Plain `pytest` from the repo root also runs everything (paths come from `pyproject.toml`).
