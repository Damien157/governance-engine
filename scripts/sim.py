#!/usr/bin/env python3
"""Run safe-tracking and unsafe-nominal demos; write plots to artifacts/."""

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from governance_engine.plant import Plant
from governance_engine.simulator import Simulator
from governance_engine.supervisor import SupervisorParams, TrackingNominal

ART = ROOT / "artifacts"
ART.mkdir(parents=True, exist_ok=True)

DEFAULTS = dict(
    dt=0.01,
    k_lyap=1.0,
    k_cbf=2.0,
    p_max=1.0,
    x_c=np.array([0.5, 0.0]),
)


def run_safe_tracking() -> Path:
    params = SupervisorParams(**DEFAULTS)
    sim = Simulator(params)
    nom = TrackingNominal(x_c=params.x_c, Kp=4.0, Kd=3.0)
    res = sim.run(x0=np.array([0.0, 0.0]), T=8.0, nominal=nom)

    fig, axes = plt.subplots(3, 1, figsize=(9, 8), sharex=True)
    axes[0].plot(res.t, res.x[:, 0], label="p(t)")
    axes[0].axhline(params.x_c[0], color="gray", ls="--", label="x_c (pos)")
    axes[0].axhline(params.p_max, color="r", ls="--", label="p_max")
    axes[0].set_ylabel("position")
    axes[0].legend(loc="best")
    axes[0].set_title("Safe tracking: CLF-CBF-QP follows nominal toward x_c")

    axes[1].plot(res.t, res.u, label="u (QP)")
    axes[1].plot(res.t, res.u_nom, ls="--", label="u_nom")
    axes[1].set_ylabel("input")
    axes[1].legend(loc="best")

    axes[2].plot(res.t, res.h, label="h = p_max - p")
    axes[2].axhline(0.0, color="r", ls="--")
    axes[2].set_ylabel("h(x)")
    axes[2].set_xlabel("time [s]")
    axes[2].legend(loc="best")

    fig.tight_layout()
    out = ART / "safe_tracking.png"
    fig.savefig(out, dpi=140)
    plt.close(fig)
    print(f"safe tracking: min_h={res.min_h:.6f}, final_p={res.x[-1,0]:.4f} -> {out}")
    return out


def run_unsafe_nominal() -> tuple[Path, float]:
    # Setpoint past the barrier so CLF and u_nom both push through p_max;
    # only the CBF keeps the safe set invariant (visually obvious).
    params = SupervisorParams(**{**DEFAULTS, "x_c": np.array([1.5, 0.0])})
    sim = Simulator(params)
    u_nom_const = 5.0
    res = sim.run(x0=np.array([0.0, 0.0]), T=5.0, nominal=u_nom_const)

    # Open-loop reference (no QP) for visual contrast
    plant = Plant()
    x_ol = np.array([0.0, 0.0], dtype=float)
    t_ol = [0.0]
    p_ol = [0.0]
    dt = params.dt
    for k in range(int(5.0 / dt)):
        x_ol = plant.euler_step(x_ol, u_nom_const, dt)
        t_ol.append((k + 1) * dt)
        p_ol.append(x_ol[0])

    fig, axes = plt.subplots(3, 1, figsize=(9, 8), sharex=True)
    axes[0].plot(res.t, res.x[:, 0], label="p(t) with CLF-CBF-QP", lw=2)
    axes[0].plot(t_ol, p_ol, ls="--", color="orange", label="p(t) open-loop u=5 (unsafe)")
    axes[0].axhline(params.p_max, color="r", ls="--", label="p_max")
    axes[0].set_ylabel("position")
    axes[0].legend(loc="best")
    axes[0].set_title(
        f"Unsafe nominal u_nom={u_nom_const}: barrier holds (min h={res.min_h:.4f})"
    )

    axes[1].plot(res.t, res.u, label="u (QP)")
    axes[1].plot(res.t, res.u_nom, ls="--", label="u_nom")
    axes[1].set_ylabel("input")
    axes[1].legend(loc="best")

    axes[2].plot(res.t, res.h, label="h = p_max - p", lw=2)
    axes[2].axhline(0.0, color="r", ls="--", label="h=0 boundary")
    axes[2].fill_between(res.t, 0, np.minimum(res.h, 0), color="red", alpha=0.3)
    axes[2].set_ylabel("h(x)")
    axes[2].set_xlabel("time [s]")
    axes[2].legend(loc="best")

    fig.tight_layout()
    out = ART / "unsafe_nominal_barrier.png"
    fig.savefig(out, dpi=140)
    plt.close(fig)
    print(
        f"unsafe nominal: min_h={res.min_h:.6f}, max_p={np.max(res.x[:,0]):.6f}, "
        f"barrier_held={res.barrier_held} -> {out}"
    )
    return out, res.min_h


def main() -> None:
    run_safe_tracking()
    _, min_h = run_unsafe_nominal()
    # Write a small summary for automation
    summary = ART / "sim_summary.txt"
    summary.write_text(
        f"min_h_unsafe_nominal={min_h}\n"
        f"barrier_held={min_h >= -1e-6}\n",
        encoding="utf-8",
    )
    print(f"wrote {summary}")


if __name__ == "__main__":
    main()
