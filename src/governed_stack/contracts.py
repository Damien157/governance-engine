"""Hard contracts for the live govern path (typed intents + stable error codes).

Validation choice (documented):
  GovernedStack.govern() never raises IntentValidationError to callers.
  Bad / untyped intents become a structured BLOCK envelope with
  error_code=GOV_INTENT_INVALID so the gate stays consistent with other
  non-ALLOW outcomes. Helpers (parse_intent, validate_*_scan) *do* raise
  IntentValidationError for unit tests and adapter builders.

Sketches must not import this module for solvers; contracts are live-gate only.
To/Cc/attendees/start/end/handles/URLs and algorithm secret keys (private_key/password/token/secret) are forbidden on scan intents (scan_intent_forbids:* → GOV_INTENT_INVALID); silent drop is not allowed.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

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
# Scan intents: reject smuggled routing fields (no silent drop)
# ---------------------------------------------------------------------------

MAIL_SCAN_FORBIDDEN = frozenset({"to", "cc", "bcc", "from"})
CALENDAR_SCAN_FORBIDDEN = frozenset({"attendees", "start", "end"})
SOCIAL_SCAN_FORBIDDEN = frozenset({"handles", "urls", "recipients"})
ALGORITHM_SCAN_FORBIDDEN = frozenset({"private_key", "password", "token", "secret"})
BIO_SCAN_FORBIDDEN = frozenset({
    "sequence",
    "fasta",
    "genome",
    "oligo",
    "oligos",
    "plasmid",
    "primer",
    "primers",
    "primer_list",
    "synthesis_order",
    "protocol",
    "protocol_steps",
    "gene_drive_construct",
    "select_agent_id",
    "pathogen_stock",
})
# Override / skip keys — sealed backdoors (never silent-allow).
BIO_BYPASS_FORBIDDEN = frozenset({
    "force_allow",
    "bypass",
    "skip_policy",
    "skip_bio_policy",
    "policy_override",
    "override_decision",
})
BIO_SCAN_REJECT_KEYS = BIO_SCAN_FORBIDDEN | BIO_BYPASS_FORBIDDEN
# Default TTL for human REVIEW approval vouchers (bio + sidecar).
BIO_VOUCHER_TTL_DEFAULT = 3600


def _reject_forbidden_scan_keys(
    data: Any,
    forbidden: frozenset,
    *,
    where: str = "intent",
) -> None:
    """Raise IntentValidationError if *data* (dict) contains forbidden routing keys.

    Also inspects a nested ``payload`` dict when present.
    """
    if not isinstance(data, dict):
        return
    for key in forbidden:
        if key in data:
            raise IntentValidationError(
                f"scan_intent_forbids:{key}",
                errors=[{"loc": [where, key], "msg": f"scan_intent_forbids:{key}", "type": "forbidden_routing"}],
            )
    payload = data.get("payload")
    if isinstance(payload, dict):
        for key in forbidden:
            if key in payload:
                raise IntentValidationError(
                    f"scan_intent_forbids:{key}",
                    errors=[{
                        "loc": [where, "payload", key],
                        "msg": f"scan_intent_forbids:{key}",
                        "type": "forbidden_routing",
                    }],
                )


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

    @model_validator(mode="before")
    @classmethod
    def _reject_routing_fields(cls, data: Any) -> Any:
        _reject_forbidden_scan_keys(data, MAIL_SCAN_FORBIDDEN, where="mail")
        return data

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

    @model_validator(mode="before")
    @classmethod
    def _reject_routing_fields(cls, data: Any) -> Any:
        _reject_forbidden_scan_keys(data, CALENDAR_SCAN_FORBIDDEN, where="calendar")
        return data

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

    @model_validator(mode="before")
    @classmethod
    def _reject_routing_fields(cls, data: Any) -> Any:
        _reject_forbidden_scan_keys(data, SOCIAL_SCAN_FORBIDDEN, where="social")
        return data

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




class AlgorithmScanPayload(BaseModel):
    """Scanned algorithm fields — purpose/cost/risk summary; no secrets."""

    model_config = ConfigDict(extra="ignore")

    subject: str = ""
    text: str = ""


class AlgorithmScanIntent(GovernIntent):
    """Algorithm run/deploy scan: Purpose + Cost + Risk; secrets forbidden."""

    action: str = "run_algorithm"
    purpose: str = ""
    summary: str = ""
    time_cost: Optional[Union[str, int, float]] = None
    space_cost: Optional[Union[str, int, float]] = None
    energy_cost: Optional[Union[str, int, float]] = None
    speedup: Optional[Union[str, int, float]] = None
    risk_notes: str = ""
    security_margin: Optional[Union[str, int, float]] = None
    payload: Optional[AlgorithmScanPayload] = None

    @model_validator(mode="before")
    @classmethod
    def _reject_secret_fields(cls, data: Any) -> Any:
        _reject_forbidden_scan_keys(data, ALGORITHM_SCAN_FORBIDDEN, where="algorithm")
        return data

    @staticmethod
    def _fmt_cost(value: Any) -> str:
        if value is None:
            return ""
        return str(value)

    @classmethod
    def _compose_scan_text(
        cls,
        *,
        summary: str,
        time_cost: Any,
        space_cost: Any,
        energy_cost: Any,
        speedup: Any,
        risk_notes: str,
        security_margin: Any,
    ) -> str:
        parts: List[str] = []
        if summary:
            parts.append(summary)
        cost_bits = []
        for label, val in (
            ("time", time_cost),
            ("space", space_cost),
            ("energy", energy_cost),
            ("speedup", speedup),
        ):
            rendered = cls._fmt_cost(val)
            if rendered:
                cost_bits.append(f"{label}={rendered}")
        if cost_bits:
            parts.append("cost: " + "; ".join(cost_bits))
        if risk_notes:
            parts.append(f"risk: {risk_notes}")
        margin = cls._fmt_cost(security_margin)
        if margin:
            parts.append(f"security_margin={margin}")
        return "\n".join(parts)

    @classmethod
    def from_scan(
        cls,
        *,
        purpose: str,
        summary: str = "",
        time_cost: Optional[Union[str, int, float]] = None,
        space_cost: Optional[Union[str, int, float]] = None,
        energy_cost: Optional[Union[str, int, float]] = None,
        speedup: Optional[Union[str, int, float]] = None,
        risk_notes: str = "",
        security_margin: Optional[Union[str, int, float]] = None,
        **kwargs: Any,
    ) -> "AlgorithmScanIntent":
        text = cls._compose_scan_text(
            summary=summary,
            time_cost=time_cost,
            space_cost=space_cost,
            energy_cost=energy_cost,
            speedup=speedup,
            risk_notes=risk_notes,
            security_margin=security_margin,
        )
        return cls(
            action="run_algorithm",
            purpose=purpose,
            summary=summary,
            time_cost=time_cost,
            space_cost=space_cost,
            energy_cost=energy_cost,
            speedup=speedup,
            risk_notes=risk_notes or "",
            security_margin=security_margin,
            payload=AlgorithmScanPayload(subject=purpose, text=text),
            **kwargs,
        )

    def dump_for_govern(self) -> Dict[str, Any]:
        purpose = self.purpose
        text = self._compose_scan_text(
            summary=self.summary,
            time_cost=self.time_cost,
            space_cost=self.space_cost,
            energy_cost=self.energy_cost,
            speedup=self.speedup,
            risk_notes=self.risk_notes,
            security_margin=self.security_margin,
        )
        if self.payload is not None:
            purpose = self.payload.subject or purpose
            text = self.payload.text if self.payload.text else text
        data = self.model_dump(
            mode="python",
            exclude_none=True,
            exclude={
                "purpose",
                "summary",
                "time_cost",
                "space_cost",
                "energy_cost",
                "speedup",
                "risk_notes",
                "security_margin",
            },
        )
        data["action"] = "run_algorithm"
        data["payload"] = {"subject": purpose, "text": text}
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
    "run_algorithm": AlgorithmScanIntent,
}


_ALGO_PAYLOAD_SCAN_KEYS = (
    "purpose",
    "summary",
    "time_cost",
    "space_cost",
    "energy_cost",
    "speedup",
    "risk_notes",
    "security_margin",
)


def _fold_algorithm_payload(raw: dict) -> dict:
    """Keep scan fields a caller put under ``payload`` for a run_algorithm intent.

    ``AlgorithmScanPayload`` only models ``subject``/``text`` and ignores other
    keys, so ``{"action": "run_algorithm", "payload": {"purpose": ...}}`` used to
    reach the policy engine with an EMPTY payload (purpose silently dropped, so
    nothing could be scanned). Fold scan-field keys (and any subject/text) from
    the payload into the top-level scan form so they are scanned and echoed.
    """
    payload = raw.get("payload")
    if not isinstance(payload, dict) or not any(k in payload for k in _ALGO_PAYLOAD_SCAN_KEYS):
        return raw

    def _join(*parts: Any) -> str:
        return "\n".join(str(x) for x in parts if x not in (None, ""))

    out = {k: v for k, v in raw.items() if k != "payload"}
    for k in _ALGO_PAYLOAD_SCAN_KEYS:
        if k in payload and k not in ("purpose", "summary"):
            out.setdefault(k, payload[k])
    out["purpose"] = _join(out.get("purpose"), payload.get("purpose"), payload.get("subject"))
    out["summary"] = _join(out.get("summary"), payload.get("summary"), payload.get("text"))
    return out


# Free-text aliases a caller may use for the contract's content fields, per
# prose channel. Anything here (and any other free-text key under ``payload``)
# is folded into the contract's scanned ``payload.subject`` / ``payload.text``
# before policy runs, so it is scanned AND echoed (hash/bind) instead of being
# dropped by the models' ``extra="ignore"``.
_MAIL_SUBJECT_ALIASES = ("subject",)
_MAIL_TEXT_ALIASES = ("body", "text", "message", "content", "html")
_CAL_SUBJECT_ALIASES = ("summary", "title")
_CAL_TEXT_ALIASES = ("description", "notes", "body", "text", "message", "content")
_SOCIAL_SUBJECT_ALIASES = ("platform",)
_SOCIAL_TEXT_ALIASES = ("text", "body", "message", "content", "title")
_PROSE_PAYLOAD_STRUCTURAL = frozenset({"subject", "text"})
_SECRET_KEYS_LOWER = frozenset(k.lower() for k in ALGORITHM_SCAN_FORBIDDEN)


def _free_text_leaves(value: Any, forbidden: frozenset, where: str) -> List[str]:
    """String leaves of *value* (str / nested list / dict) for scanning.

    Non-string scalars (numbers, bools, None) are not prose and are skipped.
    Raises IntentValidationError if a forbidden (routing/secret) key appears at
    any depth: those must never ride along inside a scanned free-text field.
    """
    if isinstance(value, str):
        return [value] if value else []
    out: List[str] = []
    if isinstance(value, dict):
        for k, v in value.items():
            if isinstance(k, str) and k.lower() in forbidden:
                raise IntentValidationError(
                    f"scan_intent_forbids:{k}",
                    errors=[{
                        "loc": [where, "payload", k],
                        "msg": f"scan_intent_forbids:{k}",
                        "type": "forbidden_routing",
                    }],
                )
            out.extend(_free_text_leaves(v, forbidden, where))
    elif isinstance(value, (list, tuple)):
        for v in value:
            out.extend(_free_text_leaves(v, forbidden, where))
    return out


def _alias_text(value: Any, forbidden: frozenset, where: str) -> List[str]:
    """Like _free_text_leaves, but a numeric value for a *named* content field
    is kept (stringified), matching the old ``str(raw.get(...))`` behaviour."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return [str(value)]
    return _free_text_leaves(value, forbidden, where)


