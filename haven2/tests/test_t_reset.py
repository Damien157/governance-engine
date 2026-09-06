"""T_reset formula hand-case tests."""

from __future__ import annotations

import math

import pytest

from haven2.energy import t_reset


def test_t_reset_hand_case():
    # |E0 - E*| = 1.0, ε = 0.1, ρ = 0.5
    # ρ^t * 1 < 0.1 ⇒ 0.5^t < 0.1 ⇒ t > log(0.1)/log(0.5) ≈ 3.3219 ⇒ ceil = 4
    assert t_reset(0.1, e0=1.0, e_star=0.0, rho=0.5) == 4


def test_t_reset_already_at_equilibrium():
    assert t_reset(0.05, e0=1.0, e_star=1.0, rho=0.9) == 0


def test_t_reset_already_within_epsilon():
    assert t_reset(0.5, e0=1.0, e_star=0.7, rho=0.9) == 0


def test_t_reset_matches_ceil_formula():
    eps, e0, e_star, rho = 0.01, 2.0, 0.5, 0.8
    gap = abs(e0 - e_star)
    expected = int(math.ceil(math.log(eps / gap) / math.log(rho)))
    assert t_reset(eps, e0, e_star, rho) == expected


def test_t_reset_rejects_bad_rho():
    with pytest.raises(ValueError):
        t_reset(0.1, 1.0, 0.0, rho=1.0)
    with pytest.raises(ValueError):
        t_reset(0.1, 1.0, 0.0, rho=0.0)


def test_t_reset_rejects_nonpositive_epsilon():
    with pytest.raises(ValueError):
        t_reset(0.0, 1.0, 0.0, rho=0.9)
