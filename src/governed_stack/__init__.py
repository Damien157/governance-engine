"""GovernedStack: single entry point composing HAIS ops, sovereign kernel, Haven2, imprint, QP.

Front door: HavenUnified (alias GovernedUnified) → GovernedStack.govern;
GovernedDecisionEngine is the live decide adapter.
Sketches stay importable via HavenUnified helpers but off the decision path.
"""

from .calendar import CalendarBlocked, GovernedCalendar
from .calendar import intent_for_scan as calendar_intent_for_scan
from .catalog import TIERS, catalog_snapshot, describe, import_check, live_ok
from .contracts import (
    GOV_AUTH_FAILED,
    GOV_HAIS_CAP,
    GOV_INTENT_INVALID,
    GOV_INTERNAL,
    GOV_LATCH_CLOSED,
    GOV_POLICY_BLOCK,
    GOV_POLICY_REVIEW,
    GOV_RATE_LIMIT,
    CalendarScanIntent,
    DecisionEnvelope,
    ErrorCode,
    GovernIntent,
    IntentValidationError,
    MailScanIntent,
    SocialScanIntent,
    parse_intent,
    validate_calendar_scan,
    validate_mail_scan,
    validate_social_scan,
)
from .key_providers import (
    EnvKMSKeyProvider,
    LocalPEMKeyProvider,
    RotatingKeyProvider,
    SigningKeyProvider,
    resolve_signing_key_provider,
)
from .mail import GovernedMail, SendBlocked, intent_for_scan
from .observability import DecisionMetrics, structured_log
from .runtime_bridge import GovernedDecisionEngine
from .social import GovernedOutboundText, GovernedPost, PostBlocked
from .social import intent_for_scan as social_intent_for_scan
from .stack import GovernedStack, ensure_import_paths
from .unified import GovernedUnified, HavenUnified

__all__ = [
    "GovernedStack",
    "GovernedMail",
    "GovernedCalendar",
    "GovernedPost",
    "GovernedOutboundText",
    "HavenUnified",
    "GovernedUnified",
    "GovernedDecisionEngine",
    "SendBlocked",
    "CalendarBlocked",
    "PostBlocked",
    "ensure_import_paths",
    "intent_for_scan",
    "calendar_intent_for_scan",
    "social_intent_for_scan",
    "TIERS",
    "describe",
    "import_check",
    "live_ok",
    "catalog_snapshot",
    "DecisionMetrics",
    "structured_log",
    "SigningKeyProvider",
    "LocalPEMKeyProvider",
    "EnvKMSKeyProvider",
    "RotatingKeyProvider",
    "resolve_signing_key_provider",
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
    "CalendarScanIntent",
    "SocialScanIntent",
    "DecisionEnvelope",
    "parse_intent",
    "validate_mail_scan",
    "validate_calendar_scan",
    "validate_social_scan",
]

__version__ = "0.3.2"
