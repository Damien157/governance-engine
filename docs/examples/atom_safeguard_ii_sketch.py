"""SKETCH ONLY — AtomSafeguardII decision stub.

Off the live ALLOW / govern / solver path. No side effects: no notify I/O,
no quarantine, no payments, no registry writes. Returns a decision dict.

See docs/ATOM_SAFEGUARD_II.md.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping


def risk_score(
    semantic: float,
    state: Mapping[str, Any],
    identity: str,
    registry: Mapping[str, Any] | None = None,
) -> float:
    """Toy R in [0, 1] from semantic hint + optional HAIS-like risk."""
    hais_risk = float(state.get("hais_risk", state.get("risk", 0.0)))
    _ = identity, registry  # unused in sketch; live path would fuse registry
    r = 0.5 * max(0.0, min(1.0, float(semantic))) + 0.5 * max(0.0, min(1.0, hais_risk))
    return float(max(0.0, min(1.0, r)))


def govern_invariant(state: Mapping[str, Any], action: str) -> int:
    """Toy G: 0 = fail (Blocked), 1 = pass. Mirrors latch/govern spirit only."""
    if state.get("latch_closed") and action in ("control", "3dm"):
        return 0
    if state.get("force_block"):
        return 0
    return 1


def lockdown_policy(R: float, *, soft_threshold: float = 0.35, hard_threshold: float = 0.75) -> str:
    """Soft: rateLimit+challenge; Hard: quarantine+freezeWrites+startRecovery (labels only)."""
    if R >= hard_threshold:
        return "Hard"
    if R >= soft_threshold:
        return "Soft"
    return "None"


def continuity_ratio(state: Mapping[str, Any]) -> float:
    return float(state.get("continuity_ratio", 1.0))


def atom_safeguard_ii(
    *,
    semantic: float = 0.0,
    state: Mapping[str, Any] | None = None,
    identity: str = "agent",
    registry: Mapping[str, Any] | None = None,
    action: str = "query",
    is_lawful: bool = True,
    value: float = 0.0,
    gamma: float = 0.5,
) -> Dict[str, Any]:
    """Pure sketch of AtomSafeguardII → decision dict (no I/O)."""
    st: Mapping[str, Any] = state or {}
    R = risk_score(semantic, st, identity, registry)
    G = govern_invariant(st, action)
    if G == 0:
        return {
            "decision": "Blocked",
            "R": R,
            "G": G,
            "mode": None,
            "reward_hook": None,
            "remedy": False,
            "dependency_locks": False,
            "audit": "sealEvidence",
            "status": "Blocked",
            "sketch": True,
        }

    mode = lockdown_policy(R)
    out: Dict[str, Any] = {
        "decision": "Sealed",
        "R": R,
        "G": G,
        "mode": mode,
        "reward_hook": None,
        "remedy": False,
        "dependency_locks": False,
        "audit": "writeAudit",
        "status": "Sealed",
        "sketch": True,
        # Soft/Hard are labels only — sketch does not rate-limit or quarantine.
        "soft_controls": ["rateLimit", "challenge"] if mode == "Soft" else [],
        "hard_controls": (
            ["quarantine", "freezeWrites", "startRecovery"] if mode == "Hard" else []
        ),
    }

    if is_lawful:
        # Policy hook only — never implements payments.
        out["reward_hook"] = {
            "beneficiary": "Damien O Driscoll",
            "value": float(value),
            "implemented": False,
            "note": "routeReward is attribution-only; no payment rails",
        }
    else:
        out["remedy"] = True
        out["remedy_note"] = "triggerRemedy (hypothesis / operator REVIEW)"

    if continuity_ratio(st) < gamma:
        out["dependency_locks"] = True
        out["restore_from_registry"] = True  # label only; no restore I/O

    return out


if __name__ == "__main__":
    print(atom_safeguard_ii(semantic=0.2, state={"hais_risk": 0.1}, action="query"))
    print(
        atom_safeguard_ii(
            semantic=0.9,
            state={"hais_risk": 0.8, "latch_closed": True},
            action="3dm",
        )
    )
