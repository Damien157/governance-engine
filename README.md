# Governed Stack — one front door

A **governed request stack** that composes Damien O’Driscoll’s existing packages without rewriting them.

**Package version:** `0.4.3` (customer-ops milestone; CI gates **ruff** + **mypy** on `src/governed_stack`).

**Front door:** `HavenUnified` (alias `GovernedUnified`) → `GovernedStack.govern(intent, token)` → mail/calendar/social adapters.

This is **not** a P vs NP proof, a theory of everything, AGI, a formal certificate authority product, or a quantum / DNA computer. Imprint’s “superposition” is a weighted classical draw over matchings; Haven2’s “transistor” is a discrete latch on energy residual; HAIS caps capability from telemetry.

## Three tiers

| Tier | Module | Path | On decision path? |
|------|--------|------|-------------------|
| **live** | `GovernedStack` | `src/governed_stack/stack.py` | **Yes** — ops → HAIS → Haven2 → optional QP/3DM |
| **live** | `GovernedMail` | `src/governed_stack/mail.py` | **Yes** — outbound mail gate |
| **live** | `GovernedCalendar` | `src/governed_stack/calendar.py` | **Yes** — calendar write gate |
| **live** | `GovernedPost` | `src/governed_stack/social.py` | **Yes** — outbound social/post gate |
| **live** | `HavenUnified` | `src/governed_stack/unified.py` | **Yes** — thin facade |
| **live** | `GovernedDecisionEngine` | `src/governed_stack/runtime_bridge.py` | **Yes** — decide adapter (constitution/halt → govern) |
| **core** | `ZKEnhancedGovernanceEngine` (unified v1.1) | `certified_governance_unified.py` | Yes (preferred ops layer; `crypto=`) |
| **core** | `CertifiedGovernanceEngine` (+ optional ZK) | `hais/certified_governance.py`, `hais/zk_enhanced_governance.py` | Fallback ops layer |
| **core** | `SovereignKernel` | `hais/hais_unified_kernel.py` | Yes — sigmoid **m=0.5**, cap `exp(-2.2·S·τ)` |
| **core** | `Haven2Engine` | `haven2/src/haven2` | Yes — energy + latch |
| **core** | `CLFCBFQPController` | `src/governance_engine` | Yes — only when `action=control` and ALLOW |
| **core** | imprint 3DM | `imprint/src/imprint` | Yes — only when `action=3dm` and ALLOW |
| **sketch** | `HAIS3SATSolver` | `solvers/hais_3sat.py` | **No** |
| **sketch** | invariant topology diagnostics | `diagnostics/invariant_topology.py` | **No** |
| **sketch** | unified control sketch (incomplete) | `solvers/unified_control_sketch.py` | **No** — does not replace HOCBF |
| **sketch** | Taylor–Green fluids | `fluids/hais_taylor_green.py` | **No** |
| **sketch** | TG Dedalus smoke | `fluids/smoke_taylor_green.py` | **No** — smoke exists; full TG not default |
| **sketch** | `HavenSovereignEngine` | `haven/haven_sovereign_engine.py` | **No** |
| **sketch** | production governed pipeline | `hais/hais_production_governed_pipeline.py` | **No** — random candidates, not real 3DM; own kernel copy (m=0.5); does **not** overwrite `hais_unified_kernel.py` |
| **sketch** | GovernanceKernel runtime v0.2 | `hais/hais_governance_unified_runtime_v02.py` | **No** — teaching kernel; not live gate |

Sketches stay **importable** via `HavenUnified` helpers (`run_3sat_demo`, `run_topology_diagnostics`, `run_control_sketch`, `haven_sovereign_demo`, `fluids_smoke`, `run_production_pipeline_sketch`) but are **never** consulted for ALLOW / BLOCK / REVIEW.

Inventory helpers: `governed_stack.catalog.TIERS`, `describe()`, `import_check()`.

## Live pipeline

1. **Ops** — `execute_governed_action` (stop solvers on error / BLOCK). PolicyEngine unchanged.
2. **HAIS** — `hais_unified_kernel.SovereignKernel.evaluate_state`; if `capability_cap < 0.25`, override to **BLOCK**.
3. **Haven2** — step energy; expose realm / `p_hat` / open.
4. **Solvers** (only if ALLOW **and** (transistor open **or** `action == "query"`)):
   - `action == "3dm"` → one demo 3DM collapse via imprint
   - `action == "control"` → one QP step from `u_nom` (live `governance_engine`, not the incomplete control sketch)