def _reject_secret_keys(raw: dict, *, where: str) -> None:
    """Secret keys are forbidden on prose-channel scan intents (top level or
    directly under ``payload``, case-insensitive): rejected loudly, never
    dropped or folded into scanned text."""
    payload = raw.get("payload")
    scopes = [(raw, [where])]
    if isinstance(payload, dict):
        scopes.append((payload, [where, "payload"]))
    for data, loc in scopes:
        for key in data:
            if isinstance(key, str) and key.lower() in _SECRET_KEYS_LOWER:
                raise IntentValidationError(
                    f"scan_intent_forbids:{key}",
                    errors=[{
                        "loc": [*loc, key],
                        "msg": f"scan_intent_forbids:{key}",
                        "type": "forbidden_routing",
                    }],
                )


def _has_prose_fields(raw: dict, aliases: tuple) -> bool:
    """True when the intent carries any content field (so it must be folded)."""
    if any(k in raw for k in aliases):
        return True
    return isinstance(raw.get("payload"), dict)


def _join_unique(parts: List[str]) -> str:
    seen: List[str] = []
    for part in parts:
        if part and part not in seen:
            seen.append(part)
    return "\n".join(seen)


def _fold_prose_scan_fields(
    raw: dict,
    *,
    where: str,
    forbidden: frozenset,
    subject_aliases: tuple,
    text_aliases: tuple,
) -> tuple:
    """Fold every content-bearing field into ``(subject_text, body_text)``.

    Sources: the contract's own fields (top-level aliases for the channel and
    ``payload.subject`` / ``payload.text``) plus EVERY other free-text key under
    ``payload`` (e.g. ``payload.body``). Secret keys (private_key / password /
    token / secret) and the channel's routing keys are rejected, never folded.
    Identical fragments are de-duplicated so the contract-conforming shapes keep
    their exact govern dump (and ``intent_sha256``).
    """
    banned = frozenset(k.lower() for k in forbidden) | _SECRET_KEYS_LOWER
    payload = raw.get("payload")
    payload_d: Dict[str, Any] = payload if isinstance(payload, dict) else {}

    subject_parts: List[str] = []
    text_parts: List[str] = []
    for k in subject_aliases:
        if k in raw:
            subject_parts.extend(_alias_text(raw[k], banned, where))
    if "subject" in payload_d:
        subject_parts.extend(_free_text_leaves(payload_d["subject"], banned, where))
    if "text" in payload_d:
        text_parts.extend(_free_text_leaves(payload_d["text"], banned, where))
    for k in text_aliases:
        if k in raw:
            text_parts.extend(_alias_text(raw[k], banned, where))
    for k, v in payload_d.items():
        if k in _PROSE_PAYLOAD_STRUCTURAL:
            continue
        text_parts.extend(_free_text_leaves(v, banned, where))
    return _join_unique(subject_parts), _join_unique(text_parts)


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
            _reject_forbidden_scan_keys(raw, MAIL_SCAN_FORBIDDEN, where="mail")
            _reject_secret_keys(raw, where="mail")
            # Every content-bearing field (payload.subject/text, top-level
            # subject/body aliases, and any other free-text payload key such as
            # payload.body) is folded into the scanned subject + body.
            if _has_prose_fields(raw, _MAIL_SUBJECT_ALIASES + _MAIL_TEXT_ALIASES):
                subject, body = _fold_prose_scan_fields(
                    raw,
                    where="mail",
                    forbidden=MAIL_SCAN_FORBIDDEN,
                    subject_aliases=_MAIL_SUBJECT_ALIASES,
                    text_aliases=_MAIL_TEXT_ALIASES,
                )
                return MailScanIntent.from_scan(
                    subject=subject,
                    body=body,
                    **{
                        k: v
                        for k, v in raw.items()
                        if k
                        not in (
                            _MAIL_SUBJECT_ALIASES + _MAIL_TEXT_ALIASES + ("action", "payload")
                        )
                    },
                )
            return MailScanIntent.model_validate(raw)
        if model_cls is CalendarScanIntent:
            _reject_forbidden_scan_keys(raw, CALENDAR_SCAN_FORBIDDEN, where="calendar")
            _reject_secret_keys(raw, where="calendar")
            if _has_prose_fields(raw, _CAL_SUBJECT_ALIASES + _CAL_TEXT_ALIASES + ("location",)):
                summary, description = _fold_prose_scan_fields(
                    raw,
                    where="calendar",
                    forbidden=CALENDAR_SCAN_FORBIDDEN,
                    subject_aliases=_CAL_SUBJECT_ALIASES,
                    text_aliases=_CAL_TEXT_ALIASES,
                )
                return CalendarScanIntent.from_scan(
                    summary=summary,
                    description=description,
                    location=_join_unique(_alias_text(raw.get("location"), frozenset(), "calendar"))
                    if raw.get("location") is not None
                    else "",
                    **{
                        k: v
                        for k, v in raw.items()
                        if k
                        not in (
                            _CAL_SUBJECT_ALIASES
                            + _CAL_TEXT_ALIASES
                            + ("location", "action", "payload")
                        )
                    },
                )
            return CalendarScanIntent.model_validate(raw)
        if model_cls is SocialScanIntent:
            _reject_forbidden_scan_keys(raw, SOCIAL_SCAN_FORBIDDEN, where="social")
            _reject_secret_keys(raw, where="social")
            if _has_prose_fields(raw, _SOCIAL_SUBJECT_ALIASES + _SOCIAL_TEXT_ALIASES):
                platform, text = _fold_prose_scan_fields(
                    raw,
                    where="social",
                    forbidden=SOCIAL_SCAN_FORBIDDEN,
                    subject_aliases=_SOCIAL_SUBJECT_ALIASES,
                    text_aliases=_SOCIAL_TEXT_ALIASES,
                )
                return SocialScanIntent.from_scan(
                    text=text,
                    platform=platform,
                    **{
                        k: v
                        for k, v in raw.items()
                        if k
                        not in (
                            _SOCIAL_SUBJECT_ALIASES
                            + _SOCIAL_TEXT_ALIASES
                            + ("action", "payload")
                        )
                    },
                )
            return SocialScanIntent.model_validate(raw)
        if model_cls is AlgorithmScanIntent:
            _reject_forbidden_scan_keys(raw, ALGORITHM_SCAN_FORBIDDEN, where="algorithm")
            raw = _fold_algorithm_payload(raw)
            if ("purpose" in raw or "summary" in raw) and "payload" not in raw:
                return AlgorithmScanIntent.from_scan(
                    purpose=str(raw.get("purpose", "")),
                    summary=str(raw.get("summary", "")),
                    time_cost=raw.get("time_cost"),
                    space_cost=raw.get("space_cost"),
                    energy_cost=raw.get("energy_cost"),
                    speedup=raw.get("speedup"),
                    risk_notes=str(raw.get("risk_notes", "")),
                    security_margin=raw.get("security_margin"),
                    **{
                        k: v
                        for k, v in raw.items()
                        if k
                        not in (
                            "purpose",
                            "summary",
                            "time_cost",
                            "space_cost",
                            "energy_cost",
                            "speedup",
                            "risk_notes",
                            "security_margin",
                            "action",
                            "payload",
                        )
                    },
                )
            return AlgorithmScanIntent.model_validate(raw)
        return GovernIntent.model_validate(raw)
    except IntentValidationError:
        raise
    except ValidationError as exc:
        # Preserve scan_intent_forbids:* if wrapped into a ValidationError message.
        for err in exc.errors():
            msg = str(err.get("msg", ""))
            if msg.startswith("scan_intent_forbids:"):
                raise IntentValidationError(msg, errors=exc.errors()) from exc
        raise IntentValidationError(
            "intent schema validation failed",
            errors=exc.errors(),
        ) from exc


