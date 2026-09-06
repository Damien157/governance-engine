"""Zeta finite-sum sanity + master / optimal-ρ search."""

from __future__ import annotations

import numpy as np

from haven2.zeta import (
    energy_zeta,
    engine_zeta,
    master_zeta,
    optimal_rho,
    realm_switch_zeta,
    s_avg,
)


def test_energy_zeta_finite_sum():
    p = [1.0, 0.5, 0.25]
    # σ=2: 1/1 + 0.5/4 + 0.25/9 = 1 + 0.125 + 0.027777...
    expected = 1.0 + 0.5 / 4.0 + 0.25 / 9.0
    assert abs(energy_zeta(p, sigma=2.0) - expected) < 1e-12


def test_realm_switch_zeta_finite_sum():
    # τ = 0, 3 → 1/1^2 + 1/4^2 = 1 + 1/16
    assert abs(realm_switch_zeta([0, 3], sigma=2.0) - (1.0 + 1.0 / 16.0)) < 1e-12


def test_engine_zeta_finite_sum():
    c = [1.0, 1.0]
    expected = 1.0 + 1.0 / 4.0
    assert abs(engine_zeta(c, sigma=2.0) - expected) < 1e-12


def test_master_zeta_uses_abs_energy():
    p = [-2.0, -1.0]
    z_e = energy_zeta(p, 2.0)
    assert z_e < 0
    z_h = master_zeta(p, switch_times=[], c_trace=[0.0, 0.0], sigma=2.0, alpha_x=1, alpha_r=0, alpha_c=0)
    assert abs(z_h - abs(z_e)) < 1e-12


def test_optimal_rho_grid_search():
    # Synthetic: higher rho yields larger |p_hat| mass and more C scores → pick max S_avg
    def run_trace(rho: float):
        T = 20
        # residual decays with rho from a unit shock
        p = [rho**t for t in range(T)]
        switches = [5] if rho < 0.85 else [5, 12]
        c = [0.5 + 0.5 * rho] * T
        return p, switches, c

    grid = [0.5, 0.7, 0.9]
    result = optimal_rho(run_trace, grid)
    assert result.rho_star in grid
    assert len(result.s_avg_curve) == len(grid)
    # argmax consistency
    assert result.rho_star == grid[int(np.argmax(result.s_avg_curve))]
    assert abs(s_avg(*run_trace(result.rho_star)) - max(result.s_avg_curve)) < 1e-12
