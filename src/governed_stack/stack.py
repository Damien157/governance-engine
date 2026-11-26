"""
GovernedStack — compose existing packages into one governed request pipeline.

Does not rewrite HAIS / Haven2 / imprint / CLF-CBF-QP internals; only wires them.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

try:  # POSIX: cross-process lock for the audit fallback file
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - non-POSIX: thread lock only
    _fcntl = None

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
        # Last-resort sink for a final-decision audit write that could not
        # reach the hash chain at all (see _record_final_write_failure).
        # Process-wide thread lock; cross-process safety is an flock on the
        # file itself (see _append_fallback_record).
        self._fallback_lock = GovernedStack._FALLBACK_THREAD_LOCK

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

            _payload = intent.get("payload") if isinstance(intent, dict) else None
            if _payload is not None and not isinstance(_payload, dict):
                # Fail closed: a non-dict payload is never scanned field-by-field
                # downstream; BLOCK instead of crashing or allowing.
                raise IntentValidationError(
                    f"payload must be an object, got {type(_payload).__name__}",
                    errors=[{"type": "dict_type", "loc": ["payload"],
                             "msg": "payload must be an object"}],
                )
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
        # Human REVIEW voucher (from resolve_review) — exact-intent ALLOW short-circuit.
        if opts.get("approval_voucher"):
            ops_kwargs["approval_voucher"] = opts["approval_voucher"]

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
            _p = intent.get("payload")
            difficulty = _p.get("difficulty", 0.5) if isinstance(_p, dict) else 0.5
        x = float(difficulty)

        # ---- b) HAIS sovereign kernel ------------------------------------
        hais_metrics = self.kernel.evaluate_state(x, telemetry)
        cap = float(hais_metrics["capability_cap"])
        if cap < self.CAP_THROTTLE:
            if decision == "ALLOW":
                notes.append(
                    "hais_capability_cap override: CGE logged ALLOW; "
                    "stack overrides decision to BLOCK; final decision appended as a linked audit entry"
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
        # Gates 3dm/control solvers only: ALLOW and (latch open OR action=="query").
        # Latch decision BLOCK below is control/3dm only — mail/calendar/social stay
        # ALLOW when closed and do not consult solver_ok (no solvers on those actions yet).
        solver_ok = decision == "ALLOW" and (
            transistor_open or action == "query"
        )
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
        qp_tightened: Optional[Dict[str, str]] = None

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
            # Feed the controller's safety signal back into governance
            # (tighten-only: ALLOW/REVIEW -> BLOCK; never loosened, and it runs after
            # the ops layer so an approval voucher cannot reopen it). The ops
            # layer (CGE) has already written its audit entry with the
            # pre-solver decision; the final decision is appended below as a
            # linked "stack_final" audit entry (audit.final_entry_id).
            qp_reason = self._qp_failure_reason(solver)
            if qp_reason is not None:
                if qp_reason not in reasons:
                    reasons.append(qp_reason)
                tightened = self._tighten(decision, "BLOCK")
                if tightened != decision:
                    notes.append(
                        f"{qp_reason}: stack tightens {decision} -> {tightened} "
                        "after control step; final decision appended as a linked "
                        "audit entry"
                    )
                    qp_tightened = {"from": decision, "to": tightened, "reason": qp_reason}
                decision = tightened

        # Record the final decision in the signed audit chain whenever the
        # stack tightened what the ops layer logged (HAIS cap, latch, QP).
        # The original row is never edited: a linked "stack_tightened" entry
        # is appended to the same hash chain (and advances the truncation
        # anchor). Tighten-only: we only ever append a stricter decision.
        #
        # Everything that explains the override goes in the *intent*, which
        # is inside the hashed + signed body: the overridden entry id AND its
        # entry_hash (so the link survives concurrent writers landing between
        # the two rows), from/to, reasons and qp_tightened. ``metadata`` and
        # ``policy_reasons`` columns are NOT covered by the entry hash, so
        # they only mirror the signed intent for convenience.
        # If the lookup or the write fails the decision stays (or becomes)
        # BLOCK.
        ops_decision = result.get("decision")
        final_entry_id: Optional[str] = None
        final_write_error: Optional[str] = None
        final_write_trace: Optional[Dict[str, Any]] = None
        if entry_id and ops_decision and decision != ops_decision:
            overridden_hash: Optional[str] = None
            try:
                overridden_hash = self.engine.storage.entry_hash_of(entry_id)
                if not overridden_hash:
                    raise LookupError(f"overridden entry {entry_id} not found in audit_log")
                link = {
                    "stack_final_for": entry_id,
                    "overrides_entry_id": entry_id,
                    "overrides_entry_hash": overridden_hash,
                    "from": ops_decision,
                    "to": decision,
                    "reasons": list(reasons),
                    **({"qp_tightened": qp_tightened} if qp_tightened else {}),
                }
                final_entry_id = self.engine.storage.log_decision(
                    intent=link,
                    decision=decision,
                    result="stack_tightened",
                    verification_score=0.3,
                    risk_signal=risk_signal,
                    anomaly_signal=0.0,
                    policy_reasons=list(reasons),
                    metadata={k: v for k, v in link.items() if k != "stack_final_for"},
                    engine_version="GovernedStack final-decision",
                    enqueue_review=False,
                )
            except Exception as exc:
                final_write_error = f"{type(exc).__name__}: {exc}"
                notes.append(f"audit_final_write_failed:{exc}")
                if "audit_final_write_failed" not in reasons:
                    reasons.append("audit_final_write_failed")
                decision = self._tighten(decision, "BLOCK")
                # Leave a trace of the failure itself: a minimal signed row
                # in the chain, else a hashed line in the fallback file.
                # Never raises; the decision is BLOCK either way.
                final_write_trace = self._record_final_write_failure(
                    entry_id=entry_id,
                    overridden_hash=overridden_hash,
                    ops_decision=ops_decision,
                    decision=decision,
                    reasons=list(reasons),
                    exc=exc,
                    risk_signal=risk_signal,
                )
                if final_write_trace.get("fallback_error"):
                    fe = final_write_trace["fallback_error"]
                    notes.append(
                        f"audit_final_trace_lost:{fe['error_class']}:{fe['error_sha256'][:16]}"
                    )

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
                **({"qp_tightened": qp_tightened} if qp_tightened else {}),
                **({"final_entry_id": final_entry_id} if final_entry_id else {}),
                **({"final_write_error": final_write_error} if final_write_error else {}),
                **({"final_write_trace": final_write_trace} if final_write_trace else {}),
            },
            notes=notes or None,
            error_code=code,
        )

    # ------------------------------------------------------------------
    # Final-decision audit failure trace
    # ------------------------------------------------------------------

    FALLBACK_GENESIS = "0" * 64
    _FALLBACK_THREAD_LOCK = threading.Lock()

    @staticmethod
    def _error_digest(exc: BaseException) -> Dict[str, Any]:
        """Exception class + sha256 (and length) of its message, never the text.

        Storage-layer exception text can carry query values or content, so
        the trace, fallback file, envelope and log only ever see this digest.
        The hash is a correlation aid (match against the process's own logs),
        not secrecy: a short, guessable message can be brute-forced.
        """
        try:
            msg = str(exc)
        except Exception:  # pragma: no cover - pathological __str__
            msg = "<unprintable>"
        return {
            "error_class": type(exc).__name__,
            "error_sha256": hashlib.sha256(msg.encode("utf-8", "replace")).hexdigest(),
            "error_len": len(msg),
        }

    def _fallback_path(self) -> Optional[str]:
        """Path of the append-only fallback file (None if none can be derived).

        ``config["audit_fallback_path"]`` wins; otherwise it sits next to a
        file-backed audit DB as ``<db>.final_write_failures.jsonl``.
        """
        p = self.config.get("audit_fallback_path")
        if p:
            return str(p)
        db = str(getattr(self.engine.storage, "db_path", "") or "")
        if not db or db == ":memory:" or db.startswith("file:"):
            return None
        return db + ".final_write_failures.jsonl"

    def _record_final_write_failure(
        self,
        *,
        entry_id: str,
        overridden_hash: Optional[str],
        ops_decision: str,
        decision: str,
        reasons: list,
        exc: BaseException,
        risk_signal: Any,
    ) -> Dict[str, Any]:
        """Leave a durable trace when the linked final-decision write failed.

        1. One minimal BLOCK row (``result=stack_final_write_failed``) via
           ``log_decision``; the failure facts sit in the signed intent.
        2. If that also fails: one JSON line appended (O_APPEND + fsync) to
           the fallback file, carrying its own sha256, the previous line's
           sha256 and the last known chain tip / seq.

        Never raises and never changes the decision (the caller has already
        tightened to BLOCK).
        """
        storage = self.engine.storage
        # A failed commit can leave this thread's connection mid-transaction
        # with the half-written row; roll it back so the trace row does not
        # commit it alongside (it would carry a stale prev_hash).
        try:
            storage.conn.rollback()
        except Exception:
            pass
        facts: Dict[str, Any] = {
            "stack_final_write_failed_for": entry_id,
            **({"overrides_entry_hash": overridden_hash} if overridden_hash else {}),
            "from": ops_decision,
            "to": decision,
            "error_class": type(exc).__name__,
            "error_sha256": self._error_digest(exc)["error_sha256"],
        }
        trace: Dict[str, Any] = {"kind": None}
        try:
            trace_id = storage.log_decision(
                intent=facts,
                decision="BLOCK",
                result="stack_final_write_failed",
                verification_score=0.0,
                risk_signal=risk_signal,
                anomaly_signal=0.0,
                policy_reasons=list(reasons),
                metadata=dict(facts),
                engine_version="GovernedStack final-write-failure",
                enqueue_review=False,
            )
            return {"kind": "audit_row", "entry_id": trace_id}
        except Exception as trace_exc:
            trace["trace_row_error"] = self._error_digest(trace_exc)
            try:
                storage.conn.rollback()
            except Exception:
                pass

        path = self._fallback_path()
        record = {
            "ts": time.time(),
            "event": "stack_final_write_failed",
            **facts,
            "reasons": list(reasons),
            "primary_error": self._error_digest(exc),
            "trace_row_error": trace["trace_row_error"],
            "chain_tip": getattr(storage, "_last_hash", None),
            "chain_seq": getattr(storage, "_seq", None),
        }
        try:
            if not path:
                raise RuntimeError("no audit_fallback_path configured for a non-file audit DB")
            digest = self._append_fallback_record(path, record)
            trace.update(kind="fallback_file", path=path, sha256=digest,
                         chain_tip=record["chain_tip"])
        except Exception as fb_exc:
            trace["kind"] = "lost"
            trace["fallback_error"] = self._error_digest(fb_exc)
            trace["record"] = record
        structured_log("audit_final_write_failed", entry_id=entry_id, trace=trace)
        return trace

    @classmethod
    def _append_fallback_record(cls, path: str, record: Dict[str, Any]) -> str:
        """Append one hash-linked line; return its sha256.

        The tail read and the append happen under one exclusive lock so two
        failures can never link to the same ``prev_sha256`` (no fork):
        a process-wide thread lock plus ``fcntl.flock(LOCK_EX)`` on the file
        itself, which also excludes other processes and other stacks. The
        file is opened once (O_RDWR|O_APPEND|O_CREAT, mode 0o600), read and
        appended through the same descriptor, and fsynced before unlock.
        Mutates ``record["prev_sha256"]``.
        """
        with cls._FALLBACK_THREAD_LOCK:
            fd = os.open(path, os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                if _fcntl is not None:
                    _fcntl.flock(fd, _fcntl.LOCK_EX)
                try:
                    st = os.fstat(fd)
                    if st.st_mode & 0o077:
                        try:  # tighten a pre-existing, looser file
                            os.fchmod(fd, 0o600)
                        except OSError:
                            pass
                    record["prev_sha256"] = cls._tail_sha(fd, st.st_size)
                    canon = json.dumps(record, sort_keys=True, default=str)
                    digest = hashlib.sha256(canon.encode()).hexdigest()
                    line = json.dumps({"record": record, "sha256": digest},
                                      sort_keys=True, default=str) + "\n"
                    data = line.encode()
                    written = os.write(fd, data)
                    if written != len(data):
                        raise OSError(f"short write to fallback file ({written}/{len(data)})")
                    os.fsync(fd)
                    return digest
                finally:
                    if _fcntl is not None:
                        _fcntl.flock(fd, _fcntl.LOCK_UN)
            finally:
                os.close(fd)

    @classmethod
    def _tail_sha(cls, fd: int, size: int) -> str:
        """sha256 of the last non-empty line, read backwards via pread."""
        if size == 0:
            return cls.FALLBACK_GENESIS
        chunk, end, buf = 4096, size, b""
        while end > 0:
            start = max(0, end - chunk)
            buf = os.pread(fd, end - start, start) + buf
            end = start
            stripped = buf.rstrip(b"\n\r \t")
            if not stripped:
                continue
            nl = stripped.rfind(b"\n")
            if nl >= 0 or end == 0:
                last = stripped[nl + 1:]
                return json.loads(last.decode("utf-8"))["sha256"]
        return cls.FALLBACK_GENESIS

    @classmethod
    def _last_fallback_sha(cls, path: str) -> str:
        """sha256 of the last line in the fallback file (genesis if none)."""
        if not os.path.exists(path):
            return cls.FALLBACK_GENESIS
        fd = os.open(path, os.O_RDONLY)
        try:
            return cls._tail_sha(fd, os.fstat(fd).st_size)
        finally:
            os.close(fd)

    @classmethod
    def verify_fallback_file(cls, path: str) -> Dict[str, Any]:
        """Check every fallback line's sha256 and its prev_sha256 link."""
        prev = cls.FALLBACK_GENESIS
        n = 0
        with open(path, "r", encoding="utf-8") as fh:
            for i, raw in enumerate(fh, 1):
                if not raw.strip():
                    continue
                try:
                    doc = json.loads(raw)
                    rec, digest = doc["record"], doc["sha256"]
                except Exception as e:
                    return {"valid": False, "line": i, "error": f"unparseable: {e}"}
                canon = json.dumps(rec, sort_keys=True, default=str)
                if hashlib.sha256(canon.encode()).hexdigest() != digest:
                    return {"valid": False, "line": i, "error": "sha256 mismatch"}
                if rec.get("prev_sha256") != prev:
                    return {"valid": False, "line": i, "error": "prev_sha256 mismatch"}
                prev = digest
                n += 1
        return {"valid": True, "records": n}

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
            "fail_closed": bool(sol.get("fail_closed", False)),
        }

    _DECISION_RANK = {"ALLOW": 0, "REVIEW": 1, "BLOCK": 2}
    _QP_OK_STATUSES = frozenset({"optimal"})

    @classmethod
    def _tighten(cls, current: str, floor: str) -> str:
        """Return the stricter of two decisions (ALLOW < REVIEW < BLOCK).

        Unknown labels rank as BLOCK so tightening never loosens.
        """
        rank = cls._DECISION_RANK
        cur = (current or "BLOCK").upper()
        if rank.get(cur, 2) >= rank.get(floor, 2):
            return cur if cur in rank else "BLOCK"
        return floor

    @classmethod
    def _qp_failure_reason(cls, solver: Optional[Dict[str, Any]]) -> Optional[str]:
        """Reason string if a control-step solver result is not a healthy solve."""
        if not solver or solver.get("kind") != "control":
            return None
        if "error" in solver:
            return "qp_solver_error"
        status = str(solver.get("status"))
        if (
            solver.get("feasible") is not True
            or solver.get("fail_closed")
            or status not in cls._QP_OK_STATUSES
        ):
            return f"qp_not_feasible:{status}"
        return None

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