def validate_bio_scan(
    *,
    purpose: str,
    domain: str,
    intervention_class: str,
    summary: str = "",
    subject_scope: str = "",
    risk_notes: str = "",
    authority_role: str = "",
    irreversible: bool = False,
    human_subjects: bool = False,
    dual_use_flag: bool = False,
) -> BioScanIntent:
    """Build/validate BioScanIntent; raises IntentValidationError on bad/forbidden."""
    if not isinstance(purpose, str) or not purpose.strip():
        raise IntentValidationError(
            "bio purpose required",
            errors=[{"loc": ["bio", "purpose"], "msg": "purpose required", "type": "missing"}],
        )
    if not isinstance(domain, str) or not domain.strip():
        raise IntentValidationError(
            "bio domain required",
            errors=[{"loc": ["bio", "domain"], "msg": "domain required", "type": "missing"}],
        )
    if not isinstance(intervention_class, str) or not intervention_class.strip():
        raise IntentValidationError(
            "bio intervention_class required",
            errors=[{"loc": ["bio", "intervention_class"], "msg": "intervention_class required", "type": "missing"}],
        )
    try:
        return BioScanIntent.from_scan(
            purpose=purpose.strip(),
            domain=domain.strip(),
            intervention_class=intervention_class.strip(),
            summary=summary or "",
            subject_scope=subject_scope or "",
            risk_notes=risk_notes or "",
            authority_role=authority_role or "",
            irreversible=bool(irreversible),
            human_subjects=bool(human_subjects),
            dual_use_flag=bool(dual_use_flag),
        )
    except ValidationError as exc:
        raise IntentValidationError(str(exc), errors=exc.errors()) from exc



