"""
Live-connector enforcement pattern (0.6.0).

Protocols + mocks + factory helpers that produce ``side_effect`` callbacks
for ``GovernedActionBus.execute``.

**Rule:** only invoke real connector SDKs / MCP write tools *inside* these
side_effects (or equivalent bus-registered callbacks). Never call Gmail
``send_message``, Calendar inserts, or social publish helpers outside the
bus. See ``scripts/lint_no_bypass.py`` and ``docs/AGENT_MANDATES.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional, Protocol, runtime_checkable


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


def bus_mail_side_effect(
    sender: MailSender,
    *,
    to: Any = None,
    subject: Optional[str] = None,
    body: Optional[str] = None,
    cc: Any = None,
) -> Callable[[dict], Any]:
    """Return a bus ``side_effect`` that sends mail via ``sender`` after ALLOW.

    Optional kwargs close over send fields (gate results omit ``body``).
    Real Gmail/SDK calls belong *only* inside ``sender.send`` (invoked here).
    """

    def _side_effect(result: dict) -> Any:
        return sender.send(
            to=result.get("to") if to is None else to,
            subject=(result.get("subject") or "") if subject is None else subject,
            body=(result.get("body") or "") if body is None else body,
            cc=result.get("cc") if cc is None else cc,
            gate_result=result,
        )

    return _side_effect


def bus_calendar_side_effect(
    writer: CalendarWriter,
    *,
    summary: Optional[str] = None,
    description: Optional[str] = None,
    location: Optional[str] = None,
    start: Any = None,
    end: Any = None,
    attendees: Any = None,
) -> Callable[[dict], Any]:
    """Return a bus ``side_effect`` that writes calendar via ``writer`` after ALLOW."""

    def _side_effect(result: dict) -> Any:
        return writer.create_event(
            summary=(result.get("summary") or "") if summary is None else summary,
            description=(result.get("description") or "")
            if description is None
            else description,
            location=(result.get("location") or "") if location is None else location,
            start=result.get("start") if start is None else start,
            end=result.get("end") if end is None else end,
            attendees=result.get("attendees") if attendees is None else attendees,
            gate_result=result,
        )

    return _side_effect


def bus_social_side_effect(
    publisher: SocialPublisher,
    *,
    text: Optional[str] = None,
    platform: Optional[str] = None,
    recipients: Any = None,
    urls: Any = None,
) -> Callable[[dict], Any]:
    """Return a bus ``side_effect`` that publishes via ``publisher`` after ALLOW.

    Optional kwargs close over publish fields (gate results may omit ``text``).
    """

    def _side_effect(result: dict) -> Any:
        return publisher.publish(
            text=(result.get("text") or result.get("body") or "")
            if text is None
            else text,
            platform=(result.get("platform") or "") if platform is None else platform,
            recipients=result.get("recipients") if recipients is None else recipients,
            urls=result.get("urls") if urls is None else urls,
            gate_result=result,
        )

    return _side_effect


def bus_bio_side_effect(logger: MockBioTicketLogger) -> Callable[[dict], Any]:
    """Return a bus ``side_effect`` for bio — metadata/ticket only (no protocols)."""

    def _side_effect(result: dict) -> Any:
        return logger.log_ticket(result)

    return _side_effect


__all__ = [
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
]
