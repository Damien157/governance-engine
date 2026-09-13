# Unified useful work — live path since Algorithm Governance Main

One map of what we kept, verified, fixed, and still owe. Honesty filter:
**live code and audited decisions** beat sketches, myths, and valuation talk.
Parked mythos stays in [SIDED_ASIDE.md](SIDED_ASIDE.md).

Spine: **presence ≠ access** — govern before side effects; HAIS throttle;
Haven2 latch; QUANTUM + ζ spectrum as **audit**, not physics theatre.

**Verification legend (this doc):**

| Label | Meaning |
|-------|---------|
| **Verified** | Reproduced from real source + local run in the integrity review (or Damien-confirmed) |
| **Reported** | Assistant local green / merged to `main`; not independently re-run by Damien |
| **Accepted residual** | Documented deliberate non-fix |

CI on GitHub remains red until Damien157 **billing unlock** — “on `main`” ≠ Actions-green.

---

## 1. Core pattern (product)

| Idea | Live meaning |
|------|----------------|
| Presence ≠ access | Being able to call an API ≠ permission to mutate |
| Purpose → Cost → Risk → Authority → Audit | Algorithm (and peer) scan axes — [ALGORITHM_GOVERNANCE_MAIN.md](ALGORITHM_GOVERNANCE_MAIN.md) |
| Sealed ALLOW | No side effect without gate ALLOW; check ≠ execute |
| Latch | Haven2 closed latch **BLOCKs** `control` / `3dm` only — not algorithm / query / mail / calendar / social |
| `solver_ok` | Gates **3dm/control solvers only** (`ALLOW` and latch open **or** `action=="query"`); comment matches code ([PR #6](https://github.com/Damien157/governance-engine/pull/6)) |
| QUANTUM line | Fixed 150-char fused audit encoding — **not** a quantum computer |
| ζ spectrum | Completeness keys `Z_E` / `Z_R` / `Z_C` / `Z_H` — [SPECTRAL_AUDIT_FIXES.md](SPECTRAL_AUDIT_FIXES.md); `Z_D` additive only (not in `SPECTRUM_KEYS`) |

Buyer one-pager: [PRODUCT_SLICE.md](PRODUCT_SLICE.md).  
Hosted HTTP: [HOSTED_CHECK_API.md](HOSTED_CHECK_API.md) · ops: [CUSTOMER_OPS.md](CUSTOMER_OPS.md) · threats: [THREAT_MODEL.md](THREAT_MODEL.md).

---

## 2. Live modules (decision path)

| Surface | Role |
|---------|------|
| `GovernedStack.govern` | Shared Authority + Audit (ops → HAIS → Haven2 → optional QP/3DM) |
| `GovernedActionBus` | In-process mutations: `require_allow` before `side_effect` |
| `GovernedMail` / `GovernedCalendar` / `GovernedPost` | Outbound channel gates |
| `GovernedAlgorithm` | Algorithm run/deploy **decide-only** |
| `HavenUnified` / `GovernedUnified` | Thin front door |
| `sidecar` `POST /v1/check` | Hosted **check-only**; **no** `/v1/execute` |
| QUANTUM + `spectral_audit` | Audit line + spectrum (FIX 1–4 honesty) |
| `audit_projection.project_governance_score` | **AUDIT-ONLY** shadow scores — **not** on the decision path |

Sketches remain importable for demos and are **never** consulted for ALLOW / BLOCK / REVIEW.

---

## 3. Integrity stack on `main` (customer-facing + Haven2)

| # | Item | Commit / PR | Verification label |
|---|------|-------------|-------------------|
| FIX 1–4 | Spectrum honesty (sigma nest, complete `Z_*`, bad `c`, `zeta_error`) | `b88d892` + exercises | **Verified** (session suite / source) |
| 5 | `require_persisted_key` missing PEM refuse | `81a4ad8` #1 | **Verified** (live + pytest + diff) |
| 6–8 | Multi-tenant shared PEM refuse, `compare_digest`, `preflight_stacks` | `d72800c` #2 | **Verified** (diff + tests) |
| 9 | Haven2 `switch_times` 0-based; `history_p_hat` seed removed at `EnergyState` (no leftover `zeta_summaries` `[1:]`) | `db1ac42` #3 | **Verified** (source + no double-fix) |
| — | `governed_delta` / `Z_D` (additive audit, not integrity) | `c04ecef` #4 | **Reported** / reframed |
| 10 | Customer-edge HTTP tests + API-key timing residual note | `97cea1b` #5 | **Reported** local green |
| — | THREAT_MODEL: `crypto_factory` + multi-process audit residuals; `solver_ok` comment | `a09271f` #6 | **Verified** (docs/comment; A/C closed) |
| — | Latch VERIFY honesty + hosted check contract + smoke | `c1dce42` #8 | **Reported** (honesty wording + smoke 8/8) |
| — | Hosted-check eval on live sidecar + ActionBus | `286fcb0` #9 | **Reported** (10/10 local) |
| — | Audit-only `project_governance_score` + eval 12 + unit tests | `a6ce977` #10 | **Verified** once Damien reviewed real source + `rg` (audit-only); local 12/12 **reported** |

Open integrity questions **A/B/C** from the review trail:

| Q | Resolution |
|---|------------|
| **A** `crypto_factory` bypass | Real hook, **no in-tree callers**; accepted residual in THREAT_MODEL (#6) |
| **B** `anomaly_signal` | Nested **dict** from CGE analytics — `or {}` is correct; not a falsy-float bug |
| **C** `solver_ok` comment | Narrowed to match code (#6); no speculative exemption widening |

---

## 4. Hosted check / eval (buyer-facing)

| Artifact | Role |
|----------|------|
| [HOSTED_CHECK_API.md](HOSTED_CHECK_API.md) | Customer contract: auth, channels, slim vs algorithm, `GOV_*`, no execute |
| `scripts/hosted_check_smoke.py` | Thin operator smoke (8 cases) |
| `scripts/hosted_check_eval.py` | Live HTTP + ActionBus matrix + optional projection attach |
| `governed_stack/audit_projection.py` | `(risk, stability, governance)` from envelope — **after** decision; never imported by `stack` / `sidecar` / `action_bus` |

`GOV_*` codes: `GOV_INTENT_INVALID`, `GOV_AUTH_FAILED`, `GOV_POLICY_BLOCK`, `GOV_POLICY_REVIEW`, `GOV_HAIS_CAP`, `GOV_LATCH_CLOSED`, `GOV_RATE_LIMIT`, `GOV_INTERNAL`.

---

## 5. Accepted residuals ([THREAT_MODEL.md](THREAT_MODEL.md))

- Multi-tenant API-key `dict.get` (not `compare_digest`) — accepted
- Injected `crypto_factory` / `crypto=` skips LocalPEM require — accepted (no in-tree callers)
- Multi-process audit writers unsupported — accepted (in-process soak only)
- TransistorLatch hysteresis while `|p̂|` large — intentional
- Projection: `GOV_LATCH_CLOSED` floors risk ≥ 0.9 **and** +0.2 governance — by design (“safety engaged”); not a gate input

---

## 6. Research lane (not product value)

| Item | Status |
|------|--------|
| [LATCH_CERTIFICATE.md](LATCH_CERTIFICATE.md) | VERIFY = determinism framing; SEARCH open; honesty caveats landed (#8); not Clay / not NP-complete |
| [NVNP_CONJECTURES.md](NVNP_CONJECTURES.md) | Operational conjectures only |
| Brute-force SEARCH cost vs `T` | Parked — would not prove hardness |

Prefer hosted-check / domain app over further spectrum toys for economic value.

---

## 7. Product / roadmap gaps (still open)

1. Unlock GitHub Actions billing → re-run CI on `main` (e.g. `gh run rerun 34711886574`).
2. Production deploy — TLS, public host, managed tenants beyond lite keys.
3. Real joule / energy meter beyond declarative `energy_cost`.
4. Repeatable Control **ALLOW** smoke with open latch + QP.
5. Soft/Hard productization beyond charter docs.
6. Optional: more eval fixtures for rate-limit / REVIEW / HAIS_CAP **live** (today partly synthetic in projection tests).

Roadmap: [FULL_GOVERNANCE_ROADMAP.md](FULL_GOVERNANCE_ROADMAP.md).

---

## 8. How to re-verify locally

```bash
cd /workspace/governance-engine
source .venv/bin/activate

.venv/bin/python scripts/unified_useful_test.py
.venv/bin/python scripts/spectral_fix_exercises.py
.venv/bin/python scripts/hosted_check_smoke.py
.venv/bin/python scripts/hosted_check_eval.py
.venv/bin/python -m unittest tests.test_audit_projection -v
.venv/bin/python -m pytest tests/test_sidecar.py haven2/tests/test_switch_time_p_hat_align.py \
  haven2/tests/test_governed_delta.py -q
```

---

## 9. Valuation stance (honest)

Integrity queue for keys / latch / spectrum honesty is **largely closed on `main`**.
Customer edge has contract + smoke + live eval; audit projection is a **shadow**, not a product SKU.
Still closer to **solid prototype / operable gate** than production SaaS until CI unlocks and deploy/TLS land.

**Not:** P vs NP proof, consciousness product, quantum computer, or remote execute API.