def validate_mail_scan(subject: str, body: str, **kwargs: Any) -> MailScanIntent:
    """Build + validate a MailScanIntent; raises IntentValidationError."""
    _reject_forbidden_scan_keys(kwargs, MAIL_SCAN_FORBIDDEN, where="mail")
    try:
        return MailScanIntent.from_scan(subject=subject, body=body, **kwargs)
    except IntentValidationError:
        raise
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
    _reject_forbidden_scan_keys(kwargs, CALENDAR_SCAN_FORBIDDEN, where="calendar")
    try:
        return CalendarScanIntent.from_scan(
            summary=summary, description=description, location=location, **kwargs
        )
    except IntentValidationError:
        raise
    except (ValidationError, TypeError, ValueError) as exc:
        raise IntentValidationError(
            "calendar scan intent invalid",
            errors=[{"msg": str(exc)}],
        ) from exc


def validate_social_scan(
    *, text: str, platform: str = "", **kwargs: Any
) -> SocialScanIntent:
    _reject_forbidden_scan_keys(kwargs, SOCIAL_SCAN_FORBIDDEN, where="social")
    try:
        return SocialScanIntent.from_scan(text=text, platform=platform, **kwargs)
    except IntentValidationError:
        raise
    except (ValidationError, TypeError, ValueError) as exc:
        raise IntentValidationError(
            "social scan intent invalid",
            errors=[{"msg": str(exc)}],
        ) from exc



