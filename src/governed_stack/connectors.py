"""
Live-connector enforcement pattern (0.6.1 + tool channel 0.7).

Protocols + mocks + factory helpers that produce ``side_effect`` callbacks
for ``GovernedActionBus.execute``.

**Rule:** only invoke real connector SDKs / MCP write tools *inside* these
side_effects (or equivalent bus-registered callbacks). Never call Gmail
``send_message``, Calendar inserts, or social publish helpers outside the
bus. See ``scripts/lint_no_bypass.py`` and ``docs/AGENT_MANDATES.md``.

**Content binding (0.6.1):** gate ``check`` / ``require_allow`` results must
carry the **exact approved content** on the envelope (mail ``body``,
calendar ``summary``/``description``/``location``, social ``text``).
``bus_*_side_effect`` factories read **only** from that envelope — they
reject closed-over content kwargs (``body=``, ``subject=``, ``text=``, …).
Custom side_effects must likewise use envelope fields only; call
``assert_bound_content`` for defense-in-depth. Never close over a different
body/text than the one that was gated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional, Protocol, runtime_checkable


class ContentBindingError(ValueError):
    """Raised when a side_effect cannot bind approved content from the envelope.

    Typically: required gated field missing from the gate result, so sending
    would risk empty/wrong content (content-swap / unbound send).
    """


# Required envelope keys per channel (gated content that must be present).
_BOUND_CONTENT_KEYS: dict[str, tuple[str, ...]] = {
    "mail": ("body", "subject", "to"),
    "calendar": ("summary", "description", "location"),
    "social": ("text",),
    "tool": ("action", "intent", "intent_sha256"),
}


def assert_bound_content(result: dict, channel: str) -> None:
    """Defense-in-depth: require gated content keys on the gate envelope.

    Side_effects and tests may call this before sending. Raises
    ``ContentBindingError`` if ``channel`` is unknown or a required key is
    missing (``None``). Empty string is allowed when the gate approved it.
    """
    if channel not in _BOUND_CONTENT_KEYS:
        raise ContentBindingError(
            f"assert_bound_content: unknown channel {channel!r}; "
            f"expected one of {sorted(_BOUND_CONTENT_KEYS)}"
        )
    missing = [k for k in _BOUND_CONTENT_KEYS[channel] if k not in result or result[k] is None]
    if missing:
        raise ContentBindingError(
            f"gate envelope missing approved content for channel={channel!r}: "
            f"missing keys {missing}; side_effect must not invent or close over "
            f"gated fields — re-check so the envelope carries exact approved content"
        )


def _require_envelope_field(result: dict, key: str, *, channel: str) -> Any:
    if key not in result or result[key] is None:
        raise ContentBindingError(
            f"{channel} side_effect: gate envelope missing {key!r}; "
            f"refusing to send unbound/empty content"
        )
    return result[key]


@runtime_checkable
class MailSender(Protocol):
    """Outbound mail connector — call only from a bus mail side_effect."""

    def send(
        self,
        *,
        to: Any,
        subject: str,
        body: str,
        cc: Any = None,
        gate_result: Optional[dict] = None,
    ) -> dict: ...


@runtime_checkable
class CalendarWriter(Protocol):
    """Calendar write connector — call only from a bus calendar side_effect."""

    def create_event(
        self,
        *,
        summary: str,
        description: str = "",
        location: str = "",
        start: Any = None,
        end: Any = None,
        attendees: Any = None,
        gate_result: Optional[dict] = None,
    ) -> dict: ...


@runtime_checkable
class SocialPublisher(Protocol):
    """Social publish connector — call only from a bus social side_effect."""

    def publish(
        self,
        *,
        text: str,
        platform: str = "",
        recipients: Any = None,
        urls: Any = None,
        gate_result: Optional[dict] = None,
    ) -> dict: ...


@dataclass
class MockMailSender:
    """In-memory mail sender for tests — never hits a network."""

    sent: List[dict] = field(default_factory=list)

    def send(
        self,
        *,
        to: Any,
        subject: str,
        body: str,
        cc: Any = None,
        gate_result: Optional[dict] = None,
    ) -> dict:
        record = {
            "mock": True,
            "to": to,
            "subject": subject,
            "body": body,
            "cc": cc,
            "entry_id": (gate_result or {}).get("entry_id"),
        }
        self.sent.append(record)
        return record


@dataclass
class MockCalendarWriter:
    """In-memory calendar writer for tests — never hits a network."""

    created: List[dict] = field(default_factory=list)

    def create_event(
        self,
        *,
        summary: str,
        description: str = "",
        location: str = "",
        start: Any = None,
        end: Any = None,
        attendees: Any = None,
        gate_result: Optional[dict] = None,
    ) -> dict:
        record = {
            "mock": True,
            "summary": summary,
            "description": description,
            "location": location,
            "start": start,
            "end": end,
            "attendees": attendees,
            "entry_id": (gate_result or {}).get("entry_id"),
        }
        self.created.append(record)
        return record


@dataclass
class MockSocialPublisher:
    """In-memory social publisher for tests — never hits a network."""

    published: List[dict] = field(default_factory=list)

    def publish(
        self,
        *,
        text: str,
        platform: str = "",
        recipients: Any = None,
        urls: Any = None,
        gate_result: Optional[dict] = None,
    ) -> dict:
        record = {
            "mock": True,
            "text": text,
            "platform": platform,
            "recipients": recipients,
            "urls": urls,
            "entry_id": (gate_result or {}).get("entry_id"),
        }
        self.published.append(record)
        return record


@dataclass
class MockBioTicketLogger:
    """Metadata / ticket mock for bio channel — no wet-lab, no protocols."""

    tickets: List[dict] = field(default_factory=list)

    def log_ticket(self, gate_result: dict) -> dict:
        record = {
            "mock": True,
            "kind": "bio_metadata_ticket",
            "decision": gate_result.get("decision"),
            "entry_id": gate_result.get("entry_id"),
            "purpose": gate_result.get("purpose"),
            "domain": gate_result.get("domain"),
            "intervention_class": gate_result.get("intervention_class"),
        }
        self.tickets.append(record)
        return record


def bus_mail_side_effect(sender: MailSender) -> Callable[[dict], Any]:
    """Return a bus ``side_effect`` that sends mail via ``sender`` after ALLOW.

    Reads **only** from the gate ``result`` envelope (``to``, ``subject``,
    ``body``, optional ``cc``). Closed-over content kwargs are rejected by
    signature — passing ``body=`` / ``subject=`` raises ``TypeError``.
    Real Gmail/SDK calls belong *only* inside ``sender.send`` (invoked here).
    """

    def _side_effect(result: dict) -> Any:
        assert_bound_content(result, "mail")
        return sender.send(
            to=_require_envelope_field(result, "to", channel="mail"),
            subject=_require_envelope_field(result, "subject", channel="mail"),
            body=_require_envelope_field(result, "body", channel="mail"),
            cc=result.get("cc"),
            gate_result=result,
        )

    return _side_effect


def bus_calendar_side_effect(writer: CalendarWriter) -> Callable[[dict], Any]:
    """Return a bus ``side_effect`` that writes calendar via ``writer`` after ALLOW.

    Reads **only** from the gate envelope (``summary``, ``description``,
    ``location``; plus out-of-band ``start``/``end``/``attendees`` if present).
    Closed-over content kwargs are rejected by signature.
    """

    def _side_effect(result: dict) -> Any:
        assert_bound_content(result, "calendar")
        return writer.create_event(
            summary=_require_envelope_field(result, "summary", channel="calendar"),
            description=_require_envelope_field(
                result, "description", channel="calendar"
            ),
            location=_require_envelope_field(result, "location", channel="calendar"),
            start=result.get("start"),
            end=result.get("end"),
            attendees=result.get("attendees"),
            gate_result=result,
        )

    return _side_effect


def bus_social_side_effect(publisher: SocialPublisher) -> Callable[[dict], Any]:
    """Return a bus ``side_effect`` that publishes via ``publisher`` after ALLOW.

    Reads **only** from the gate envelope (``text``, optional ``platform`` /
    ``recipients`` / ``urls``). Closed-over ``text=`` overrides are rejected
    by signature.
    """

    def _side_effect(result: dict) -> Any:
        assert_bound_content(result, "social")
        return publisher.publish(
            text=_require_envelope_field(result, "text", channel="social"),
            platform=result.get("platform") or "",
            recipients=result.get("recipients"),
            urls=result.get("urls"),
            gate_result=result,
        )

    return _side_effect



@runtime_checkable
class ToolInvoker(Protocol):
    """Generic tool/agent invoker — call only from a bus tool side_effect."""

    def invoke(
        self,
        *,
        intent: dict,
        action: str = "",
        gate_result: Optional[dict] = None,
    ) -> dict: ...


@dataclass
class MockToolInvoker:
    """In-memory tool invoker for tests — never hits a real tool runtime."""

    calls: List[dict] = field(default_factory=list)

    def invoke(
        self,
        *,
        intent: dict,
        action: str = "",
        gate_result: Optional[dict] = None,
    ) -> dict:
        record = {
            "mock": True,
            "action": action or (intent or {}).get("action"),
            "intent": intent,
            "entry_id": (gate_result or {}).get("entry_id"),
            "intent_sha256": (gate_result or {}).get("intent_sha256"),
        }
        self.calls.append(record)
        return record


def bus_tool_side_effect(invoker: ToolInvoker) -> Callable[[dict], Any]:
    """Return a bus ``side_effect`` that invokes a tool from the ALLOW envelope only.

    Reads ``action``, ``intent``, ``intent_sha256`` from the gate result.
    Closed-over intent kwargs are rejected by signature (connector only).
    """

    def _side_effect(result: dict) -> Any:
        assert_bound_content(result, "tool")
        intent = _require_envelope_field(result, "intent", channel="tool")
        action = _require_envelope_field(result, "action", channel="tool")
        # Defense: sha must match envelope intent bytes.
        from .tool import intent_sha256

        expected = _require_envelope_field(result, "intent_sha256", channel="tool")
        actual = intent_sha256(intent if isinstance(intent, dict) else {})
        if actual != expected:
            raise ContentBindingError(
                "tool side_effect: intent_sha256 mismatch — refusing swapped intent"
            )
        return invoker.invoke(
            intent=intent,
            action=str(action),
            gate_result=result,
        )

    return _side_effect


def bus_bio_side_effect(logger: MockBioTicketLogger) -> Callable[[dict], Any]:
    """Return a bus ``side_effect`` for bio — metadata/ticket only (no protocols)."""

    def _side_effect(result: dict) -> Any:
        return logger.log_ticket(result)

    return _side_effect


__all__ = [
    "ContentBindingError",
    "assert_bound_content",
    "MailSender",
    "CalendarWriter",
    "SocialPublisher",
    "MockMailSender",
    "MockCalendarWriter",
    "MockSocialPublisher",
    "MockBioTicketLogger",
    "bus_mail_side_effect",
    "bus_calendar_side_effect",
    "bus_social_side_effect",
    "bus_bio_side_effect",
    "ToolInvoker",
]
