"""Control Lyapunov Function (CLF) for exponential convergence to x_c."""

from __future__ import annotations

import numpy as np

from .plant import Plant


def default_P() -> np.ndarray:
    """
    Positive-definite P for V(x) = (x - x_c)^T P (x - x_c).

    PD-like matrix with mild p–v coupling so Lg V = (2 P e)^T B is nonzero
    for pure position errors (diagonal P would give Lg V = 0 when v = v_c).
    Eigenvalues remain positive. Documented default for the package.
    """
    return np.array([[2.0, 0.5], [0.5, 1.0]], dtype=float)


class ControlLyapunovFunction:
    """
    Classical CLF:
        V(x) = (x - x_c)^T P (x - x_c)
        constraint: Lf V + Lg V u <= -k_lyap V(x)
    """

    def __init__(
        self,
        plant: Plant,
        x_c: np.ndarray,
        P: np.ndarray | None = None,
        k_lyap: float = 1.0,
    ) -> None:
        self.plant = plant
        self.x_c = np.asarray(x_c, dtype=float).reshape(2)
        self.P = np.asarray(P if P is not None else default_P(), dtype=float)
        self.k_lyap = float(k_lyap)
        # Ensure P is symmetric positive definite (soft check)
        eig = np.linalg.eigvalsh(0.5 * (self.P + self.P.T))
        if np.any(eig <= 0):
            raise ValueError(f"P must be positive definite; eigenvalues={eig}")

    def V(self, x: np.ndarray) -> float:
        e = np.asarray(x, dtype=float).reshape(2) - self.x_c
        return float(e @ self.P @ e)

    def grad_V(self, x: np.ndarray) -> np.ndarray:
        """dV/dx = 2 P (x - x_c)."""
        e = np.asarray(x, dtype=float).reshape(2) - self.x_c
        return 2.0 * (self.P @ e)

    def lie_derivatives(self, x: np.ndarray) -> tuple[float, float]:
        """
        Return (Lf V, Lg V) where
            Lf V = grad_V · f(x)
            Lg V = grad_V · g(x)  (scalar for SISO)
        """
        grad = self.grad_V(x)
        Lf = float(grad @ self.plant.f(x))
        Lg = float(grad @ self.plant.g(x).reshape(2))
        return Lf, Lg

    def constraint_coeffs(self, x: np.ndarray) -> tuple[float, float]:
        """
        CLF inequality: Lg V * u <= -k_lyap V - Lf V
        Returns (a, b) for a * u <= b  (used by QP).
        """
        Lf, Lg = self.lie_derivatives(x)
        V = self.V(x)
        # Lg u <= -k_lyap V - Lf
        a = Lg
        b = -self.k_lyap * V - Lf
        return a, b