Envelope: `{decision, reasons, entry_id, hais{cap,risk,instability}, haven2{realm,p_hat,open}, solver?, audit?, notes?}`.

## Setup (one venv)

```bash
cd /workspace/governance-engine
source .venv/bin/activate
pip install -r requirements.txt    # runtime deps (matches pyproject)
# optional pinned freeze: pip install -r requirements.lock.txt
pip install -r hais/requirements.txt
pip install -e ".[dev]"            # optional; PYTHONPATH also works
```

`ensure_import_paths()` adds repo root (for `diagnostics/`, `solvers/`, `fluids/`, `haven/`) plus `hais/`, `imprint/src`, `haven2/src`, and `src/`.

## Demo

```bash
.venv/bin/python scripts/unify_demo.py
.venv/bin/python scripts/govern_demo.py
```

`unify_demo.py` prints the manifest, runs `import_check`, one ALLOW govern query, mail + calendar ALLOW checks, one 3sat solve, one topology summary, skips heavy fluids if Dedalus is missing, and exits 0 when live imports are OK.

## Outbound mail gate

`GovernedMail` puts `GovernedStack.govern()` in front of outbound email without ever sending. Recipients (`To`/`Cc`) stay out of the scanned intent; subject and body are governed — **BLOCK means do not send**.

```bash
.venv/bin/python scripts/governed_mail.py --to alice@example.com --subject "Lunch" --body "Are you free tomorrow?"
```

**Never call Gmail send/reply/forward/create_draft until `GovernedMail.check` returns ALLOW.** Prefer `require_allow` / `HavenUnified().mail`. Agent rules: `src/governed_stack/AGENT_MAIL.md`.

## Calendar write gate

`GovernedCalendar` puts `GovernedStack.govern()` in front of calendar creates/updates without ever writing events. Attendees/start/end stay out of the scanned intent; summary → `subject`, description+location → `text` — **BLOCK means do not create**.

```bash
.venv/bin/python scripts/governed_calendar.py --summary "Team sync" --description "Weekly status" --attendee alice@example.com
```

**Never call Calendar create/update until `GovernedCalendar.check` returns ALLOW.** Prefer `require_allow` / `HavenUnified().calendar`. Agent rules: `src/governed_stack/AGENT_CALENDAR.md`.

## Outbound social / post gate

`GovernedPost` (alias `GovernedOutboundText`) puts `GovernedStack.govern()` in front of LinkedIn/X/etc. posts without ever publishing. Recipients and URLs stay out of the scanned intent; optional platform tag → `subject`, body → `text` — **BLOCK means do not publish**. There is no official LinkedIn connector in the plugin catalog; the gate is ready when one appears.

```bash
.venv/bin/python scripts/governed_post.py --platform linkedin --body "Excited to share a short update on our governance stack."
```

**Never call social publish/create_post until `GovernedPost.check` returns ALLOW.** Prefer `require_allow` / `HavenUnified().post`. Agent rules: `src/governed_stack/AGENT_SOCIAL.md`.


## Customer ops

Customer-operable live gate: stdlib HTTP sidecar (check-only), multi-tenant lite (API-key → isolated audit DB) + per-tenant rate limits, concurrent audit soak, threat model, runbook, and example env.

