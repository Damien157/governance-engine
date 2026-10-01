"""Bio semantic judge — adapter between the bio channel and the overlay kernel.

The kernel (``bio_semantic_overlay``, user-authored, vendored verbatim) owns
the decision logic: validated scores -> ALLOW / REVIEW / BLOCK, tighten-only
against the structural decision, fail closed on any classifier problem. This
module only *wires* it into the governed bio channel:

1. Structural bridge. ``classify_bio`` returns ALLOW_CANDIDATE / REVIEW /
   BLOCK. That maps 1:1 onto the kernel's structural input
   (ALLOW_CANDIDATE -> ALLOW). Anything else is passed as an invalid state, so
   the kernel blocks it (``unknown_structural``). The kernel's ALLOW maps back
   to ALLOW_CANDIDATE: the bio layer never grants ALLOW, the stack does. The
   merge back into ``BioPolicyResult`` can only tighten.
2. Judge runs before the voucher. ``govern_bio_request`` runs the judge
   *before* ``stack.govern``, so it also runs before voucher honoring. When
   the judged policy is BLOCK, the approval voucher is not passed to the
   stack at all, and ``apply_bio_voucher_honor`` refuses BLOCK as before.
   This is the only bio pipeline: ``GovernedBio.check`` and the sidecar
   ``channel=bio`` both call it.
3. Classifier. The default is ``stub_heuristic_classifier``, which maps the
   offline stub scorer (``bio_semantic.score_bio_text``) onto the kernel's
   labels. A model judge can be injected through ``BioSemanticJudge``. Its
   classifier only has to match ``SemanticClassifier``
   (``text -> SemanticScores``). Nothing here makes network calls.
4. Reason hygiene. Decision reasons come from a closed vocabulary
   (``CHARTER_REASONS``) built from the charter labels. They are derived only
   from validated scores and the kernel's failure category. Classifier
   free-text reasons are never copied into decisions or the audit trail.
   Request text is represented only by its SHA-256.
5. Fail closed. Classifier output is snapshotted (each required label read
   exactly once, into a plain dict) before the kernel validates it. A mapping
   that changes between validation and use therefore cannot slip past.
   Exceptions from the kernel call itself, or an outcome that is malformed or
   looser than structural, give a REVIEW floor (BLOCK if the structural state
   is unknown).
6. Audit. Each judgement, including structural BLOCKs where the kernel skips
   the classifier, is written as its own signed, chained audit row that
   references the governed entry. If that write fails, an ALLOW is tightened
   to REVIEW.

Check-only: never emits protocols, sequences, or wet-lab content.
"""

from __future__ import annotations

import asyncio
import hashlib
import numbers
from dataclasses import dataclass, field
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Mapping,
    Optional,
    Protocol,
    Tuple,
    cast,
    runtime_checkable,
)

from .bio_policy import (
    BioPolicyResult,
    apply_bio_voucher_honor,
    classify_bio,
    enqueue_bio_overlay_review,
    tighten_decision,
)
from .bio_semantic import (
    LABEL_BENIGN,
    LABEL_CLINICAL,
    LABEL_DUAL_USE,
    LABEL_ENHANCEMENT,
    LABEL_SYNTHESIS,
    LABEL_UNCERTAIN,
    score_bio_text,
)
from .bio_semantic_overlay import (
    DEFAULT_CONFIG,
    HARMFUL_LABELS,
    REQUIRED_LABELS,
    UNCERTAIN_LABEL,
    OverlayConfig,
    OverlayOutcome,
    SemanticDecision,
    SemanticScores,
    evaluate_overlay,
    tighten,
)

# --------------------------------------------------------------------------
# Reason vocabulary (charter §2 labels + overlay/merge markers). Closed set.
# --------------------------------------------------------------------------

REASON_PREFIX = "bio_semantic:"

