"""Euler-integrated closed-loop simulator for the CLF-CBF-QP controller."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from .cbf import ControlBarrierFunction
from .clf import ControlLyapunovFunction
from .plant import Plant
from .qp_controller import CLFCBFQPController
from .supervisor import ConstantNominal, NominalProvider, SupervisorParams


@dataclass
class SimResult:
    t: np.ndarray
    x: np.ndarray  # (N, 2)
    u: np.ndarray
    u_nom: np.ndarray
    h: np.ndarray
    V: np.ndarray
    delta: np.ndarray
    status: list[str] = field(default_factory=list)

    @property
    def min_h(self) -> float:
        return float(np.min(self.h))

    @property
    def barrier_held(self) -> bool:
        return self.min_h >= -1e-6


class Simulator:
    """Simulate plant + CLF-CBF-QP with Euler integration."""

    def __init__(self, params: SupervisorParams | None = None) -> None:
        self.params = params or SupervisorParams()
        self.plant = Plant()
        self.clf = ControlLyapunovFunction(
            self.plant,
            x_c=self.params.x_c,
            P=self.params.P,
            k_lyap=self.params.k_lyap,
        )
        self.cbf = ControlBarrierFunction(
            self.plant,
            p_max=self.params.p_max,
            k_cbf=self.params.k_cbf,
        )
        self.controller = CLFCBFQPController(self.clf, self.cbf)

    def run(
        self,
        x0: np.ndarray,
        T: float,
        nominal: NominalProvider | float | Callable[[float, np.ndarray], float] | None = None,
        dt: float | None = None,
    ) -> SimResult:
        dt = float(self.params.dt if dt is None else dt)
        n = int(np.ceil(T / dt))
        x = np.asarray(x0, dtype=float).reshape(2).copy()

        if nominal is None:
            nom: NominalProvider = ConstantNominal(self.params.u_nom)
        elif isinstance(nominal, (int, float)):
            nom = ConstantNominal(float(nominal))
        elif callable(nominal) and not hasattr(nominal, "u_nom"):

            class _Fn:
                def __init__(self, fn: Callable):
                    self._fn = fn

                def u_nom(self, t: float, xv: np.ndarray) -> float:
                    return float(self._fn(t, xv))

            nom = _Fn(nominal)
        else:
            nom = nominal  # type: ignore[assignment]

        ts = np.zeros(n + 1)
        xs = np.zeros((n + 1, 2))
        us = np.zeros(n + 1)
        u_noms = np.zeros(n + 1)
        hs = np.zeros(n + 1)
        Vs = np.zeros(n + 1)
        deltas = np.zeros(n + 1)
        statuses: list[str] = []

        xs[0] = x
        hs[0] = self.cbf.h(x)
        Vs[0] = self.clf.V(x)

        for k in range(n):
            t = k * dt
            u_nom = nom.u_nom(t, x)
            sol = self.controller.solve(x, u_nom)
            u = sol["u"]
            x = self.plant.euler_step(x, u, dt)

            ts[k] = t
            us[k] = u
            u_noms[k] = u_nom
            deltas[k] = sol.get("delta", 0.0) if sol.get("delta") == sol.get("delta") else 0.0
            statuses.append(str(sol.get("status", "")))
            xs[k + 1] = x
            hs[k + 1] = self.cbf.h(x)
            Vs[k + 1] = self.clf.V(x)

        ts[n] = n * dt
        us[n] = us[n - 1] if n else 0.0
        u_noms[n] = u_noms[n - 1] if n else 0.0
        deltas[n] = deltas[n - 1] if n else 0.0

        return SimResult(
            t=ts, x=xs, u=us, u_nom=u_noms, h=hs, V=Vs, delta=deltas, status=statuses
        )
