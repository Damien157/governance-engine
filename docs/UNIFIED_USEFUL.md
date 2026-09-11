# Unified useful work — live path since Algorithm Governance Main

One map of what we kept, verified, fixed, and still owe. Honesty filter:
**live code and audited decisions** beat sketches, myths, and valuation talk.
Parked mythos stays in [SIDED_ASIDE.md](SIDED_ASIDE.md).

Spine: **presence ≠ access** — govern before side effects; HAIS throttle;
Haven2 latch; QUANTUM + ζ spectrum as **audit**, not physics theatre.

---

## 1. Core pattern (product)

| Idea | Live meaning |
|------|----------------|
| Presence ≠ access | Being able to call an API ≠ permission to mutate |
| Purpose → Cost → Risk → Authority → Audit | Algorithm (and peer) scan axes — [ALGORITHM_GOVERNANCE_MAIN.md](ALGORITHM_GOVERNANCE_MAIN.md) |
| Sealed ALLOW | No side effect without gate ALLOW; check ≠ execute |
| Latch | Haven2 closed latch **BLOCKs** `control` / `3dm` only — not algorithm / query / mail / calendar / social |
| QUANTUM line | Fixed 150-char fused audit encoding — **not** a quantum computer |
| ζ spectrum | Finite Haven2 Dirichlet summaries (`Z_E`, `Z_R`, `Z_C`, `Z_H`) — honesty rules in [SPECTRAL_AUDIT_FIXES.md](SPECTRAL_AUDIT_FIXES.md) |

Buyer one-pager: [PRODUCT_SLICE.md](PRODUCT_SLICE.md).  
Hosted HTTP: [HOSTED_CHECK_API.md](HOSTED_CHECK_API.md) · ops: [CUSTOMER_OPS.md](CUSTOMER_OPS.md) · threats: [THREAT_MODEL.md](THREAT_MODEL.md).

---

## 2. Live modules (decision path)

| Surface | Role |
|---------|------|
| `GovernedStack.govern` | Shared Authority + Audit (ops → HAIS → Haven2 → optional QP/3DM) |
| `GovernedActionBus` | In-process mutations: `require_allow` before `side_effect` |
| `GovernedMail` / `GovernedCalendar` / `GovernedPost` | Outbound channel gates |
| `GovernedAlgorithm` | Algorithm run/deploy **decide-only** (Purpose→Cost→Risk) |
| `HavenUnified` / `GovernedUnified` | Thin front door |
| `GovernedDecisionEngine` | decide adapter (constitution/halt → govern) |
| `sidecar` `POST /v1/check` | Hosted **check-only** HTTP; **no** `/v1/execute` |
| QUANTUM + `spectral_audit` | Attach audit line + spectrum (FIX 1–4 honesty) |

Sketches (3SAT demo, topology, fluids, production pipeline sketch, …) remain
importable for demos and are **never** consulted for ALLOW / BLOCK / REVIEW.
See README three-tier table and `governed_stack.catalog`.

---

## 3. What we built / landed this arc

| Item | Where | Status |
|------|--------|--------|
| Algorithm Governance Main charter + gate | `docs/ALGORITHM_GOVERNANCE_MAIN.md`, `GovernedAlgorithm` | Live |
| QUANTUM line (10 fields, 150 chars, T1–T3 placeholders) | `quantum_line.py`, docs | Live audit |
| Haven2 ζ beside QUANTUM on algorithm envelope | `spectral_audit.py`, stack attach | Live |
| Spectrum honesty FIX 1–4 | sigma nest, complete `Z_*`, bad `c`, `zeta_error` | Live + exercises |
| `scripts/spectral_fix_exercises.py` | main `b88d892` | Live verification harness |
| `scripts/unified_useful_test.py` | algorithm ALLOW + control latch BLOCK smoke | Live |
| Hosted check API algorithm channel | `sidecar` `/v1/check` `channel:algorithm` | Live (`c80f836`) |
| Bug→fix log discipline | [BUGFIXES.md](BUGFIXES.md) | Ongoing |
| `require_persisted_key` missing-PEM refuse | LocalPEM + sidecar | **PR #1** `dea893c` (merge when ready) |

---

## 4. Verified behavior (do not re-litigate)

