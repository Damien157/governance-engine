# Threat model — governed request gate (operator view)

Short, honest model for operators running the customer-ops sidecar / `GovernedStack`.
Not a formal certification, penetration-test report, or SaaS security whitepaper.

Package context: **0.4.4** — bug→fix pass on live contracts + latch; builds on 0.4.3 audit chain race fix + this doc.

## Assets

| Asset | Why it matters |
|-------|----------------|
| **Signing private key** (PEM / env PEM) | Signs audit chain entries and (demo) JWTs. Theft → forged audit + forged tokens. |
| **Audit SQLite DB** (and WAL/SHM sidecars) | Hash-chained, signed decision history. Tamper without key fails `verify_chain()`; with key, rewrite is possible. |
| **API keys** (`GOVERNANCE_API_KEY` / `GOVERNANCE_API_KEYS` / tenants JSON) | Map callers to tenants and authorize `/v1/*`. Leak → cross-tenant check abuse (lite isolation). |
| **REVIEW vouchers** (JWT jti, single-use after ALLOW+log) | Human-approved bypass of policy REVIEW for one intent. Replay / theft → unintended ALLOW. |
| **Decision metrics / logs** | Operational signal; may reflect policy outcomes and latency (not a secret store). |

## Trust boundaries

1. **Caller → sidecar network** — HTTP on a bind address you choose (`GOVERNANCE_HOST`/`PORT`). No TLS termination in-process; put a reverse proxy if you need it.
2. **API key → tenant** — Multi-tenant **lite**: process-local registry, isolated audit DB paths, optional per-tenant PEM. Not hard multi-tenant SaaS isolation (no separate processes/VMs by default).
3. **Adapters → `govern()`** — Mail/calendar/social adapters scan content fields only; recipients/attendees/handles stay out of band. Adapters **never** send/create/publish.
4. **Ops engine → audit + HAIS** — Live path: ops decide → HAIS SovereignKernel (**m=0.5**) → Haven2. Sketches are importable demos and stay **off** `govern()`.

## Threats and mitigations

| Threat | Mitigation in this tree | Residual risk |
|--------|-------------------------|---------------|
| **Key theft** (disk/env) | Local PEM permissions; `GOVERNANCE_REQUIRE_PERSISTED_KEY`; optional rotate via `RotatingKeyProvider` + `signing_keys.json` | No HSM/cloud KMS SDK; host compromise wins |
| **Audit tamper** | SHA-256 hash chain + RSA signatures; `scripts/audit_verify.py` / `verify_chain()` | Attacker with signing key can rewrite a plausible chain |
| **Intent injection / PII in To** | Recipients not placed in scanned intent; policy PII rules on subject/body; redact before audit envelope | Callers can still put PII in scanned fields; To is out-of-band by convention |
| **Rate abuse** | Sidecar in-memory sliding window per tenant (`GOVERNANCE_RATE_LIMIT_PER_MIN`); ops user rate signals | Process-local only; restart resets; not Redis/distributed |
| **REVIEW voucher replay** | Single-use jti revoke after durable ALLOW+log; hard gates still apply | Stolen unused voucher still works until use/expiry |
| **Sketch confusion** | Catalog/README mark sketches off-path; soak/tests exercise live `govern()` only | Operators wiring sketches into production themselves |
| **Concurrent audit corruption** | `AuditStorage` WAL + **single-writer** `_chain_lock` held across tip→ts→hash→INSERT; monotonic timestamps under lock; soak `tests/test_audit_soak.py` | Identical intents may cache-hit (TTL) and skip a new row — expected. Multi-process writers to one DB are **not** supported for chain integrity. |

## Explicit non-claims

- **Not** multi-tenant SaaS hard isolation (no per-tenant process/cgroup/network policy by default).
- **Not** HSM / cloud KMS; `EnvKMSKeyProvider` is a **local** KMS-shaped stand-in.
- **Not** formal certification (Common Criteria, SOC2 evidence pack, etc.).
- **Not** a mail/calendar/social sender — check-only gate.
- **Not** an SLA; measure latency via `/metrics` in your environment.
- HAIS **m=0.5** is fixed on the live kernel; do not “tune” sketches into the decision path.

## Operator checks

```bash
.venv/bin/python scripts/audit_verify.py --db <audit.db> --key <signing_key.pem>
.venv/bin/python -m unittest tests.test_audit_soak -v   # concurrent soak
.venv/bin/python scripts/audit_soak.py                  # manual soak
```

See also: [`CUSTOMER_OPS.md`](CUSTOMER_OPS.md), repo `README.md` (customer-ops section).
