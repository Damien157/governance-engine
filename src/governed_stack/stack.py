"""
GovernedStack — compose existing packages into one governed request pipeline.

Does not rewrite HAIS / Haven2 / imprint / CLF-CBF-QP internals; only wires them.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

# ---------------------------------------------------------------------------
# Import path bootstrap (repo layout is multi-root)
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]


def ensure_import_paths() -> Path:
    """Put src/, repo root, hais/, imprint/src, haven2/src on sys.path once.

    Repo root precedes hais/ so certified_governance_unified.py (v1.1) wins
    over the hais/ re-export stub. Also enables sketch imports: diagnostics/,
    solvers/, fluids/, haven/.
    """
    roots = [
        _REPO_ROOT / "src",
        _REPO_ROOT,  # unified module + diagnostics/solvers/fluids/haven
        _REPO_ROOT / "hais",
        _REPO_ROOT / "imprint" / "src",
        _REPO_ROOT / "haven2" / "src",
    ]
    # Insert reversed so the first entry in roots ends up at sys.path[0].
    for p in reversed(roots):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)
    # If both root and hais are present, keep root ahead of hais (unified wins).
    root_s = str(_REPO_ROOT)
    hais_s = str(_REPO_ROOT / "hais")
    if root_s in sys.path and hais_s in sys.path:
        if sys.path.index(root_s) > sys.path.index(hais_s):
            sys.path.remove(root_s)
            sys.path.insert(sys.path.index(hais_s), root_s)
    return _REPO_ROOT


ensure_import_paths()


# Sole ops source of truth is certified_governance_unified v1.1.
# Legacy split modules (hais/certified_governance.py, zk_enhanced_governance.py)
# remain on disk but are not imported here.
from certified_governance_unified import (  # noqa: E402
    ZKEnhancedGovernanceEngine as _OpsEngine,
)
from hais_unified_kernel import SovereignKernel  # noqa: E402
from haven2 import Haven2Engine  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from .contracts import (  # noqa: E402
    IntentValidationError,
    intent_invalid_envelope,
    intent_to_govern_dict,
    map_error_code,
)
from .observability import (  # noqa: E402
    DecisionMetrics,
    decision_label_from_envelope,
    structured_log,
)


class GovernedStack:
    """
    Unified entry point:

      Ops (CGE/ZK) → HAIS SovereignKernel → Haven2 energy/latch
        → optional imprint 3DM / CLF-CBF-QP solvers when ALLOW.
    """

    CAP_THROTTLE = 0.25

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        crypto: Any = None,
    ) -> None:
        ensure_import_paths()
        cfg = dict(_OpsEngine._default_config())
        if config:
            cfg.update(config)
        self.config = cfg
        self.engine = _OpsEngine(cfg, crypto=crypto)
        self.kernel = SovereignKernel()  # sigmoid m=0.5, cap=exp(-2.2*S*τ)
        self.haven2 = Haven2Engine()
        self._qp = None
        self._plant = None
        self._imprint_ready = False
        self.metrics = DecisionMetrics()

    # ------------------------------------------------------------------
    # Lazy solvers
    # ------------------------------------------------------------------

    def _ensure_qp(self):
        if self._qp is not None:
            return self._qp
        import numpy as np

        from governance_engine import (
            CLFCBFQPController,
            ControlBarrierFunction,
            ControlLyapunovFunction,
            Plant,
        )

        plant = Plant()
        clf = ControlLyapunovFunction(plant, x_c=np.array([0.5, 0.0]))
        cbf = ControlBarrierFunction(plant, p_max=1.0, k_cbf=2.0)
        self._plant = plant
        self._qp = CLFCBFQPController(clf, cbf)
        return self._qp

    def _ensure_imprint(self):
        if self._imprint_ready:
            return
        # Touch imports so failures surface once, lazily.
        import imprint  # noqa: F401

        self._imprint_ready = True

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def issue_token(self, user: str, role: str = "user") -> str:
        """Issue a JWT for demos / tests via the underlying SecurityLayer."""
        return self.engine.security.generate_token(user, role)

    async def govern(self, intent: dict, token: str, **opts) -> dict:
        """
        Run the composed pipeline. Always returns a unified envelope:

          {decision, reasons, entry_id, hais{...}, haven2{...}, solver?, audit?,
           notes?, error_code?, latency_ms?}

        Intent contracts (pydantic): BaseModel intents are dumped; plain dicts
        are validated via GovernIntent / channel models. Validation failures
        return BLOCK + error_code=GOV_INTENT_INVALID (do not raise) so the
        gate stays consistent with other non-ALLOW outcomes.
        """
        t0 = time.perf_counter()
        action_hint = None
        try:
            if isinstance(intent, BaseModel):
                action_hint = getattr(intent, "action", None)
                intent = intent_to_govern_dict(intent)
            elif isinstance(intent, dict):
                action_hint = intent.get("action")
                try:
                    intent = intent_to_govern_dict(intent)
                except IntentValidationError as exc:
                    latency_ms = (time.perf_counter() - t0) * 1000.0
                    envelope = intent_invalid_envelope(
                        errors=exc.errors, message=str(exc)
                    )
                    envelope["latency_ms"] = round(latency_ms, 3)
                    self.metrics.record("BLOCK", latency_ms)
                    structured_log(
                        "govern_decision",
                        decision="BLOCK",
                        latency_ms=round(latency_ms, 3),
                        entry_id=None,
                        action=action_hint,
                        reasons=envelope.get("reasons"),
                        error_code=envelope.get("error_code"),
                    )
                    return envelope
            else:
                latency_ms = (time.perf_counter() - t0) * 1000.0
                envelope = intent_invalid_envelope(
                    errors=[{"msg": f"unsupported intent type: {type(intent).__name__}"}],
                    message=f"unsupported intent type: {type(intent).__name__}",
                )
                envelope["latency_ms"] = round(latency_ms, 3)
                self.metrics.record("BLOCK", latency_ms)
                structured_log(
                    "govern_decision",
                    decision="BLOCK",
                    latency_ms=round(latency_ms, 3),
                    entry_id=None,
                    action=None,
                    reasons=envelope.get("reasons"),
                    error_code=envelope.get("error_code"),
                )
                return envelope

            envelope = await self._govern_inner(intent, token, **opts)
        except IntentValidationError as exc:
            latency_ms = (time.perf_counter() - t0) * 1000.0
            envelope = intent_invalid_envelope(errors=exc.errors, message=str(exc))
            envelope["latency_ms"] = round(latency_ms, 3)
            self.metrics.record("BLOCK", latency_ms)
            structured_log(
                "govern_decision",
                decision="BLOCK",
                latency_ms=round(latency_ms, 3),
                entry_id=None,
                action=action_hint,
                reasons=envelope.get("reasons"),
                error_code=envelope.get("error_code"),
            )
            return envelope
        except Exception as exc:
            latency_ms = (time.perf_counter() - t0) * 1000.0
            self.metrics.record("ERROR", latency_ms)
            structured_log(
                "govern_error",
                decision="ERROR",
                latency_ms=round(latency_ms, 3),
                error=str(exc),
                action=(intent or {}).get("action") if isinstance(intent, dict) else action_hint,
            )
            raise
        latency_ms = (time.perf_counter() - t0) * 1000.0
        label = decision_label_from_envelope(envelope)
        code = map_error_code(
            decision=label,
            reasons=envelope.get("reasons"),
            notes=envelope.get("notes"),
        )
        if code and "error_code" not in envelope:
            envelope["error_code"] = code
        envelope["latency_ms"] = round(latency_ms, 3)
        self.metrics.record(label, latency_ms)
        structured_log(
            "govern_decision",
            decision=label,
            latency_ms=round(latency_ms, 3),
            entry_id=envelope.get("entry_id"),
            action=(intent or {}).get("action") if isinstance(intent, dict) else action_hint,
            reasons=envelope.get("reasons"),
            error_code=envelope.get("error_code"),
        )
        return envelope

    async def _govern_inner(self, intent: dict, token: str, **opts) -> dict:
        """Inner live pipeline (metrics recorded by govern())."""
        generate_proofs = bool(opts.get("generate_proofs", False))
        proof_mode = opts.get("proof_mode", "background")

        # ---- a) Ops layer -------------------------------------------------
        ops_kwargs: Dict[str, Any] = {}
        if type(self.engine).__name__ == "ZKEnhancedGovernanceEngine":
            ops_kwargs["generate_proofs"] = generate_proofs
            ops_kwargs["proof_mode"] = proof_mode

        ops = await self.engine.execute_governed_action(intent, token, **ops_kwargs)

        if ops.get("status") == "error":
            reasons = [f"ops_error:{ops.get('error', 'unknown')}"]
            code = map_error_code(decision="BLOCK", reasons=reasons)
            return self._envelope(
                decision="BLOCK",
                reasons=reasons,
                entry_id=None,
                hais=self._empty_hais(),
                haven2=self._haven2_snapshot(None),
                audit={"ops": ops},
                notes=["ops layer returned status=error; solvers skipped"],
                error_code=code,
            )

        result = ops.get("result") or {}
        decision = result.get("decision", "BLOCK")
        reasons = list(result.get("policy_reasons") or [])
        entry_id = result.get("entry_id")
        risk_signal = float(result.get("risk_signal") or 0.0)
        notes: list[str] = []

        # Telemetry for HAIS: prefer intent-provided, else map from ops signals.
        telemetry = dict(intent.get("telemetry") or {})
        analytics = ops.get("analytics") or {}
        anomaly_payload = analytics.get("anomaly_signal") or {}
        telemetry.setdefault("stress", float(intent.get("stress", risk_signal)))
        telemetry.setdefault(
            "anomaly",
            float(anomaly_payload.get("anomaly_signal", risk_signal)),
        )
        telemetry.setdefault("drift", float(intent.get("drift", 0.0)))

        difficulty = intent.get("difficulty")
        if difficulty is None:
            difficulty = (intent.get("payload") or {}).get("difficulty", 0.5)
        x = float(difficulty)

        # ---- b) HAIS sovereign kernel ------------------------------------
        hais_metrics = self.kernel.evaluate_state(x, telemetry)
        cap = float(hais_metrics["capability_cap"])
        if cap < self.CAP_THROTTLE:
            if decision == "ALLOW":
                notes.append(
                    "hais_capability_cap override: CGE logged ALLOW; "
                    "stack overrides decision to BLOCK without a second audit write"
                )
            decision = "BLOCK"
            if "hais_capability_cap" not in reasons:
                reasons.append("hais_capability_cap")

        # Haven2 always runs for envelope completeness; solvers gated below.

        # ---- c) Haven2 energy + transistor --------------------------------
        c_t = float(risk_signal if risk_signal else hais_metrics["risk_metric"])
        _v_raw = intent.get("v_t", telemetry.get("anomaly", 0.02))
        v_t = float(_v_raw if _v_raw is not None else 0.02)
        step = self.haven2.step(c_t=c_t, v_t=v_t)
        transistor_open = bool(step.open)
        haven2_info = {
            "realm": step.realm.value if hasattr(step.realm, "value") else str(step.realm),
            "p_hat": float(step.p_hat),
            "open": transistor_open,
            "e": float(step.e),
            "c": float(step.c),
        }
        # Finite Dirichlet spectral summaries over engine history (not Riemann).
        # FIX 4: surface zeta_summaries failures explicitly (do not silently drop).
        try:
            haven2_info["zeta"] = self.haven2.zeta_summaries()
        except Exception as exc:
            haven2_info["zeta_error"] = f"{type(exc).__name__}: {exc}"
            notes.append(f"haven2_zeta_summaries_error:{exc}")

        action = str(intent.get("action", "query"))
        solver_ok = decision == "ALLOW" and (
            transistor_open or action == "query"
        )
        # Prefer: control/3dm require open transistor; query/mail/calendar/social may
        # proceed when ALLOW (latch does not block those). Closed latch → BLOCK.
        if decision == "ALLOW" and action in ("control", "3dm") and not transistor_open:
            decision = "BLOCK"
            if "transistor_closed" not in reasons:
                reasons.append("transistor_closed")
            notes.append(
                "haven2 transistor/latch closed: control/3dm blocked "
                "(GOV_LATCH_CLOSED); query/mail/calendar/social would still be ALLOW"
            )
            solver_ok = False

        solver: Optional[Dict[str, Any]] = None

        # ---- d) imprint 3DM (demo catalog instance) ------------------------
        if solver_ok and action == "3dm":
            try:
                solver = {"kind": "3dm", **self._run_3dm_demo(intent)}
            except Exception as exc:
                notes.append(f"3dm_solver_error:{exc}")
                solver = {"kind": "3dm", "error": str(exc)}

        # ---- e) CLF-CBF-QP one step ----------------------------------------
        if solver_ok and action == "control":
            try:
                solver = {"kind": "control", **self._run_qp_step(intent)}
            except Exception as exc:
                notes.append(f"qp_solver_error:{exc}")
                solver = {"kind": "control", "error": str(exc)}

        code = map_error_code(decision=decision, reasons=reasons, notes=notes)
        return self._envelope(
            decision=decision,
            reasons=reasons,
            entry_id=entry_id,
            hais={
                "cap": cap,
                "risk": float(hais_metrics["risk_metric"]),
                "instability": float(hais_metrics["instability"]),
                "S": float(hais_metrics["S"]),
                "tau": float(hais_metrics["tau"]),
                "r_prime": float(hais_metrics["r_prime"]),
                "delta_r_prime": float(hais_metrics["delta_r_prime"]),
            },
            haven2=haven2_info,
            solver=solver,
            audit={
                "ops_decision": result.get("decision"),
                "logged": result.get("logged"),
                "risk_signal": risk_signal,
                "attestations": ops.get("attestations"),
            },
            notes=notes or None,
            error_code=code,
        )

    # ------------------------------------------------------------------
    # Solvers
    # ------------------------------------------------------------------

    def _run_3dm_demo(self, intent: dict) -> Dict[str, Any]:
        """One small catalog/demo instance — not the full 6-instance suite."""
        self._ensure_imprint()
        import numpy as np
        from imprint import SuperpositionSampler, generate_demo
        from imprint.enumerator import enumerate_S_L, matching_key
        from imprint.verifier import SoftVerifier

        inst = generate_demo()
        S, L = enumerate_S_L(inst)
        soft = SoftVerifier()
        soft.train_on_instance(inst, S)
        sampler = SuperpositionSampler(inst, S, soft, mode="reversed")
        L_labels = {matching_key(M) for M in L}
        hit = sampler.hit_probability(L_labels)
        seed = int(intent.get("seed", 0))
        collapsed = sampler.collapse(np.random.default_rng(seed))
        # collapse returns a matching key (tuple of triples)
        key = collapsed if isinstance(collapsed, tuple) else matching_key(collapsed)
        return {
            "instance": inst.name,
            "abs_S": len(S),
            "abs_L": len(L),
            "matching_key": str(key),
            "hit_imprint_reversed": float(hit),
            "in_L": key in L_labels,
        }

    def _run_qp_step(self, intent: dict) -> Dict[str, Any]:
        import numpy as np

        qp = self._ensure_qp()
        u_nom = float(intent.get("u_nom", 5.0))
        x = intent.get("x")
        if x is None:
            x = np.array([0.8, 0.5], dtype=float)  # near barrier, needs filter
        else:
            x = np.asarray(x, dtype=float).reshape(2)
        sol = qp.solve(x, u_nom)
        return {
            "u": float(sol["u"]),
            "h": float(sol["h"]),
            "p": float(x[0]),
            "v": float(x[1]),
            "u_nom": u_nom,
            "status": sol.get("status"),
            "feasible": bool(sol.get("feasible")),
        }

    # ------------------------------------------------------------------
    # Envelope helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _empty_hais() -> Dict[str, Optional[float]]:
        return {
            "cap": None,
            "risk": None,
            "instability": None,
            "S": None,
            "tau": None,
            "r_prime": None,
            "delta_r_prime": None,
        }

    def _haven2_snapshot(self, step) -> Dict[str, Any]:
        if step is None:
            return {
                "realm": self.haven2.realm.value
                if hasattr(self.haven2.realm, "value")
                else str(self.haven2.realm),
                "p_hat": float(self.haven2.p_hat),
                "open": False,
            }
        return {
            "realm": step.realm.value if hasattr(step.realm, "value") else str(step.realm),
            "p_hat": float(step.p_hat),
            "open": bool(step.open),
        }

    @staticmethod
    def _envelope(
        *,
        decision: str,
        reasons: list,
        entry_id,
        hais: dict,
        haven2: dict,
        solver: Optional[dict] = None,
        audit: Optional[dict] = None,
        notes: Optional[list] = None,
        error_code: Optional[str] = None,
    ) -> dict:
        out: Dict[str, Any] = {
            "decision": decision,
            "reasons": reasons,
            "entry_id": entry_id,
            "hais": hais,
            "haven2": haven2,
        }
        if solver is not None:
            out["solver"] = solver
        if audit is not None:
            out["audit"] = audit
        if notes:
            out["notes"] = notes
        if error_code:
            out["error_code"] = error_code
        return out
