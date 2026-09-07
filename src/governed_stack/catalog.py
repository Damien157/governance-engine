"""
Structured inventory of governed-stack modules by tier.

live  — front-door decision path (GovernedStack + action bus + mail/calendar/social + runtime_bridge)
core  — packages composed by GovernedStack on the live path
sketch — importable experiments; never consulted for ALLOW / BLOCK / REVIEW
"""

from __future__ import annotations

import importlib
from typing import Any, Dict, List

# Each entry: module import path, short role, on-disk location (repo-relative).
TIERS: Dict[str, List[Dict[str, str]]] = {
    "live": [
        {
            "module": "governed_stack.stack",
            "symbol": "GovernedStack",
            "path": "src/governed_stack/stack.py",
            "role": "Front door: ops → HAIS → Haven2 → optional QP/3DM",
        },
        {
            "module": "governed_stack.action_bus",
            "symbol": "GovernedActionBus",
            "path": "src/governed_stack/action_bus.py",
            "role": "Mutation facade: require_allow before side_effect (no bypass)",
        },
        {
            "module": "governed_stack.mail",
            "symbol": "GovernedMail",
            "path": "src/governed_stack/mail.py",
            "role": "Outbound mail gate (subject/body only)",
        },
        {
            "module": "governed_stack.calendar",
            "symbol": "GovernedCalendar",
            "path": "src/governed_stack/calendar.py",
            "role": "Calendar write gate (summary/description/location)",
        },
        {
            "module": "governed_stack.social",
            "symbol": "GovernedPost",
            "path": "src/governed_stack/social.py",
            "role": "Outbound social/post gate (platform tag + body)",
        },
        {
            "module": "governed_stack.unified",
            "symbol": "HavenUnified",
            "path": "src/governed_stack/unified.py",
            "role": "Thin facade over GovernedStack + sketch accessors",
        },
        {
            "module": "governed_stack.runtime_bridge",
            "symbol": "GovernedDecisionEngine",
            "path": "src/governed_stack/runtime_bridge.py",
            "role": "Live decide adapter (constitution/halt → GovernedStack.govern)",
        },
    ],
    "core": [
        {
            "module": "certified_governance_unified",
            "symbol": "ZKEnhancedGovernanceEngine",
            "path": "certified_governance_unified.py",
            "role": "Ops (preferred): unified v1.1 CGE+ZK, crypto=, tight phone regex",
        },
        {
            "module": "certified_governance",
            "symbol": "CertifiedGovernanceEngine",
            "path": "hais/certified_governance.py",
            "role": "LEGACY (not preferred): JWT/PolicyEngine/signed audit — keep for catalog; stack hard-requires unified v1.1",
        },
        {
            "module": "zk_enhanced_governance",
            "symbol": "ZKEnhancedGovernanceEngine",
            "path": "hais/zk_enhanced_governance.py",
            "role": "LEGACY (not preferred): ZK/attestation side-channel — keep for catalog; stack hard-requires unified v1.1",
        },
        {
            "module": "hais_unified_kernel",
            "symbol": "SovereignKernel",
            "path": "hais/hais_unified_kernel.py",
            "role": "HAIS kernel: sigmoid m=0.5, capability cap",
        },
        {
            "module": "haven2",
            "symbol": "Haven2Engine",
            "path": "haven2/src/haven2",
            "role": "Energy recurrence + transistor latch",
        },
        {
            "module": "governance_engine",
            "symbol": "CLFCBFQPController",
            "path": "src/governance_engine",
            "role": "CLF–CBF–QP safety filter (live control action)",
        },
        {
            "module": "imprint",
            "symbol": "generate_demo",
            "path": "imprint/src/imprint",
            "role": "3DM weighted-collapse sampler (live 3dm action)",
        },
    ],
    "sketch": [
        {
            "module": "solvers.cdcl",
            "symbol": "(binary)",
            "path": "solvers/cdcl.c",
            "role": "CDCL SAT teaching solver (exact instance decision; not P=NP) — off decision path",
            "probe": "source",
        },

        {
            "module": "solvers.hais_3sat",
            "symbol": "HAIS3SATSolver",
            "path": "solvers/hais_3sat.py",
            "role": "3-SAT DPLL demo — off decision path",
        },
        {
            "module": "diagnostics.invariant_topology",
            "symbol": "run_rigorous_diagnostics_test",
            "path": "diagnostics/invariant_topology.py",
            "role": "Topology / invariant diagnostics — off decision path",
        },
        {
            "module": "solvers.unified_control_sketch",
            "symbol": "simulate_unified_control",
            "path": "solvers/unified_control_sketch.py",
            "role": "Incomplete control sketch — does NOT replace HOCBF QP",
        },
        {
            "module": "solvers.governance_engine_osqp_qp",
            "symbol": "GovernanceEngine",
            "path": "solvers/governance_engine_osqp_qp.py",
            "role": "Alternate OSQP CLF–CBF QP sketch — live QP remains CVXOPT CLFCBFQPController",
        },
        {
            "module": "solvers.hais_osqp_clf_cbf_engine",
            "symbol": "GovernanceEngine",
            "path": "solvers/hais_osqp_clf_cbf_engine.py",
            "role": "OSQP CLF–CBF+slack+harness sketch — NOT live CVXOPT QP",
        },
        {
            "module": "fluids.hais_taylor_green",
            "symbol": "(module)",
            "path": "fluids/hais_taylor_green.py",
            "role": "Taylor–Green fluids sketch (Dedalus) — off decision path",
            "probe": "source",
        },
        {
            "module": "fluids.hais_taylor_green_v3_paste",
            "symbol": "(module)",
            "path": "fluids/hais_taylor_green_v3_paste.py",
            "role": "Raw TG v3 paste (same Dedalus-3 blockers) — off decision path",
            "probe": "source",
        },
        {
            "module": "fluids.smoke_taylor_green",
            "symbol": "(module)",
            "path": "fluids/smoke_taylor_green.py",
            "role": "Tiny TG smoke (Dedalus optional) — off decision path",
            "probe": "source",
        },
        {
            "module": "fluids.tg_vortex_control_fixed",
            "symbol": "(module)",
            "path": "fluids/tg_vortex_control_fixed.py",
            "role": "FIXED TG + CLF–CBF Dedalus sketch (f_ctrl external, nu_field, margin CBF, volume_average) — off decision path",
            "probe": "source",
        },
        {
            "module": "fluids.control_law",
            "symbol": "cbf_correction",
            "path": "fluids/control_law.py",
            "role": "Pure NumPy TG control-law helpers (CBF margin, clamped adaptive Re) — off decision path",
        },
        {
            "module": "haven.haven_sovereign_engine",
            "symbol": "HavenSovereignEngine",
            "path": "haven/haven_sovereign_engine.py",
            "role": "HavenSovereign sketch — off decision path",
        },
        {
            "module": "hais_production_governed_pipeline",
            "symbol": "production_governed_pipeline",
            "path": "hais/hais_production_governed_pipeline.py",
            "role": "Sketch pipeline (random candidates, not real 3DM); own kernel copy m=0.5 — not live SovereignKernel",
        },
        {
            "module": "hais_unified_all",
            "symbol": "production_governed_pipeline",
            "path": "hais/hais_unified_all.py",
            "role": "Unified HAIS harness (kernel+pipeline+sweeps, m=0.5); random candidates — off decision path",
        },
        {
            "module": "hais_governance_unified_runtime_v02",
            "symbol": "GovernanceKernel",
            "path": "hais/hais_governance_unified_runtime_v02.py",
            "role": "Sketch runtime v0.2 (constitution/bounds/audit/halt) — not live GovernedStack",
        },
    ],
}


