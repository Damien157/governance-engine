"""GovernedStack: single entry point composing HAIS ops, sovereign kernel, Haven2, imprint, QP.

Front door: HavenUnified (alias GovernedUnified) → GovernedStack.govern;
GovernedDecisionEngine is the live decide adapter.
GovernedActionBus: mutation facade — require_allow before any side_effect (no bypass).
Sketches stay importable via HavenUnified helpers but off the decision path.
"""

__version__ = "0.7.0"


from .action_bus import (
    CHANNELS,
    PUBLIC_EXECUTE_HELPERS,
    ActionDenied,
    GovernedActionBus,
)
from .algorithm import AlgorithmBlocked, GovernedAlgorithm
from .algorithm import intent_for_scan as algorithm_intent_for_scan
from .bio import BioBlocked, GovernedBio
from .tool import GovernedTool, ToolBlocked, intent_sha256
from .bio import intent_for_scan as bio_intent_for_scan
from .calendar import CalendarBlocked, GovernedCalendar
from .calendar import intent_for_scan as calendar_intent_for_scan
from .catalog import TIERS, catalog_snapshot, describe, import_check, live_ok
from .connectors import (
    CalendarWriter,
    ContentBindingError,
    MailSender,
    MockBioTicketLogger,
    MockCalendarWriter,
    MockMailSender,
    MockSocialPublisher,
    SocialPublisher,
    assert_bound_content,
    bus_bio_side_effect,
    bus_tool_side_effect,
    MockToolInvoker,
    ToolInvoker,
    bus_calendar_side_effect,
    bus_mail_side_effect,
    bus_social_side_effect,
)
from .contracts import (
    GOV_AUTH_FAILED,
    GOV_HAIS_CAP,
    GOV_INTENT_INVALID,
    GOV_INTERNAL,
    GOV_LATCH_CLOSED,
    GOV_POLICY_BLOCK,
    GOV_POLICY_REVIEW,
    GOV_RATE_LIMIT,
    AlgorithmScanIntent,
    BioScanIntent,
    CalendarScanIntent,
    DecisionEnvelope,
    ErrorCode,
    GovernIntent,
    IntentValidationError,
    MailScanIntent,
    SocialScanIntent,
    parse_intent,
    validate_algorithm_scan,
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
from .quantum_line import (
    attach_quantum,
    build_quantum_state,
    decode_quantum_line,
    encode_quantum_line,
    quantum_from_hais_envelope,
)
from .runtime_bridge import GovernedDecisionEngine
from .sidecar import (
    RateLimiter,
    SidecarService,
    TenantRegistry,
    TenantSpec,
    create_server,
    load_sidecar_config,
    serve_forever,
)
from .social import GovernedOutboundText, GovernedPost, PostBlocked
from .social import intent_for_scan as social_intent_for_scan
from .spectral_audit import attach_spectrum, build_spectrum
from .stack import GovernedStack, ensure_import_paths
from .unified import GovernedUnified, HavenUnified

__all__ = [
    "GovernedStack",
    "GovernedActionBus",
    "MailSender",
    "CalendarWriter",
    "SocialPublisher",
    "MockMailSender",
    "MockCalendarWriter",
    "MockSocialPublisher",
    "MockBioTicketLogger",
    "ContentBindingError",
    "assert_bound_content",
    "bus_mail_side_effect",
    "bus_calendar_side_effect",
    "bus_social_side_effect",
    "bus_bio_side_effect",
    "bus_tool_side_effect",
    "MockToolInvoker",
    "ToolInvoker",
    "GovernedMail",
    "GovernedCalendar",
    "GovernedPost",
    "GovernedAlgorithm",
    "GovernedBio",
    "ToolInvoker",
    "intent_sha256",
    "ToolBlocked",
    "GovernedTool",
    "BioBlocked",
    "attach_quantum",
    "attach_spectrum",
    "build_spectrum",
    "build_quantum_state",
    "encode_quantum_line",
    "decode_quantum_line",
    "quantum_from_hais_envelope",
    "GovernedOutboundText",
    "HavenUnified",
    "GovernedUnified",
    "GovernedDecisionEngine",
    "SendBlocked",
    "ActionDenied",
    "CalendarBlocked",
    "PostBlocked",
    "AlgorithmBlocked",
    "CHANNELS",
    "PUBLIC_EXECUTE_HELPERS",
    "ensure_import_paths",
    "intent_for_scan",
    "calendar_intent_for_scan",
    "social_intent_for_scan",
    "algorithm_intent_for_scan",
    "bio_intent_for_scan",
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
    "AlgorithmScanIntent",
    "BioScanIntent",
    "DecisionEnvelope",
    "parse_intent",
    "validate_mail_scan",
    "validate_calendar_scan",
    "validate_social_scan",
    "validate_algorithm_scan",
    "RateLimiter",
    "TenantRegistry",
    "TenantSpec",
    "SidecarService",
    "create_server",
    "load_sidecar_config",
    "serve_forever",
]

