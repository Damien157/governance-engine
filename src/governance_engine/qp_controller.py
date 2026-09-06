"""CLF-CBF Quadratic Program controller via CVXOPT."""

from __future__ import annotations

import numpy as np
from cvxopt import matrix, solvers

from .cbf import ControlBarrierFunction
from .clf import ControlLyapunovFunction

# Quiet CVXOPT
solvers.options["show_progress"] = False


class CLFCBFQPController:
    """
    QP inner loop (Damien O Driscoll notes):

        min_u  (u - u_nom)^2
        s.t.   CLF:  Lf V + Lg V u  <= -k_lyap V     (soft / relaxable)
               CBF:  high-order form keeping {h>=0}   (hard)

    Standard QP:  1/2 u^T H u + f^T u  with H=2, f=-2 u_nom.

    If CLF and CBF conflict, CBF/safety wins: CLF is relaxed with a
    non-negative slack delta maximized against a large penalty.
    """

    def __init__(
        self,
        clf: ControlLyapunovFunction,
        cbf: ControlBarrierFunction,
        clf_relax_weight: float = 50.0,
        u_min: float = -50.0,
        u_max: float = 50.0,
        use_hocbf: bool = True,
    ) -> None:
        self.clf = clf
        self.cbf = cbf
        self.clf_relax_weight = float(clf_relax_weight)
        self.u_min = float(u_min)
        self.u_max = float(u_max)
        self.use_hocbf = bool(use_hocbf)

    def solve(self, x: np.ndarray, u_nom: float) -> dict:
        """
        Solve the CLF-CBF-QP at state x with nominal input u_nom.

        Decision variables: z = [u, delta] where delta >= 0 relaxes CLF.
        Cost: (u - u_nom)^2 + w * delta^2
            = 1/2 z^T H z + f^T z  (constant dropped)
        with H = diag(2, 2w), f = [-2 u_nom, 0].
        """
        x = np.asarray(x, dtype=float).reshape(2)
        u_nom = float(u_nom)
        w = self.clf_relax_weight

        # --- CLF: Lg V * u - delta <= -k_lyap V - Lf V
        Lf_V, Lg_V = self.clf.lie_derivatives(x)
        V = self.clf.V(x)
        # Lg_V * u - delta <= -k_lyap V - Lf_V
        # row: [Lg_V, -1] z <= -k_lyap V - Lf_V

        # --- CBF (hard)
        Lf_h, Lg_h = self.cbf.lie_derivatives(x)
        if self.use_hocbf or abs(Lg_h) < 1e-12:
            # Relative-degree-2 HOCBF: a_cbf * u <= b_cbf with a_cbf=1
            a_cbf, b_cbf = self.cbf.extended_constraint_coeffs(x)
            # a_cbf * u + 0*delta <= b_cbf
        else:
            # Classic: -Lg_h * u <= Lf_h + k_cbf h
            a_cbf, b_cbf = self.cbf.constraint_coeffs(x)

        # Inequalities G z <= h  for z = [u, delta]
        # 1) CLF relaxed: Lg_V u - delta <= -k_lyap V - Lf_V
        # 2) CBF hard:    a_cbf u        <= b_cbf
        # 3) delta >= 0:  -delta         <= 0
        # 4) u bounds:    u <= u_max, -u <= -u_min
        G_rows = [
            [Lg_V, -1.0],
            [a_cbf, 0.0],
            [0.0, -1.0],
            [1.0, 0.0],
            [-1.0, 0.0],
        ]
        h_rows = [
            -self.clf.k_lyap * V - Lf_V,
            b_cbf,
            0.0,
            self.u_max,
            -self.u_min,
        ]

        H = matrix([[2.0, 0.0], [0.0, 2.0 * w]])
        f = matrix([-2.0 * u_nom, 0.0])
        G_np = np.array(G_rows, dtype=float)
        h_np = np.array(h_rows, dtype=float)
        G = matrix(G_np)
        h_mat = matrix(h_np)

        try:
            sol = solvers.qp(H, f, G, h_mat)
        except Exception as exc:  # pragma: no cover
            return {
                "u": u_nom,
                "delta": 0.0,
                "status": f"error:{exc}",
                "feasible": False,
                "u_nom": u_nom,
                "h": self.cbf.h(x),
                "V": V,
            }

        status = sol["status"]
        if status != "optimal" or sol["x"] is None:
            # Fallback: try CBF-only (drop CLF) for safety
            return self._solve_cbf_only(x, u_nom, a_cbf, b_cbf, V, status)

        z = np.array(sol["x"]).reshape(2)
        return {
            "u": float(z[0]),
            "delta": float(z[1]),
            "status": status,
            "feasible": True,
            "u_nom": u_nom,
            "h": self.cbf.h(x),
            "V": V,
            "Lf_V": Lf_V,
            "Lg_V": Lg_V,
            "Lf_h": Lf_h,
            "Lg_h": Lg_h,
        }

    def _solve_cbf_only(
        self,
        x: np.ndarray,
        u_nom: float,
        a_cbf: float,
        b_cbf: float,
        V: float,
        prior_status: str,
    ) -> dict:
        """Safety-only QP: min (u-u_nom)^2 s.t. CBF and bounds."""
        # Single variable u: 1/2 * 2 u^2 - 2 u_nom u
        H = matrix([[2.0]])
        f = matrix([-2.0 * u_nom])
        G_np = np.array(
            [[a_cbf], [1.0], [-1.0]],
            dtype=float,
        )
        h_np = np.array([b_cbf, self.u_max, -self.u_min], dtype=float)
        sol = solvers.qp(H, f, matrix(G_np), matrix(h_np))
        if sol["status"] != "optimal" or sol["x"] is None:
            # Last resort: clamp to feasible CBF bound if a_cbf > 0
            u_safe = u_nom
            if abs(a_cbf) > 1e-12:
                # a u <= b => u <= b/a if a>0, u >= b/a if a<0
                if a_cbf > 0:
                    u_safe = min(u_nom, b_cbf / a_cbf)
                else:
                    u_safe = max(u_nom, b_cbf / a_cbf)
            u_safe = float(np.clip(u_safe, self.u_min, self.u_max))
            return {
                "u": u_safe,
                "delta": float("nan"),
                "status": f"fallback_after:{prior_status}",
                "feasible": False,
                "u_nom": u_nom,
                "h": self.cbf.h(x),
                "V": V,
            }
        u = float(np.array(sol["x"]).ravel()[0])
        return {
            "u": u,
            "delta": float("nan"),
            "status": f"cbf_only_after:{prior_status}",
            "feasible": True,
            "u_nom": u_nom,
            "h": self.cbf.h(x),
            "V": V,
        }
