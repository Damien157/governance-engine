"""Thin supervisor stub: supplies nominal command and safety/setpoint params."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import numpy as np

from .clf import default_P


class NominalProvider(Protocol):
    """Protocol for anything that can supply a nominal input u_nom(t, x)."""

    def u_nom(self, t: float, x: np.ndarray) -> float: ...


@dataclass
class SupervisorParams:
    """
    Parameters a higher-level supervisor would push down to the inner loop.

    No wave-ODE / outer dynamics here — just the knobs the QP needs.
    """

    u_nom: float = 0.0
    p_max: float = 1.0
    x_c: np.ndarray = field(default_factory=lambda: np.array([0.5, 0.0]))
    k_lyap: float = 1.0
    k_cbf: float = 2.0
    dt: float = 0.01
    P: np.ndarray = field(default_factory=default_P)

    def with_u_nom(self, u_nom: float) -> "SupervisorParams":
        return SupervisorParams(
            u_nom=u_nom,
            p_max=self.p_max,
            x_c=np.asarray(self.x_c, dtype=float).copy(),
            k_lyap=self.k_lyap,
            k_cbf=self.k_cbf,
            dt=self.dt,
            P=np.asarray(self.P, dtype=float).copy(),
        )


@dataclass
class ConstantNominal:
    """Constant nominal input provider."""

    value: float

    def u_nom(self, t: float, x: np.ndarray) -> float:
        return float(self.value)


@dataclass
class TrackingNominal:
    """
    Simple PD-like nominal toward x_c (for the 'safe tracking' demo).
    u_nom = -Kp (p - p_c) - Kd (v - v_c)
    """

    x_c: np.ndarray
    Kp: float = 4.0
    Kd: float = 3.0

    def u_nom(self, t: float, x: np.ndarray) -> float:
        x = np.asarray(x, dtype=float).reshape(2)
        xc = np.asarray(self.x_c, dtype=float).reshape(2)
        e = x - xc
        return float(-self.Kp * e[0] - self.Kd * e[1])
