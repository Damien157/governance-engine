# Hosted check API

Thin HTTP surface over the live govern gate. **Check-only** — there is no
``POST /v1/execute``. Mutations stay in-process via ``GovernedActionBus``.

## Run

```bash
cd /workspace/governance-engine   # or your checkout
.venv/bin/python scripts/run_sidecar.py
# default http://127.0.0.1:8080
```

## Issue a body token

``/v1/check`` requires a JWT in the JSON body (``token``), signed by the sidecar
stack key:

```bash
.venv/bin/python - <<'PY'
from governed_stack.sidecar import SidecarService, load_sidecar_config
svc = SidecarService(config=load_sidecar_config())
print(svc.issue_token("customer", "operator"))
PY
```

Optional: if ``GOVERNANCE_API_KEY`` (or multi-tenant keys) is set, also send
``X-API-Key``.

## Algorithm channel

```bash
curl -s -X POST http://127.0.0.1:8080/v1/check \
  -H 'Content-Type: application/json' \
  -d "{
    \"channel\": \"algorithm\",
    \"token\": \"$TOKEN\",
    \"purpose\": \"batch_dedupe\",
    \"summary\": \"Nightly anonymized id dedupe\",
    \"time_cost\": \"O(n log n)\",
    \"space_cost\": \"O(n)\",
    \"energy_cost\": \"low\",
    \"speedup\": \"~2x\",
    \"risk_notes\": \"read-only replica\",
    \"security_margin\": \"standard\"
  }"
```

Scanned fields: ``purpose`` (required), optional ``summary``, ``time_cost``,
``space_cost``, ``energy_cost``, ``speedup``, ``risk_notes``, ``security_margin``.
Forbidden on the scan (same as contracts): ``private_key``, ``password``,
``secret``, and scan-level ``token`` (the auth JWT is a separate body field).

### Sample response keys (algorithm)

``decision``, ``ok``, ``reasons``, ``entry_id``, ``error_code``, ``latency_ms``,
``hais`` (slim), ``haven2`` (slim: ``realm`` / ``open`` / ``p_hat`` + ``zeta_error`` if any),
``quantum``, ``quantum_line``, ``spectrum``.

Mail / calendar / social stay slim (``decision``, ``reasons``, ``error_code``,
``latency_ms``, ``entry_id``) so existing clients keep working.


## Customer check contract

Stable operator-facing shape for `POST /v1/check`. Check-only — no remote side effects.

### Auth

| Mechanism | Where | Notes |
|-----------|--------|------|
| Body JWT `token` | JSON field | Required for `/v1/check`. Issue with same signing key as the sidecar (`SidecarService.issue_token`). Missing/invalid → `decision=BLOCK`, `error_code=GOV_AUTH_FAILED`. |
| `X-API-Key` | HTTP header | Optional unless `GOVERNANCE_API_KEY` / multi-tenant keys are configured; then required on `/v1/*`. |

### Channels (scanned body fields)

| channel | Required scanned fields | Forbidden on scan (→ `GOV_INTENT_INVALID`) | Out of band |
|---------|-------------------------|--------------------------------------------|-------------|
| `mail` | `subject`, `body` (or `text`) | routing secrets / smuggled keys per contracts | To / Cc / From |
| `calendar` | `summary`, `description`, `location` | attendees / start / end on scan | attendees / start / end |
| `social` | `text` (optional `platform`) | handles / URLs on scan | recipients / URLs / handles |
| `algorithm` | `purpose` | `private_key` / `password` / `secret` / scan-level `token` | — |
| `raw` | `intent` dict | per `GovernIntent` / contracts | — |

### Response shapes

| Channel | Typical keys |
|---------|----------------|
| mail / calendar / social | `decision`, `ok`, `reasons`, `entry_id`, `error_code`, `latency_ms` (**slim** — no `quantum` / `spectrum` / `hais` / `haven2`) |
| algorithm | slim keys **plus** `hais` (slim), `haven2` (slim), `quantum`, `quantum_line`, `spectrum` when available |

`decision` ∈ {`ALLOW`, `BLOCK`, `REVIEW`}. HTTP 200 is common even on BLOCK (decision in body). Malformed JSON → HTTP 400 `{ "error": "bad_request" }`.

### Error codes (selected)

`GOV_AUTH_FAILED`, `GOV_INTENT_INVALID`, `GOV_POLICY_BLOCK`, `GOV_POLICY_REVIEW`, `GOV_HAIS_CAP`, `GOV_LATCH_CLOSED`, `GOV_RATE_LIMIT`, `GOV_INTERNAL`.

### Explicitly refused

- `POST /v1/execute` → HTTP 405 `{ "error": "execute_not_supported" }`
- Not a sender / calendar writer / social publisher
- Not a Clay / P vs NP claim surface

### Operator smoke + eval

```bash
.venv/bin/python scripts/hosted_check_smoke.py   # thin contract smoke
.venv/bin/python scripts/hosted_check_eval.py    # fuller matrix: HTTP + ActionBus require_allow
```

`hosted_check_eval.py` hits the **live** ephemeral sidecar and `GovernedActionBus`
(mail stub side_effect only on ALLOW). It is not a parallel simulated gate.
Audit-only `project_governance_score` (`governed_stack.audit_projection`) projects `(risk, stability, governance)` from live `decision` / `error_code` / `hais` / `haven2` — **not** wired into `govern()` / HAIS. Complements `tests/test_sidecar.py` / `tests/test_action_bus.py`;
does not replace CI.

## Not claimed

- Not a Millennium / NP subset-of-P proof
- Not consciousness detection
- Not quantum computing hardware (``QUANTUM`` = fused HAIS audit label)
- Not remote side-effect execution