LABEL_REASONS: Mapping[str, str] = {
    label: f"{REASON_PREFIX}{label}" for label in sorted(REQUIRED_LABELS)
}
REASON_BENIGN = f"{REASON_PREFIX}benign_science"
REASON_TIGHTEN_BLOCK = f"{REASON_PREFIX}tighten_block"
REASON_TIGHTEN_REVIEW = f"{REASON_PREFIX}tighten_review"
REASON_REVIEW_NOTED = f"{REASON_PREFIX}review_noted"
REASON_STRUCTURAL_BLOCK_FINAL = f"{REASON_PREFIX}structural_block_final"
REASON_VOUCHER_WITHHELD = f"{REASON_PREFIX}voucher_withheld"
REASON_AUDIT_WRITE_FAILED = f"{REASON_PREFIX}audit_write_failed"

# Kernel fail_reason categories (prefix before the first ':') plus the two
# adapter-level failures. Anything else collapses to unknown_failure.
FAIL_CATEGORIES = frozenset(
    {
        "timeout",
        "classifier_error",
        "classifier_not_implemented",
        "invalid_scores",
        "invalid_text",
        "text_too_long",
        "internal_error",
        "unknown_structural",
        "unknown_failure",
        "adapter_error",
        "malformed_outcome",
    }
)
FAIL_REASONS: Mapping[str, str] = {
    cat: f"{REASON_PREFIX}judge_failed:{cat}" for cat in sorted(FAIL_CATEGORIES)
}

CHARTER_REASONS = frozenset(
    {
        *LABEL_REASONS.values(),
        *FAIL_REASONS.values(),
        REASON_BENIGN,
        REASON_TIGHTEN_BLOCK,
        REASON_TIGHTEN_REVIEW,
        REASON_REVIEW_NOTED,
        REASON_STRUCTURAL_BLOCK_FINAL,
        REASON_VOUCHER_WITHHELD,
        REASON_AUDIT_WRITE_FAILED,
    }
)

GOV_BIO_SEMANTIC_REVIEW = SemanticDecision.REVIEW.value
GOV_BIO_SEMANTIC_BLOCK = SemanticDecision.BLOCK.value

# Audit row label for judgement records. Deliberately not ALLOW/REVIEW/BLOCK:
# REVIEW rows are auto-enqueued by AuditStorage.log_decision (that would give
# a second, unusable pending item) and storage.stats() counts ALLOW/BLOCK rows
# as governed decisions. The final bio decision is in ``result`` + metadata.
AUDIT_DECISION_LABEL = "BIO_SEMANTIC_JUDGEMENT"
AUDIT_ACTION = "bio_semantic_judgement"


def fail_reason_token(fail_reason: str) -> str:
    """Map a kernel/adapter fail_reason onto the closed reason vocabulary."""
    category = str(fail_reason).split(":", 1)[0]
    if category not in FAIL_CATEGORIES:
        category = "unknown_failure"
    return FAIL_REASONS[category]


# --------------------------------------------------------------------------
# Classifier interface + default stub classifier
# --------------------------------------------------------------------------


@runtime_checkable
class SemanticClassifier(Protocol):
    """Model-judge slot: untrusted text in, label probabilities out.

    Must return ``SemanticScores`` with a probability in [0, 1] for every
    label in ``REQUIRED_LABELS``. It must treat the text as data, never as
    instructions (charter §1.1). Exceptions, timeouts and malformed output
    fail closed in the kernel.
    """

    def __call__(self, text: str) -> SemanticScores: ...


class StubScorerError(RuntimeError):
    """Stub scorer reported an internal error. Kernel maps it to REVIEW."""


_STUB_LABEL_TO_KERNEL = {
    LABEL_SYNTHESIS: "synthesis_howto",
    LABEL_ENHANCEMENT: "enhancement",
    LABEL_DUAL_USE: "dual_use_adjacent",
}


