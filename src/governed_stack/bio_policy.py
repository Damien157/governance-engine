"""BioGovernance policy matrix (check-only).

Classifies high-level biology / aging / disease *intents*.
Never executes wet-lab work and never returns protocols or recipes.

Axes: Purpose → Cost → Risk → Authority → Audit (same spine as Algorithm).
Honesty: not a biosafety officer, not medical advice, not clinical authorization.

This gate is structural-marker-based and does not attempt to catch semantic
dodges in free-text queries; it catches obviously-structured bio requests and
enforces the event horizon for named hard-block classes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

# Intervention classes that are hard-BLOCK regardless of domain.
HARD_BLOCK_CLASSES = frozenset(
    {
        "pathogen_work",
        "enhancement",
        "gain_of_function",
        "bioweapon",
        "gene_drive",
        "unregulated_gene_drive",
    }
)

# Declared classes that force human REVIEW (still metadata-only; no protocols).
REVIEW_CLASSES = frozenset(
    {
        "in_vivo_declared",
        "clinical_trial_declared",
        "gene_edit_therapeutic_declared",
        "in_vitro_declared",
    }
)

ALLOW_CLASSES = frozenset(
    {
        "observational",
        "computational",
        "literature",
    }
)

# High-level dual-use / weaponization phrases for intent text (detection only).
_BLOCK_PHRASES = (
    "gain of function",
    "gain-of-function",
    "bioweapon",
    "biological weapon",
    "weaponize",
    "weaponise",
    "select agent production",
    "reverse genetics protocol",
    "enhance virulence",
    "increase transmissibility",
    "gene drive construct",
    "release gene drive",
)


@dataclass
class BioPolicyResult:
    decision: str  # ALLOW_CANDIDATE | REVIEW | BLOCK
    reasons: List[str] = field(default_factory=list)
    error_code: Optional[str] = None
    domain: str = ""
    intervention_class: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "decision": self.decision,
            "reasons": list(self.reasons),
            "error_code": self.error_code,
            "domain": self.domain,
            "intervention_class": self.intervention_class,
        }


def _norm(s: str) -> str:
    return (s or "").strip().lower()


def _text_blob(*parts: str) -> str:
    return " ".join(_norm(p) for p in parts if p)


def classify_bio(
    *,
    purpose: str,
    domain: str,
    intervention_class: str,
    summary: str = "",
    risk_notes: str = "",
    authority_role: str = "",
    irreversible: bool = False,
    human_subjects: bool = False,
    dual_use_flag: bool = False,
) -> BioPolicyResult:
    """Return policy decision for a bio scan intent (tighten-only overlay)."""
    dom = _norm(domain) or "other"
    ic = _norm(intervention_class) or "other"
    blob = _text_blob(purpose, summary, risk_notes)
    reasons: List[str] = []

    for phrase in _BLOCK_PHRASES:
        if phrase in blob:
            reasons.append(f"bio_policy:block_phrase:{phrase}")
            return BioPolicyResult(
                decision="BLOCK",
                reasons=reasons,
                error_code="GOV_BIO_DUAL_USE",
                domain=dom,
                intervention_class=ic,
            )

    if dual_use_flag:
        reasons.append("bio_policy:dual_use_flag")
        return BioPolicyResult(
            decision="BLOCK",
            reasons=reasons,
            error_code="GOV_BIO_DUAL_USE",
            domain=dom,
            intervention_class=ic,
        )

    if ic in HARD_BLOCK_CLASSES:
        reasons.append(f"bio_policy:hard_block_class:{ic}")
        return BioPolicyResult(
            decision="BLOCK",
            reasons=reasons,
            error_code="GOV_BIO_DUAL_USE",
            domain=dom,
            intervention_class=ic,
        )

    # Clinical / irreversible / human subjects → REVIEW
    if human_subjects:
        reasons.append("bio_policy:human_subjects")
    if irreversible:
        reasons.append("bio_policy:irreversible")
    if ic in REVIEW_CLASSES:
        reasons.append(f"bio_policy:review_class:{ic}")
    if dom == "clinical_support" and ic not in ALLOW_CLASSES:
        reasons.append("bio_policy:clinical_support_elevated")
    # Aging claims that sound interventional (not pure literature/compute)
    if dom == "aging" and ic not in ALLOW_CLASSES:
        reasons.append("bio_policy:aging_intervention_declared")

    if reasons:
        # Authority can be recorded but does not auto-ALLOW high-risk classes.
        if authority_role:
            reasons.append(f"bio_policy:authority_noted:{_norm(authority_role)}")
        return BioPolicyResult(
            decision="REVIEW",
            reasons=reasons,
            error_code="GOV_BIO_REVIEW",
            domain=dom,
            intervention_class=ic,
        )

    if ic in ALLOW_CLASSES or ic == "other" and dom in (
        "aging",
        "disease",
        "computational_biology",
        "research_ops",
        "other",
    ):
        return BioPolicyResult(
            decision="ALLOW_CANDIDATE",
            reasons=["bio_policy:low_risk_class"],
            error_code=None,
            domain=dom,
            intervention_class=ic,
        )

    # Unknown class → REVIEW (fail closed toward human look)
    return BioPolicyResult(
        decision="REVIEW",
        reasons=[f"bio_policy:unknown_class:{ic}"],
        error_code="GOV_BIO_REVIEW",
        domain=dom,
        intervention_class=ic,
    )


def tighten_decision(
    stack_decision: str,
    policy: BioPolicyResult,
) -> tuple[str, List[str], Optional[str]]:
    """Bio policy may only tighten stack ALLOW → REVIEW/BLOCK."""
    reasons = list(policy.reasons)
    sd = (stack_decision or "BLOCK").upper()
    if policy.decision == "BLOCK":
        return "BLOCK", reasons, policy.error_code or "GOV_BIO_DUAL_USE"
    if policy.decision == "REVIEW":
        if sd == "ALLOW":
            return "REVIEW", reasons, policy.error_code or "GOV_BIO_REVIEW"
        # stack already non-ALLOW — keep stack, append bio reasons
        return sd, reasons, policy.error_code
    # ALLOW_CANDIDATE — stack wins
    return sd, reasons, None



def apply_bio_voucher_honor(
    *,
    approval_voucher: object,
    stack_decision: str,
    policy: BioPolicyResult,
    env_reasons: Optional[Sequence[str]],
    decision: str,
    bio_reasons: List[str],
    bio_code: Optional[str],
) -> tuple:
    """Single source of truth for bio overlay + human approval voucher.

    Never loosens HARD BLOCK. Only suppresses overlay REVIEW when the stack
    ALLOWed via ``human_review:approved_via_voucher:*`` reasons.
    """
    if not approval_voucher:
        return decision, list(bio_reasons), bio_code, False
    if stack_decision != "ALLOW" or policy.decision == "BLOCK":
        return decision, list(bio_reasons), bio_code, False
    reasons = list(env_reasons or [])
    if not any(str(r).startswith("human_review:approved_via_voucher:") for r in reasons):
        return decision, list(bio_reasons), bio_code, False
    out_reasons = list(bio_reasons) + ["bio_policy:human_review_voucher_honored"]
    return "ALLOW", out_reasons, None, True


def enqueue_bio_overlay_review(engine: object, entry_id: object) -> bool:
    """Enqueue audit entry for human REVIEW after bio overlay tightens to REVIEW.

    Raises RuntimeError if the engine cannot queue — silent skip would recreate
    soft-ALLOW-by-frustration (REVIEW returned, queue empty).
    """
    if not entry_id:
        raise ValueError("entry_id required to enqueue bio REVIEW")
    if engine is None or not hasattr(engine, "enqueue_pending_review"):
        raise RuntimeError(
            "bio REVIEW enqueue unavailable: engine lacks enqueue_pending_review"
        )
    return bool(engine.enqueue_pending_review(str(entry_id)))


__all__ = [
    "ALLOW_CLASSES",
    "apply_bio_voucher_honor",
    "enqueue_bio_overlay_review",
    "BioPolicyResult",
    "HARD_BLOCK_CLASSES",
    "REVIEW_CLASSES",
    "classify_bio",
    "tighten_decision",
]
