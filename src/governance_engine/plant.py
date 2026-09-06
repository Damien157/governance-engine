"""Damped 1-D mass plant: xdot = A x + B u, x = [p, v]."""

from __future__ import annotations

import numpy as np

# Continuous-time state-space matrices (Damien O Driscoll notes)
A = np.array([[0.0, 1.0], [0.0, -0.1]], dtype=float)
B = np.array([[0.0], [1.0]], dtype=float)


class Plant:
    """Linear plant xdot = A x + B u for a damped 1-D mass."""

    def __init__(
        self,
        A_mat: np.ndarray | None = None,
        B_mat: np.ndarray | None = None,
    ) -> None:
        self.A = np.asarray(A_mat if A_mat is not None else A, dtype=float)
        self.B = np.asarray(B_mat if B_mat is not None else B, dtype=float)
        if self.A.shape != (2, 2):
            raise ValueError(f"A must be 2x2, got {self.A.shape}")
        if self.B.shape != (2, 1):
            raise ValueError(f"B must be 2x1, got {self.B.shape}")

    def f(self, x: np.ndarray) -> np.ndarray:
        """Drift dynamics f(x) = A x."""
        x = np.asarray(x, dtype=float).reshape(2)
        return self.A @ x

    def g(self, x: np.ndarray | None = None) -> np.ndarray:
        """Control vector field g(x) = B (constant for LTI plant)."""
        return self.B.copy()

    def dynamics(self, x: np.ndarray, u: float) -> np.ndarray:
        """xdot = A x + B u."""
        x = np.asarray(x, dtype=float).reshape(2)
        return self.A @ x + (self.B @ np.array([u], dtype=float)).reshape(2)

    def euler_step(self, x: np.ndarray, u: float, dt: float) -> np.ndarray:
        """x_{k+1} = x_k + dt (A x_k + B u_k)."""
        return np.asarray(x, dtype=float).reshape(2) + dt * self.dynamics(x, u)
