"""Energy recurrence, equilibrium deviation, and reset-time formula."""

from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class EnergyState:
    """Discrete energy backbone: E_{t+1} = ρ E_t + c_t."""

    rho: float
    e0: float = 0.0
    e_star: float | None = None
    """Fixed equilibrium E* = E[c]/(1-ρ). If None, use running mean of c."""

    e: float = field(init=False)
    t: int = field(init=False, default=0)
    _c_sum: float = field(init=False, default=0.0)
    _c_count: int = field(init=False, default=0)
    history_e: list[float] = field(init=False, default_factory=list)
    history_c: list[float] = field(init=False, default_factory=list)
    history_p_hat: list[float] = field(init=False, default_factory=list)

    def __post_init__(self) -> None:
        if not (0.0 < self.rho < 1.0):
            raise ValueError(f"rho must be in (0, 1), got {self.rho}")
        self.e = float(self.e0)
        self.history_e = [self.e]
        self.history_c = []
        self.history_p_hat = [self.p_hat]

    @property
    def running_mean_c(self) -> float:
        if self._c_count == 0:
            return 0.0
        return self._c_sum / self._c_count

    @property
    def equilibrium(self) -> float:
        """E* = E[c] / (1-ρ). Uses fixed e_star if set, else running mean of c."""
        if self.e_star is not None:
            return float(self.e_star)
        return self.running_mean_c / (1.0 - self.rho)

    @property
    def p_hat(self) -> float:
        """Ṕ_t = E_t - E* (residual influence of past shocks)."""
        return self.e - self.equilibrium

    def step(self, c_t: float) -> float:
        """Advance one step: E_{t+1} = ρ E_t + c_t. Returns new E."""
        c = float(c_t)
        self._c_sum += c
        self._c_count += 1
        self.e = self.rho * self.e + c
        self.t += 1
        self.history_c.append(c)
        self.history_e.append(self.e)
        self.history_p_hat.append(self.p_hat)
        return self.e


def equilibrium_from_mean_c(mean_c: float, rho: float) -> float:
    """E* = E[c] / (1-ρ)."""
    if not (0.0 < rho < 1.0):
        raise ValueError(f"rho must be in (0, 1), got {rho}")
    return float(mean_c) / (1.0 - rho)


def t_reset(epsilon: float, e0: float, e_star: float, rho: float) -> int:
    """
    Reset time: how long before a switch is even eligible under pure decay.

    T_reset(ε) = ceil( ln(ε / |E_0 - E*|) / ln(ρ) )

    Edge cases:
    - E_0 == E* or |E_0 - E*| <= ε → already eligible → 0
    - ε <= 0 → raise (threshold must be positive)
    - ρ not in (0, 1) → raise
    """
    if not (0.0 < rho < 1.0):
        raise ValueError(f"rho must be in (0, 1), got {rho}")
    if epsilon <= 0.0:
        raise ValueError(f"epsilon must be > 0, got {epsilon}")

    gap = abs(float(e0) - float(e_star))
    if gap == 0.0 or gap <= epsilon:
        return 0

    # Pure homogeneous decay |E_t - E*| ≈ ρ^t |E_0 - E*| (drive at equilibrium).
    # Solve ρ^t * gap < ε  ⇒  t > ln(ε/gap) / ln(ρ)
    ratio = epsilon / gap
    # ratio in (0, 1) here
    value = math.log(ratio) / math.log(rho)
    return int(math.ceil(value))
