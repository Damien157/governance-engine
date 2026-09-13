# Cost-to-recreate — live vs R&D (accounts justification)

**Not a valuation.** This document justifies a **blended labour cost-to-recreate**
for statutory / planning use by mapping components to **live useful path**,
**core-on-path**, or **R&D / sketch (aside)**.

Honesty filter matches [UNIFIED_USEFUL.md](UNIFIED_USEFUL.md) and
[SIDED_ASIDE.md](SIDED_ASIDE.md). Catalog tiers: `governed_stack.catalog`
(`live` / `core` / `sketch`).

Rates used in the planning blend (Damien, 2026-09-13):

| Tier | Profile | Rate |
|------|---------|------|
| 1 | Narrow specialist (ZK / control-theory math) | €500/hr |
| 2 | Senior applied security / systems | €250/hr |
| 3 | Generalist / docs / tests / plumbing | €165/hr |

Hours are **judgement bands** for recreate-from-scratch by a competent shop,
not timesheets. Totals below are planning figures; auditors should treat the
**classification** (live vs aside) as the primary claim to evidence.

---

## 1. Two book totals (do not collapse)

| Book | Scope | Planning total (this blend) |
|------|--------|------------------------------|
| **A — Useful product path** | Gate + adapters + sidecar + HAIS kernel + Haven2 latch/energy + audit/crypto used on path + tests/docs for those | **≈ €180k–€280k** (mid ≈ **€230k**) |
| **B — Full repo including research** | A + ZK extensions + CBF/CLF demos + mesh scaffolds + AGI kernel sketches + NVNP/latch research docs | **≈ €322k** (Damien three-tier sheet) |

Statutory **product asset** arguments should prefer **Book A** unless R&D is
capitalised separately under a clear R&D policy.

---

## 2. Component → path → suggested tier / hours

### Book A — live / core-on-path (charge to product recreate)

| Component | Repo anchors | Path | Tier | Hours (planning) | € (at tier rate) |
|-----------|--------------|------|------|------------------|------------------|
| Core governance (policy, signed audit, auth primitives) | `hais/certified_governance*.py`, stack ops path | **core → live** | 2 | 100 | 25,000 |
| Signing / key providers (PEM, rotate, require_persisted) | `key_providers`, LocalPEM, integrity #1–#2 | **live** | 2 | 50 | 12,500 |
| HAIS SovereignKernel (m=0.5, cap) | `hais/hais_unified_kernel.py` | **core → live** | 2 | 25 | 6,250 |
| GovernedStack orchestration | `src/governed_stack/stack.py` | **live** | 2 | 50 | 12,500 |
| Channel gates + ActionBus | `mail` / `calendar` / `social` / `algorithm` / `action_bus` | **live** | 2 | 60 | 15,000 |
| Multi-tenant check-only sidecar | `sidecar.py`, `HOSTED_CHECK_API.md` | **live** | 2 | 60 | 15,000 |
| Haven2 energy + latch (decision-relevant) | `haven2/.../energy.py`, `transistor.py`, `engine.py` step/latch | **core → live** | 1–2 | 40 | ~10,000–20,000 |
| Haven2 ζ / spectrum **audit attach** (honesty FIX 1–4) | `spectral_audit.py`, `quantum_line.py` | **live audit** | 2 | 35 | 8,750 |
| Contracts / `GOV_*` / scan forbid | `contracts.py` | **live** | 2 | 25 | 6,250 |
| Test infrastructure (sidecar, action bus, keys, haven2 align) | `tests/`, haven2 tests | **live** | 3 | 50 | 8,250 |
| Hosted smoke + eval harness | `scripts/hosted_check_*.py` | **live** | 3 | 25 | 4,125 |
| Audit projection (shadow only) | `audit_projection.py` | **live audit tool** | 3 | 15 | 2,475 |
| Threat model / customer ops / bugfix log | `THREAT_MODEL`, `CUSTOMER_OPS`, `BUGFIXES`, `UNIFIED_USEFUL` | **live** | 3 | 40 | 6,600 |
| Integrity bug-hunt / verify cycle (this arc) | PRs #1–#11 review work | **live** | 3 | 80 | 13,200 |
| CLF-CBF-QP **only as optional `action=control` solver** | `src/governance_engine` (on ALLOW path only) | **live optional** | 1 | 25 | 12,500 |

**Book A subtotal (indicative):** ≈ **€160k–€250k** depending on how much
Haven2 spectral math is counted as specialist vs senior systems.

---

### Book B add-on — R&D / sketch (do **not** load into product asset without policy)

| Component | Repo anchors | Path | Tier | Hours (planning) | Notes |
|-----------|--------------|------|------|------------------|--------|
| ZK-enhanced extension (Pedersen, CDS94-style claims) | `hais/zk_enhanced_governance.py`, unified ZK paths | **sketch / optional core** | 1 | 80 | Not required for ALLOW/BLOCK |
| CLF-CBF v2 reconciliation + forward-invariance harness beyond live QP step | solvers / HOCBF demos | **sketch** | 1 | 90 | Catalog: sketches off gate |
| Separate CBF-QP solver experiments | sketch controllers | **sketch** | 1 | 25 | |
| Haven2 spectral zeta **research depth** beyond audit attach | zeta maths exploration | **R&D** | 1 | 35 | Delta above live audit hours |
| “8 sub-kernels incl. AGI governance” | teaching/runtime sketches, pipeline copies | **sketch** | 2 | ≤40 live-named; rest **aside** | Name each kernel; don’t lump |
| Mesh runtime scaffold / rewrite | mesh prototypes | **sketch** | 2 | 75 | Justify against shipped code |
| Latch certificate / NVNP conjectures docs | `LATCH_CERTIFICATE.md`, `NVNP_CONJECTURES.md` | **research docs** | 3 | 20 | Explicit non-Clay |
| Compliance/legal suite beyond threat model | extra policy packs | **ops/legal** | 3 | 40–80 | Split product vs corporate |

**Book B full-repo blend (Damien sheet):** Tier1 295h + Tier2 505h + Tier3 292h
→ **≈ €322k**. Use only when capitalising **research + product** together.

---

## 3. Explicit exclusions (not in either book as “product IP”)

- Collapse Universality / NP⊆P claims — [SIDED_ASIDE.md](SIDED_ASIDE.md)
- Consciousness / ket marketing
- Remote `/v1/execute` (refused by design)
- Valuation or revenue multiples

---

## 4. Auditor one-pager claims (evidence)

1. **Classification** follows `catalog.TIERS` and UNIFIED_USEFUL live modules.
2. **Book A** is sufficient to recreate a check-only governed request gate with
   HAIS + Haven2 latch + signed audit + multi-tenant lite sidecar.
3. **Tier 1** hours in Book A are limited to control/Haven2 math actually
   wired on or beside the live path; ZK/CBF research stays Book B.
4. **Cost ≠ fair value**; impairment / market evidence still required for
   statutory fair value if used beyond management accounts.

---

## 5. Suggested planning numbers to cite

| Cite as | Figure |
|---------|--------|
| Internal book value (useful path) | **~€200k–€230k** |
| Full-repo recreate (product + R&D) | **~€322k** |
| Replacement band (earlier systems estimate) | **€120k–€500k** useful path |

Update this file when catalog tiers or live wiring change.
