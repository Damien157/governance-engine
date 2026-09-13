"""Audit-only governance score projection from live govern/sidecar envelopes.

Does **not** feed ``govern()``, HAIS, Haven2, or ``GOV_*`` decisions.
Readable shadow for eval / dashboards / hosted_check_eval.
"""

from __future__ import annotations

import math
from typing import Any, Dict


def _norm_p_hat(p_hat: Any) -> float:
    """Euclidean norm when p_hat is scalar / sequence / mapping; else 0."""
    if p_hat is None:
        return 0.0
    if isinstance(p_hat, (int, float)):
        return abs(float(p_hat))
    if isinstance(p_hat, (list, tuple)):
        return math.sqrt(sum(float(x) ** 2 for x in p_hat))
    if isinstance(p_hat, dict):
        return math.sqrt(sum(float(v) ** 2 for v in p_hat.values()))
    return 0.0


def project_governance_score(envelope: Dict[str, Any]) -> Dict[str, float]:
    """
    Project (risk, stability, governance) from a live GOV envelope.

    Expected keys (when present):
      decision, error_code,
      hais: {cap, risk, instability, ...},
      haven2: {realm, open, p_hat, ...}
    """
    decision = envelope.get("decision")
    error_code = envelope.get("error_code")

    hais = envelope.get("hais") or {}
    haven2 = envelope.get("haven2") or {}

    cap = float(hais.get("cap", 1.0) if hais.get("cap") is not None else 1.0)
    risk_metric = float(hais.get("risk", 0.0) if hais.get("risk") is not None else 0.0)
    instability = float(
        hais.get("instability", 0.0) if hais.get("instability") is not None else 0.0
    )

    realm = str(haven2.get("realm") or "normal")
    open_latch = bool(haven2.get("open", True)) if haven2.get("open") is not None else True
    p_norm = _norm_p_hat(haven2.get("p_hat"))

    base_risk = risk_metric
    # Align with GovernedStack.CAP_THROTTLE = 0.25
    if cap < 0.25:
        base_risk += (0.25 - cap) * 2.0
    inst_component = 1.0 - math.exp(-instability)
    base_risk += 0.3 * inst_component

    if decision == "BLOCK":
        base_risk = max(base_risk, 0.85)
    elif decision == "REVIEW":
        base_risk = max(base_risk, 0.65)

    if error_code in ("GOV_POLICY_BLOCK", "GOV_HAIS_CAP", "GOV_LATCH_CLOSED"):
        base_risk = max(base_risk, 0.9)
    elif error_code == "GOV_AUTH_FAILED":
        base_risk = max(base_risk, 0.7)
    elif error_code == "GOV_INTERNAL":
        base_risk = max(base_risk, 0.8)

    risk = max(0.0, min(1.0, base_risk))

    base_stability = math.exp(-2.0 * instability)
    if not open_latch:
        base_stability *= 0.4
    if realm == "defensive":
        base_stability *= 0.6
    elif realm == "calm":
        base_stability *= 1.1
    base_stability *= math.exp(-p_norm)
    stability = max(0.0, min(1.0, base_stability))

    gov_base = cap * (1.0 - 0.5 * risk)
    if decision == "ALLOW":
        pass
    elif decision == "REVIEW":
        gov_base *= 0.7
    elif decision == "BLOCK":
        gov_base *= 0.5

    if error_code in ("GOV_POLICY_BLOCK", "GOV_POLICY_REVIEW"):
        gov_base += 0.15
    if error_code == "GOV_LATCH_CLOSED":
        gov_base += 0.2
    if error_code in ("GOV_AUTH_FAILED", "GOV_INTERNAL"):
        gov_base -= 0.2

    governance = max(0.0, min(1.0, gov_base))

    return {
        "risk": round(risk, 3),
        "stability": round(stability, 3),
        "governance": round(governance, 3),
    }


__all__ = ["project_governance_score", "_norm_p_hat"]
