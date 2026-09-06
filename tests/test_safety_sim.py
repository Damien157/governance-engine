"""Closed-loop safety: h must stay non-negative under unsafe nominal."""

from __future__ import annotations

import numpy as np

from governance_engine.simulator import Simulator
from governance_engine.supervisor import SupervisorParams, TrackingNominal


def test_unsafe_nominal_barrier_holds():
    params = SupervisorParams(
        p_max=1.0,
        x_c=np.array([1.5, 0.0]),  # past barrier: CLF wants to cross
        k_lyap=1.0,
        k_cbf=2.0,
        dt=0.01,
        u_nom=5.0,  # constant push toward / through barrier
    )
    sim = Simulator(params)
    # Start inside safe set, moving toward p_max
    result = sim.run(x0=np.array([0.0, 0.0]), T=5.0, nominal=5.0)
    assert result.barrier_held, f"barrier violated: min_h={result.min_h}"
    assert result.min_h >= -1e-6
    # Without safety, open-loop would cross; with safety, p stays <= p_max
    assert np.max(result.x[:, 0]) <= params.p_max + 1e-5


def test_open_loop_would_violate():
    """Sanity: same u_nom without QP crosses p_max (documents the demo)."""
    from governance_engine.plant import Plant

    plant = Plant()
    x = np.array([0.0, 0.0], dtype=float)
    p_max = 1.0
    dt = 0.01
    violated = False
    for _ in range(500):
        x = plant.euler_step(x, u=5.0, dt=dt)
        if x[0] > p_max:
            violated = True
            break
    assert violated


def test_safe_tracking_converges_near_setpoint():
    params = SupervisorParams(
        p_max=1.0,
        x_c=np.array([0.5, 0.0]),
        k_lyap=1.0,
        k_cbf=2.0,
        dt=0.01,
    )
    sim = Simulator(params)
    nom = TrackingNominal(x_c=params.x_c, Kp=4.0, Kd=3.0)
    result = sim.run(x0=np.array([0.0, 0.0]), T=8.0, nominal=nom)
    assert result.barrier_held
    # Should end near setpoint
    assert abs(result.x[-1, 0] - 0.5) < 0.15
