# HAIS Certified Governance Engine

Reference stack for Damien O'Driscoll's Certified Governance Engine (v1.0) plus optional ZK enhancements.

**Important disclaimers**

- This is **not** a regulator certificate or compliance attestation.
- Signal math (risk, integrity pressure, OLS trend, anomaly z-score) is **heuristic**, not certified statistics.
- Zero-knowledge proofs apply **only** to the risk-threshold claim (`RISK_THRESHOLD`: Pedersen + CDS94 bit OR-proofs). Policy redaction and review-approval claims are **RSA-PSS attestations**, not ZK.
- Default RSA private keys are unencrypted PEM — suitable for local demos only; protect keys in production.

## Layout

- `certified_governance.py` — auth, policy, audit chain, circuit breaker, flow control, governed execute
- `zk_enhanced_governance.py` — optional ZK risk proofs + signed attestations off the decision path
- `hais_tests/` — unittest suites (`test_certified_engine`, `test_zk_proofs`, `test_phone_policy`); renamed from `tests/` to avoid shadowing repo `tests/`

## Quick start

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest hais_tests.test_certified_engine hais_tests.test_zk_proofs -v
```

Set `ZK_RUN_SLOW=1` only if you want the slow range-proof / engine integration tests.