- Algorithm / query can **ALLOW** with `haven2.open: false`; latch BLOCK is for `control` / `3dm`.
- Live algorithm check: decision + quantum + spectrum; completeness `|Z_E|+Z_R+Z_C ≈ Z_H` when available.
- Happy-path curl alone does **not** prove FIX 1 (default sigma); use spectral exercises A–D.
- **One** `zeta_summaries` on `Haven2Engine`; `float(vols[i])` / equity / forecast live in `Haven2Engine.run` / `step`, **not** in `GovernedStack.govern`.
- Sidecar: TenantRegistry duplicate API keys → `ValueError` at construct; `resolve` fail-closed; RateLimiter sliding window; `check_async` requires non-empty body `token`.
- Auth + rate-limit are **wired**: `do_POST` `/v1/*` → `_authorize_v1` → resolve / API key → rate limit → `check`.
- `/v1/execute` → 405 (library ActionBus only).
- CI red on GitHub: Damien157 **billing lock** (jobs never start) — not suite failure. Local pytest green for touched suites.

---

## 5. Integrity finding → fix (customer-facing keys)

**Bug:** `GOVERNANCE_REQUIRE_PERSISTED_KEY=1` looked like production posture, but sidecar did `pass` then built `CryptoEngine` without the flag; LocalPEM only refused `path=None`, so a **missing PEM still auto-generated** (audit continuity break). Injected crypto meant GovernedStack config could not save you.

**Fix (PR #1):** LocalPEM raises `FileNotFoundError` under require + missing file; sidecar passes `require_persisted_key`; `rotate()` still intentionally creates after backup.

---

## 6. Queued bug→fix (future PRs)

| # | Bug | Intended fix | Severity |
|---|-----|----------------|----------|
| 1 | API-key / master-key compares use `==` | `hmac.compare_digest` on equality checks | Defense-in-depth |
| 2 | Multi-tenant falls back to **shared default PEM** when per-tenant key missing | Require per-tenant PEM when multi-tenant (esp. under REQUIRE=1); refuse shared fallback | Integrity / isolation |
| 3 | Lazy tenant stack build: `/ready` not ready but first `/v1/check` can 500 under REQUIRE=1 | Eager-build or preflight all tenants at serve start | Ops fail-fast |

---

## 7. Product / roadmap gaps (not code bugs)

1. Production deploy — public host, TLS, managed tenants beyond lite API keys.
2. Real joule / energy meter — beyond declarative `energy_cost` strings.
3. Repeatable Control **ALLOW** smoke with open latch + QP path.
4. AtomSafeguard Soft/Hard productization beyond charter docs.
5. Unlock GitHub Actions billing → re-run CI on main and PR #1.
6. Deeper customer-edge verification still worth scripted cases: JWT missing → `GOV_AUTH_FAILED`, malformed/unknown/execute refuses, slim vs enriched response shape (leak / omit errors).

Roadmap narrative: [FULL_GOVERNANCE_ROADMAP.md](FULL_GOVERNANCE_ROADMAP.md).  
Operational conjectures (not Clay P vs NP): [NVNP_CONJECTURES.md](NVNP_CONJECTURES.md).

---

## 8. How to re-verify locally

```bash
cd /workspace/governance-engine
source .venv/bin/activate

# Live useful smoke (algorithm + spectrum expectations)
.venv/bin/python scripts/unified_useful_test.py

# Spectrum honesty FIX 1–4
.venv/bin/python scripts/spectral_fix_exercises.py

# Key / sidecar integrity (after PR #1 branch)
.venv/bin/python -m pytest tests/test_key_providers.py \
  tests/test_sidecar.py::TestSidecarRequirePersistedKey -q

# Hosted check (check-only)
.venv/bin/python scripts/run_sidecar.py
# then POST /v1/check with channel=algorithm + body JWT (+ X-API-Key if set)
```

---

## 9. Valuation stance (honest)

Closer to **real architecture with unverified customer-edge integrity** than
“production-ready SaaS.” Path from belief to solid: merge PR #1, burn down
§6 integrity queue, script §7 edge cases, unlock CI, then deploy/TLS.

What this is **not:** P vs NP proof, consciousness product, quantum computer,
or a remote execute API.