def stub_heuristic_classifier(text: str) -> SemanticScores:
    """Default classifier: offline stub scorer -> kernel label scores.

    Mapping preserves the stub's 0.6.2 actions under the kernel's default
    thresholds (synthesis/enhancement/dual-use -> BLOCK, clinical ->
    REVIEW, uncertain/injection -> REVIEW, benign -> ALLOW). Stub errors
    and unknown labels raise, so they hit the kernel's fail-closed path.
    Reasons are category tokens only: the stub's cue phrases are never
    forwarded.
    """
    sr = score_bio_text(purpose=text)
    if sr.error:
        raise StubScorerError("stub scorer error")
    scores = {label: 0.0 for label in REQUIRED_LABELS}
    p = float(sr.p_harmful)
    conf = float(sr.confidence)
    if sr.label in _STUB_LABEL_TO_KERNEL:
        kernel_label = _STUB_LABEL_TO_KERNEL[sr.label]
        scores[kernel_label] = max(p, conf)
        return SemanticScores(scores=scores, reasons=[LABEL_REASONS[kernel_label]])
    if sr.label == LABEL_CLINICAL:
        scores["clinical_irreversible"] = max(p, 0.45)
        return SemanticScores(
            scores=scores, reasons=[LABEL_REASONS["clinical_irreversible"]]
        )
    if sr.label == LABEL_UNCERTAIN:
        scores[UNCERTAIN_LABEL] = max(p, conf, 0.5)
        return SemanticScores(scores=scores, reasons=[LABEL_REASONS[UNCERTAIN_LABEL]])
    if sr.label == LABEL_BENIGN:
        return SemanticScores(scores=scores, reasons=[REASON_BENIGN])
    raise StubScorerError("stub scorer returned an unknown label")


STUB_SCORER_ID = "stub_adapter_v1"


@dataclass(frozen=True)
class BioSemanticJudge:
    """Injectable judge = classifier + audit id + kernel config."""

    classifier: Callable[[str], SemanticScores] = stub_heuristic_classifier
    scorer_id: str = STUB_SCORER_ID
    config: OverlayConfig = DEFAULT_CONFIG

    def __post_init__(self) -> None:
        if not callable(self.classifier):
            raise TypeError("BioSemanticJudge.classifier must be callable(text) -> SemanticScores")
        sid = self.scorer_id
        if (
            not isinstance(sid, str)
            or not sid
            or len(sid) > 64
            or not sid.isprintable()
        ):
            raise ValueError("scorer_id must be a printable string of 1..64 chars")
        if not isinstance(self.config, OverlayConfig):
            raise TypeError("config must be an OverlayConfig")


DEFAULT_JUDGE = BioSemanticJudge()


# --------------------------------------------------------------------------
# Output snapshot (anti rug-pull) and guarded kernel call
# --------------------------------------------------------------------------


def snapshot_classifier_output(result: object) -> SemanticScores:
    """Freeze classifier output so validation and use see the same values.

    Each required label is read exactly once into a plain dict. Real numbers
    are converted to ``float`` (bools are left alone so the kernel rejects
    them). Missing labels and non-numeric values are passed through as-is
    for the kernel to reject. Non-mapping scores are passed through
    unchanged, also for the kernel to reject. Only ``str`` reasons are kept.
    """
    raw = getattr(result, "scores", None)
    scores: object
    if isinstance(raw, Mapping):
        snap: Dict[str, object] = {}
        for label in sorted(REQUIRED_LABELS):
            try:
                value = raw[label]
            except KeyError:
                continue
            if isinstance(value, numbers.Real) and not isinstance(value, bool):
                value = float(value)
            snap[label] = value
        scores = snap
    else:
        scores = raw
    raw_reasons = getattr(result, "reasons", None)
    reasons: List[str] = []
    if isinstance(raw_reasons, (list, tuple)):
        reasons = [r for r in raw_reasons if isinstance(r, str)]
    return SemanticScores(scores=cast(Dict[str, float], scores), reasons=reasons)


def _guarded(classifier: Callable[[str], SemanticScores]) -> Callable[[str], SemanticScores]:
    def call(text: str) -> SemanticScores:
        return snapshot_classifier_output(classifier(text))

    return call


