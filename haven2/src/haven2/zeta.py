"""
Spectral / zeta summaries (Dirichlet series over the trace, truncated).

These are spectral summaries of memory, agility, and scored behaviour —
not proofs of consciousness.

  Energy zeta:      Ž_E(σ) = sum_{t=0}^{T} Ṕ_t / (t+1)^σ
  Realm-switch zeta: Z_R(σ) = sum_k 1/(τ_k+1)^σ
  Engine zeta:       Z_C(σ) = sum_t C_t / (t+1)^σ

Master / optimal memory:
  Z_H(σ) = α_X ||Z_X(σ)|| + α_R Z_R(σ) + α_C Z_C(σ)
  S(ρ,σ) = w_E E_spec + w_R R_spec + w_C C_spec
  S_avg(ρ) = (1/|Σ|) sum_{σ in Σ} S(ρ,σ)
  ρ* = argmax_ρ S_avg(ρ)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

import numpy as np

# Defaults for master zeta / combined scalar
DEFAULT_ALPHA = {"X": 1.0, "R": 1.0, "C": 1.0}
DEFAULT_S_WEIGHTS = {"E": 1.0 / 3.0, "R": 1.0 / 3.0, "C": 1.0 / 3.0}
DEFAULT_SIGMA = 2.0
DEFAULT_SIGMA_GRID = (1.5, 2.0, 2.5)


def energy_zeta(p_hat: Sequence[float], sigma: float = DEFAULT_SIGMA) -> float:
    """Ž_E(σ) = sum_{t=0}^{T} Ṕ_t / (t+1)^σ  (can be signed)."""
    arr = np.asarray(p_hat, dtype=float)
    if arr.size == 0:
        return 0.0
    t = np.arange(arr.size, dtype=float)
    return float(np.sum(arr / np.power(t + 1.0, sigma)))


def realm_switch_zeta(switch_times: Sequence[int], sigma: float = DEFAULT_SIGMA) -> float:
    """Z_R(σ) = sum_k 1/(τ_k+1)^σ."""
    if not switch_times:
        return 0.0
    tau = np.asarray(switch_times, dtype=float)
    return float(np.sum(1.0 / np.power(tau + 1.0, sigma)))


def engine_zeta(c_trace: Sequence[float], sigma: float = DEFAULT_SIGMA) -> float:
    """Z_C(σ) = sum_t C_t / (t+1)^σ."""
    arr = np.asarray(c_trace, dtype=float)
    if arr.size == 0:
        return 0.0
    t = np.arange(arr.size, dtype=float)
    return float(np.sum(arr / np.power(t + 1.0, sigma)))


def master_zeta(
    p_hat: Sequence[float],
    switch_times: Sequence[int],
    c_trace: Sequence[float],
    sigma: float = DEFAULT_SIGMA,
    *,
    alpha_x: float = DEFAULT_ALPHA["X"],
    alpha_r: float = DEFAULT_ALPHA["R"],
    alpha_c: float = DEFAULT_ALPHA["C"],
) -> float:
    """Z_H(σ) = α_X ||Z_X(σ)|| + α_R Z_R(σ) + α_C Z_C(σ)."""
    z_x = energy_zeta(p_hat, sigma)
    z_r = realm_switch_zeta(switch_times, sigma)
    z_c = engine_zeta(c_trace, sigma)
    return float(alpha_x * abs(z_x) + alpha_r * z_r + alpha_c * z_c)


@dataclass
class SpectralSpecs:
    """Per-σ spectral components used in S(ρ,σ)."""

    e_spec: float
    r_spec: float
    c_spec: float
    z_h: float

    def combined(
        self,
        *,
        w_e: float = DEFAULT_S_WEIGHTS["E"],
        w_r: float = DEFAULT_S_WEIGHTS["R"],
        w_c: float = DEFAULT_S_WEIGHTS["C"],
    ) -> float:
        """S(ρ,σ) = w_E E_spec + w_R R_spec + w_C C_spec."""
        return float(w_e * self.e_spec + w_r * self.r_spec + w_c * self.c_spec)


def spectral_specs(
    p_hat: Sequence[float],
    switch_times: Sequence[int],
    c_trace: Sequence[float],
    sigma: float = DEFAULT_SIGMA,
    *,
    alpha_x: float = DEFAULT_ALPHA["X"],
    alpha_r: float = DEFAULT_ALPHA["R"],
    alpha_c: float = DEFAULT_ALPHA["C"],
) -> SpectralSpecs:
    """
    Build spectral components.

    E_spec = ||Ž_E|| (abs of energy zeta — memory residual mass)
    R_spec = Z_R      (switch agility)
    C_spec = Z_C      (scored behaviour mass)
    """
    z_e = energy_zeta(p_hat, sigma)
    z_r = realm_switch_zeta(switch_times, sigma)
    z_c = engine_zeta(c_trace, sigma)
    e_spec = abs(z_e)
    z_h = alpha_x * e_spec + alpha_r * z_r + alpha_c * z_c
    return SpectralSpecs(e_spec=e_spec, r_spec=z_r, c_spec=z_c, z_h=z_h)


def s_avg(
    p_hat: Sequence[float],
    switch_times: Sequence[int],
    c_trace: Sequence[float],
    sigma_grid: Iterable[float] = DEFAULT_SIGMA_GRID,
    *,
    w_e: float = DEFAULT_S_WEIGHTS["E"],
    w_r: float = DEFAULT_S_WEIGHTS["R"],
    w_c: float = DEFAULT_S_WEIGHTS["C"],
    alpha_x: float = DEFAULT_ALPHA["X"],
    alpha_r: float = DEFAULT_ALPHA["R"],
    alpha_c: float = DEFAULT_ALPHA["C"],
) -> float:
    """S_avg = (1/|Σ|) sum_{σ in Σ} S(ρ,σ)."""
    sigmas = list(sigma_grid)
    if not sigmas:
        raise ValueError("sigma_grid must be non-empty")
    total = 0.0
    for sigma in sigmas:
        specs = spectral_specs(
            p_hat,
            switch_times,
            c_trace,
            sigma,
            alpha_x=alpha_x,
            alpha_r=alpha_r,
            alpha_c=alpha_c,
        )
        total += specs.combined(w_e=w_e, w_r=w_r, w_c=w_c)
    return total / len(sigmas)


@dataclass
class RhoSearchResult:
    rho_star: float
    rho_grid: list[float]
    s_avg_curve: list[float]


def optimal_rho(
    run_trace_fn,
    rho_grid: Sequence[float],
    *,
    sigma_grid: Iterable[float] = DEFAULT_SIGMA_GRID,
    w_e: float = DEFAULT_S_WEIGHTS["E"],
    w_r: float = DEFAULT_S_WEIGHTS["R"],
    w_c: float = DEFAULT_S_WEIGHTS["C"],
    alpha_x: float = DEFAULT_ALPHA["X"],
    alpha_r: float = DEFAULT_ALPHA["R"],
    alpha_c: float = DEFAULT_ALPHA["C"],
) -> RhoSearchResult:
    """
    ρ* = argmax_ρ S_avg(ρ) on a fixed shock/regime scenario.

    run_trace_fn(rho) -> (p_hat_trace, switch_times, c_trace)
    """
    rhos = [float(r) for r in rho_grid]
    if not rhos:
        raise ValueError("rho_grid must be non-empty")
    curve: list[float] = []
    for rho in rhos:
        p_hat, switches, c_trace = run_trace_fn(rho)
        curve.append(
            s_avg(
                p_hat,
                switches,
                c_trace,
                sigma_grid,
                w_e=w_e,
                w_r=w_r,
                w_c=w_c,
                alpha_x=alpha_x,
                alpha_r=alpha_r,
                alpha_c=alpha_c,
            )
        )
    best_idx = int(np.argmax(curve))
    return RhoSearchResult(rho_star=rhos[best_idx], rho_grid=rhos, s_avg_curve=curve)
