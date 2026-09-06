"""
GovernedDecisionEngine — live decision adapter over GovernedStack.

Bridges the teaching sketch (hais_governance_unified_runtime_v02) concepts
(constitution pre-check, human halt, stable decide shape) onto the LIVE
request gate without replacing certified_governance_unified or weakening
RSA-PSS audit. Does NOT put tau/fatigue on mail/calendar.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional, Set

from .stack import GovernedStack, ensure_import_paths

ensure_import_paths()

try:
    from certified_governance_unified import CryptoEngine
except Exception:  # pragma: no cover
    try:
        from certified_governance import CryptoEngine
    except Exception:  # pragma: no cover
        CryptoEngine = None  # type: ignore

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_DIR = _REPO_ROOT / "artifacts" / "runtime"
_DEFAULT_DB = _DEFAULT_DIR / "audit.db"
_DEFAULT_KEY = _DEFAULT_DIR / "signing_key.pem"

# Sketch constitution forbidden set (v0.2) — pre-check only; not tau/fatigue.
FORBIDDEN_ACTIONS: Set[str] = {
    "harm_human",
    "disable_override",
    "self_replicate_unbounded",
}


class _AuditFacade:
    """Expose engine.audit.verify_chain() → CGE AuditStorage when present."""

    def __init__(self, owner: "GovernedDecisionEngine") -> None:
        self._owner = owner

    def verify_chain(self) -> Dict[str, Any]:
        engine = getattr(self._owner.stack, "engine", None)
        storage = getattr(engine, "storage", None) if engine is not None else None
        if storage is not None and hasattr(storage, "verify_chain"):
            return storage.verify_chain()
        return {"ok": True, "note": "sketch-only"}


class GovernedDecisionEngine:
    """
    Thin live adapter:

      constitution pre-check → GovernedStack.govern → stable decide envelope

    Human halt is a local flag (optionally audited via CGE storage).
    """

    def __init__(self, stack: Optional[GovernedStack] = None, **stack_kwargs: Any) -> None:
        ensure_import_paths()
        if stack is not None:
            self.stack = stack
        else:
            self.stack = self._build_default_stack(**stack_kwargs)
        self._halted: bool = False
        self._halt_reason: Optional[str] = None
        self._local_notes: list[str] = []
        self.audit = _AuditFacade(self)

    @staticmethod
    def _build_default_stack(**stack_kwargs: Any) -> GovernedStack:
        runtime_dir = _DEFAULT_DIR
        runtime_dir.mkdir(parents=True, exist_ok=True)
        cfg = {
            "db_path": str(_DEFAULT_DB),
            "signing_key_path": str(_DEFAULT_KEY),
            "log_level": 40,
        }
        user_cfg = dict(stack_kwargs.pop("config", None) or {})
        cfg.update(user_cfg)
        crypto = stack_kwargs.pop("crypto", None)
        if crypto is None and CryptoEngine is not None:
            crypto = CryptoEngine(private_key_path=str(_DEFAULT_KEY))
        return GovernedStack(config=cfg, crypto=crypto, **stack_kwargs)

    # ------------------------------------------------------------------
    # Token / security aliases
    # ------------------------------------------------------------------

    @property
    def security(self):
        """Underlying CGE SecurityLayer (JWT via generate_token)."""
        return self.stack.engine.security

    def issue_token(self, user: str, role: str = "user", ttl: Optional[int] = None) -> str:
        """JWT via stack / security.generate_token."""
        if ttl is not None:
            return self.security.generate_token(user, role, ttl=ttl)
        return self.stack.issue_token(user, role)

    # ------------------------------------------------------------------
    # Human override
    # ------------------------------------------------------------------

    def halt(self, reason: str = "human intervention") -> Dict[str, Any]:
        """Local human halt flag; audit via CGE storage when available."""
        self._halted = True
        self._halt_reason = reason
        note = f"human_halt:{reason}"
        self._local_notes.append(note)
        entry_id = None
        try:
            storage = getattr(self.stack.engine, "storage", None)
            if storage is not None and hasattr(storage, "log_decision"):
                entry_id = storage.log_decision(
                    intent={"action": "human_halt", "payload": {"reason": reason}},
                    decision="BLOCK",
                    result="halted",
                    verification_score=0.0,
                    risk_signal=1.0,
                    anomaly_signal=0.0,
                    policy_reasons=["human_halt", "Runtime"],
                    metadata={"source": "GovernedDecisionEngine.halt", "reason": reason},
                )
        except Exception as exc:  # pragma: no cover — best-effort audit
            self._local_notes.append(f"halt_audit_skipped:{exc}")
        return {"executed": True, "reason": reason, "entry_id": entry_id}

    def reset(self) -> Dict[str, Any]:
        """Clear human halt (does not wipe CGE audit chain)."""
        self._halted = False
        self._halt_reason = None
        self._local_notes.append("reset")
        return {"halted": False}

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    def health(self) -> Dict[str, Any]:
        """Short status: engine module, halted?, cheap live_ok."""
        eng = getattr(self.stack, "engine", None)
        eng_mod = type(eng).__module__ if eng is not None else None
        eng_name = type(eng).__name__ if eng is not None else None
        # Cheap live_ok: stack + govern present (skip full catalog import_check).
        live = bool(
            self.stack is not None
            and callable(getattr(self.stack, "govern", None))
            and eng is not None
        )
        return {
            "engine_module": eng_mod,
            "engine": eng_name,
            "halted": self._halted,
            "live_ok": live,
            "runtime": "governed_stack",
        }

    # ------------------------------------------------------------------
    # Intent normalize + decide
    # ------------------------------------------------------------------

    @staticmethod
    def normalize_intent(intent: Any) -> Dict[str, Any]:
        """
        Map loose demo dicts to govern()-suitable intents.

        If dict has only text/subject keys → generic_request + payload.
        """
        if not isinstance(intent, dict):
            return {
                "action": "generic_request",
                "payload": {"text": str(intent)},
            }
        if "action" in intent:
            return intent
        keys = set(intent.keys())
        if keys and keys <= {"text", "subject"}:
            return {
                "action": "generic_request",
                "payload": {
                    "subject": intent.get("subject", ""),
                    "text": intent.get("text", ""),
                },
            }
        if "payload" in intent:
            out = dict(intent)
            out.setdefault("action", "generic_request")
            return out
        return intent

    def _early_block(
        self,
        *,
        reasons: list,
        risk_signal: float = 1.0,
        notes: Optional[list] = None,
        intent: Optional[dict] = None,
    ) -> Dict[str, Any]:
        note_list = list(notes or [])
        env = {
            "decision": "BLOCK",
            "reasons": list(reasons),
            "entry_id": None,
            "hais": {"cap": None, "risk": None, "instability": None},
            "haven2": {"realm": None, "p_hat": None, "open": False},
            "notes": note_list,
            "intent": intent,
        }
        return {
            "decision": "BLOCK",
            "risk_signal": risk_signal,
            "policy_reasons": list(reasons),
            "audit_id": None,
            "envelope": env,
            "runtime": "governed_stack",
        }

    async def decide(
        self,
        intent: Any,
        token: Optional[str] = None,
        user: str = "damien",
        role: str = "user",
    ) -> Dict[str, Any]:
        """
        Live decide path with constitution pre-check and human halt.

        Returns a stable demo shape over GovernedStack.govern.
        """
        # 1) Human halt
        if self._halted:
            reason = self._halt_reason or "human intervention"
            note = f"blocked by human halt: {reason}"
            self._local_notes.append(note)
            return self._early_block(
                reasons=["human_halt", "Runtime", reason],
                notes=[note],
                intent=intent if isinstance(intent, dict) else {"text": str(intent)},
            )

        normalized = self.normalize_intent(intent)
        action = str(normalized.get("action", ""))

        # 2) Constitution pre-check (before govern); local note only
        if action in FORBIDDEN_ACTIONS:
            note = f"constitution_forbidden:{action}"
            self._local_notes.append(note)
            return self._early_block(
                reasons=[f"constitution:{action}", "constitution_violation"],
                notes=[note],
                intent=normalized,
            )

        if token is None:
            token = self.issue_token(user, role)

        # 3) Live gate
        env = await self.stack.govern(normalized, token)

        hais = env.get("hais") or {}
        audit = env.get("audit") or {}
        risk = audit.get("risk_signal")
        if risk is None:
            risk = hais.get("risk")
        if risk is None:
            risk = 0.0

        return {
            "decision": env.get("decision", "BLOCK"),
            "risk_signal": float(risk),
            "policy_reasons": list(env.get("reasons") or []),
            "audit_id": env.get("entry_id"),
            "envelope": env,
            "runtime": "governed_stack",
        }


__all__ = ["GovernedDecisionEngine", "FORBIDDEN_ACTIONS"]
