"""
HavenUnified — thin facade over GovernedStack plus off-path sketch accessors.

Live decisions always go through GovernedStack.govern (ops → HAIS SovereignKernel
from hais_unified_kernel → Haven2 → optional QP / imprint 3DM). Sketch helpers
import and run demos only; they never feed ALLOW / BLOCK / REVIEW.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any, Dict, Optional

from .algorithm import GovernedAlgorithm
from .calendar import GovernedCalendar
from .catalog import TIERS, catalog_snapshot, describe
from .mail import GovernedMail
from .runtime_bridge import GovernedDecisionEngine
from .social import GovernedPost
from .stack import GovernedStack, ensure_import_paths

_REPO_ROOT = Path(__file__).resolve().parents[2]


class HavenUnified:
    """
    Single front door:

      .govern / .control_step / .demo_3dm  → live GovernedStack
      .mail / .calendar / .post / .algorithm → adapters sharing the same stack
      sketch helpers                       → importable demos, not on decision path
    """

    def __init__(self, stack: Optional[GovernedStack] = None, **stack_kwargs: Any) -> None:
        ensure_import_paths()
        self.stack = stack if stack is not None else GovernedStack(**stack_kwargs)
        self._mail: Optional[GovernedMail] = None
        self._calendar: Optional[GovernedCalendar] = None
        self._post: Optional[GovernedPost] = None
        self._algorithm: Optional[GovernedAlgorithm] = None
        self._runtime: Optional[GovernedDecisionEngine] = None

    # ------------------------------------------------------------------
    # Live adapters (shared stack)
    # ------------------------------------------------------------------

    @property
    def mail(self) -> GovernedMail:
        if self._mail is None:
            self._mail = GovernedMail(stack=self.stack)
        return self._mail

    @property
    def calendar(self) -> GovernedCalendar:
        if self._calendar is None:
            self._calendar = GovernedCalendar(stack=self.stack)
        return self._calendar

    @property
    def post(self) -> GovernedPost:
        if self._post is None:
            self._post = GovernedPost(stack=self.stack)
        return self._post

    # Alias for GovernedOutboundText naming
    @property
    def social(self) -> GovernedPost:
        return self.post

    @property
    def algorithm(self) -> GovernedAlgorithm:
        if self._algorithm is None:
            self._algorithm = GovernedAlgorithm(stack=self.stack)
        return self._algorithm

    @property
    def runtime(self) -> GovernedDecisionEngine:
        """Live decision adapter (constitution pre-check + govern)."""
        if self._runtime is None:
            self._runtime = GovernedDecisionEngine(stack=self.stack)
        return self._runtime

    async def decide(
        self,
        intent: Any,
        token: Optional[str] = None,
        user: str = "damien",
        role: str = "user",
    ) -> Dict[str, Any]:
        """Passthrough to GovernedDecisionEngine.decide (live gate)."""
        return await self.runtime.decide(intent, token=token, user=user, role=role)

    def issue_token(self, user: str, role: str = "user") -> str:
        return self.stack.issue_token(user, role)

    @property
    def metrics(self):
        """Live DecisionMetrics from the underlying GovernedStack (not sketches)."""
        return self.stack.metrics

    async def govern(self, intent: dict, token: str, **opts) -> dict:
        """Passthrough to GovernedStack.govern — the only live decision path."""
        return await self.stack.govern(intent, token, **opts)

    async def control_step(self, intent: dict, token: str, **opts) -> dict:
        """Govern with action=control (live CLF–CBF–QP when ALLOW)."""
        payload = dict(intent)
        payload["action"] = "control"
        return await self.stack.govern(payload, token, **opts)

    async def demo_3dm(self, intent: dict, token: str, **opts) -> dict:
        """Govern with action=3dm (live imprint demo when ALLOW)."""
        payload = dict(intent)
        payload["action"] = "3dm"
        return await self.stack.govern(payload, token, **opts)

    # ------------------------------------------------------------------
    # Sketch accessors — DO NOT call govern for decisions
    # ------------------------------------------------------------------

    def run_3sat_demo(
        self,
        num_vars: int = 4,
        clauses: Optional[list] = None,
    ) -> Dict[str, Any]:
        """Import solvers.hais_3sat and solve a small formula (sketch only)."""
        ensure_import_paths()
        from solvers.hais_3sat import HAIS3SATSolver

        if clauses is None:
            clauses = [
                [1, -2, 3],
                [-1, 2, -3],
                [2, 3, -4],
                [-1, -3, 4],
            ]
        solver = HAIS3SATSolver(num_vars, clauses)
        solution = solver.solve()
        return {
            "tier": "sketch",
            "module": "solvers.hais_3sat",
            "on_decision_path": False,
            "num_vars": num_vars,
            "clauses": clauses,
            "solution": solution,
            "sat": solution is not None,
        }

    def run_topology_diagnostics(self) -> Dict[str, Any]:
        """Run diagnostics.invariant_topology summary (sketch only)."""
        ensure_import_paths()
        from diagnostics.invariant_topology import run_rigorous_diagnostics_test

        results = run_rigorous_diagnostics_test(seed=2026)
        barrier_count = sum(1 for r in results.values() if r.barrier_suspected)
        circular_hits = sum(1 for r in results.values() if r.circular_invariants)
        return {
            "tier": "sketch",
            "module": "diagnostics.invariant_topology",
            "on_decision_path": False,
            "techniques": len(results),
            "barrier_suspected": barrier_count,
            "circular_hits": circular_hits,
            "summary": (
                f"{len(results)} techniques; "
                f"barrier={barrier_count}; circular={circular_hits}"
            ),
        }

    def run_control_sketch(self) -> Dict[str, Any]:
        """
        Run solvers.unified_control_sketch (INCOMPLETE).

        Does not replace src/governance_engine HOCBF / CLFCBFQPController.
        """
        ensure_import_paths()
        mod = importlib.import_module("solvers.unified_control_sketch")
        # Capture prints by just invoking; return a status envelope.
        mod.simulate_unified_control()
        return {
            "tier": "sketch",
            "module": "solvers.unified_control_sketch",
            "on_decision_path": False,
            "label": "incomplete",
            "note": (
                "Incomplete control sketch — live control uses "
                "governance_engine.CLFCBFQPController via GovernedStack"
            ),
            "ok": True,
        }

    def haven_sovereign_demo(self) -> Dict[str, Any]:
        """Import haven.haven_sovereign_engine demo bits (sketch only)."""
        ensure_import_paths()
        from haven.haven_sovereign_engine import HavenSovereignEngine

        engine = HavenSovereignEngine(
            ["NA", "LS", "WM", "RE", "PS", "SC"],
            {"strict_compliance": True},
        )
        status = engine.evaluate_global_governance({"mock_state": True})
        density = engine.compute_intelligence_density([0.12, 0.15, 0.18])
        return {
            "tier": "sketch",
            "module": "haven.haven_sovereign_engine",
            "on_decision_path": False,
            "governance_status": status,
            "intelligence_density": float(density),
        }

    def fluids_smoke(self) -> Dict[str, Any]:
        """Optional Dedalus smoke; skip if dedalus not installed (no heavy install)."""
        ensure_import_paths()
        try:
            importlib.import_module("dedalus.public")
        except Exception:
            return {
                "tier": "sketch",
                "module": "fluids.smoke_taylor_green",
                "on_decision_path": False,
                "skipped": "dedalus",
            }
        # Importable but still heavy — do not run full TG by default.
        return {
            "tier": "sketch",
            "module": "fluids.smoke_taylor_green",
            "on_decision_path": False,
            "skipped": "heavy",
            "note": "Dedalus present; full TG smoke not run by default",
            "importable": True,
        }

    def run_production_pipeline_sketch(
        self,
        payload: Optional[Dict[str, Any]] = None,
        system_state: Optional[Dict[str, float]] = None,
    ) -> Dict[str, Any]:
        """
        Sketch: hais_production_governed_pipeline (random candidates, not real 3DM).

        Uses its own local SovereignKernel copy (m=0.5). Does NOT replace or
        overwrite hais_unified_kernel; live path keeps GovernedStack's kernel.
        """
        ensure_import_paths()
        from hais_production_governed_pipeline import production_governed_pipeline

        payload = payload or {
            "id": "sketch-demo",
            "difficulty": 0.3,
            "telemetry": {},
        }
        system_state = system_state or {
            "stress": 0.1,
            "anomaly": 0.05,
            "drift": 0.0,
        }
        result = production_governed_pipeline(payload, system_state)
        return {
            "tier": "sketch",
            "module": "hais_production_governed_pipeline",
            "on_decision_path": False,
            "note": (
                "Random candidate sketch — not real imprint 3DM; "
                "live path uses GovernedStack + hais_unified_kernel"
            ),
            "result": result,
        }

    # ------------------------------------------------------------------
    # Manifest
    # ------------------------------------------------------------------

    def manifest(self) -> Dict[str, Any]:
        ensure_import_paths()
        snap = catalog_snapshot()
        return {
            "version": "0.3.1",
            "repo_root": str(_REPO_ROOT),
            "front_door": "HavenUnified → GovernedStack.govern",
            "live_kernel": "hais.hais_unified_kernel.SovereignKernel (m=0.5)",
            "tiers": {k: [e["module"] for e in v] for k, v in TIERS.items()},
            "tier_details": snap["tiers"],
            "paths": {
                "src": str(_REPO_ROOT / "src"),
                "hais": str(_REPO_ROOT / "hais"),
                "haven2": str(_REPO_ROOT / "haven2" / "src"),
                "imprint": str(_REPO_ROOT / "imprint" / "src"),
                "diagnostics": str(_REPO_ROOT / "diagnostics"),
                "solvers": str(_REPO_ROOT / "solvers"),
                "fluids": str(_REPO_ROOT / "fluids"),
                "haven": str(_REPO_ROOT / "haven"),
            },
            "import_check": snap["import_check"],
            "live_ok": snap["live_ok"],
            "inventory": describe(),
        }


# Alias requested in the brief
GovernedUnified = HavenUnified

__all__ = ["HavenUnified", "GovernedUnified", "GovernedDecisionEngine"]
