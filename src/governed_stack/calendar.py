"""
GovernedCalendar — thin calendar-write gate in front of GovernedStack.govern().

Routing/scheduling metadata (attendees, start, end) stays out of band so
PolicyEngine never sees attendee addresses in the scanned intent. Only
summary + description + location are governed (as subject/text).
Does not create calendar events; it only decides ALLOW vs BLOCK/REVIEW.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .contracts import validate_calendar_scan
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
_DEFAULT_CAL_DIR = _REPO_ROOT / "artifacts" / "calendar"
_DEFAULT_DB = _DEFAULT_CAL_DIR / "audit.db"
_DEFAULT_KEY = _DEFAULT_CAL_DIR / "signing_key.pem"

# Alias: same PermissionError subclass as outbound mail.
CalendarBlocked = SendBlocked

Attendees = Union[str, List[str]]


def intent_for_scan(
    *,
    summary: str,
    description: str = "",
    location: str = "",
) -> Dict[str, Any]:
    """Exact intent passed to govern(); attendees/start/end intentionally absent.

    Builds/validates CalendarScanIntent then dumps — no attendees/start/end.
    """
    return validate_calendar_scan(
        summary=summary, description=description, location=location
    ).dump_for_govern()


def _normalize_attendees(attendees: Optional[Attendees]) -> List[str]:
    if attendees is None:
        return []
    if isinstance(attendees, str):
        return [attendees]
    return list(attendees)


class GovernedCalendar:
    """Adapter: keep attendees/start/end out of PolicyEngine's scanned intent."""

    def __init__(self, stack: Optional[GovernedStack] = None) -> None:
        if stack is not None:
            self.stack = stack
        else:
            self.stack = self._build_default_stack()
        self._token_cache: Dict[tuple, str] = {}

    @staticmethod
    def _build_default_stack() -> GovernedStack:
        cal_dir = _DEFAULT_CAL_DIR
        cal_dir.mkdir(parents=True, exist_ok=True)
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
        summary: str,
        description: str = "",
        location: str = "",
        start: Any = None,
        end: Any = None,
        attendees: Optional[Attendees] = None,
        user: str = "damien",
        role: str = "user",
    ) -> dict:
        attendee_list = _normalize_attendees(attendees)
        # start/end/attendees accepted for API completeness but never enter
        # the scanned intent (routing/scheduling metadata).
        intent = intent_for_scan(
            summary=summary, description=description, location=location
        )
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
            "summary": summary,
            "attendees": attendee_list,
            "start": start,
            "end": end,
            "blocked_write": not ok,
        }

    async def require_allow(
        self,
        *,
        summary: str,
        description: str = "",
        location: str = "",
        start: Any = None,
        end: Any = None,
        attendees: Optional[Attendees] = None,
        user: str = "damien",
        role: str = "user",
    ) -> dict:
        """
        Like check(), but raises SendBlocked/CalendarBlocked unless ALLOW.
        Callers cannot ignore ``ok`` — non-ALLOW cannot proceed to write.
        """
        result = await self.check(
            summary=summary,
            description=description,
            location=location,
            start=start,
            end=end,
            attendees=attendees,
            user=user,
            role=role,
        )
        if not result.get("ok"):
            raise SendBlocked(result)
        return result

    def check_sync(
        self,
        *,
        summary: str,
        description: str = "",
        location: str = "",
        start: Any = None,
        end: Any = None,
        attendees: Optional[Attendees] = None,
        user: str = "damien",
        role: str = "user",
    ) -> dict:
        """Sync wrapper around ``check`` (asyncio.run / running-loop safe)."""

        async def _run() -> dict:
            return await self.check(
                summary=summary,
                description=description,
                location=location,
                start=start,
                end=end,
                attendees=attendees,
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
        summary: str,
        description: str = "",
        location: str = "",
        start: Any = None,
        end: Any = None,
        attendees: Optional[Attendees] = None,
        user: str = "damien",
        role: str = "user",
    ) -> dict:
        """Sync wrapper around ``require_allow``."""

        async def _run() -> dict:
            return await self.require_allow(
                summary=summary,
                description=description,
                location=location,
                start=start,
                end=end,
                attendees=attendees,
                user=user,
                role=role,
            )

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(_run())
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, _run()).result()


__all__ = [
    "GovernedCalendar",
    "CalendarBlocked",
    "SendBlocked",
    "intent_for_scan",
]
