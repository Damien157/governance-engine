"""
GovernedActionBus — single facade so every outbound mutation must pass
require_allow / channel check before any side_effect callback runs.

Kills the pattern of “check optional, send anyway.”

Channels: mail | calendar | social | tool
  - mail/calendar/social: delegate to GovernedMail / GovernedCalendar / GovernedPost
    require_allow (recipients/attendees/URLs stay out of band).
  - tool: raw GovernIntent via parse_intent / stack.govern; still no side_effect
    without ALLOW.

Mutations raise SendBlocked (alias ActionDenied) on BLOCK/REVIEW — callers
cannot ignore a soft deny.

Sidecar stays check-only: there is intentionally no HTTP /v1/execute that
would open remote arbitrary side effects. Execute is library-side only.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import inspect
from typing import Any, Awaitable, Callable, Dict, FrozenSet, Optional, Union

from .calendar import GovernedCalendar
from .contracts import IntentValidationError, intent_invalid_envelope, parse_intent
from .mail import GovernedMail, SendBlocked
from .social import GovernedPost
from .stack import GovernedStack, ensure_import_paths

ensure_import_paths()

# Alias: same PermissionError subclass for all mutation denials.
ActionDenied = SendBlocked

CHANNELS: FrozenSet[str] = frozenset({"mail", "calendar", "social", "tool"})

SideEffect = Callable[[dict], Any]

# Public mutation entry points exported by this package (documentation + tests).
PUBLIC_EXECUTE_HELPERS: FrozenSet[str] = frozenset({"GovernedActionBus"})


def _run_sync(coro_factory: Callable[[], Awaitable[Any]]) -> Any:
    """asyncio.run / running-loop-safe sync bridge (same pattern as adapters)."""

    async def _run() -> Any:
        return await coro_factory()

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_run())
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, _run()).result()


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


class GovernedActionBus:
    """One facade: require_allow first; side_effect only on ALLOW."""

    def __init__(
        self,
        stack: Optional[GovernedStack] = None,
        *,
        mail: Optional[GovernedMail] = None,
        calendar: Optional[GovernedCalendar] = None,
        post: Optional[GovernedPost] = None,
    ) -> None:
        if stack is not None:
            self.stack = stack
        elif mail is not None:
            self.stack = mail.stack
        elif calendar is not None:
            self.stack = calendar.stack
        elif post is not None:
            self.stack = post.stack
        else:
            # Shared default stack via mail adapter (persists under artifacts/mail).
            mail = GovernedMail()
            self.stack = mail.stack

        self._mail = mail if mail is not None else GovernedMail(stack=self.stack)
        self._calendar = (
            calendar if calendar is not None else GovernedCalendar(stack=self.stack)
        )
        self._post = post if post is not None else GovernedPost(stack=self.stack)

    @property
    def mail(self) -> GovernedMail:
        return self._mail

    @property
    def calendar(self) -> GovernedCalendar:
        return self._calendar

    @property
    def post(self) -> GovernedPost:
        return self._post

    def issue_token(self, user: str, role: str = "user") -> str:
        return self.stack.issue_token(user, role)

    async def execute(
        self,
        channel: str,
        *,
        side_effect: SideEffect,
        token: Optional[str] = None,
        user: str = "damien",
        role: str = "user",
        # mail
        to: Any = None,
        subject: str = "",
        body: str = "",
        cc: Any = None,
        # calendar
        summary: str = "",
        description: str = "",
        location: str = "",
        start: Any = None,
        end: Any = None,
        attendees: Any = None,
        # social
        text: str = "",
        platform: str = "",
        recipients: Any = None,
        urls: Any = None,
        # tool
        intent: Optional[Dict[str, Any]] = None,
    ) -> dict:
        """
        Gate then mutate.

        Runs the channel's require_allow / govern check. Only if decision is
        ALLOW is ``side_effect(result)`` invoked. BLOCK and REVIEW raise
        ``SendBlocked`` (``ActionDenied``) so the side effect never runs.
        """
        if channel not in CHANNELS:
            raise ValueError(
                f"unknown channel {channel!r}; expected one of {sorted(CHANNELS)}"
            )
        if side_effect is None or not callable(side_effect):
            raise TypeError("side_effect must be a callable")

        if channel == "mail":
            if to is None:
                raise TypeError("mail channel requires keyword argument 'to'")
            result = await self._mail.require_allow(
                to=to,
                subject=subject,
                body=body,
                cc=cc,
                user=user,
                role=role,
            )
        elif channel == "calendar":
            result = await self._calendar.require_allow(
                summary=summary or subject,
                description=description or body,
                location=location,
                start=start,
                end=end,
                attendees=attendees,
                user=user,
                role=role,
            )
        elif channel == "social":
            result = await self._post.require_allow(
                text=text or body,
                platform=platform,
                recipients=recipients,
                urls=urls,
                user=user,
                role=role,
            )
        else:  # tool
            result = await self._check_tool(
                intent=intent,
                token=token,
                user=user,
                role=role,
            )
            if not result.get("ok"):
                raise SendBlocked(result)

        # ALLOW only reaches here.
        se_out = await _maybe_await(side_effect(result))
        out = dict(result)
        out["channel"] = channel
        out["side_effect_result"] = se_out
        out["executed"] = True
        return out

    async def _check_tool(
        self,
        *,
        intent: Optional[Dict[str, Any]],
        token: Optional[str],
        user: str,
        role: str,
    ) -> dict:
        if intent is None:
            raise TypeError("tool channel requires keyword argument 'intent'")
        if not isinstance(intent, dict):
            raise TypeError("tool channel intent must be a dict")

        # Validate early so smuggled routing fails before govern/side_effect.
        try:
            parsed = parse_intent(intent)
            govern_intent: Union[dict, Any] = (
                parsed.dump_for_govern()
                if hasattr(parsed, "dump_for_govern")
                else parsed
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
                "blocked_send": True,
                "notes": env.get("notes"),
                "audit": env.get("audit"),
            }

        tok = token if token is not None else self.issue_token(user, role)
        env = await self.stack.govern(govern_intent, tok)
        decision = env.get("decision", "BLOCK")
        ok = decision == "ALLOW"
        return {
            "ok": ok,
            "decision": decision,
            "reasons": env.get("reasons"),
            "entry_id": env.get("entry_id"),
            "hais": env.get("hais"),
            "haven2": env.get("haven2"),
            "error_code": env.get("error_code"),
            "blocked_send": not ok,
            "notes": env.get("notes"),
            "audit": env.get("audit"),
        }

    def execute_sync(
        self,
        channel: str,
        *,
        side_effect: SideEffect,
        token: Optional[str] = None,
        user: str = "damien",
        role: str = "user",
        to: Any = None,
        subject: str = "",
        body: str = "",
        cc: Any = None,
        summary: str = "",
        description: str = "",
        location: str = "",
        start: Any = None,
        end: Any = None,
        attendees: Any = None,
        text: str = "",
        platform: str = "",
        recipients: Any = None,
        urls: Any = None,
        intent: Optional[Dict[str, Any]] = None,
    ) -> dict:
        """Sync wrapper around ``execute``."""

        return _run_sync(
            lambda: self.execute(
                channel,
                side_effect=side_effect,
                token=token,
                user=user,
                role=role,
                to=to,
                subject=subject,
                body=body,
                cc=cc,
                summary=summary,
                description=description,
                location=location,
                start=start,
                end=end,
                attendees=attendees,
                text=text,
                platform=platform,
                recipients=recipients,
                urls=urls,
                intent=intent,
            )
        )


__all__ = [
    "GovernedActionBus",
    "ActionDenied",
    "SendBlocked",
    "CHANNELS",
    "PUBLIC_EXECUTE_HELPERS",
]