_STRUCTURAL = {
    "ALLOW_CANDIDATE": SemanticDecision.ALLOW,
    "REVIEW": SemanticDecision.REVIEW,
    "BLOCK": SemanticDecision.BLOCK,
}


def structural_from_policy(policy: BioPolicyResult) -> Optional[SemanticDecision]:
    """Map structural decision to kernel input; None = invalid (kernel blocks)."""
    decision = getattr(policy, "decision", None)
    if not isinstance(decision, str):
        return None
    return _STRUCTURAL.get(decision)


def _sha256_text(text: object) -> str:
    if not isinstance(text, str):
        return ""
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def _failure_outcome(
    structural: Optional[SemanticDecision], text: object, fail: str
) -> OverlayOutcome:
    if isinstance(structural, SemanticDecision):
        decision = tighten(structural, SemanticDecision.REVIEW)
    else:
        decision = SemanticDecision.BLOCK
    return OverlayOutcome(
        decision=decision,
        structural=structural if isinstance(structural, SemanticDecision) else None,
        semantic=None,
        p_max=None,
        scores=None,
        reasons=(),
        fail_reason=fail,
        text_sha256=_sha256_text(text),
        chunks=0,
    )


def _outcome_well_formed(
    outcome: object, structural: Optional[SemanticDecision]
) -> bool:
    if not isinstance(outcome, OverlayOutcome):
        return False
    if not isinstance(outcome.decision, SemanticDecision):
        return False
    if outcome.structural is not structural:
        return False  # outcome must describe the structural input we passed
    if not isinstance(structural, SemanticDecision):
        return outcome.decision is SemanticDecision.BLOCK
    if outcome.decision.severity < structural.severity:
        return False
    if outcome.fail_reason and outcome.decision.severity < SemanticDecision.REVIEW.severity:
        return False
    if isinstance(outcome.semantic, SemanticDecision) and (
        outcome.decision.severity < outcome.semantic.severity
    ):
        return False
    return True


def run_semantic_judge(
    structural: Optional[SemanticDecision],
    text: str,
    judge: BioSemanticJudge = DEFAULT_JUDGE,
) -> OverlayOutcome:
    """Call the kernel with a snapshotting classifier. Never raises.

    Any exception from the kernel call, or an outcome that is malformed or
    looser than structural, gives REVIEW (BLOCK if structural is invalid).
    """
    try:
        outcome = evaluate_overlay(
            cast(SemanticDecision, structural),
            text,
            classifier=_guarded(judge.classifier),
            config=judge.config,
        )
    except Exception as exc:  # message deliberately not propagated
        return _failure_outcome(structural, text, f"adapter_error:{type(exc).__name__}")
    if not _outcome_well_formed(outcome, structural):
        return _failure_outcome(structural, text, "malformed_outcome")
    return outcome


# --------------------------------------------------------------------------
# Reasons + merge back into BioPolicyResult (tighten-only)
# --------------------------------------------------------------------------


def charter_reasons(outcome: OverlayOutcome, config: OverlayConfig = DEFAULT_CONFIG) -> List[str]:
    """Closed-vocabulary reasons. Uses only validated scores + fail category."""
    if outcome.fail_reason:
        return [fail_reason_token(outcome.fail_reason)]
    if outcome.semantic is None:
        # Kernel skipped the classifier: structural BLOCK is final.
        return [REASON_STRUCTURAL_BLOCK_FINAL]
    out: List[str] = []
    scores = outcome.scores or {}
    for label in sorted(HARMFUL_LABELS):
        threshold = min(config.review_at, config.label_block_at.get(label, config.block_at))
        if float(scores.get(label, 0.0)) >= threshold:
            out.append(LABEL_REASONS[label])
    if float(scores.get(UNCERTAIN_LABEL, 0.0)) >= config.uncertain_review_at:
        out.append(LABEL_REASONS[UNCERTAIN_LABEL])
    if not out and outcome.semantic is SemanticDecision.ALLOW:
        out.append(REASON_BENIGN)
    return out


