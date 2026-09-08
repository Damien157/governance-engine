"""
GovernedAlgorithm — thin algorithm run/deploy gate in front of GovernedStack.govern().

Scans purpose/summary, Cost axes (time/space/energy/speedup), and Risk notes /
security_margin. Secrets/credentials stay out of band and are rejected if
smuggled into the scan intent. Does not execute algorithms; it only decides
ALLOW vs BLOCK/REVIEW.

Axes: Purpose → Cost → Risk → Authority → Audit (see docs/ALGORITHM_GOVERNANCE_MAIN.md).
Not a P vs NP proof.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
from pathlib import Path
from typing import Any, Dict, Optional, Union

from .contracts import validate_algorithm_scan
from .mail import SendBlocked
from .quantum_line import attach_quantum
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
_DEFAULT_ALGO_DIR = _REPO_ROOT / "artifacts" / "algorithm"
_DEFAULT_DB = _DEFAULT_ALGO_DIR / "audit.db"
_DEFAULT_KEY = _DEFAULT_ALGO_DIR / "signing_key.pem"

# Alias: same PermissionError subclass as outbound mail.
AlgorithmBlocked = SendBlocked

CostValue = Union[str, int, float, None]


def intent_for_scan(
    *,
    purpose: str,
    summary: str = "",
    time_cost: CostValue = None,
    space_cost: CostValue = None,
    energy_cost: CostValue = None,
    speedup: CostValue = None,
    risk_notes: str = "",
    security_margin: CostValue = None,
) -> Dict[str, Any]:
    """Exact intent passed to govern(); secrets intentionally absent.

    Builds/validates AlgorithmScanIntent then dumps — no private_key/password/token/secret.
    """
    return validate_algorithm_scan(
        purpose=purpose,
        summary=summary,
        time_cost=time_cost,
        space_cost=space_cost,
        energy_cost=energy_cost,
        speedup=speedup,
        risk_notes=risk_notes,
        security_margin=security_margin,
    ).dump_for_govern()


class GovernedAlgorithm:
    """Adapter: Purpose → Cost → Risk scan; Authority/Audit via GovernedStack."""

    def __init__(self, stack: Optional[GovernedStack] = None) -> None:
        if stack is not None:
            self.stack = stack
        else:
            self.stack = self._build_default_stack()
        self._token_cache: Dict[tuple, str] = {}

    @staticmethod
    def _build_default_stack() -> GovernedStack:
        algo_dir = _DEFAULT_ALGO_DIR
        algo_dir.mkdir(parents=True, exist_ok=True)
        db_path = str(_DEFAULT_DB)
        key_path = str(_DEFAULT_KEY)
        crypto = None
        if CryptoEngine is not None:
            # Persist RSA-3072 once; subsequent boots load the PEM.
            crypto = CryptoEngine(private_key_path=key_path)
        return GovernedStack(
            config={
                "db_path": db_path,
                "signing_key_path": key_path,
                "log_level": 40,
            },
            crypto=crypto,
        )

    def issue_token(self, user: str, role: str = "user") -> str:
        """Passthrough with per-(user, role) cache on this instance."""
        key = (user, role)
        tok = self._token_cache.get(key)
        if tok is None:
            tok = self.stack.issue_token(user, role)
            self._token_cache[key] = tok
        return tok

    async def check(
        self,
        *,
        purpose: str,
        summary: str = "",
        time_cost: CostValue = None,
        space_cost: CostValue = None,
        energy_cost: CostValue = None,
        speedup: CostValue = None,
        risk_notes: str = "",
        security_margin: CostValue = None,
        user: str = "damien",
        role: str = "user",
    ) -> dict:
        intent = intent_for_scan(
            purpose=purpose,
            summary=summary,
            time_cost=time_cost,
            space_cost=space_cost,
            energy_cost=energy_cost,
            speedup=speedup,
            risk_notes=risk_notes,
            security_margin=security_margin,
        )
        token = self.issue_token(user, role)
        env = await self.stack.govern(intent, token)
        decision = env.get("decision", "BLOCK")
        ok = decision == "ALLOW"
        cost_summary = {
            "time": time_cost,
            "space": space_cost,
            "energy": energy_cost,
            "speedup": speedup,
        }
        risk_summary = {
            "notes": risk_notes or "",
            "security_margin": security_margin,
        }
        result = {
            "ok": ok,
            "decision": decision,
            "reasons": env.get("reasons"),
            "entry_id": env.get("entry_id"),
            "hais": env.get("hais"),
            "haven2": env.get("haven2"),
            "purpose": purpose,
            "summary": summary or "",
            "cost": cost_summary,
            "risk": risk_summary,
            "blocked_run": not ok,
        }
        # QUANTUM audit snapshot from HAIS envelope (Algorithm Audit axis).
        return attach_quantum(result)

    async def require_allow(
        self,
        *,
        purpose: str,
        summary: str = "",
        time_cost: CostValue = None,
        space_cost: CostValue = None,
        energy_cost: CostValue = None,
        speedup: CostValue = None,
        risk_notes: str = "",
        security_margin: CostValue = None,
        user: str = "damien",
        role: str = "user",
    ) -> dict:
        """
        Like check(), but raises AlgorithmBlocked/SendBlocked unless ALLOW.
        Callers cannot ignore ``ok`` — non-ALLOW cannot proceed to run/deploy.
        """
        result = await self.check(
            purpose=purpose,
            summary=summary,
            time_cost=time_cost,
            space_cost=space_cost,
            energy_cost=energy_cost,
            speedup=speedup,
            risk_notes=risk_notes,
            security_margin=security_margin,
            user=user,
            role=role,
        )
        if not result.get("ok"):
            raise SendBlocked(result)
        return result

    def check_sync(
        self,
        *,
        purpose: str,
        summary: str = "",
        time_cost: CostValue = None,
        space_cost: CostValue = None,
        energy_cost: CostValue = None,
        speedup: CostValue = None,
        risk_notes: str = "",
        security_margin: CostValue = None,
        user: str = "damien",
        role: str = "user",
    ) -> dict:
        """Sync wrapper around ``check`` (asyncio.run / running-loop safe)."""

        async def _run() -> dict:
            return await self.check(
                purpose=purpose,
                summary=summary,
                time_cost=time_cost,
                space_cost=space_cost,
                energy_cost=energy_cost,
                speedup=speedup,
                risk_notes=risk_notes,
                security_margin=security_margin,
                user=user,
                role=role,
            )

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(_run())
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, _run()).result()

    def require_allow_sync(
        self,
        *,
        purpose: str,
        summary: str = "",
        time_cost: CostValue = None,
        space_cost: CostValue = None,
        energy_cost: CostValue = None,
        speedup: CostValue = None,
        risk_notes: str = "",
        security_margin: CostValue = None,
        user: str = "damien",
        role: str = "user",
    ) -> dict:
        """Sync wrapper around ``require_allow``."""

        async def _run() -> dict:
            return await self.require_allow(
                purpose=purpose,
                summary=summary,
                time_cost=time_cost,
                space_cost=space_cost,
                energy_cost=energy_cost,
                speedup=speedup,
                risk_notes=risk_notes,
                security_margin=security_margin,
                user=user,
                role=role,
            )

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(_run())
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, _run()).result()


__all__ = ["GovernedAlgorithm", "AlgorithmBlocked", "SendBlocked", "intent_for_scan"]
