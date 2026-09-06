# Customer operations — governed request gate

Honest prototype → **operable service shape**. First customer-ops slice on the live gate.
Not a full multi-tenant SaaS product yet.

## What this is

- A **non-fiction governed request gate**: decide ALLOW / REVIEW / BLOCK before outbound mail, calendar writes, or social posts.
- A thin **HTTP sidecar** (`governed_stack.sidecar`) that exposes health, readiness, Prometheus metrics, and **check-only** decisions.
- Operator CLIs for review queue, audit verify, and metrics report.
- HAIS SovereignKernel **m=0.5** (unchanged). Sketches stay **off** `govern()`.

## What this is not

- Not a mail/calendar/social **sender** — the sidecar and adapters never send, create events, or publish.
- Not cloud KMS / HSM — signing uses local PEM or a **local** KMS-shaped env provider (`EnvKMSKeyProvider`).
- Not multi-tenant SaaS, not an SLA, not a formal certificate authority product.
- Not sketches on the decision path (3SAT, fluids, topology diagnostics, teaching kernels).

## Deploy (local)

```bash
cd /workspace/governance-engine   # or your checkout
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e ".[dev]"          # optional; for tests / ruff / mypy

# Generate a persisted signing key (local RSA-3072)
.venv/bin/python scripts/key_ops.py generate --path artifacts/customer/signing_key.pem

# Configure
cp config/customer.env.example /tmp/customer.env
# edit GOVERNANCE_* as needed, then:
set -a && source /tmp/customer.env && set +a

# Run sidecar (default 127.0.0.1:8080)
.venv/bin/python scripts/run_sidecar.py
```

### Health checks

```bash
curl -s http://127.0.0.1:8080/health
# {"status":"ok","version":"0.4.0"}

curl -s http://127.0.0.1:8080/ready
# 200 {"status":"ready","reasons":[]}  — or 503 with reasons

curl -s http://127.0.0.1:8080/metrics
# Prometheus text from DecisionMetrics
```

### Check (never send)

Issue a JWT from the same stack (demo/operator) or your token issuer wired to the same signing key:

```bash
# Example: mail scan — To/Cc stay out of band; subject+body only
curl -s -X POST http://127.0.0.1:8080/v1/check \
  -H 'Content-Type: application/json' \
  -H "X-API-Key: $GOVERNANCE_API_KEY" \
  -d '{"channel":"mail","token":"<JWT>","subject":"Lunch","body":"Are you free tomorrow?"}'
```

Channels:

| channel   | Body fields (scanned)              | Out of band (do not put in scan) |
|-----------|------------------------------------|----------------------------------|
| `mail`    | `subject`, `body` (or `text`)      | To / Cc / From                   |
| `calendar`| `summary`, `description`, `location` | attendees / start / end        |
| `social`  | `text` (optional `platform`)       | recipients / URLs / handles      |
| `raw`     | `intent` dict (contracts-validated)| —                                |

Response JSON: `decision`, `reasons`, `error_code`, `latency_ms`, and `entry_id` when present.

Optional: `POST /v1/review/list` lists pending REVIEW rows (same engine as CLI). Prefer CLI for approve/deny.

### Optional Docker

```bash
docker build -t governed-sidecar:0.4.0 .
docker run --rm -p 8080:8080 \
  -e GOVERNANCE_REQUIRE_PERSISTED_KEY=1 \
  -e GOVERNANCE_API_KEY=... \
  -v "$PWD/artifacts/customer:/app/artifacts/customer" \
  governed-sidecar:0.4.0
```

Tests do **not** require Docker.

## Operator loop

1. **REVIEW queue** — human approve / deny (never sends):

   ```bash
   .venv/bin/python scripts/review_ops.py list --db artifacts/customer/audit.db
   .venv/bin/python scripts/review_ops.py approve <entry_id> --notes "ok"
   .venv/bin/python scripts/review_ops.py deny <entry_id>
   ```

2. **Audit chain verify** (cron-friendly, exit 0/1):

   ```bash
   .venv/bin/python scripts/audit_verify.py --db artifacts/customer/audit.db \
     --key artifacts/customer/signing_key.pem
   ```

3. **Metrics report** (offline from audit DB + optional live `/metrics`):

   ```bash
   .venv/bin/python scripts/metrics_report.py --db artifacts/customer/audit.db
   curl -s http://127.0.0.1:8080/metrics
   ```

## Channel adapters (CLI / library)

Prefer adapters when integrating agents; always **check before send**:

```bash
.venv/bin/python scripts/governed_mail.py --to alice@example.com --subject "Lunch" --body "..."
.venv/bin/python scripts/governed_calendar.py --summary "Sync" --description "Weekly"
.venv/bin/python scripts/governed_post.py --platform linkedin --body "Short update"
```

Library: `GovernedMail` / `GovernedCalendar` / `GovernedPost` — `check` / `require_allow`.
**BLOCK / REVIEW means do not send / create / publish.** Routing metadata stays out of the scanned intent.

Agent rules: `src/governed_stack/AGENT_MAIL.md`, `AGENT_CALENDAR.md`, `AGENT_SOCIAL.md`.

## Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `GOVERNANCE_HOST` | `127.0.0.1` | Sidecar bind host |
| `GOVERNANCE_PORT` | `8080` | Sidecar bind port |
| `GOVERNANCE_DB_PATH` | `artifacts/customer/audit.db` | Durable audit SQLite |
| `GOVERNANCE_SIGNING_KEY_PATH` | `artifacts/customer/signing_key.pem` | RSA-3072 PEM for audit/JWT |
| `GOVERNANCE_SIGNING_KEY_PEM` | — | Optional PEM text (local KMS-shaped) |
| `GOVERNANCE_REQUIRE_PERSISTED_KEY` | unset/`0` | `1` = refuse ephemeral keys |
| `GOVERNANCE_API_KEY` | unset | If set, `/v1/*` requires `X-API-Key` (`/health` `/ready` `/metrics` stay open) |
| `GOVERNANCE_SIDECAR_VERBOSE` | unset | `1` = HTTP access logs |

See `config/customer.env.example`.

## Failure modes

| Outcome | Meaning | Operator action |
|---------|---------|-----------------|
| **ALLOW** | Gate permits the outbound action | Caller may proceed to send/create/publish **outside** this service |
| **REVIEW** | Policy wants a human | Use `review_ops.py`; do not send until approved (and re-check if required by your runbook) |
| **BLOCK** | Hard deny | Do not send; inspect `reasons` / `error_code` |
| **GOV_INTENT_INVALID** | Bad / untyped intent (`error_code`) | Fix payload schema; never retry the same bad body as ALLOW |
| **GOV_AUTH_FAILED** | Bad/missing JWT | Fix token issuance |
| **GOV_POLICY_BLOCK** / **GOV_POLICY_REVIEW** | Mapped policy outcomes | Follow policy + review queue |
| **503 /ready** | Missing signing key path or audit parent not writable | Generate key; fix paths/permissions |

## Honest limits

- **Single-tenant first** — one process, one audit DB, one signing key (or local rotation sidecar).
- **Local keys** — PEM on disk or env PEM; not AWS/GCP KMS SDKs.
- **No SLA claims** — prototype operable shape; measure your own latency via `/metrics`.
- **No real sends** from this repo’s gate/sidecar.
- **No GitHub push / cloud deploy required** to operate locally.
- HAIS **m=0.5** is fixed on the live kernel path; do not “tune” sketches into `govern()`.

## Version

Customer-ops milestone: package **0.4.0** (`governed_stack.__version__`).
