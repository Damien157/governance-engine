"""CLF-CBF Quadratic Program controller via CVXOPT."""

from __future__ import annotations

import math

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

    Fail-closed contract
    --------------------
    ``solve`` never returns the raw nominal input on an error path.  If the
    solver raises, returns a non-optimal status, produces a non-finite
    input, or the hard barrier row is infeasible inside the input box, the
    command comes from :meth:`_fail_closed_command` (derivation there) and
    the status is never ``"optimal"``.
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

        # Hard barrier row infeasible inside the input box: no admissible u
        # exists, and CVXOPT would raise ValueError('domain error') here.
        # Skip the solver and brake as hard as the box allows.
        if self._barrier_infeasible(a_cbf, b_cbf):
            return self._fail_closed_result(x, u_nom, a_cbf, b_cbf, V, "infeasible_barrier")

        try:
            sol = solvers.qp(H, f, G, h_mat)
        except Exception as exc:
            # Fail closed: NEVER hand back the unfiltered nominal input.
            return self._fail_closed_result(x, u_nom, a_cbf, b_cbf, V, f"error:{exc}")

        status = sol["status"]
        if status != "optimal" or sol["x"] is None:
            # Fallback: try CBF-only (drop CLF) for safety
            return self._solve_cbf_only(x, u_nom, a_cbf, b_cbf, V, status)

        z = np.array(sol["x"]).reshape(2)
        if not np.all(np.isfinite(z)):
            return self._fail_closed_result(x, u_nom, a_cbf, b_cbf, V, "error:non_finite_solution")
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
        try:
            sol = solvers.qp(H, f, matrix(G_np), matrix(h_np))
        except Exception as exc:
            # e.g. CVXOPT ValueError('domain error') on an infeasible row.
            # Previously this escaped solve() and the last-resort clamp below
            # was unreachable.
            return self._fail_closed_result(
                x, u_nom, a_cbf, b_cbf, V, f"fallback_after:{prior_status}|{exc}"
            )
        sol_u = None
        if sol["status"] == "optimal" and sol["x"] is not None:
            sol_u = float(np.array(sol["x"]).ravel()[0])
            if not math.isfinite(sol_u):
                sol_u = None
        if sol_u is None:
            # Last resort: clamp to feasible CBF bound
            return self._fail_closed_result(
                x, u_nom, a_cbf, b_cbf, V, f"fallback_after:{prior_status}"
            )
        return {
            "u": sol_u,
            "delta": float("nan"),
            "status": f"cbf_only_after:{prior_status}",
            "feasible": True,
            "u_nom": u_nom,
            "h": self.cbf.h(x),
            "V": V,
        }

    # ------------------------------------------------------------------
    # Fail-closed fallback
    # ------------------------------------------------------------------

    _ROW_EPS = 1e-12

    def _barrier_infeasible(self, a_cbf: float, b_cbf: float) -> bool:
        """True iff {u in [u_min, u_max] : a_cbf u <= b_cbf} is empty."""
        if not (math.isfinite(a_cbf) and math.isfinite(b_cbf)):
            return False  # let the solver / fail-closed path deal with it
        if abs(a_cbf) <= self._ROW_EPS:
            return b_cbf < 0.0  # 0 <= b violated, no input can fix it
        bound = b_cbf / a_cbf
        if a_cbf > 0:
            return bound < self.u_min
        return bound > self.u_max

    def _fail_closed_command(self, u_nom: float, a_cbf: float, b_cbf: float) -> float:
        """
        Safe input when the QP cannot be trusted.  Pure arithmetic, no solver.

        Math (from the real barrier in cbf.py).  h(x) = p_max - p, relative
        degree 2, psi_1 = dh/dt + k h = -v + k h, and the HOCBF condition
        ``psi_1' + alpha_1 psi_1 >= 0`` is, for xdot = [v, -0.1 v + u],
        exactly the affine row ``a_cbf * u <= b_cbf`` with a_cbf = 1 and
        b_cbf = 0.1 v - k v + alpha_1 psi_1.  For the plant,
        ``psi_1' + alpha_1 psi_1 = b_cbf - u``, strictly DECREASING in u:
        a larger u accelerates the mass into the barrier p = p_max.

        * Row feasible in the box (``b_cbf/a_cbf >= u_min`` for a_cbf > 0):
          u = clip(min(u_nom, b_cbf/a_cbf), u_min, u_max).  Every u <= b/a
          satisfies the HOCBF row, so {psi_1 >= 0} (hence {h >= 0}) stays
          forward invariant, and u is the admissible input closest to u_nom.
        * Row infeasible in the box (b/a < u_min): no admissible input exists.
          The clip then lands on the braking extreme u_min.  This maximises
          ``psi_1' + alpha_1 psi_1 = b - u`` over the box, and because the
          impulse response of the plant (e^{-0.1 t} > 0) is positive, the
          input u(.) = u_min gives the pointwise smallest position p(t), i.e.
          the largest h(t), among all input signals in the box.
        * For a_cbf < 0 (classic row, mirrored): u = clip(max(u_nom, b/a)),
          braking extreme u_max when infeasible.
        * |a_cbf| ~ 0: the row does not depend on u, so no input can help;
          return 0 clipped to the box (never u_nom).
        * Non-finite u_nom is replaced by 0; a non-finite b_cbf (NaN state)
          yields the braking extreme.
        """
        u_ref = u_nom if math.isfinite(u_nom) else 0.0
        if abs(a_cbf) <= self._ROW_EPS or not math.isfinite(a_cbf):
            u_safe = 0.0
        elif not math.isfinite(b_cbf):
            u_safe = self.u_min if a_cbf > 0 else self.u_max
        else:
            bound = b_cbf / a_cbf
            u_safe = min(u_ref, bound) if a_cbf > 0 else max(u_ref, bound)
        return float(np.clip(u_safe, self.u_min, self.u_max))

    def _fail_closed_result(
        self,
        x: np.ndarray,
        u_nom: float,
        a_cbf: float,
        b_cbf: float,
        V: float,
        status: str,
    ) -> dict:
        """Result dict for every non-optimal path; ``status`` is never 'optimal'."""
        return {
            "u": self._fail_closed_command(u_nom, a_cbf, b_cbf),
            "delta": float("nan"),
            "status": status,
            "feasible": False,
            "fail_closed": True,
            "u_nom": u_nom,
            "h": self.cbf.h(x),
            "V": V,
        }