class BioScanPayload(BaseModel):
    """Scanned bio fields — high-level purpose/risk only; no sequences/protocols."""

    model_config = ConfigDict(extra="ignore")

    subject: str = ""
    text: str = ""


class BioScanIntent(GovernIntent):
    """Biology / aging / disease intent scan; wet-lab payloads forbidden."""

    action: str = "bio_govern"
    purpose: str = ""
    domain: str = ""
    intervention_class: str = ""
    summary: str = ""
    subject_scope: str = ""
    risk_notes: str = ""
    authority_role: str = ""
    irreversible: bool = False
    human_subjects: bool = False
    dual_use_flag: bool = False
    payload: Optional[BioScanPayload] = None

    @model_validator(mode="before")
    @classmethod
    def _reject_bio_payloads(cls, data: Any) -> Any:
        _reject_forbidden_scan_keys(data, BIO_SCAN_REJECT_KEYS, where="bio")
        return data

    @classmethod
    def _compose_scan_text(
        cls,
        *,
        domain: str,
        intervention_class: str,
        summary: str,
        subject_scope: str,
        risk_notes: str,
        authority_role: str,
        irreversible: bool,
        human_subjects: bool,
        dual_use_flag: bool,
    ) -> str:
        parts: List[str] = []
        if summary:
            parts.append(summary)
        parts.append(f"domain={domain}")
        parts.append(f"intervention_class={intervention_class}")
        if subject_scope:
            parts.append(f"subject_scope={subject_scope}")
        if risk_notes:
            parts.append(f"risk: {risk_notes}")
        if authority_role:
            parts.append(f"authority_role={authority_role}")
        parts.append(f"irreversible={bool(irreversible)}")
        parts.append(f"human_subjects={bool(human_subjects)}")
        parts.append(f"dual_use_flag={bool(dual_use_flag)}")
        return "\n".join(parts)

    @classmethod
    def from_scan(
        cls,
        *,
        purpose: str,
        domain: str,
        intervention_class: str,
        summary: str = "",
        subject_scope: str = "",
        risk_notes: str = "",
        authority_role: str = "",
        irreversible: bool = False,
        human_subjects: bool = False,
        dual_use_flag: bool = False,
        **kwargs: Any,
    ) -> "BioScanIntent":
        text = cls._compose_scan_text(
            domain=domain,
            intervention_class=intervention_class,
            summary=summary,
            subject_scope=subject_scope,
            risk_notes=risk_notes,
            authority_role=authority_role,
            irreversible=irreversible,
            human_subjects=human_subjects,
            dual_use_flag=dual_use_flag,
        )
        return cls(
            action="bio_govern",
            purpose=purpose,
            domain=domain,
            intervention_class=intervention_class,
            summary=summary,
            subject_scope=subject_scope,
            risk_notes=risk_notes or "",
            authority_role=authority_role or "",
            irreversible=bool(irreversible),
            human_subjects=bool(human_subjects),
            dual_use_flag=bool(dual_use_flag),
            payload=BioScanPayload(subject=purpose, text=text),
            **kwargs,
        )

    def dump_for_govern(self) -> Dict[str, Any]:
        text = self._compose_scan_text(
            domain=self.domain,
            intervention_class=self.intervention_class,
            summary=self.summary,
            subject_scope=self.subject_scope,
            risk_notes=self.risk_notes,
            authority_role=self.authority_role,
            irreversible=self.irreversible,
            human_subjects=self.human_subjects,
            dual_use_flag=self.dual_use_flag,
        )
        data = self.model_dump(
            mode="python",
            exclude_none=True,
            exclude={
                "purpose",
                "domain",
                "intervention_class",
                "summary",
                "subject_scope",
                "risk_notes",
                "authority_role",
                "irreversible",
                "human_subjects",
                "dual_use_flag",
            },
        )
        data["action"] = "bio_govern"
        data["payload"] = {"subject": self.purpose, "text": text}
        # Keep typed fields for bio adapters / policy re-read
        data["purpose"] = self.purpose
        data["domain"] = self.domain
        data["intervention_class"] = self.intervention_class
        data["summary"] = self.summary
        data["subject_scope"] = self.subject_scope
        data["risk_notes"] = self.risk_notes
        data["authority_role"] = self.authority_role
        data["irreversible"] = self.irreversible
        data["human_subjects"] = self.human_subjects
        data["dual_use_flag"] = self.dual_use_flag
        return data


