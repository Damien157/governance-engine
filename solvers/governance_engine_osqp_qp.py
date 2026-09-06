"""Sketch / alternate OSQP CLF–CBF QP (Damien paste).

Live path remains CVXOPT ``CLFCBFQPController`` in
``src/governance_engine/qp_controller.py`` (used by GovernedStack).
Keep this module off mail/calendar/govern decision path — catalog tier: sketch.
"""

import numpy as np
import scipy.sparse as sp

class GovernanceEngine:
    """
    GovernanceEngine: CLF–CBF QP with actuator limits and failsafe.

    Mathematical problem (each call to solve):

        Let u ∈ R^m be the control input, u_nom the nominal control.

        We solve the QP:

            minimize_u    ||u - u_nom||^2
                         = (u - u_nom)^T (u - u_nom)

        subject to:

            1) CBF constraints (safety):
               A_cbf u <= b_cbf

               In standard CBF theory:
                   L_f h(x) + L_g h(x) u >= -α h(x)
               can be rearranged into linear form
                   A_cbf u <= b_cbf
               where A_cbf, b_cbf are provided by the caller.

            2) CLF constraint (stability):
               lg_v u <= -lf_v - α V_x

               Here:
                   V_x  = V(x)      (Lyapunov function value)
                   lf_v = L_f V(x)
                   lg_v = L_g V(x)
               and we encode:
                   A_clf = lg_v
                   b_clf = -lf_v - α V_x
               with α taken as 1.0 for simplicity.

            3) Actuator limits (hard bounds):
               u_min <= u <= u_max

               In linear inequality form:
                   u_i <= u_max_i
                   -u_i <= -u_min_i

        This is a convex QP: quadratic cost, linear constraints.
        The solution u_opt is the Euclidean projection of u_nom onto
        the convex feasible set defined by CBF, CLF, and actuator box.

    Failsafe philosophy:

        - Any infeasibility, NaN, shape mismatch, or solver non-optimal
          status triggers a failsafe:
              u_opt = 0
              status = "FAIL_SAFE_TRIGGERED"
              failsafe_active = True
              failsafe_steps += 1

        - A subsequent valid, optimal solve clears failsafe:
              failsafe_active = False
              failsafe_steps = 0
              status = "OPTIMAL_SAFE"
    """

    def __init__(
        self,
        dim_u: int,
        max_cbf_constraints: int,
        u_min: np.ndarray | None = None,
        u_max: np.ndarray | None = None,
    ):
        self.dim_u = dim_u
        self.max_cbf_constraints = max_cbf_constraints

        # Actuator bounds validation
        if u_min is not None and u_max is not None:
            if np.any(u_min > u_max):
                raise ValueError("u_min > u_max is invalid (must have u_min <= u_max)")
        self.u_min = u_min
        self.u_max = u_max

        # Solver state
        self._is_setup = False
        self.solver = None

        # Failsafe tracking
        self.failsafe_active = False
        self.failsafe_steps = 0

        # Constraint template sizes:
        # - max_cbf_constraints rows reserved for CBF
        # - 1 row reserved for CLF
        # - 2 * dim_u rows reserved for actuator limits (upper + lower),
        #   if any bounds are provided.
        self.n_cbf_rows = max_cbf_constraints
        self.n_clf_rows = 1
        self.n_act_rows = 0
        if self.u_min is not None or self.u_max is not None:
            self.n_act_rows = 2 * self.dim_u

        self.n_total_rows = self.n_cbf_rows + self.n_clf_rows + self.n_act_rows

        # Fixed dense templates; converted to CSC for OSQP.
        # These define the *structure* of the constraint matrix A and
        # bounds l, u. We only change numerical values per call, never
        # the sparsity pattern.
        self._A_template = np.zeros((self.n_total_rows, self.dim_u))
        self._l_template = -np.inf * np.ones(self.n_total_rows)
        self._u_template = np.inf * np.ones(self.n_total_rows)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def solve(self, u_nom, A_cbf, b_cbf, V_x, lf_v, lg_v):
        """
        Solve the CLF–CBF QP with actuator limits and failsafe.

        Inputs:
            u_nom : nominal control (shape (dim_u,))
            A_cbf : CBF constraint matrix (shape (k, dim_u), k <= max_cbf_constraints)
            b_cbf : CBF bounds (shape (k,))
            V_x   : scalar Lyapunov function value
            lf_v  : scalar L_f V(x)
            lg_v  : gradient L_g V(x) (shape (dim_u,))

        Returns:
            (u_opt, status_str)
            status_str ∈ {"OPTIMAL_SAFE", "FAIL_SAFE_TRIGGERED"}
        """
        u_nom = np.asarray(u_nom, dtype=float).reshape(-1)
        A_cbf = np.asarray(A_cbf, dtype=float)
        b_cbf = np.asarray(b_cbf, dtype=float).reshape(-1)
        lg_v = np.asarray(lg_v, dtype=float).reshape(-1)

        # Dimension checks
        if u_nom.shape[0] != self.dim_u:
            return self._failsafe("u_nom dimension mismatch")

        if A_cbf.ndim != 2 or A_cbf.shape[1] != self.dim_u:
            return self._failsafe("A_cbf shape mismatch")
        if A_cbf.shape[0] > self.max_cbf_constraints:
            return self._failsafe("too many CBF constraints")
        if b_cbf.shape[0] != A_cbf.shape[0]:
            return self._failsafe("b_cbf length mismatch")

        # NaN checks for CBF inputs
        if np.isnan(A_cbf).any() or np.isnan(b_cbf).any():
            return self._failsafe("NaN in CBF inputs")

        # Structural impossibility: any row 0*u <= negative bound
        for i in range(A_cbf.shape[0]):
            if np.allclose(A_cbf[i, :], 0.0) and b_cbf[i] < 0.0:
                return self._failsafe("structurally impossible CBF row")

        # Build constraint matrices/vectors into fixed templates
        A_dense, l_vec, u_vec = self._build_constraints(
            A_cbf=A_cbf,
            b_cbf=b_cbf,
            V_x=V_x,
            lf_v=lf_v,
            lg_v=lg_v,
        )

        # QP cost: minimize ||u - u_nom||^2
        # H = 2 I, f = -2 u_nom
        H = 2.0 * np.eye(self.dim_u)
        f = -2.0 * u_nom

        try:
            if not self._is_setup:
                self._setup_solver(H, f, A_dense, l_vec, u_vec)
                self._is_setup = True
            else:
                self._update_solver(H, f, A_dense, l_vec, u_vec)

            res = self.solver.solve()
        except Exception:
            return self._failsafe("solver exception")

        # Interpret solver result: accept only true optimality.
        # OSQP 1.x exposes status on res.info.status; older bindings used res.status.
        info = getattr(res, "info", None)
        status = getattr(info, "status", None) if info is not None else None
        if status is None:
            status = getattr(res, "status", "")
        status = str(status).lower()
        if status not in ("optimal", "solved"):
            return self._failsafe(f"solver status {status}")

        u_opt = np.asarray(res.x, dtype=float).reshape(-1)
        if u_opt.shape[0] != self.dim_u:
            return self._failsafe("solver returned wrong dimension")

        # Successful solve clears failsafe tracking
        self.failsafe_active = False
        self.failsafe_steps = 0
        return u_opt, "OPTIMAL_SAFE"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _build_constraints(self, A_cbf, b_cbf, V_x, lf_v, lg_v):
        """
        Fill the fixed templates (A, l, u) with current numerical values.

        Layout of rows:

            0 .. n_cbf_rows-1      : CBF constraints (A_cbf u <= b_cbf)
            n_cbf_rows             : CLF constraint (lg_v u <= -lf_v - V_x)
            n_cbf_rows+1 .. end    : actuator limits (if any)

        CBF block:
            For the first n_cbf rows, we write the actual A_cbf, b_cbf.
            Any unused rows remain as structural zeros with infinite bounds.

        CLF row:
            A_clf = lg_v
            l_clf = -inf
            u_clf = -lf_v - V_x

        Actuator limits:
            Upper bounds: u_i <= u_max_i
                A[row + i, i] = 1
                l[row + i]    = -inf
                u[row + i]    = u_max_i

            Lower bounds: -u_i <= -u_min_i
                A[base + i, i] = -1
                l[base + i]    = -inf
                u[base + i]    = -u_min_i
        """
        A = self._A_template.copy()
        l = self._l_template.copy()
        u = self._u_template.copy()

        row = 0

        # CBF rows: A_cbf u <= b_cbf
        n_cbf = A_cbf.shape[0]
        if n_cbf > 0:
            A[row : row + n_cbf, :] = A_cbf
            l[row : row + n_cbf] = -np.inf
            u[row : row + n_cbf] = b_cbf
        # Advance by full reserved CBF block (even if some rows unused)
        row += self.n_cbf_rows

        # CLF row: lg_v u <= -lf_v - V_x
        # If LgV≈0 and the bound is negative, the row is structurally impossible;
        # leave it non-binding (sketch has no CLF slack variable).
        if lg_v.shape[0] == self.dim_u:
            b_clf = -float(lf_v) - float(V_x)
            if np.allclose(lg_v, 0.0) and b_clf < 0.0:
                A[row, :] = 0.0
                l[row] = -np.inf
                u[row] = np.inf
            else:
                A[row, :] = lg_v
                l[row] = -np.inf
                u[row] = b_clf
        else:
            # If CLF gradient dimension is wrong, leave as a non-binding row.
            A[row, :] = 0.0
            l[row] = -np.inf
            u[row] = np.inf
        row += self.n_clf_rows

        # Actuator limits: if provided, encode as linear constraints
        if self.n_act_rows > 0:
            # Upper bounds: u <= u_max
            if self.u_max is not None:
                for i in range(self.dim_u):
                    A[row + i, :] = 0.0
                    A[row + i, i] = 1.0
                    l[row + i] = -np.inf
                    u[row + i] = self.u_max[i]
            else:
                for i in range(self.dim_u):
                    A[row + i, :] = 0.0
                    l[row + i] = -np.inf
                    u[row + i] = np.inf

            # Lower bounds: -u <= -u_min
            base = row + self.dim_u
            if self.u_min is not None:
                for i in range(self.dim_u):
                    A[base + i, :] = 0.0
                    A[base + i, i] = -1.0
                    l[base + i] = -np.inf
                    u[base + i] = -self.u_min[i]
            else:
                for i in range(self.dim_u):
                    A[base + i, :] = 0.0
                    l[base + i] = -np.inf
                    u[base + i] = np.inf

        return A, l, u

    def _setup_solver(self, H, f, A_dense, l_vec, u_vec):
        """
        Initial OSQP setup with fixed sparsity pattern.

        We convert H and A to CSC once and call solver.setup().
        """
        import osqp

        H_csc = sp.csc_matrix(H)
        A_csc = sp.csc_matrix(A_dense)

        self.solver = osqp.OSQP()
        self.solver.setup(
            P=H_csc,
            q=f,
            A=A_csc,
            l=l_vec,
            u=u_vec,
            verbose=False,
        )

    def _update_solver(self, H, f, A_dense, l_vec, u_vec):
        """
        Update OSQP with new numerical values but identical sparsity.

        OSQP 0.6+ ``update()`` may not accept full A/P matrix replacement the
        same way as setup; on failure, fall back to a fresh ``setup()``.
        """
        H_csc = sp.csc_matrix(H)
        A_csc = sp.csc_matrix(A_dense)

        try:
            self.solver.update(
                P=H_csc,
                q=f,
                A=A_csc,
                l=l_vec,
                u=u_vec,
            )
        except Exception:
            # Re-setup if update rejects full matrix replacement / API mismatch.
            self._setup_solver(H, f, A_dense, l_vec, u_vec)

    def _failsafe(self, reason: str):
        """
        Enter failsafe mode: return zero control and track the episode.
        """
        self.failsafe_active = True
        self.failsafe_steps += 1
        u_opt = np.zeros(self.dim_u, dtype=float)
        return u_opt, "FAIL_SAFE_TRIGGERED"


