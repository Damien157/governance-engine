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

## Not claimed

- Not a Millennium / NP subset-of-P proof
- Not consciousness detection
- Not quantum computing hardware (``QUANTUM`` = fused HAIS audit label)
- Not remote side-effect execution
