"""Control Barrier Function (CBF) for forward invariance of {h >= 0}."""

from __future__ import annotations

import numpy as np

from .plant import Plant


class ControlBarrierFunction:
    """
    Ames-style CBF:
        h(x) = p_max - p
        constraint: Lf h + Lg h u >= -k_cbf h(x)
    Guarantees forward invariance of the safe set {x : h(x) >= 0}.
    """

    def __init__(
        self,
        plant: Plant,
        p_max: float = 1.0,
        k_cbf: float = 2.0,
    ) -> None:
        self.plant = plant
        self.p_max = float(p_max)
        self.k_cbf = float(k_cbf)

    def h(self, x: np.ndarray) -> float:
        p = float(np.asarray(x, dtype=float).reshape(2)[0])
        return self.p_max - p

    def grad_h(self, x: np.ndarray | None = None) -> np.ndarray:
        """dh/dx = [-1, 0]."""
        return np.array([-1.0, 0.0], dtype=float)

    def lie_derivatives(self, x: np.ndarray) -> tuple[float, float]:
        """
        Return (Lf h, Lg h) where
            Lf h = grad_h · f(x) = -v   (for this plant)
            Lg h = grad_h · g(x) = 0    (relative degree 2 — see note)
        """
        grad = self.grad_h(x)
        Lf = float(grad @ self.plant.f(x))
        Lg = float(grad @ self.plant.g(x).reshape(2))
        return Lf, Lg

    def constraint_coeffs(self, x: np.ndarray) -> tuple[float, float]:
        """
        CBF inequality: Lf h + Lg h u >= -k_cbf h
        <=>  -Lg h * u <= Lf h + k_cbf h
        Returns (a, b) for a * u <= b  (standard QP inequality form).

        Note: for the double-integrator-like plant, Lg h = 0 (relative
        degree 2). We therefore use the exponential CBF form on the
        first derivative classically, but also expose a high-relative-
        degree fallback via velocity-aware extended barrier when Lg h≈0.
        See `extended_constraint_coeffs`.
        """
        Lf, Lg = self.lie_derivatives(x)
        hx = self.h(x)
        # Lf + Lg u >= -k_cbf h  =>  -Lg u <= Lf + k_cbf h
        a = -Lg
        b = Lf + self.k_cbf * hx
        return a, b

    def extended_constraint_coeffs(
        self, x: np.ndarray, alpha1: float | None = None
    ) -> tuple[float, float]:
        """
        High-relative-degree CBF for position barrier on a mass.

        Safe set still {p <= p_max}. With relative degree 2,
        define psi_0 = h, psi_1 = dh/dt + alpha0 * h, and enforce
            d(psi_1)/dt + alpha1 * psi_1 >= 0
        which yields an affine constraint on u.

        With alpha0 = k_cbf:
            psi_1 = -v + k_cbf (p_max - p)
            d psi_1 / dt = -a + k_cbf (-v)  where a = A_row2·x + u
                        = -(-0.1 v + u) - k_cbf v
                        = 0.1 v - u - k_cbf v
            Require: d psi_1/dt + alpha1 psi_1 >= 0
                0.1 v - u - k_cbf v + alpha1 (-v + k_cbf h) >= 0
                -u >= -0.1 v + k_cbf v - alpha1 (-v + k_cbf h)
                u <= 0.1 v - k_cbf v + alpha1 (-v + k_cbf h) wait — rearrange:

            0.1*v - u - k_cbf*v + alpha1*psi_1 >= 0
            -u >= -0.1*v + k_cbf*v - alpha1*psi_1
            u <= 0.1*v - k_cbf*v + alpha1*psi_1

        Returns (a, b) for a*u <= b with a=1.
        """
        x = np.asarray(x, dtype=float).reshape(2)
        p, v = float(x[0]), float(x[1])
        hx = self.p_max - p
        k = self.k_cbf
        if alpha1 is None:
            alpha1 = k
        psi1 = -v + k * hx
        # u <= 0.1*v - k*v + alpha1*psi1
        # From: 0.1 v - u - k v + alpha1 psi1 >= 0
        upper = 0.1 * v - k * v + alpha1 * psi1
        # a * u <= b  with a = 1, b = upper
        return 1.0, float(upper)