def describe() -> str:
    """Human-readable tier inventory."""
    lines = [
        "Governed stack inventory (three tiers)",
        "======================================",
        "live  = decision path front door",
        "core  = composed by GovernedStack when ALLOW",
        "sketch = importable only; never drives ALLOW/BLOCK/REVIEW",
        "",
    ]
    for tier in ("live", "core", "sketch"):
        lines.append(f"[{tier}]")
        for entry in TIERS[tier]:
            lines.append(
                f"  - {entry['symbol']:28s}  {entry['path']:42s}  {entry['role']}"
            )
        lines.append("")
    return "\n".join(lines)


def _try_import(module: str) -> bool:
    try:
        importlib.import_module(module)
        return True
    except Exception:
        return False


def _try_source(path_rel: str) -> bool:
    """True if the source file exists and parses (no side-effect import)."""
    import ast
    from pathlib import Path as _Path

    root = _Path(__file__).resolve().parents[2]
    p = root / path_rel
    if not p.is_file():
        return False
    try:
        ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
        return True
    except Exception:
        return False


def import_check() -> Dict[str, bool]:
    """
    Try importing every catalogued module.

    Fluids TG scripts execute Dedalus at import time — those entries use
    probe="source" (file present + parses) so import_check stays light.

    Returns a flat map module -> ok. Live imports must all be True for a
    healthy front door; sketch failures are reported but do not block live use.
    """
    from .stack import ensure_import_paths

    ensure_import_paths()
    out: Dict[str, bool] = {}
    for tier_entries in TIERS.values():
        for entry in tier_entries:
            mod = entry["module"]
            if mod in out:
                continue
            if entry.get("probe") == "source":
                out[mod] = _try_source(entry["path"])
            else:
                out[mod] = _try_import(mod)
    return out


def live_ok(check: Dict[str, bool] | None = None) -> bool:
    """True iff every live-tier module imports cleanly."""
    status = check if check is not None else import_check()
    return all(status.get(e["module"], False) for e in TIERS["live"])


def catalog_snapshot() -> Dict[str, Any]:
    """Tiers plus import status for manifests."""
    check = import_check()
    return {
        "tiers": {k: list(v) for k, v in TIERS.items()},
        "import_check": check,
        "live_ok": live_ok(check),
    }


__all__ = [
    "TIERS",
    "describe",
    "import_check",
    "live_ok",
    "catalog_snapshot",
]