if __name__ == "__main__":
    import sys

    def _run_self_tests() -> int:
        try:
            import osqp  # noqa: F401
            osqp_ok = True
        except ImportError:
            osqp_ok = False

        # u_min > u_max raises ValueError
        try:
            GovernanceEngine(
                dim_u=1,
                max_cbf_constraints=2,
                u_min=np.array([1.0]),
                u_max=np.array([-1.0]),
            )
            print("FAIL: expected ValueError for u_min > u_max")
            return 1
        except ValueError:
            print("ok: u_min > u_max raises ValueError")

        eng = GovernanceEngine(
            dim_u=1,
            max_cbf_constraints=2,
            u_min=np.array([-1.0]),
            u_max=np.array([1.0]),
        )

        # Feasible case
        u_opt, status = eng.solve(
            u_nom=0.5,
            A_cbf=np.array([[0.0]]),
            b_cbf=np.array([1.0]),
            V_x=1.0,
            lf_v=0.0,
            lg_v=np.array([0.0]),
        )
        if osqp_ok:
            if status != "OPTIMAL_SAFE":
                print(f"FAIL: expected OPTIMAL_SAFE, got {status!r} u={u_opt}")
                return 1
            print(f"ok: feasible -> {status} u={u_opt}")
        else:
            if status != "FAIL_SAFE_TRIGGERED":
                print(f"FAIL: without osqp expected FAIL_SAFE_TRIGGERED, got {status!r}")
                return 1
            print(f"ok: osqp missing -> failsafe {status}")

        # NaN CBF -> FAIL_SAFE_TRIGGERED
        eng2 = GovernanceEngine(
            dim_u=1,
            max_cbf_constraints=2,
            u_min=np.array([-1.0]),
            u_max=np.array([1.0]),
        )
        _, status_nan = eng2.solve(
            u_nom=0.5,
            A_cbf=np.array([[np.nan]]),
            b_cbf=np.array([1.0]),
            V_x=1.0,
            lf_v=0.0,
            lg_v=np.array([0.0]),
        )
        if status_nan != "FAIL_SAFE_TRIGGERED":
            print(f"FAIL: NaN CBF expected FAIL_SAFE_TRIGGERED, got {status_nan!r}")
            return 1
        print(f"ok: NaN CBF -> {status_nan}")
        print("all self-tests passed")
        return 0

    sys.exit(_run_self_tests())
