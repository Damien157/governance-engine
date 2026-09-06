"""Hard contracts for the live govern path (typed intents + stable error codes).

Validation choice (documented):
  GovernedStack.govern() never raises IntentValidationError to callers.
  Bad / untyped intents become a structured BLOCK envelope with
  error_code=GOV_INTENT_INVALID so the gate stays consistent with other
  non-ALLOW outcomes. Helpers (parse_intent, validate_*_scan) *do* raise
  IntentValidationError for unit tests and adapter builders.

Sketches must not import this module for solvers; contracts are live-gate only.
To/Cc/attendees/start/end/handles/URLs are not fields on scan intents.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

# ---------------------------------------------------------------------------
# Stable error codes
# ---------------------------------------------------------------------------


class ErrorCode(str, Enum):
    """Stable machine-readable codes attached to non-ALLOW envelopes when mapped."""

    GOV_INTENT_INVALID = "GOV_INTENT_INVALID"
    GOV_AUTH_FAILED = "GOV_AUTH_FAILED"
    GOV_POLICY_BLOCK = "GOV_POLICY_BLOCK"
    GOV_POLICY_REVIEW = "GOV_POLICY_REVIEW"
    GOV_HAIS_CAP = "GOV_HAIS_CAP"
    GOV_LATCH_CLOSED = "GOV_LATCH_CLOSED"
    GOV_RATE_LIMIT = "GOV_RATE_LIMIT"
    GOV_INTERNAL = "GOV_INTERNAL"


# String aliases for callers that prefer constants over the Enum.
GOV_INTENT_INVALID = ErrorCode.GOV_INTENT_INVALID.value
GOV_AUTH_FAILED = ErrorCode.GOV_AUTH_FAILED.value
GOV_POLICY_BLOCK = ErrorCode.GOV_POLICY_BLOCK.value
GOV_POLICY_REVIEW = ErrorCode.GOV_POLICY_REVIEW.value
GOV_HAIS_CAP = ErrorCode.GOV_HAIS_CAP.value
GOV_LATCH_CLOSED = ErrorCode.GOV_LATCH_CLOSED.value
GOV_RATE_LIMIT = ErrorCode.GOV_RATE_LIMIT.value
GOV_INTERNAL = ErrorCode.GOV_INTERNAL.value


class IntentValidationError(ValueError):
    """Raised by parse/validate helpers; .code is always GOV_INTENT_INVALID."""

    def __init__(self, message: str, *, errors: Optional[List[Any]] = None):
        super().__init__(message)
        self.code = GOV_INTENT_INVALID
        self.errors = list(errors or [])


# ---------------------------------------------------------------------------
# Intent models
# ---------------------------------------------------------------------------


class GovernIntent(BaseModel):
    """Base live-gate intent. Unknown keys ignored (passthrough via dump helpers).

    Ops / CGE expect ``action`` + optional ``payload`` dict; stack also reads
    telemetry / stress / drift / v_t / difficulty / seed / u_nom / x.
    """

    model_config = ConfigDict(extra="ignore")

    action: str
    payload: Optional[Any] = None  # subclasses may use typed payload models
    telemetry: Optional[Dict[str, Any]] = None
    stress: Optional[float] = None
    drift: Optional[float] = None
    v_t: Optional[float] = None
    difficulty: Optional[float] = None
    seed: Optional[int] = None
    u_nom: Optional[float] = None
    x: Optional[Any] = None

    @field_validator("action")
    @classmethod
    def _action_nonempty(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("action must be a non-empty string")
        return v


class MailScanPayload(BaseModel):
    """Scanned mail fields only — no to/cc/from."""

    model_config = ConfigDict(extra="ignore")

    subject: str = ""
    text: str = ""


class MailScanIntent(GovernIntent):
    """Outbound mail scan intent: subject + body text; recipients out of band."""

    action: str = "send_email"
    payload: MailScanPayload = Field(default_factory=MailScanPayload)

    @classmethod
    def from_scan(cls, subject: str, body: str, **kwargs: Any) -> "MailScanIntent":
        return cls(
            action="send_email",
            payload=MailScanPayload(subject=subject, text=body),
            **kwargs,
        )

    def dump_for_govern(self) -> Dict[str, Any]:
        """Dict shape expected by GovernedStack / CGE (payload.subject + payload.text)."""
        data = self.model_dump(mode="python", exclude_none=True)
        # Ensure payload is a plain dict with only scan fields.
        pl = data.get("payload") or {}
        data["payload"] = {
            "subject": str(pl.get("subject", "")),
            "text": str(pl.get("text", "")),
        }
        data["action"] = "send_email"
        return data


class CalendarScanPayload(BaseModel):
    """Scanned calendar fields mapped to subject/text — no attendees/start/end."""

    model_config = ConfigDict(extra="ignore")

    subject: str = ""
    text: str = ""


class CalendarScanIntent(GovernIntent):
    """Calendar write scan: summary/description/location; scheduling OOB."""

    action: str = "calendar_write"
    summary: str = ""
    description: str = ""
    location: str = ""
    payload: Optional[CalendarScanPayload] = None

    @classmethod
    def from_scan(
        cls,
        *,
        summary: str,
        description: str = "",
        location: str = "",
        **kwargs: Any,
    ) -> "CalendarScanIntent":
        text = "\n".join(p for p in [description, location] if p)
        return cls(
            action="calendar_write",
            summary=summary,
            description=description,
            location=location,
            payload=CalendarScanPayload(subject=summary, text=text),
            **kwargs,
        )

    def dump_for_govern(self) -> Dict[str, Any]:
        text = "\n".join(p for p in [self.description, self.location] if p)
        subject = self.summary
        if self.payload is not None:
            subject = self.payload.subject or subject
            text = self.payload.text if self.payload.text else text
        data = self.model_dump(
            mode="python",
            exclude_none=True,
            exclude={"summary", "description", "location"},
        )
        data["action"] = "calendar_write"
        data["payload"] = {"subject": subject, "text": text}
        return data


class SocialScanPayload(BaseModel):
    """Scanned social fields — no recipients/URLs/handles as routing fields."""

    model_config = ConfigDict(extra="ignore")

    subject: str = ""
    text: str = ""


class SocialScanIntent(GovernIntent):
    """Outbound social/post scan: platform tag + body; handles/URLs OOB."""

    action: str = "publish_post"
    text: str = ""
    platform: str = ""
    payload: Optional[SocialScanPayload] = None

    @classmethod
    def from_scan(
        cls, *, text: str, platform: str = "", **kwargs: Any
    ) -> "SocialScanIntent":
        return cls(
            action="publish_post",
            text=text,
            platform=platform or "",
            payload=SocialScanPayload(subject=platform or "", text=text),
            **kwargs,
        )

    def dump_for_govern(self) -> Dict[str, Any]:
        platform = self.platform or ""
        body = self.text
        if self.payload is not None:
            platform = self.payload.subject if self.payload.subject else platform
            body = self.payload.text if self.payload.text else body
        data = self.model_dump(
            mode="python",
            exclude_none=True,
            exclude={"text", "platform"},
        )
        data["action"] = "publish_post"
        data["payload"] = {"subject": platform, "text": body}
        return data


class DecisionEnvelope(BaseModel):
    """Typed view of a govern() result (optional fields for additive envelopes)."""

    model_config = ConfigDict(extra="allow")

    decision: str
    reasons: List[str] = Field(default_factory=list)
    error_code: Optional[str] = None
    latency_ms: Optional[float] = None
    entry_id: Optional[Any] = None
    hais: Optional[Dict[str, Any]] = None
    haven2: Optional[Dict[str, Any]] = None
    solver: Optional[Dict[str, Any]] = None
    audit: Optional[Dict[str, Any]] = None
    notes: Optional[List[str]] = None


# ---------------------------------------------------------------------------
# Parse / validate helpers
# ---------------------------------------------------------------------------

_CHANNEL_ACTIONS = {
    "send_email": MailScanIntent,
    "calendar_write": CalendarScanIntent,
    "publish_post": SocialScanIntent,
}


def parse_intent(raw: dict) -> GovernIntent:
    """Parse a raw dict into GovernIntent (or channel subclass). Raises IntentValidationError."""
    if not isinstance(raw, dict):
        raise IntentValidationError(
            "intent must be a dict",
            errors=[{"type": "dict_type", "msg": "intent must be a dict"}],
        )
    action = raw.get("action")
    model_cls = _CHANNEL_ACTIONS.get(action, GovernIntent) if isinstance(action, str) else GovernIntent
    try:
        if model_cls is MailScanIntent:
            # Prefer payload subject/text; allow top-level subject/body aliases.
            if "payload" not in raw and ("subject" in raw or "body" in raw):
                return MailScanIntent.from_scan(
                    subject=str(raw.get("subject", "")),
                    body=str(raw.get("body", "")),
                    **{
                        k: v
                        for k, v in raw.items()
                        if k not in ("subject", "body", "action", "payload")
                    },
                )
            return MailScanIntent.model_validate(raw)
        if model_cls is CalendarScanIntent:
            if "summary" in raw and "payload" not in raw:
                return CalendarScanIntent.from_scan(
                    summary=str(raw.get("summary", "")),
                    description=str(raw.get("description", "")),
                    location=str(raw.get("location", "")),
                    **{
                        k: v
                        for k, v in raw.items()
                        if k
                        not in (
                            "summary",
                            "description",
                            "location",
                            "action",
                            "payload",
                            "attendees",
                            "start",
                            "end",
                        )
                    },
                )
            return CalendarScanIntent.model_validate(raw)
        if model_cls is SocialScanIntent:
            if ("text" in raw or "platform" in raw) and "payload" not in raw:
                return SocialScanIntent.from_scan(
                    text=str(raw.get("text", raw.get("body", ""))),
                    platform=str(raw.get("platform", "")),
                    **{
                        k: v
                        for k, v in raw.items()
                        if k
                        not in (
                            "text",
                            "body",
                            "platform",
                            "action",
                            "payload",
                            "recipients",
                            "urls",
                        )
                    },
                )
            return SocialScanIntent.model_validate(raw)
        return GovernIntent.model_validate(raw)
    except ValidationError as exc:
        raise IntentValidationError(
            "intent schema validation failed",
            errors=exc.errors(),
        ) from exc


def validate_mail_scan(subject: str, body: str, **kwargs: Any) -> MailScanIntent:
    """Build + validate a MailScanIntent; raises IntentValidationError."""
    try:
        return MailScanIntent.from_scan(subject=subject, body=body, **kwargs)
    except ValidationError as exc:
        raise IntentValidationError(
            "mail scan intent invalid",
            errors=exc.errors(),
        ) from exc
    except (TypeError, ValueError) as exc:
        raise IntentValidationError(
            "mail scan intent invalid",
            errors=[{"msg": str(exc)}],
        ) from exc


def validate_calendar_scan(
    *,
    summary: str,
    description: str = "",
    location: str = "",
    **kwargs: Any,
) -> CalendarScanIntent:
    try:
        return CalendarScanIntent.from_scan(
            summary=summary, description=description, location=location, **kwargs
        )
    except (ValidationError, TypeError, ValueError) as exc:
        raise IntentValidationError(
            "calendar scan intent invalid",
            errors=[{"msg": str(exc)}],
        ) from exc


def validate_social_scan(
    *, text: str, platform: str = "", **kwargs: Any
) -> SocialScanIntent:
    try:
        return SocialScanIntent.from_scan(text=text, platform=platform, **kwargs)
    except (ValidationError, TypeError, ValueError) as exc:
        raise IntentValidationError(
            "social scan intent invalid",
            errors=[{"msg": str(exc)}],
        ) from exc


def intent_to_govern_dict(intent: Union[GovernIntent, dict, BaseModel]) -> Dict[str, Any]:
    """Normalize BaseModel / channel intent / dict to a govern() dict."""
    if isinstance(intent, (MailScanIntent, CalendarScanIntent, SocialScanIntent)):
        return intent.dump_for_govern()
    if isinstance(intent, GovernIntent):
        return intent.model_dump(mode="python", exclude_none=True)
    if isinstance(intent, BaseModel):
        return intent.model_dump(mode="python", exclude_none=True)
    if isinstance(intent, dict):
        parsed = parse_intent(intent)
        return intent_to_govern_dict(parsed)
    raise IntentValidationError(
        f"unsupported intent type: {type(intent).__name__}",
        errors=[{"msg": f"unsupported intent type: {type(intent).__name__}"}],
    )


def normalize_envelope(raw: dict) -> Dict[str, Any]:
    """Ensure decision/reasons/error_code/latency_ms keys are present when known."""
    if not isinstance(raw, dict):
        return {
            "decision": "BLOCK",
            "reasons": ["invalid_envelope"],
            "error_code": GOV_INTERNAL,
        }
    out = dict(raw)
    out.setdefault("decision", "BLOCK")
    out.setdefault("reasons", [])
    # Keep optional keys only if already present or when DecisionEnvelope fills them.
    try:
        env = DecisionEnvelope.model_validate(out)
        dumped = env.model_dump(mode="python", exclude_none=True)
        # Preserve extra keys from original.
        for k, v in out.items():
            if k not in dumped:
                dumped[k] = v
        return dumped
    except ValidationError:
        return out


def map_error_code(
    *,
    decision: str,
    reasons: Optional[List[str]] = None,
    notes: Optional[List[str]] = None,
) -> Optional[str]:
    """Map non-ALLOW outcomes to a stable code when the reason is clear; else None."""
    dec = (decision or "").upper()
    if dec == "ALLOW":
        return None
    rs = [str(r) for r in (reasons or [])]
    ns = [str(n) for n in (notes or [])]
    joined = " | ".join(rs + ns).lower()

    if any("hais_capability_cap" in r for r in rs):
        return GOV_HAIS_CAP
    if any("rate_limit" in r.lower() for r in rs):
        return GOV_RATE_LIMIT
    if any(
        "latch" in r.lower() or "transistor_closed" in r.lower() or "haven2_closed" in r.lower()
        for r in rs
    ):
        return GOV_LATCH_CLOSED
    if any(r.startswith("intent_invalid") or "gov_intent_invalid" in r.lower() for r in rs):
        return GOV_INTENT_INVALID
    if any(
        r.startswith("ops_error:")
        and any(
            tok in r.lower()
            for tok in ("auth", "token", "jwt", "unauthorized", "revoked", "expired")
        )
        for r in rs
    ):
        return GOV_AUTH_FAILED
    if dec == "REVIEW":
        return GOV_POLICY_REVIEW
    if dec == "BLOCK":
        # Honest policy mapping: pii / banned / explicit policy reasons.
        if any(
            r.startswith("pii:")
            or r.startswith("banned_term:")
            or r.startswith("policy")
            for r in rs
        ):
            return GOV_POLICY_BLOCK
        # Opaque ops failures — internal, not policy.
        if any(r.startswith("ops_error:") for r in rs):
            return GOV_INTERNAL
        # Unknown BLOCK reasons: omit rather than invent.
        _ = joined
        return None
    return None


def intent_invalid_envelope(
    *,
    errors: Optional[List[Any]] = None,
    message: str = "intent validation failed",
) -> Dict[str, Any]:
    """Structured BLOCK used by govern() on bad intents (no raise)."""
    detail = message
    if errors:
        # Keep reasons short; full errors list lives under audit.
        detail = f"intent_invalid:{message}"
    return {
        "decision": "BLOCK",
        "reasons": [detail if detail.startswith("intent_invalid:") else f"intent_invalid:{detail}"],
        "entry_id": None,
        "hais": {"cap": None, "risk": None, "instability": None},
        "haven2": {"realm": None, "p_hat": None, "open": False},
        "error_code": GOV_INTENT_INVALID,
        "audit": {"validation_errors": errors or [], "ops": None},
        "notes": ["contracts: intent failed schema validation; solvers skipped"],
    }


__all__ = [
    "ErrorCode",
    "GOV_INTENT_INVALID",
    "GOV_AUTH_FAILED",
    "GOV_POLICY_BLOCK",
    "GOV_POLICY_REVIEW",
    "GOV_HAIS_CAP",
    "GOV_LATCH_CLOSED",
    "GOV_RATE_LIMIT",
    "GOV_INTERNAL",
    "IntentValidationError",
    "GovernIntent",
    "MailScanIntent",
    "MailScanPayload",
    "CalendarScanIntent",
    "CalendarScanPayload",
    "SocialScanIntent",
    "SocialScanPayload",
    "DecisionEnvelope",
    "parse_intent",
    "validate_mail_scan",
    "validate_calendar_scan",
    "validate_social_scan",
    "intent_to_govern_dict",
    "normalize_envelope",
    "map_error_code",
    "intent_invalid_envelope",
]
