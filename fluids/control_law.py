"""Pure NumPy control-law helpers for TG / NS sketches (no Dedalus). Off live govern path."""
from __future__ import annotations

import numpy as np


def cbf_correction(h_enst, vorticity_arr, cbf_gain, cbf_margin):
    if h_enst < cbf_margin:
        deficit = cbf_margin - h_enst
        return -cbf_gain * deficit * vorticity_arr
    return 0 * vorticity_arr


def adaptive_re_update_unclamped(Ens, Reynolds, Re_target_enstrophy, Re_min, Re_max):
    """Unclamped — can overshoot bounds (opt-in / regression only)."""
    if Ens < Re_target_enstrophy and Reynolds < Re_max:
        Reynolds *= 1.01
    elif Ens > Re_target_enstrophy and Reynolds > Re_min:
        Reynolds *= 0.99
    return Reynolds


def adaptive_re_update_clamped(Ens, Reynolds, Re_target_enstrophy, Re_min, Re_max):
    """Clamped adaptive Re — stays in [Re_min, Re_max]. Default public API."""
    if Ens < Re_target_enstrophy:
        Reynolds *= 1.01
    elif Ens > Re_target_enstrophy:
        Reynolds *= 0.99
    return min(max(Reynolds, Re_min), Re_max)


def adaptive_re_update(Ens, Reynolds, Re_target_enstrophy, Re_min, Re_max):
    """Default public API: clamped. Unclamped is opt-in via adaptive_re_update_unclamped."""
    return adaptive_re_update_clamped(
        Ens, Reynolds, Re_target_enstrophy, Re_min, Re_max
    )


def energy_clf_terms(E, target_energy, u_g, control_gain, clf_gain):
    V_energy = (E - target_energy) ** 2
    f_base = control_gain * (target_energy - E) * u_g
    dV_du = 2 * (E - target_energy) * u_g
    f_clf = -clf_gain * dV_du
    return V_energy, f_base, f_clf
