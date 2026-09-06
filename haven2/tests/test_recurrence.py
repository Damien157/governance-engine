"""Energy recurrence identity tests."""

from __future__ import annotations

import numpy as np

from haven2.energy import EnergyState, equilibrium_from_mean_c


def test_recurrence_identity_single_step():
    rho, e0, c = 0.8, 1.5, 0.25
    en = EnergyState(rho=rho, e0=e0, e_star=0.0)
    en.step(c)
    assert abs(en.e - (rho * e0 + c)) < 1e-12


def test_recurrence_identity_multi_step():
    rho = 0.9
    e0 = 0.0
    cs = [0.1, 0.2, -0.05, 0.3]
    en = EnergyState(rho=rho, e0=e0, e_star=equilibrium_from_mean_c(0.1, rho))
    e = e0
    for c in cs:
        e = rho * e + c
        en.step(c)
        assert abs(en.e - e) < 1e-12


def test_p_hat_is_deviation_from_equilibrium():
    rho = 0.85
    mean_c = 0.1
    e_star = equilibrium_from_mean_c(mean_c, rho)
    en = EnergyState(rho=rho, e0=e_star, e_star=e_star)
    assert abs(en.p_hat) < 1e-12
    en.step(mean_c + 0.5)
    assert abs(en.p_hat - (en.e - e_star)) < 1e-12


def test_running_mean_equilibrium_when_e_star_unknown():
    rho = 0.7
    en = EnergyState(rho=rho, e0=0.0, e_star=None)
    cs = [0.2, 0.2, 0.2]
    for c in cs:
        en.step(c)
    assert abs(en.running_mean_c - 0.2) < 1e-12
    assert abs(en.equilibrium - (0.2 / (1 - rho))) < 1e-12