_POLICY_SEVERITY = {"ALLOW_CANDIDATE": 0, "REVIEW": 1, "BLOCK": 2}


def merge_judgement(
    policy: BioPolicyResult,
    outcome: OverlayOutcome,
    config: OverlayConfig = DEFAULT_CONFIG,
) -> BioPolicyResult:
    """Fold the kernel outcome into the structural policy. Tighten-only.

    Structural BLOCK keeps its own error_code (e.g. GOV_BIO_DUAL_USE).
    Unknown structural states come back as BLOCK.
    """
    reasons = list(policy.reasons)
    for r in charter_reasons(outcome, config):
        if r not in reasons:
            reasons.append(r)

    def result(decision: str, code: Optional[str], extra: Optional[str]) -> BioPolicyResult:
        rs = reasons + ([extra] if extra and extra not in reasons else [])
        return BioPolicyResult(
            decision=decision,
            reasons=rs,
            error_code=code,
            domain=policy.domain,
            intervention_class=policy.intervention_class,
        )

    if policy.decision not in _POLICY_SEVERITY:
        return result("BLOCK", GOV_BIO_SEMANTIC_BLOCK, REASON_TIGHTEN_BLOCK)
    if policy.decision == "BLOCK":
        return result("BLOCK", policy.error_code or "GOV_BIO_DUAL_USE", None)
    if outcome.decision is SemanticDecision.BLOCK:
        return result("BLOCK", GOV_BIO_SEMANTIC_BLOCK, REASON_TIGHTEN_BLOCK)
    if outcome.decision is SemanticDecision.REVIEW:
        if policy.decision == "ALLOW_CANDIDATE":
            return result("REVIEW", GOV_BIO_SEMANTIC_REVIEW, REASON_TIGHTEN_REVIEW)
        return result(
            "REVIEW", policy.error_code or GOV_BIO_SEMANTIC_REVIEW, REASON_REVIEW_NOTED
        )
    if outcome.decision is SemanticDecision.ALLOW:
        # Structural wins (ALLOW_CANDIDATE stays a candidate; REVIEW stays REVIEW).
        return result(policy.decision, policy.error_code, None)
    return result("BLOCK", GOV_BIO_SEMANTIC_BLOCK, REASON_TIGHTEN_BLOCK)


# --------------------------------------------------------------------------
# Request + judgement
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class BioRequest:
    purpose: str
    domain: str
    intervention_class: str
    summary: str = ""
    subject_scope: str = ""
    risk_notes: str = ""
    authority_role: str = ""
    irreversible: bool = False
    human_subjects: bool = False
    dual_use_flag: bool = False

    def judge_text(self) -> str:
        """All free-text fields the judge sees, in a fixed order."""
        parts = (
            self.purpose,
            self.summary,
            self.subject_scope,
            self.risk_notes,
            self.authority_role,
            self.domain,
            self.intervention_class,
        )
        return "\n".join(str(p) for p in parts if p)


@dataclass(frozen=True)
class BioJudgement:
    structural_policy: BioPolicyResult
    policy: BioPolicyResult  # structural merged with the semantic outcome
    outcome: OverlayOutcome
    scorer_id: str
    config: OverlayConfig = DEFAULT_CONFIG


def judge_bio_request(
    request: BioRequest, judge: BioSemanticJudge = DEFAULT_JUDGE
) -> BioJudgement:
    """Structural ``classify_bio`` followed by the semantic kernel, merged tighten-only."""
    structural_policy = classify_bio(
        purpose=request.purpose,
        domain=request.domain,
        intervention_class=request.intervention_class,
        summary=request.summary,
        risk_notes=request.risk_notes,
        authority_role=request.authority_role,
        irreversible=request.irreversible,
        human_subjects=request.human_subjects,
        dual_use_flag=request.dual_use_flag,
    )
    outcome = run_semantic_judge(
        structural_from_policy(structural_policy), request.judge_text(), judge
    )
    merged = merge_judgement(structural_policy, outcome, judge.config)
    return BioJudgement(
        structural_policy=structural_policy,
        policy=merged,
        outcome=outcome,
        scorer_id=judge.scorer_id,
        config=judge.config,
    )


