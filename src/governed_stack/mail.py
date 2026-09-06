"""
GovernedMail — thin outbound-email gate in front of GovernedStack.govern().

Routing (To/Cc/From) stays out of band so PolicyEngine never sees recipient
addresses in the scanned intent. Only subject + body are governed.
Does not send mail; it only decides ALLOW vs BLOCK/REVIEW.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .contracts import validate_mail_scan
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
_DEFAULT_MAIL_DIR = _REPO_ROOT / "artifacts" / "mail"
_DEFAULT_DB = _DEFAULT_MAIL_DIR / "audit.db"
_DEFAULT_KEY = _DEFAULT_MAIL_DIR / "signing_key.pem"

Recipients = Union[str, List[str]]


class SendBlocked(PermissionError):
    """Raised when outbound mail is not ALLOW (BLOCK or REVIEW)."""

    def __init__(self, result: dict):
        self.result = result
        decision = result.get("decision", "BLOCK")
        reasons = result.get("reasons") or []
        super().__init__(
            f"outbound mail {decision}: reasons={reasons!r}"
        )


def intent_for_scan(subject: str, body: str) -> Dict[str, Any]:
    """Exact intent passed to govern(); recipients intentionally absent.

    Builds/validates MailScanIntent then dumps — no to/cc/from in output.
    """
    return validate_mail_scan(subject=subject, body=body).dump_for_govern()


def _normalize_recipients(to: Recipients) -> List[str]:
    if isinstance(to, str):
        return [to]
    return list(to)


class GovernedMail:
    """Adapter: keep To/Cc out of PolicyEngine's scanned intent."""

    def __init__(self, stack: Optional[GovernedStack] = None) -> None:
        if stack is not None:
            self.stack = stack
        else:
            self.stack = self._build_default_stack()
        self._token_cache: Dict[tuple, str] = {}

    @staticmethod
    def _build_default_stack() -> GovernedStack:
        mail_dir = _DEFAULT_MAIL_DIR
        mail_dir.mkdir(parents=True, exist_ok=True)
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
        to: Recipients,
        subject: str,
        body: str,
        cc: Optional[Recipients] = None,
        user: str = "damien",
        role: str = "user",
    ) -> dict:
        recipients = _normalize_recipients(to)
        # cc accepted for API completeness but never enters the scanned intent.
        _ = cc
        intent = intent_for_scan(subject, body)
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
            "to": recipients,
            "subject": subject,
            "blocked_send": not ok,
        }

    async def require_allow(
        self,
        *,
        to: Recipients,
        subject: str,
        body: str,
        cc: Optional[Recipients] = None,
        user: str = "damien",
        role: str = "user",
    ) -> dict:
        """
        Like check(), but raises SendBlocked (PermissionError) unless ALLOW.
        Callers cannot ignore ``ok`` — non-ALLOW cannot proceed to send.
        """
        result = await self.check(
            to=to, subject=subject, body=body, cc=cc, user=user, role=role
        )
        if not result.get("ok"):
            raise SendBlocked(result)
        return result

    def check_sync(
        self,
        *,
        to: Recipients,
        subject: str,
        body: str,
        cc: Optional[Recipients] = None,
        user: str = "damien",
        role: str = "user",
    ) -> dict:
        """Sync wrapper around ``check`` (asyncio.run / running-loop safe)."""

        async def _run() -> dict:
            return await self.check(
                to=to, subject=subject, body=body, cc=cc, user=user, role=role
            )

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(_run())
        # Already inside an event loop — run on a fresh loop in a worker thread.
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, _run()).result()

    def require_allow_sync(
        self,
        *,
        to: Recipients,
        subject: str,
        body: str,
        cc: Optional[Recipients] = None,
        user: str = "damien",
        role: str = "user",
    ) -> dict:
        """Sync wrapper around ``require_allow``."""

        async def _run() -> dict:
            return await self.require_allow(
                to=to, subject=subject, body=body, cc=cc, user=user, role=role
            )

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(_run())
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, _run()).result()


__all__ = ["GovernedMail", "SendBlocked", "intent_for_scan"]
