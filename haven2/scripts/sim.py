#!/usr/bin/env python3
"""
Haven2 shock + regime-change demo.

Injects a large drive shock, then flips the volatility regime while |Ṕ| is
still large — transistor latch must hold. After decay, switch is allowed.
Also grid-searches ρ* on the same fixed trace and writes S_avg(ρ).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from haven2.engine import Haven2Engine, run_shock_regime_trace
from haven2.realms import Realm
from haven2.zeta import DEFAULT_ALPHA, DEFAULT_S_WEIGHTS, DEFAULT_SIGMA_GRID, optimal_rho

ART = ROOT / "artifacts"
ART.mkdir(parents=True, exist_ok=True)

# Scenario defaults
RHO = 0.92
EPS = 0.05
MEAN_C = 0.02
SHOCK_C = 1.0
SHOCK_T = 5
REGIME_T = 15
HORIZON = 100
VOL_LOW = 0.008
VOL_HIGH = 0.05


def realm_to_int(r: Realm) -> int:
    return {Realm.CALM: 0, Realm.NORMAL: 1, Realm.DEFENSIVE: 2}[r]


def main() -> None:
    p_hat, switches, c_scores, eng = run_shock_regime_trace(
        RHO,
        epsilon_switch=EPS,
        e0=0.0,
        mean_c=MEAN_C,
        shock_c=SHOCK_C,
        shock_t=SHOCK_T,
        regime_change_t=REGIME_T,
        horizon=HORIZON,
        vol_low_level=VOL_LOW,
        vol_high_level=VOL_HIGH,
        initial_realm=Realm.CALM,
    )

    ts = np.arange(1, HORIZON + 1)
    e_hist = np.asarray(eng.energy.history_e[1:])  # post-step
    p_hist = np.asarray([r.p_hat for r in eng.history])
    open_hist = np.asarray([1.0 if r.open else 0.0 for r in eng.history])
    g_hist = np.asarray([realm_to_int(r.realm) for r in eng.history])

    # Find blocked switch attempt: first time after regime change where
    # TargetRealm wants DEFENSIVE but latch is closed.
    blocked_idx = None
    blocked_p = None
    for i, rec in enumerate(eng.history):
        if rec.t >= REGIME_T + 1 and not rec.open:
            # target would be defensive given high vol
            if rec.v >= VOL_HIGH * 0.99 and rec.realm != Realm.DEFENSIVE:
                blocked_idx = i
                blocked_p = abs(rec.p_hat)
                break

    # First successful switch to defensive after regime change
    switch_after = None
    for i, rec in enumerate(eng.history):
        if rec.switched and rec.realm == Realm.DEFENSIVE and rec.t > REGIME_T:
            switch_after = i
            break

    latch_held = blocked_idx is not None and (
        switch_after is None or switch_after > blocked_idx
    )

    # --- plots ---
    fig, axes = plt.subplots(4, 1, figsize=(10, 10), sharex=True)

    axes[0].plot(ts, e_hist, color="C0", label=r"$E_t$")
    axes[0].axhline(eng.energy.equilibrium, color="gray", ls="--", label=r"$E^*$")
    axes[0].axvline(SHOCK_T + 1, color="C3", ls=":", label="shock")
    axes[0].axvline(REGIME_T + 1, color="C1", ls=":", label="regime↑")
    axes[0].set_ylabel(r"$E_t$")
    axes[0].legend(loc="upper right", fontsize=8)
    axes[0].set_title("Haven2 backbone — shock then regime change (latch)")

    axes[1].plot(ts, p_hist, color="C2", label=r"$\hat{P}_t$")
    axes[1].axhline(EPS, color="k", ls="--", lw=0.8)
    axes[1].axhline(-EPS, color="k", ls="--", lw=0.8, label=r"$\pm\varepsilon_{switch}$")
    axes[1].axvline(SHOCK_T + 1, color="C3", ls=":")
    axes[1].axvline(REGIME_T + 1, color="C1", ls=":")
    axes[1].set_ylabel(r"$\hat{P}_t$")
    axes[1].legend(loc="upper right", fontsize=8)

    axes[2].step(ts, g_hist, where="post", color="C4", label=r"$g_t$")
    axes[2].set_yticks([0, 1, 2])
    axes[2].set_yticklabels(["calm", "normal", "defensive"])
    axes[2].axvline(SHOCK_T + 1, color="C3", ls=":")
    axes[2].axvline(REGIME_T + 1, color="C1", ls=":")
    axes[2].set_ylabel(r"$g_t$")
    axes[2].legend(loc="upper right", fontsize=8)

    axes[3].fill_between(ts, 0, open_hist, step="post", alpha=0.4, color="C5", label="open")
    axes[3].step(ts, open_hist, where="post", color="C5")
    axes[3].set_yticks([0, 1])
    axes[3].set_yticklabels(["closed", "open"])
    axes[3].axvline(SHOCK_T + 1, color="C3", ls=":")
    axes[3].axvline(REGIME_T + 1, color="C1", ls=":")
    axes[3].set_xlabel("t")
    axes[3].set_ylabel("transistor")
    axes[3].legend(loc="upper right", fontsize=8)

    fig.tight_layout()
    plot_path = ART / "haven2_latch_sim.png"
    fig.savefig(plot_path, dpi=140)
    plt.close(fig)

    # --- ρ* grid search on fixed shock/regime scenario ---
    rho_grid = [round(x, 3) for x in np.linspace(0.70, 0.98, 15)]

    def run_for_rho(rho: float):
        ph, sw, cs, _ = run_shock_regime_trace(
            rho,
            epsilon_switch=EPS,
            e0=0.0,
            mean_c=MEAN_C,
            shock_c=SHOCK_C,
            shock_t=SHOCK_T,
            regime_change_t=REGIME_T,
            horizon=HORIZON,
            vol_low_level=VOL_LOW,
            vol_high_level=VOL_HIGH,
            initial_realm=Realm.CALM,
        )
        return ph, sw, cs

    search = optimal_rho(run_for_rho, rho_grid)

    fig2, ax = plt.subplots(figsize=(8, 4))
    ax.plot(search.rho_grid, search.s_avg_curve, "o-", color="C0")
    ax.axvline(search.rho_star, color="C3", ls="--", label=rf"$\rho^*={search.rho_star:.3f}$")
    ax.set_xlabel(r"$\rho$")
    ax.set_ylabel(r"$S_{avg}(\rho)$")
    ax.set_title("Optimal memory grid search")
    ax.legend()
    fig2.tight_layout()
    s_avg_path = ART / "haven2_s_avg_rho.png"
    fig2.savefig(s_avg_path, dpi=140)
    plt.close(fig2)

    summary = {
        "rho": RHO,
        "epsilon_switch": EPS,
        "shock_t": SHOCK_T,
        "regime_change_t": REGIME_T,
        "latch_held": latch_held,
        "blocked_switch_attempt_t": None if blocked_idx is None else eng.history[blocked_idx].t,
        "min_abs_p_hat_at_blocked_attempt": blocked_p,
        "first_defensive_switch_t": None
        if switch_after is None
        else eng.history[switch_after].t,
        "switch_times": eng.latch.switch_times,
        "final_realm": eng.realm.value,
        "zeta": eng.zeta_summaries(),
        "rho_star": search.rho_star,
        "s_avg_curve": {
            "rho": search.rho_grid,
            "s_avg": search.s_avg_curve,
        },
        "alpha_defaults": DEFAULT_ALPHA,
        "s_weights_defaults": DEFAULT_S_WEIGHTS,
        "sigma_grid": list(DEFAULT_SIGMA_GRID),
        "artifacts": {
            "latch_plot": str(plot_path),
            "s_avg_plot": str(s_avg_path),
        },
    }
    summary_path = ART / "haven2_sim_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2))

    # human-readable
    lines = [
        f"latch_held={latch_held}",
        f"min_|P|_at_blocked={blocked_p}",
        f"blocked_t={summary['blocked_switch_attempt_t']}",
        f"first_defensive_switch_t={summary['first_defensive_switch_t']}",
        f"rho_star={search.rho_star}",
        f"plot={plot_path}",
        f"s_avg_plot={s_avg_path}",
    ]
    (ART / "haven2_sim_summary.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
