"""Finite behavioural realms and a toy volatility TargetRealm table."""

from __future__ import annotations

from enum import Enum
from typing import Callable


class Realm(str, Enum):
    CALM = "calm"
    NORMAL = "normal"
    DEFENSIVE = "defensive"


class VolRegime(str, Enum):
    LOW = "low"
    MID = "mid"
    HIGH = "high"


# Default toy volatility engine: low/mid/high vol → calm/normal/defensive
DEFAULT_TARGET_REALM: dict[VolRegime, Realm] = {
    VolRegime.LOW: Realm.CALM,
    VolRegime.MID: Realm.NORMAL,
    VolRegime.HIGH: Realm.DEFENSIVE,
}


def classify_volatility(v_t: float, *, low: float = 0.01, high: float = 0.03) -> VolRegime:
    """Map a scalar volatility observation to a coarse regime."""
    if v_t < low:
        return VolRegime.LOW
    if v_t > high:
        return VolRegime.HIGH
    return VolRegime.MID


def target_realm(
    v_t: float,
    *,
    table: dict[VolRegime, Realm] | None = None,
    low: float = 0.01,
    high: float = 0.03,
) -> Realm:
    """TargetRealm(V_t) for the toy volatility engine."""
    tab = table if table is not None else DEFAULT_TARGET_REALM
    return tab[classify_volatility(v_t, low=low, high=high)]


TargetRealmFn = Callable[[float], Realm]