def semantic_audit_dict(judgement: BioJudgement) -> Dict[str, Any]:
    """Envelope/audit view of a judgement: SHA-256 only, never raw text."""
    o = judgement.outcome
    classifier_reasons = list(o.reasons)
    return {
        "decision": o.decision.name,
        "structural": o.structural.name if o.structural else None,
        "semantic": o.semantic.name if o.semantic else None,
        "p_max": o.p_max,
        "scores": dict(o.scores) if o.scores is not None else None,
        "reasons": [r for r in charter_reasons(o, judgement.config) if r in CHARTER_REASONS],
        "classifier_reasons_dropped": len(classifier_reasons),
        "fail_reason": o.fail_reason,
        "text_sha256": o.text_sha256,
        "chunks": o.chunks,
        "scorer": judgement.scorer_id,
    }


# --------------------------------------------------------------------------
# Audit record
# --------------------------------------------------------------------------


def record_judgement(
    engine: object,
    *,
    judged_entry_id: Optional[str],
    judgement: BioJudgement,
    final_decision: str,
    final_error_code: Optional[str],
    stack_decision: str,
    voucher_presented: bool,
    voucher_honored: bool,
    extra_reasons: Tuple[str, ...] = (),
) -> str:
    """Write the judgement as its own signed, chained audit row.

    Same append-only pattern as ``resolve_review`` / runtime halt: a new row
    referencing the governed entry, never mutating it. Raises if the engine
    has no audit storage.
    """
    storage = getattr(engine, "storage", None)
    log = getattr(storage, "log_decision", None)
    if not callable(log):
        raise RuntimeError("bio semantic audit unavailable: engine lacks storage.log_decision")
    audit = semantic_audit_dict(judgement)
    reasons = list(audit["reasons"])
    for r in extra_reasons:
        if r in CHARTER_REASONS and r not in reasons:
            reasons.append(r)
    o = judgement.outcome
    entry_id = log(
        intent={
            "action": AUDIT_ACTION,
            "judged_entry_id": judged_entry_id,
            "text_sha256": o.text_sha256,
        },
        decision=AUDIT_DECISION_LABEL,
        result=f"bio_semantic:{final_decision}",
        verification_score=0.0 if o.fail_reason else 1.0,
        risk_signal=float(o.p_max) if o.p_max is not None else 0.0,
        anomaly_signal=0.0,
        policy_reasons=reasons,
        metadata={
            "judged_entry_id": judged_entry_id,
            "final_decision": final_decision,
            "final_error_code": final_error_code,
            "stack_decision": stack_decision,
            "structural_decision": judgement.structural_policy.decision,
            "structural_error_code": judgement.structural_policy.error_code,
            "policy_decision": judgement.policy.decision,
            "policy_error_code": judgement.policy.error_code,
            "voucher_presented": bool(voucher_presented),
            "voucher_honored": bool(voucher_honored),
            "bio_semantic": audit,
        },
    )
    return str(entry_id)


# --------------------------------------------------------------------------
# The bio pipeline (single path for GovernedBio + sidecar channel=bio)
# --------------------------------------------------------------------------


@dataclass
class BioGovernResult:
    decision: str
    bio_reasons: List[str]
    error_code: Optional[str]
    voucher_honored: bool
    review_enqueued: bool
    env: Dict[str, Any]
    judgement: BioJudgement
    judgement_entry_id: Optional[str]
    semantic: Dict[str, Any] = field(default_factory=dict)

    @property
    def policy(self) -> BioPolicyResult:
        return self.judgement.policy