- Runbook: [`docs/CUSTOMER_OPS.md`](docs/CUSTOMER_OPS.md)
- Threat model: [`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md)
- Example env: [`config/customer.env.example`](config/customer.env.example)
- Sidecar: `src/governed_stack/sidecar.py` via `scripts/run_sidecar.py`
- Optional thin `Dockerfile` (tests do not need Docker)

```bash
.venv/bin/python scripts/key_ops.py generate --path artifacts/customer/signing_key.pem
# optional: export $(grep -v '^#' config/customer.env.example | xargs)  # edit first
.venv/bin/python scripts/run_sidecar.py
curl -s http://127.0.0.1:8080/health
curl -s http://127.0.0.1:8080/ready
```

`POST /v1/check` decides ALLOW/REVIEW/BLOCK for mail/calendar/social/raw — **never sends**. Auth: single `GOVERNANCE_API_KEY` or multi-tenant `GOVERNANCE_API_KEYS` / `GOVERNANCE_TENANTS_JSON` (`X-API-Key` → tenant). Rate limit: `GOVERNANCE_RATE_LIMIT_PER_MIN` (default 60) on `/v1/*`. Operator loop: `scripts/review_ops.py`, `scripts/audit_verify.py`, `scripts/metrics_report.py`.

## Keys (signing)

Production-posture providers live in `src/governed_stack/key_providers.py` and are
re-exported from `certified_governance_unified` for backward compatibility.

| Provider | Role |
|----------|------|
| `LocalPEMKeyProvider` | File-backed RSA-3072 (default for demos/tests) |
| `EnvKMSKeyProvider` | **Local** KMS-shaped stand-in: key from `GOVERNANCE_SIGNING_KEY_PEM` / `GOVERNANCE_SIGNING_KEY_PATH`; `private_pem` is always `None` (not a cloud HSM SDK) |
| `RotatingKeyProvider` | Active PEM + `signing_keys.json` sidecar for verify-after-rotate |

Refuse ephemeral keys when `GOVERNANCE_REQUIRE_PERSISTED_KEY=1` or config `require_persisted_key=True`.

```bash
.venv/bin/python scripts/key_ops.py generate --path artifacts/mail/signing_key.pem
.venv/bin/python scripts/key_ops.py public --path artifacts/mail/signing_key.pem
.venv/bin/python scripts/key_ops.py check --path artifacts/mail/signing_key.pem
.venv/bin/python scripts/key_ops.py rotate --path artifacts/mail/signing_key.pem
```

## Observability (decision metrics)

Live-path only (`GovernedStack.govern` → `stack.metrics` / `HavenUnified.metrics`).
Sketches do not record. Module: `src/governed_stack/observability.py`.

```bash
# Cron-friendly audit chain verify (exit 0/1):
.venv/bin/python scripts/audit_verify.py --db artifacts/mail/audit.db
# Offline decision counts from audit DB + optional metrics JSON:
.venv/bin/python scripts/metrics_report.py --db artifacts/mail/audit.db
```

## Contracts (typed intents)

Live-gate only (`src/governed_stack/contracts.py`). Pydantic v2 models for
`GovernIntent` and channel scan intents (`MailScanIntent`, `CalendarScanIntent`,
`SocialScanIntent`). Scan models intentionally omit routing fields (no To/Cc,
attendees/start/end, handles/URLs).

`GovernedStack.govern()` validates dict / BaseModel intents; on failure it
returns **BLOCK** with `error_code=GOV_INTENT_INVALID` (does not raise).
Adapters build scan intents via `validate_*_scan` → `dump_for_govern()`.
Non-ALLOW envelopes may include a stable `error_code` when reasons map cleanly
(`GOV_POLICY_BLOCK`, `GOV_POLICY_REVIEW`, `GOV_HAIS_CAP`, `GOV_AUTH_FAILED`,
`GOV_RATE_LIMIT`, …). Sketches do not use pydantic contracts.

## Operator tools

### Runtime bridge

`GovernedDecisionEngine` (`src/governed_stack/runtime_bridge.py`, also `hais/hais_os.py`) is the live **decide** adapter: optional constitution pre-check + human halt, then `GovernedStack.govern`. It does **not** put tau/fatigue on mail/calendar and does **not** replace `certified_governance_unified` / RSA-PSS audit. Sketch teaching kernel stays at `hais/hais_governance_unified_runtime_v02.py`.

```bash
cd /workspace/governance-engine
PYTHONPATH=src:hais:. .venv/bin/python hais/hais_main.py
```

`HavenUnified.runtime` / `.decide` share the same stack as mail/calendar/social.

Read-only audit report over a durable mail/calendar/social audit DB (defaults to `artifacts/mail/audit.db` when present):

```bash
.venv/bin/python scripts/audit_report.py
.venv/bin/python scripts/audit_report.py artifacts/calendar/audit.db -n 20
.venv/bin/python scripts/audit_report.py --hours 48 -n 5
```

Prints `storage.stats` plus the last N decisions (`decision`, `reasons`, `entry_id`).

### Review ops

Human operators can list / approve / deny pending `REVIEW` entries (and optionally issue a single-use approval voucher) against a durable audit DB. Defaults to `artifacts/mail/audit.db`; override with `--db`. Never sends mail or calendar events.

```bash
.venv/bin/python scripts/review_ops.py list
.venv/bin/python scripts/review_ops.py list --json
.venv/bin/python scripts/review_ops.py approve <entry_id> --notes "ok"
.venv/bin/python scripts/review_ops.py deny <entry_id>
.venv/bin/python scripts/review_ops.py voucher <entry_id>   # prints single-use JWT
.venv/bin/python scripts/review_ops.py --db artifacts/calendar/audit.db list
```

`--resolved-by` defaults to `damien`. Demo on a throwaway DB: `scripts/review_path_demo.py`.

CI gates **ruff** (`E`/`F`/`I`) and **mypy** on `src/governed_stack` only (see `[tool.ruff]` / `[tool.mypy]` in `pyproject.toml`). The `certified_governance_unified.py` monolith is **not** type-checked to perfection in this pass (`files = ["src/governed_stack"]`; followed imports use `ignore_errors` / `follow_imports=silent`). mypy `python_version` is **3.12** (numpy stubs / CI 3.12–3.13); runtime `requires-python` stays `>=3.10`. `pip install -e ".[dev]"` pulls pytest, ruff, and mypy.


Topology diagnostics harness (sketch only; off decision path):

```bash
.venv/bin/python -m diagnostics.invariant_topology --seed 2026
```

## Fluids note

Dedalus TG **smoke** lives under `fluids/smoke_taylor_green.py`. `HavenUnified.fluids_smoke()` returns `{"skipped": "dedalus"}` when Dedalus is not installed — full TG is **not** the default path and is never wired into ALLOW decisions.

## Tests

**Full bug-finding harness** (live gates + maths + sketches + CDCL; sectioned LIVE / MATH / SKETCH / EXTERNAL report):

```bash
cd /workspace/governance-engine
.venv/bin/python scripts/test_harness.py
```

CI / all-works unittest suite (includes `tests.test_math_cores`):

```bash
.venv/bin/python scripts/run_all_works_tests.py
# local CI mirror (same command as GitHub Actions):
bash scripts/ci_local.sh
# or just the unified end-to-end class:
.venv/bin/python -m unittest tests.test_all_works -v
# previous focused modules still work:
.venv/bin/python -m unittest tests.test_unified tests.test_governed_stack tests.test_governed_mail tests.test_governed_calendar tests.test_governed_social tests.test_runtime_bridge -v
# pytest maths (also invoked by test_harness.py):
.venv/bin/python -m pytest tests/test_lie_and_constraints.py tests/test_safety_sim.py -v
```

CI (`.github/workflows/ci.yml`, Python 3.12/3.13): `pip install -e ".[dev]"`, then **`ruff check src/governed_stack`**, **`mypy src/governed_stack`**, then `python scripts/run_all_works_tests.py`. Local mirror: `bash scripts/ci_local.sh` (same order). Prefer `scripts/test_harness.py` locally when hunting maths / sketch bugs. Remote Origin vs GitHub hosting / Actions push is a parallel track.

## Layout

```
governance-engine/
  src/governed_stack/     # HavenUnified + GovernedStack + mail/calendar/social + contracts + runtime_bridge + catalog
  certified_governance_unified.py  # preferred ops (v1.1)
  src/governance_engine/  # CLF-CBF-QP (live control)
  hais/                   # CGE + ZK + SovereignKernel (+ production pipeline sketch)
  haven2/src/haven2/      # energy + latch
  imprint/src/imprint/    # 3DM pipeline
  diagnostics/            # sketch
  solvers/                # sketch (3sat, incomplete control)
  fluids/                 # sketch (TG / Dedalus smoke)
  haven/                  # sketch (HavenSovereign)
  scripts/unify_demo.py
  scripts/govern_demo.py
  scripts/governed_mail.py
  scripts/governed_calendar.py
  scripts/governed_post.py
  scripts/audit_report.py
  tests/test_unified.py
  tests/test_governed_*.py
```

Inner packages keep their own READMEs and tests; this README is the single front door for the unified stack.