def validate_algorithm_scan(
    *,
    purpose: str,
    summary: str = "",
    time_cost: Optional[Union[str, int, float]] = None,
    space_cost: Optional[Union[str, int, float]] = None,
    energy_cost: Optional[Union[str, int, float]] = None,
    speedup: Optional[Union[str, int, float]] = None,
    risk_notes: str = "",
    security_margin: Optional[Union[str, int, float]] = None,
    **kwargs: Any,
) -> AlgorithmScanIntent:
    _reject_forbidden_scan_keys(kwargs, ALGORITHM_SCAN_FORBIDDEN, where="algorithm")
    try:
        return AlgorithmScanIntent.from_scan(
            purpose=purpose,
            summary=summary,
            time_cost=time_cost,
            space_cost=space_cost,
            energy_cost=energy_cost,
            speedup=speedup,
            risk_notes=risk_notes,
            security_margin=security_margin,
            **kwargs,
        )
    except IntentValidationError:
        raise
    except (ValidationError, TypeError, ValueError) as exc:
        raise IntentValidationError(
            "algorithm scan intent invalid",
            errors=[{"msg": str(exc)}],
        ) from exc


def intent_to_govern_dict(intent: Union[GovernIntent, dict, BaseModel]) -> Dict[str, Any]:
    """Normalize BaseModel / channel intent / dict to a govern() dict."""
    if isinstance(intent, (MailScanIntent, CalendarScanIntent, SocialScanIntent, AlgorithmScanIntent, BioScanIntent)):
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
        for r in (rs + ns)
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
    if dec == "BLOCK" and any(r.startswith("qp:") for r in rs):
        # Control QP solver failed / infeasible: fail-closed internal safety block.
        return GOV_INTERNAL
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
    "MAIL_SCAN_FORBIDDEN",
    "CALENDAR_SCAN_FORBIDDEN",
    "SOCIAL_SCAN_FORBIDDEN",
    "ALGORITHM_SCAN_FORBIDDEN",
    "GovernIntent",
    "MailScanIntent",
    "MailScanPayload",
    "CalendarScanIntent",
    "CalendarScanPayload",
    "SocialScanIntent",
    "SocialScanPayload",
    "AlgorithmScanIntent",
    "AlgorithmScanPayload",
    "DecisionEnvelope",
    "parse_intent",
    "validate_mail_scan",
    "validate_calendar_scan",
    "validate_social_scan",
    "validate_algorithm_scan",
    "validate_bio_scan",
    "intent_to_govern_dict",
    "normalize_envelope",
    "map_error_code",
    "intent_invalid_envelope",
]
