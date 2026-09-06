"""
GovernedPost — thin outbound-social / post gate in front of GovernedStack.govern().

Recipients and URLs stay out of band so PolicyEngine never sees them in the
scanned intent. Only an optional platform tag (as subject) + body text are
governed. Does not publish; it only decides ALLOW vs BLOCK/REVIEW.

Gates LinkedIn / X / etc. content whenever a connector appears — value now
is the gate itself (no LinkedIn MCP in the plugin catalog).
"""

from __future__ import annotations

import asyncio
import concurrent.futures
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .contracts import validate_social_scan
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
_DEFAULT_SOCIAL_DIR = _REPO_ROOT / "artifacts" / "social"
_DEFAULT_DB = _DEFAULT_SOCIAL_DIR / "audit.db"
_DEFAULT_KEY = _DEFAULT_SOCIAL_DIR / "signing_key.pem"

# Alias: same PermissionError subclass as outbound mail.
PostBlocked = SendBlocked


Targets = Union[str, List[str]]


def intent_for_scan(*, text: str, platform: str = "") -> Dict[str, Any]:
    """Exact intent passed to govern(); recipients/URLs intentionally absent.

    Builds/validates SocialScanIntent then dumps — no recipients/URLs/handles.
    """
    return validate_social_scan(text=text, platform=platform).dump_for_govern()


def _normalize_targets(value: Optional[Targets]) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return list(value)


class GovernedPost:
    """Adapter: keep recipients/URLs out of PolicyEngine's scanned intent."""

    def __init__(self, stack: Optional[GovernedStack] = None) -> None:
        if stack is not None:
            self.stack = stack
        else:
            self.stack = self._build_default_stack()
        self._token_cache: Dict[tuple, str] = {}

    @staticmethod
    def _build_default_stack() -> GovernedStack:
        social_dir = _DEFAULT_SOCIAL_DIR
        social_dir.mkdir(parents=True, exist_ok=True)
        db_path = str(_DEFAULT_DB)
        key_path = str(_DEFAULT_KEY)
        crypto = None
        if CryptoEngine is not None:
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
        text: str,
        platform: str = "",
        recipients: Optional[Targets] = None,
        urls: Optional[Targets] = None,
        user: str = "damien",
        role: str = "user",
    ) -> dict:
        recipient_list = _normalize_targets(recipients)
        url_list = _normalize_targets(urls)
        # recipients/urls accepted for API completeness but never enter
        # the scanned intent (routing metadata).
        intent = intent_for_scan(text=text, platform=platform)
        token = self.issue_token(user, role)
        env = await self.stack.govern(intent, token)
        decision = env.get("decision", "BLOCK")
        ok = decision == "ALLOW"
        return {
            "ok": ok,
            "decision": decision,
            "reasons": env.get("reasons"),
            "entry_id": env.get("entry_id"),
            "hais": env.get("hais"),
            "haven2": env.get("haven2"),
            "platform": platform or "",
            "recipients": recipient_list,
            "urls": url_list,
            "blocked_publish": not ok,
        }

    async def require_allow(
        self,
        *,
        text: str,
        platform: str = "",
        recipients: Optional[Targets] = None,
        urls: Optional[Targets] = None,
        user: str = "damien",
        role: str = "user",
    ) -> dict:
        """
        Like check(), but raises PostBlocked/SendBlocked unless ALLOW.
        Callers cannot ignore ``ok`` — non-ALLOW cannot proceed to publish.
        """
        result = await self.check(
            text=text,
            platform=platform,
            recipients=recipients,
            urls=urls,
            user=user,
            role=role,
        )
        if not result.get("ok"):
            raise SendBlocked(result)
        return result

    def check_sync(
        self,
        *,
        text: str,
        platform: str = "",
        recipients: Optional[Targets] = None,
        urls: Optional[Targets] = None,
        user: str = "damien",
        role: str = "user",
    ) -> dict:
        """Sync wrapper around ``check`` (asyncio.run / running-loop safe)."""

        async def _run() -> dict:
            return await self.check(
                text=text,
                platform=platform,
                recipients=recipients,
                urls=urls,
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
        text: str,
        platform: str = "",
        recipients: Optional[Targets] = None,
        urls: Optional[Targets] = None,
        user: str = "damien",
        role: str = "user",
    ) -> dict:
        """Sync wrapper around ``require_allow``."""

        async def _run() -> dict:
            return await self.require_allow(
                text=text,
                platform=platform,
                recipients=recipients,
                urls=urls,
                user=user,
                role=role,
            )

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(_run())
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, _run()).result()


# Alias requested in the brief
GovernedOutboundText = GovernedPost

__all__ = [
    "GovernedPost",
    "GovernedOutboundText",
    "PostBlocked",
    "SendBlocked",
    "intent_for_scan",
]
