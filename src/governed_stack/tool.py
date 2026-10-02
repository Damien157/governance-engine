"""
GovernedTool — generic tool/agent intent gate in front of GovernedStack (0.7).

Every tool mutation should go through GovernedActionBus channel ``tool`` (or
this adapter's ``require_allow`` then a bus side_effect). Check-only relative
to remote execute: sidecar still has no ``/v1/execute``.

Envelope echoes approved ``action`` + ``intent`` (govern dump) so
``bus_tool_side_effect`` cannot close over a different tool/args.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Optional

from .contracts import (
    IntentValidationError,
    intent_invalid_envelope,
    parse_intent,
)
from .mail import SendBlocked
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
_DEFAULT_DIR = _REPO_ROOT / "artifacts" / "tool"
_DEFAULT_DB = _DEFAULT_DIR / "audit.db"
_DEFAULT_KEY = _DEFAULT_DIR / "signing_key.pem"

ToolBlocked = SendBlocked


def _canonical_intent_json(intent: Dict[str, Any]) -> str:
    return json.dumps(intent, sort_keys=True, separators=(",", ":"), default=str)


def intent_sha256(intent: Dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_intent_json(intent).encode("utf-8")).hexdigest()


class GovernedTool:
    """Adapter: parse_intent + stack.govern; envelope carries approved intent."""

    def __init__(self, stack: Optional[GovernedStack] = None) -> None:
        if stack is not None:
            self.stack = stack
        else:
            self.stack = self._build_default_stack()
        self._token_cache: Dict[tuple, str] = {}

    @staticmethod
    def _build_default_stack() -> GovernedStack:
        d = _DEFAULT_DIR
        d.mkdir(parents=True, exist_ok=True)
        crypto = None
        if CryptoEngine is not None:
            crypto = CryptoEngine(private_key_path=str(_DEFAULT_KEY))
        return GovernedStack(
            config={
                "db_path": str(_DEFAULT_DB),
                "signing_key_path": str(_DEFAULT_KEY),
                "log_level": 40,
            },
            crypto=crypto,
        )

    def issue_token(self, user: str, role: str = "user") -> str:
        key = (user, role)
        tok = self._token_cache.get(key)
        if tok is None:
            tok = self.stack.issue_token(user, role)
            self._token_cache[key] = tok
        return tok

    async def check(
        self,
        *,
        intent: Dict[str, Any],
        token: Optional[str] = None,
        user: str = "damien",
        role: str = "user",
    ) -> dict:
        if not isinstance(intent, dict):
            raise TypeError("intent must be a dict")
        try:
            parsed = parse_intent(intent)
            govern_intent = (
                parsed.dump_for_govern()
                if hasattr(parsed, "dump_for_govern")
                else dict(intent)
            )
        except IntentValidationError as exc:
            env = intent_invalid_envelope(errors=exc.errors, message=str(exc))
            return {
                "ok": False,
                "decision": env.get("decision", "BLOCK"),
                "reasons": env.get("reasons"),
                "entry_id": None,
                "hais": env.get("hais"),
                "haven2": env.get("haven2"),
                "error_code": env.get("error_code"),
                "blocked_tool": True,
                "action": str(intent.get("action") or ""),
                "intent": None,
                "intent_sha256": None,
                "notes": env.get("notes"),
                "audit": env.get("audit"),
            }

        if not isinstance(govern_intent, dict):
            govern_intent = dict(intent)

        tok = token if token is not None else self.issue_token(user, role)
        env = await self.stack.govern(govern_intent, tok)
        decision = env.get("decision", "BLOCK")
        ok = decision == "ALLOW"
        action = str(govern_intent.get("action") or intent.get("action") or "")
        return {
            "ok": ok,
            "decision": decision,
            "reasons": env.get("reasons"),
            "entry_id": env.get("entry_id"),
            "hais": env.get("hais"),
            "haven2": env.get("haven2"),
            "error_code": env.get("error_code"),
            "blocked_tool": not ok,
            "action": action,
            "intent": govern_intent,
            "intent_sha256": intent_sha256(govern_intent),
            "notes": env.get("notes"),
            "audit": env.get("audit"),
        }

    async def require_allow(self, **kwargs: Any) -> dict:
        result = await self.check(**kwargs)
        if not result.get("ok"):
            raise SendBlocked(result)
        return result

    def check_sync(self, **kwargs: Any) -> dict:
        async def _run() -> dict:
            return await self.check(**kwargs)

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(_run())
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, _run()).result()

    def require_allow_sync(self, **kwargs: Any) -> dict:
        result = self.check_sync(**kwargs)
        if not result.get("ok"):
            raise SendBlocked(result)
        return result


__all__ = [
    "GovernedTool",
    "ToolBlocked",
    "SendBlocked",
    "intent_sha256",
]
