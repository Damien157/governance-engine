# Agent rules — algorithm run / deploy

Hard gate. Do not bypass.

1. **Never** run, deploy, ship, or schedule an algorithm (solver, model job,
   batch, or production rollout) until `GovernedAlgorithm.check` (or
   `require_allow`) returns **ALLOW**.
2. On **BLOCK** — refuse. Do not run, deploy, or rewrite around the policy.
   Tell the user the decision and reasons.
3. On **REVIEW** — escalate to a human. Do not run or deploy. Use the review
   queue / human approval path.
4. Prefer `GovernedAlgorithm.require_allow(...)` in code paths so a non-ALLOW
   raises `AlgorithmBlocked` (`PermissionError`) and cannot be ignored via `ok`.
5. Scan only purpose/summary, Cost axes (time / space / energy / speedup), and
   Risk notes / security margin. **Never** put secrets, credentials, private
   keys, passwords, or tokens in the scan intent — forbidden keys are rejected
   (no silent drop).
6. Speedup is a **Cost** claim; security lives under **Risk**; energy is a
   Cost estimate. This gate does **not** prove P vs NP or certify asymptotic
   optimality.
7. Default audit artifacts live under `artifacts/algorithm/` (same pattern as
   `artifacts/mail/`). The gate does **not** execute the algorithm.
8. Do not weaken PolicyEngine or HAIS (m=0.5) to force a run.

Example:

```python
from governed_stack import GovernedAlgorithm

gate = GovernedAlgorithm()
result = gate.check_sync(
    purpose="batch_dedupe",
    summary="Nightly customer dedupe on anonymized ids",
    time_cost="O(n log n)",
    space_cost="O(n)",
    energy_cost="low",
    speedup="~2x vs naive",
    risk_notes="no PII in inputs; read-only DB replica",
    security_margin="standard",
)
if not result["ok"]:
    raise SystemExit(result["decision"])
# Only after ALLOW: run or deploy the algorithm out of band.
```