async def govern_bio_request(
    stack: Any,
    intent: Dict[str, Any],
    token: str,
    request: BioRequest,
    *,
    approval_voucher: Optional[str] = None,
    judge: BioSemanticJudge = DEFAULT_JUDGE,
) -> BioGovernResult:
    """Run the bio pipeline: judge, then govern, voucher honor, audit, enqueue.

    The order is fixed: the semantic judge runs before ``stack.govern``, so
    before any voucher is checked or honored. A judged BLOCK withholds the
    voucher from the stack. The judge runs in a worker thread so that a slow
    model judge (bounded by ``config.timeout_s``) does not block the event loop.
    """
    judgement = await asyncio.to_thread(judge_bio_request, request, judge)
    policy = judgement.policy
    extra: List[str] = []

    voucher = approval_voucher if isinstance(approval_voucher, str) and approval_voucher else None
    if voucher and policy.decision == "BLOCK":
        voucher = None
        extra.append(REASON_VOUCHER_WITHHELD)

    govern_opts: Dict[str, Any] = {}
    if voucher:
        govern_opts["approval_voucher"] = voucher
    env = await stack.govern(intent, token, **govern_opts)
    stack_decision = str(env.get("decision", "BLOCK"))

    decision, bio_reasons, bio_code = tighten_decision(stack_decision, policy)
    decision, bio_reasons, bio_code, voucher_honored = apply_bio_voucher_honor(
        approval_voucher=voucher,
        stack_decision=stack_decision,
        policy=policy,
        env_reasons=env.get("reasons"),
        decision=decision,
        bio_reasons=bio_reasons,
        bio_code=bio_code,
    )
    if policy.decision == "BLOCK" and decision != "BLOCK":  # defense in depth
        decision, bio_code, voucher_honored = "BLOCK", policy.error_code, False

    entry_id = env.get("entry_id")
    engine = getattr(stack, "engine", None)
    judgement_entry_id: Optional[str] = None
    try:
        judgement_entry_id = record_judgement(
            engine,
            judged_entry_id=entry_id,
            judgement=judgement,
            final_decision=decision,
            final_error_code=bio_code or env.get("error_code"),
            stack_decision=stack_decision,
            voucher_presented=voucher is not None,
            voucher_honored=voucher_honored,
            extra_reasons=tuple(extra),
        )
    except Exception:
        extra.append(REASON_AUDIT_WRITE_FAILED)
        if decision == "ALLOW":
            decision, bio_code, voucher_honored = "REVIEW", GOV_BIO_SEMANTIC_REVIEW, False

    for r in extra:
        if r not in bio_reasons:
            bio_reasons.append(r)

    queued = False
    if decision == "REVIEW" and entry_id:
        queued = enqueue_bio_overlay_review(engine, entry_id)

    semantic = semantic_audit_dict(judgement)
    semantic["audit_entry_id"] = judgement_entry_id
    return BioGovernResult(
        decision=decision,
        bio_reasons=list(bio_reasons),
        error_code=bio_code,
        voucher_honored=bool(voucher_honored),
        review_enqueued=queued,
        env=env,
        judgement=judgement,
        judgement_entry_id=judgement_entry_id,
        semantic=semantic,
    )


__all__ = [
    "AUDIT_ACTION",
    "AUDIT_DECISION_LABEL",
    "BioGovernResult",
    "BioJudgement",
    "BioRequest",
    "BioSemanticJudge",
    "CHARTER_REASONS",
    "DEFAULT_JUDGE",
    "FAIL_CATEGORIES",
    "STUB_SCORER_ID",
    "SemanticClassifier",
    "StubScorerError",
    "charter_reasons",
    "fail_reason_token",
    "govern_bio_request",
    "judge_bio_request",
    "merge_judgement",
    "record_judgement",
    "run_semantic_judge",
    "semantic_audit_dict",
    "snapshot_classifier_output",
    "structural_from_policy",
    "stub_heuristic_classifier",
]
