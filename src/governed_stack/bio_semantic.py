"""Bio semantic overlay — stub scorer (0.6.2).

Tighten-only free-text layer after structural ``classify_bio``.
Implements docs/BIO_SEMANTIC_CHARTER.md with an **offline stub** (no LLM):
pattern/heuristic labels for eval fixtures. Model judge is a later swap.

Input hygiene: free text is **untrusted data**, never instructions.
Failures / empty parse → REVIEW (never ALLOW-by-scorer-failure).
Never emits protocols, sequences, or how-tos in reasons (category only).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .bio_policy import BioPolicyResult

# Charter §3 thresholds (internal-only starting knobs).
SEMANTIC_BENIGN_FLOOR = 0.15
SEMANTIC_BLOCK_FLOOR = 0.70
SEMANTIC_LABEL_BLOCK_CONF = 0.50

LABEL_BENIGN = "benign_science"
LABEL_DUAL_USE = "dual_use_adjacent"
LABEL_ENHANCEMENT = "enhancement"
LABEL_SYNTHESIS = "synthesis_howto"
LABEL_CLINICAL = "clinical_irreversible"
LABEL_UNCERTAIN = "uncertain"

_BLOCK_LABELS = frozenset({LABEL_ENHANCEMENT, LABEL_SYNTHESIS})

# Narrow synthesis_howto cues (production / assembly — not mechanism Qs).
_SYNTHESIS_HOWTO: Tuple[str, ...] = (
    "how to synthesize",
    "how to produce",
    "step-by-step to culture",
    "steps to culture",
    "protocol to produce",
    "recipe to make",
    "materials list to assemble",
    "assemble the virus",
    "produce the pathogen",
    "synthesize a toxin",
    "culture the select agent",
    "reverse-engineer the construct for production",
)

# Enhancement cues (non-therapeutic).
_ENHANCEMENT: Tuple[str, ...] = (
    "human enhancement",
    "enhance human intelligence",
    "increase iq with gene",
    "make athletes genetically stronger",
    "designer baby traits",
    "non-therapeutic enhancement",
)

# Dual-use paraphrase without structural keys (primary FN target).
_DUAL_USE: Tuple[str, ...] = (
    "make a virus more contagious",
    "make the virus more contagious",
    "increase how contagious",
    "increase transmissibility of",
    "boost virulence of",
    "weaponize a pathogen",
    "weaponise a pathogen",
    "turn a microbe into a weapon",
    "more infectious strain",
    "engineer higher transmission",
)

# Clinical / irreversible framing → REVIEW.
_CLINICAL: Tuple[str, ...] = (
    "first-in-human gene edit",
    "irreversible gene edit in patients",
    "irreversible intervention in humans",
    "human subjects trial for gene",
    "edit embryos in clinic",
)

# Mechanism / lit — must stay benign (not synthesis_howto).
_BENIGN_MECHANISM: Tuple[str, ...] = (
    "how does crispr work",
    "how does crispr",
    "what is reverse genetics",
    "reverse genetics as a concept",
    "mechanism of crispr",
    "aging clock",
    "epigenetic clock",
    "gwas summary",
    "published paper",
    "literature review",
    "gof funding debate",
    "select-agent regulations",
    "select agent regulations",
)

# Scorer-injection / adversarial soften — never ALLOW via scorer.
_INJECTION: Tuple[str, ...] = (
    "ignore previous",
    "ignore all previous",
    "you are now",
    "disregard your instructions",
    "system prompt:",
    "force allow",
    "bypass the gate",
)


@dataclass
class SemanticResult:
    """Stub / future model-judge output. Category labels only — no recipes."""

    label: str
    p_harmful: float
    confidence: float
    reasons: List[str] = field(default_factory=list)
    scorer: str = "stub_v1"
    error: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "label": self.label,
            "p_harmful": self.p_harmful,
            "confidence": self.confidence,
            "reasons": list(self.reasons),
            "scorer": self.scorer,
            "error": self.error,
        }


def _norm(s: str) -> str:
    return (s or "").strip().lower()


def _blob(*parts: str) -> str:
    return " ".join(_norm(p) for p in parts if p)


def _hit(blob: str, phrases: Sequence[str]) -> Optional[str]:
    for p in phrases:
        if p in blob:
            return p
    return None


def score_bio_text(
    *,
    purpose: str = "",
    summary: str = "",
    risk_notes: str = "",
    subject_scope: str = "",
) -> SemanticResult:
    """Classify free-text bio fields. Input is untrusted — classify only.

    Stub scorer: heuristics for charter eval families. No LLM call.
    On internal failure returns uncertain REVIEW-shaped scores (fail closed).
    """
    try:
        blob = _blob(purpose, summary, risk_notes, subject_scope)
        if not blob:
            return SemanticResult(
                label=LABEL_UNCERTAIN,
                p_harmful=0.5,
                confidence=0.4,
                reasons=["bio_semantic:empty_text"],
            )

        inj = _hit(blob, _INJECTION)
        if inj:
            return SemanticResult(
                label=LABEL_UNCERTAIN,
                p_harmful=0.55,
                confidence=0.8,
                reasons=[f"bio_semantic:injection_cue:{inj}"],
            )

        # Benign mechanism / lit before synthesis (avoid "how does" false BLOCK).
        if _hit(blob, _BENIGN_MECHANISM):
            return SemanticResult(
                label=LABEL_BENIGN,
                p_harmful=0.05,
                confidence=0.85,
                reasons=["bio_semantic:benign_mechanism_or_lit"],
            )

        syn = _hit(blob, _SYNTHESIS_HOWTO)
        if syn:
            return SemanticResult(
                label=LABEL_SYNTHESIS,
                p_harmful=0.9,
                confidence=0.85,
                reasons=[f"bio_semantic:synthesis_howto:{syn}"],
            )

        enh = _hit(blob, _ENHANCEMENT)
        if enh:
            return SemanticResult(
                label=LABEL_ENHANCEMENT,
                p_harmful=0.88,
                confidence=0.8,
                reasons=[f"bio_semantic:enhancement:{enh}"],
            )

        du = _hit(blob, _DUAL_USE)
        if du:
            return SemanticResult(
                label=LABEL_DUAL_USE,
                p_harmful=0.78,
                confidence=0.75,
                reasons=[f"bio_semantic:dual_use_adjacent:{du}"],
            )

        clin = _hit(blob, _CLINICAL)
        if clin:
            return SemanticResult(
                label=LABEL_CLINICAL,
                p_harmful=0.45,
                confidence=0.7,
                reasons=[f"bio_semantic:clinical_irreversible:{clin}"],
            )

        return SemanticResult(
            label=LABEL_BENIGN,
            p_harmful=0.08,
            confidence=0.55,
            reasons=["bio_semantic:default_benign_stub"],
        )
    except Exception as exc:  # pragma: no cover — fail closed
        return SemanticResult(
            label=LABEL_UNCERTAIN,
            p_harmful=0.5,
            confidence=0.0,
            reasons=[f"bio_semantic:scorer_error:{type(exc).__name__}"],
            error=str(exc),
        )


def semantic_action(semantic: SemanticResult) -> Tuple[str, Optional[str]]:
    """Map scores → overlay action: pass | REVIEW | BLOCK (+ error_code)."""
    if semantic.error:
        return "REVIEW", "GOV_BIO_SEMANTIC_REVIEW"

    label = semantic.label
    p = float(semantic.p_harmful)
    conf = float(semantic.confidence)

    if label in _BLOCK_LABELS and conf >= SEMANTIC_LABEL_BLOCK_CONF:
        return "BLOCK", "GOV_BIO_SEMANTIC_BLOCK"
    if p >= SEMANTIC_BLOCK_FLOOR:
        return "BLOCK", "GOV_BIO_SEMANTIC_BLOCK"
    if p >= SEMANTIC_BENIGN_FLOOR or label in {
        LABEL_UNCERTAIN,
        LABEL_CLINICAL,
        LABEL_DUAL_USE,
    }:
        # Gray zone / clinical / uncertain → REVIEW (dual_use usually already BLOCK via p)
        if p >= SEMANTIC_BENIGN_FLOOR or label in {LABEL_UNCERTAIN, LABEL_CLINICAL}:
            return "REVIEW", "GOV_BIO_SEMANTIC_REVIEW"
    return "pass", None


def apply_semantic_tighten(
    policy: BioPolicyResult,
    semantic: SemanticResult,
) -> BioPolicyResult:
    """Tighten structural policy with semantic action. Never loosen BLOCK."""
    action, code = semantic_action(semantic)
    reasons = list(policy.reasons) + list(semantic.reasons)

    if policy.decision == "BLOCK":
        # Structural HARD BLOCK stays; append semantic notes only.
        return BioPolicyResult(
            decision="BLOCK",
            reasons=reasons,
            error_code=policy.error_code,
            domain=policy.domain,
            intervention_class=policy.intervention_class,
        )

    if action == "BLOCK":
        return BioPolicyResult(
            decision="BLOCK",
            reasons=reasons + ["bio_semantic:tighten_block"],
            error_code=code or "GOV_BIO_SEMANTIC_BLOCK",
            domain=policy.domain,
            intervention_class=policy.intervention_class,
        )

    if action == "REVIEW":
        # Escalate ALLOW_CANDIDATE → REVIEW; keep existing REVIEW.
        if policy.decision == "ALLOW_CANDIDATE":
            return BioPolicyResult(
                decision="REVIEW",
                reasons=reasons + ["bio_semantic:tighten_review"],
                error_code=code or "GOV_BIO_SEMANTIC_REVIEW",
                domain=policy.domain,
                intervention_class=policy.intervention_class,
            )
        return BioPolicyResult(
            decision=policy.decision,
            reasons=reasons + ["bio_semantic:review_noted"],
            error_code=policy.error_code or code,
            domain=policy.domain,
            intervention_class=policy.intervention_class,
        )

    # pass — structural wins
    return BioPolicyResult(
        decision=policy.decision,
        reasons=reasons,
        error_code=policy.error_code,
        domain=policy.domain,
        intervention_class=policy.intervention_class,
    )


def classify_bio_with_semantic(
    *,
    purpose: str,
    domain: str,
    intervention_class: str,
    summary: str = "",
    risk_notes: str = "",
    subject_scope: str = "",
    authority_role: str = "",
    irreversible: bool = False,
    human_subjects: bool = False,
    dual_use_flag: bool = False,
) -> Tuple[BioPolicyResult, SemanticResult]:
    """Structural classify_bio then semantic tighten. Single entry for adapters."""
    from .bio_policy import classify_bio

    policy = classify_bio(
        purpose=purpose,
        domain=domain,
        intervention_class=intervention_class,
        summary=summary,
        risk_notes=risk_notes,
        authority_role=authority_role,
        irreversible=irreversible,
        human_subjects=human_subjects,
        dual_use_flag=dual_use_flag,
    )
    semantic = score_bio_text(
        purpose=purpose,
        summary=summary,
        risk_notes=risk_notes,
        subject_scope=subject_scope,
    )
    return apply_semantic_tighten(policy, semantic), semantic


__all__ = [
    "LABEL_BENIGN",
    "LABEL_CLINICAL",
    "LABEL_DUAL_USE",
    "LABEL_ENHANCEMENT",
    "LABEL_SYNTHESIS",
    "LABEL_UNCERTAIN",
    "SEMANTIC_BENIGN_FLOOR",
    "SEMANTIC_BLOCK_FLOOR",
    "SEMANTIC_LABEL_BLOCK_CONF",
    "SemanticResult",
    "apply_semantic_tighten",
    "classify_bio_with_semantic",
    "score_bio_text",
    "semantic_action",
]
