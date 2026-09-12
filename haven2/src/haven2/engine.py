"""Haven2 empirical engine backbone — shared dynamical core."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence


from haven2.energy import EnergyState, t_reset
from haven2.evte import EVTEWeights, score_c_t
from haven2.realms import Realm, TargetRealmFn
from haven2.transistor import TransistorLatch

# Numeric codes for governed_delta (Realm is a str Enum; .value is not float).
_REALM_ORDINAL: dict[Realm, float] = {
    Realm.CALM: 0.0,
    Realm.NORMAL: 1.0,
    Realm.DEFENSIVE: 2.0,
}
from haven2.zeta import (
    DEFAULT_SIGMA,
    energy_zeta,
    engine_zeta,
    master_zeta,
    realm_switch_zeta,
)


@dataclass
class StepRecord:
    t: int
    e: float
    c: float
    p_hat: float
    e_star: float
    realm: Realm
    open: bool
    switched: bool
    v: float
    c_score: float
    delta: float


@dataclass
class Haven2Engine:
    """
    Shared dynamical backbone:
      energy recurrence → deviation Ṕ → transistor latch → realm + EVTE score.
    """

    rho: float = 0.9
    epsilon_switch: float = 0.05
    e0: float = 0.0
    e_star: float | None = None
    initial_realm: Realm = Realm.NORMAL
    weights: EVTEWeights = field(default_factory=EVTEWeights)
    vol_low: float = 0.01
    vol_high: float = 0.03
    target_fn: TargetRealmFn | None = None

    energy: EnergyState = field(init=False)
    latch: TransistorLatch = field(init=False)
    history: list[StepRecord] = field(init=False, default_factory=list)
    c_scores: list[float] = field(init=False, default_factory=list)
    history_delta: list[float] = field(init=False, default_factory=list)
    _equity: float = field(init=False, default=1.0)
    _equity_peak: float = field(init=False, default=1.0)
    _steps_since_switch: int = field(init=False, default=10**9)
    _last_forecast: float | None = field(init=False, default=None)

    def __post_init__(self) -> None:
        self.energy = EnergyState(rho=self.rho, e0=self.e0, e_star=self.e_star)
        self.latch = TransistorLatch(
            epsilon_switch=self.epsilon_switch,
            realm=self.initial_realm,
        )
        self.history = []
        self.c_scores = []
        self.history_delta = []
        self._equity = 1.0
        self._equity_peak = 1.0
        self._steps_since_switch = 10**9
        self._last_forecast = None

    @property
    def realm(self) -> Realm:
        return self.latch.realm

    @property
    def p_hat(self) -> float:
        return self.energy.p_hat

    def reset_time(self, epsilon: float | None = None) -> int:
        eps = self.epsilon_switch if epsilon is None else epsilon
        return t_reset(eps, self.energy.e0, self.energy.equilibrium, self.rho)

    def governed_delta(
        self,
        p_hat: float,
        realm: Realm,
        c_score: float,
        v_t: float,
        abs_err: float,
    ) -> float:
        """Tunable governed functional over residual / realm / C / vol / forecast err."""
        w = self.weights
        realm_code = _REALM_ORDINAL.get(realm, 1.0)
        return float(
            w.delta_p * float(p_hat)
            + w.delta_r * realm_code
            + w.delta_c * float(c_score)
            + w.delta_v * float(v_t)
            + w.delta_f * float(abs_err)
        )

    def step(
        self,
        c_t: float,
        v_t: float,
        *,
        equity_return: float = 0.0,
        forecast: float | None = None,
    ) -> StepRecord:
        """
        One backbone step.

        c_t : engine-specific drive (e.g. volatility shock / anomaly)
        v_t : volatility observation for TargetRealm
        """
        self.energy.step(c_t)
        p_hat = self.energy.p_hat
        # Leave latch time alone — TransistorLatch._t is 0-based step index
        # (matches history_p_hat / c_scores list indices for zeta).
        realm = self.latch.step(
            p_hat,
            v_t,
            target_fn=self.target_fn,
        )
        switched = self.latch.history_switched[-1]
        open_ = self.latch.history_open[-1]

        self._equity *= 1.0 + float(equity_return)
        self._equity_peak = max(self._equity_peak, self._equity)

        if self._last_forecast is None:
            abs_err = 0.0
        else:
            abs_err = abs(float(v_t) - self._last_forecast)
        self._last_forecast = float(forecast) if forecast is not None else float(v_t)

        c_score = score_c_t(
            realm=realm,
            v_t=v_t,
            switched=switched,
            open_=open_,
            steps_since_switch=self._steps_since_switch,
            equity=self._equity,
            equity_peak=self._equity_peak,
            forecast_abs_err=abs_err,
            p_hat=p_hat,
            epsilon_switch=self.epsilon_switch,
            weights=self.weights,
            vol_low=self.vol_low,
            vol_high=self.vol_high,
        )
        if switched:
            self._steps_since_switch = 0
        else:
            self._steps_since_switch += 1

        delta = self.governed_delta(
            p_hat=p_hat,
            realm=realm,
            c_score=c_score,
            v_t=v_t,
            abs_err=abs_err,
        )
        rec = StepRecord(
            t=self.energy.t,
            e=self.energy.e,
            c=float(c_t),
            p_hat=p_hat,
            e_star=self.energy.equilibrium,
            realm=realm,
            open=open_,
            switched=switched,
            v=float(v_t),
            c_score=c_score,
            delta=delta,
        )
        self.history.append(rec)
        self.c_scores.append(c_score)
        self.history_delta.append(delta)
        return rec

    def run(
        self,
        drives: Sequence[float],
        vols: Sequence[float],
        *,
        equity_returns: Sequence[float] | None = None,
        forecasts: Sequence[float] | None = None,
    ) -> list[StepRecord]:
        n = len(drives)
        if len(vols) != n:
            raise ValueError("drives and vols must have same length")
        eq = list(equity_returns) if equity_returns is not None else [0.0] * n
        fc: list[float | None]
        if forecasts is None:
            fc = [None] * n
        else:
            fc = list(forecasts)
        out: list[StepRecord] = []
        for i in range(n):
            out.append(
                self.step(
                    float(drives[i]),
                    float(vols[i]),
                    equity_return=float(eq[i]),
                    forecast=None if fc[i] is None else float(fc[i]),
                )
            )
        return out

    def zeta_summaries(self, sigma: float = DEFAULT_SIGMA) -> dict[str, float]:
        p_hat = self.energy.history_p_hat
        return {
            "Z_E": energy_zeta(p_hat, sigma),
            "Z_R": realm_switch_zeta(self.latch.switch_times, sigma),
            "Z_C": engine_zeta(self.c_scores, sigma),
            "Z_D": engine_zeta(self.history_delta, sigma),
            "Z_H": master_zeta(p_hat, self.latch.switch_times, self.c_scores, sigma),
            "sigma": float(sigma),
        }


def run_shock_regime_trace(
    rho: float,
    *,
    epsilon_switch: float = 0.05,
    e0: float = 0.0,
    mean_c: float = 0.02,
    shock_c: float = 1.0,
    shock_t: int = 5,
    regime_change_t: int = 15,
    horizon: int = 80,
    vol_low_level: float = 0.008,
    vol_high_level: float = 0.05,
    initial_realm: Realm = Realm.CALM,
) -> tuple[list[float], list[int], list[float], Haven2Engine]:
    """
    Fixed shock + later regime-change scenario used by sim and ρ* search.

    Quiet drive, large shock at shock_t, then vol regime jumps to HIGH at
    regime_change_t while residual may still be large — latch should block —
    then after decay the switch to DEFENSIVE is allowed.
    """
    e_star = mean_c / (1.0 - rho)
    eng = Haven2Engine(
        rho=rho,
        epsilon_switch=epsilon_switch,
        e0=e0,
        e_star=e_star,
        initial_realm=initial_realm,
    )
    drives = [mean_c] * horizon
    drives[shock_t] = shock_c
    vols = [vol_low_level] * horizon
    for t in range(regime_change_t, horizon):
        vols[t] = vol_high_level
    # mild negative returns during high-vol window for drawdown metric
    rets = [0.001] * horizon
    for t in range(regime_change_t, min(regime_change_t + 10, horizon)):
        rets[t] = -0.01
    eng.run(drives, vols, equity_returns=rets)
    return eng.energy.history_p_hat, eng.latch.switch_times, eng.c_scores, eng
