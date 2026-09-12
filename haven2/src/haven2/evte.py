"""
EVTE composite score C_t for a toy volatility engine.

Weights from Damien's notes (behavioural score, not a consciousness claim):
  w_A  = 0.3  correct realm vs volatility
  w_SF = 0.2  healthy switching pattern
  w_DD = 0.2  drawdown discipline
  w_FE = 0.2  forecast calibration
  w_E  = 0.1  energy deviation sweet spot
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from haven2.realms import Realm, VolRegime, classify_volatility, DEFAULT_TARGET_REALM


DEFAULT_WEIGHTS = {
    "A": 0.3,
    "SF": 0.2,
    "DD": 0.2,
    "FE": 0.2,
    "E": 0.1,
}


@dataclass
class EVTEWeights:
    w_A: float = 0.3
    w_SF: float = 0.2
    w_DD: float = 0.2
    w_FE: float = 0.2
    w_E: float = 0.1
    # Governed delta (tuneable) — linear mix over residual / realm / C / vol / forecast err
    delta_p: float = 0.25
    delta_r: float = 0.25
    delta_c: float = 0.25
    delta_v: float = 0.15
    delta_f: float = 0.10

    def as_dict(self) -> dict[str, float]:
        return {
            "A": self.w_A,
            "SF": self.w_SF,
            "DD": self.w_DD,
            "FE": self.w_FE,
            "E": self.w_E,
            "delta_p": self.delta_p,
            "delta_r": self.delta_r,
            "delta_c": self.delta_c,
            "delta_v": self.delta_v,
            "delta_f": self.delta_f,
        }


def clip01(x: float) -> float:
    return float(np.clip(x, 0.0, 1.0))


def metric_alignment(realm: Realm, vol_regime: VolRegime) -> float:
    """1 if current realm matches TargetRealm for observed vol regime."""
    return 1.0 if DEFAULT_TARGET_REALM[vol_regime] == realm else 0.0


def metric_switch_health(
    switched: bool,
    open_: bool,
    steps_since_switch: int,
    *,
    min_dwell: int = 5,
) -> float:
    """
    Healthy switching: reward open+switch when dwell was long enough;
    reward closed (no thrash); penalize rapid re-switch.
    """
    if not open_:
        return 1.0 if not switched else 0.0
    if switched:
        return 1.0 if steps_since_switch >= min_dwell else 0.3
    return 0.8  # open but held (target already matched)


def metric_drawdown(equity_peak: float, equity: float) -> float:
    """Drawdown discipline in [0,1]: 1 = no drawdown."""
    if equity_peak <= 0:
        return 1.0
    dd = max(0.0, (equity_peak - equity) / equity_peak)
    return clip01(1.0 - dd)


def metric_forecast_error(abs_err: float, *, scale: float = 0.05) -> float:
    """Forecast calibration: soft decay of absolute error."""
    return clip01(float(np.exp(-abs_err / max(scale, 1e-12))))


def metric_energy_sweet_spot(p_hat: float, epsilon_switch: float) -> float:
    """
    Energy deviation sweet spot: near 0 is best; still score well inside
    the latch band, decay outside.
    """
    mag = abs(float(p_hat))
    band = max(float(epsilon_switch), 1e-12)
    if mag < band:
        return 1.0 - 0.5 * (mag / band)
    return clip01(np.exp(-(mag - band) / band))


def score_c_t(
    *,
    realm: Realm,
    v_t: float,
    switched: bool,
    open_: bool,
    steps_since_switch: int,
    equity: float,
    equity_peak: float,
    forecast_abs_err: float,
    p_hat: float,
    epsilon_switch: float,
    weights: EVTEWeights | None = None,
    vol_low: float = 0.01,
    vol_high: float = 0.03,
) -> float:
    """C_t = clip(sum w_i metric_i, 0, 1)."""
    w = weights or EVTEWeights()
    regime = classify_volatility(v_t, low=vol_low, high=vol_high)
    m_A = metric_alignment(realm, regime)
    m_SF = metric_switch_health(switched, open_, steps_since_switch)
    m_DD = metric_drawdown(equity_peak, equity)
    m_FE = metric_forecast_error(forecast_abs_err)
    m_E = metric_energy_sweet_spot(p_hat, epsilon_switch)
    raw = (
        w.w_A * m_A
        + w.w_SF * m_SF
        + w.w_DD * m_DD
        + w.w_FE * m_FE
        + w.w_E * m_E
    )
    return clip01(raw)
