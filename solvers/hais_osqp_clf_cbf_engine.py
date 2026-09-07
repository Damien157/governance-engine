"""HAIS-grade OSQP CLF–CBF safety filter + closed-loop harness (sketch).

Alternate to live CVXOPT CLFCBFQPController in src/governance_engine/.
Keep off govern()/mail/calendar.
"""
import numpy as np
import scipy.sparse as sp
import osqp
import logging
from dataclasses import dataclass
from typing import Tuple, Optional, Callable, List
import numpy.typing as npt

# ----------------------------------------------------------------------
# Logging
# ----------------------------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("GovernanceEngine")

# ----------------------------------------------------------------------
# HAIS-Grade CLF–CBF Safety Filter Engine
# ----------------------------------------------------------------------
class GovernanceEngine:
    """
    HAIS-Grade CLF-CBF Safety Filter Engine.

    - Hard safety (CBF), soft stability (CLF via slack).
    - Handles relative degree via caller-provided A_cbf, b_cbf.
    - Detects structurally impossible CBF rows (including NaN/Inf).
    - Uses governed fail-safe modes instead of naive clipping.
    - Keeps OSQP structure stable (fixed max constraints, fixed sparsity).
    """

    def __init__(
        self,
        dim_u: int,
        max_cbf_constraints: int,
        alpha: float = 1.0,
        lambda_slack: float = 1e3,
    ):
        self.dim_u = dim_u
        self.alpha = alpha
        self.lambda_slack = lambda_slack
        self.max_cbf_constraints = max_cbf_constraints

        # Variables: u (dim_u) + delta (1 slack for CLF)
        self.n_vars = dim_u + 1

        # Constraints: max_cbf_constraints (CBF) + 1 (CLF)
        self.n_constraints = max_cbf_constraints + 1

        # OSQP solver
        self.solver = osqp.OSQP()
        self._is_setup = False

        # Governance fail-safe tracking
        self.failsafe_active = False
        self.failsafe_steps = 0
        self.failsafe_escalated = False
        self.failsafe_escalation_threshold = 50

        # Constraint template indexing
        self._A_row_idx: npt.NDArray[np.int64]
        self._A_col_idx: npt.NDArray[np.int64]

        # Pre-build templates
        self._P_template = self._build_objective_template()
        self._A_template = self._build_constraint_template()

    def _build_objective_template(self) -> sp.csc_matrix:
        """
        P = diag([I_dim_u, lambda_slack]) for cost:
        0.5 * ||u - u_nom||^2 + 0.5 * lambda_slack * delta^2
        """
        P_dense = np.zeros((self.n_vars, self.n_vars))
        P_dense[:self.dim_u, :self.dim_u] = np.eye(self.dim_u)
        P_dense[self.dim_u, self.dim_u] = self.lambda_slack
        return sp.csc_matrix(P_dense)

    def _build_constraint_template(self) -> sp.csc_matrix:
        """
        Fixed sparsity template for A:
        - First max_cbf_constraints rows: CBF (A_cbf * u <= b_cbf)
        - Last row: CLF (LfV + LgV u + alpha V <= delta)
        """
        rows, cols = [], []
        for r in range(self.max_cbf_constraints):
            for c in range(self.dim_u):
                rows.append(r)
                cols.append(c)
        clf_row = self.max_cbf_constraints
        for c in range(self.dim_u + 1):
            rows.append(clf_row)
            cols.append(c)

        placeholder = np.full(len(rows), 1e-20)
        A = sp.csc_matrix(
            (placeholder, (rows, cols)),
            shape=(self.n_constraints, self.n_vars),
        )
        A.sort_indices()

        coo = A.tocoo()
        self._A_row_idx = coo.row.copy()
        self._A_col_idx = coo.col.copy()
        return A

    def _extract_A_values(
        self, A_dense: npt.NDArray[np.float64]
    ) -> npt.NDArray[np.float64]:
        return A_dense[self._A_row_idx, self._A_col_idx]

    def _setup_solver(
        self,
        q: npt.NDArray[np.float64],
        A_dense: npt.NDArray[np.float64],
        l_vec: npt.NDArray[np.float64],
        u_vec: npt.NDArray[np.float64],
    ) -> None:
        P = self._P_template.copy()
        values = self._extract_A_values(A_dense)
        A = sp.csc_matrix(
            (values, (self._A_row_idx, self._A_col_idx)),
            shape=(self.n_constraints, self.n_vars),
        )
        A.sort_indices()
        self.solver.setup(P=P, q=q, A=A, l=l_vec, u=u_vec,
                          verbose=False, polish=True)
        self._is_setup = True

    def _update_solver(
        self,
        q: npt.NDArray[np.float64],
        A_dense: npt.NDArray[np.float64],
        l_vec: npt.NDArray[np.float64],
        u_vec: npt.NDArray[np.float64],
    ) -> None:
        values = self._extract_A_values(A_dense)
        self.solver.update(q=q, Ax=values, l=l_vec, u=u_vec)

    def _check_cbf_structure(
        self,
        A_cbf: npt.NDArray[np.float64],
        b_cbf: npt.NDArray[np.float64],
    ) -> Optional[str]:
        if not np.all(np.isfinite(A_cbf)) or not np.all(np.isfinite(b_cbf)):
            logger.error("Non-finite CBF coefficients or bounds detected.")
            return "NON_FINITE_CBF_INPUT"

        n_cbf = A_cbf.shape[0]
        for i in range(n_cbf):
            row = A_cbf[i]
            if np.allclose(row, 0.0) and b_cbf[i] < 0.0:
                logger.error(
                    f"Structural CBF violation at row {i}: "
                    f"0 * u <= {b_cbf[i]} is impossible."
                )
                return "STRUCTURAL_CBF_VIOLATION"
        return None

    def _enter_failsafe(self) -> Tuple[npt.NDArray[np.float64], str]:
        self.failsafe_active = True
        self.failsafe_steps += 1

        if self.failsafe_steps >= self.failsafe_escalation_threshold:
            if not self.failsafe_escalated:
                logger.critical(
                    "Fail-safe has persisted beyond threshold. "
                    "Escalating to higher-level supervisor."
                )
                self.failsafe_escalated = True

        logger.warning(
            f"Fail-safe triggered. Steps in fail-safe: {self.failsafe_steps}."
        )

        return np.zeros(self.dim_u), "FAIL_SAFE_TRIGGERED"

    def reset_failsafe(self) -> None:
        self.failsafe_active = False
        self.failsafe_steps = 0
        self.failsafe_escalated = False

    def solve(
        self,
        u_nom: npt.NDArray[np.float64],
        A_cbf: npt.NDArray[np.float64],
        b_cbf: npt.NDArray[np.float64],
        V_x: float,
        lf_v: float,
        lg_v: npt.NDArray[np.float64],
    ) -> Tuple[npt.NDArray[np.float64], str]:
        """
        Solve CLF–CBF QP:

        Minimize:
            0.5 * ||u - u_nom||^2 + 0.5 * lambda_slack * delta^2

        Subject to:
            CBF: A_cbf u <= b_cbf   (hard safety)
            CLF: LfV + LgV u + alpha V_x <= delta  (soft stability)
                 => LgV u - delta <= -LfV - alpha V_x
        """
        n_cbf = A_cbf.shape[0]
        if n_cbf > self.max_cbf_constraints:
            logger.error(
                f"Number of CBF constraints ({n_cbf}) exceeds "
                f"max_cbf_constraints ({self.max_cbf_constraints})."
            )
            return self._enter_failsafe()

        if A_cbf.shape[1] != self.dim_u:
            logger.error(
                f"A_cbf has wrong number of columns: {A_cbf.shape[1]} "
                f"(expected {self.dim_u})."
            )
            return self._enter_failsafe()

        if b_cbf.shape[0] != n_cbf:
            logger.error(
                f"b_cbf length ({b_cbf.shape[0]}) does not match "
                f"A_cbf row count ({n_cbf})."
            )
            return self._enter_failsafe()

        if lg_v.shape[0] != self.dim_u:
            logger.error(
                f"lg_v has wrong length: {lg_v.shape[0]} "
                f"(expected {self.dim_u})."
            )
            return self._enter_failsafe()

        structural_issue = self._check_cbf_structure(A_cbf, b_cbf)
        if structural_issue is not None:
            return self._enter_failsafe()

        if not (np.isfinite(V_x) and np.isfinite(lf_v)
                and np.all(np.isfinite(lg_v)) and np.all(np.isfinite(u_nom))):
            logger.error("Non-finite CLF input or nominal control detected.")
            return self._enter_failsafe()

        # Objective vector q
        q = np.zeros(self.n_vars)
        q[:self.dim_u] = -u_nom

        # Constraint arrays
        A_dense = np.zeros((self.n_constraints, self.n_vars))
        l_vec = np.full(self.n_constraints, -np.inf)
        u_vec = np.full(self.n_constraints, np.inf)

        # CBF: A_cbf u <= b_cbf
        A_dense[:n_cbf, :self.dim_u] = A_cbf
        u_vec[:n_cbf] = b_cbf

        # CLF: LgV u - delta <= -LfV - alpha V_x
        clf_row = self.max_cbf_constraints
        A_dense[clf_row, :self.dim_u] = lg_v
        A_dense[clf_row, self.dim_u] = -1.0
        u_vec[clf_row] = -lf_v - self.alpha * V_x

        try:
            if not self._is_setup:
                self._setup_solver(q=q, A_dense=A_dense, l_vec=l_vec, u_vec=u_vec)
            else:
                self._update_solver(q=q, A_dense=A_dense, l_vec=l_vec, u_vec=u_vec)
            results = self.solver.solve()
        except Exception:
            logger.exception(
                "OSQP raised an exception during setup/update/solve. "
                "Entering governed fail-safe mode."
            )
            return self._enter_failsafe()

        if str(getattr(results.info, "status", "")).lower() not in ("solved", "optimal"):
            logger.error(
                f"QP Solver failed. Status: {results.info.status}. "
                "Entering governed fail-safe mode."
            )
            return self._enter_failsafe()

        u_opt = results.x[:self.dim_u]

        if not np.all(np.isfinite(u_opt)):
            logger.error(
                "Solver reported 'solved' but returned non-finite u. "
                "Entering governed fail-safe mode."
            )
            return self._enter_failsafe()

        if self.failsafe_active:
            logger.info("Exiting fail-safe mode; solver returned optimal solution.")
            self.reset_failsafe()

        return u_opt, "OPTIMAL_SAFE"

# ----------------------------------------------------------------------
# Closed-loop verification harness
# ----------------------------------------------------------------------
@dataclass
class Trajectory:
    xs: np.ndarray        # (T+1, state_dim)
    us: np.ndarray        # (T, dim_u)
    statuses: List[str]   # length T, engine.solve() status per step

def run_closed_loop(engine,
                    dynamics,
                    cbf,
                    clf,
                    nominal_controller,
                    x0,
                    steps: int) -> Trajectory:
    """
    Closed-loop test:
      1. nominal_controller(x) -> u_nom
      2. cbf.build(x) -> (A_cbf, b_cbf)
      3. clf.build(x) -> (V_x, LfV, LgV)
      4. engine.solve(...) -> u_opt
      5. dynamics.step(x, u_opt) -> x_next
    """
    x = np.asarray(x0, dtype=float)
    xs = [x.copy()]
    us = []
    statuses = []
    for _ in range(steps):
        u_nom = nominal_controller(x)
        A_cbf, b_cbf = cbf.build(x)
        V_x, lf_v, lg_v = clf.build(x)
        u_opt, status = engine.solve(u_nom, A_cbf, b_cbf, V_x, lf_v, lg_v)
        x = dynamics.step(x, u_opt)
        xs.append(x.copy())
        us.append(np.asarray(u_opt).copy())
        statuses.append(status)
    return Trajectory(xs=np.array(xs), us=np.array(us), statuses=statuses)

def forward_invariant(traj: Trajectory,
                      h_fn: Callable[[np.ndarray], float],
                      tol: float = 1e-6) -> bool:
    """True iff h(x) >= -tol along the trajectory."""
    return all(h_fn(x) >= -tol for x in traj.xs)

def min_barrier_value(traj: Trajectory,
                      h_fn: Callable[[np.ndarray], float]) -> float:
    """Minimum barrier value along trajectory."""
    return min(h_fn(x) for x in traj.xs)

# ----------------------------------------------------------------------
# Example: Double integrator + CLF/CBF maths
# ----------------------------------------------------------------------
class DoubleIntegratorDynamics:
    """
    x = [p, v], u = acceleration
    p_{k+1} = p_k + dt * v_k
    v_{k+1} = v_k + dt * u_k
    """
    def __init__(self, dt: float = 0.01):
        self.dt = dt

    def step(self, x: np.ndarray, u: np.ndarray) -> np.ndarray:
        p, v = x
        a = u[0]
        p_next = p + self.dt * v
        v_next = v + self.dt * a
        return np.array([p_next, v_next])

class SurrogatePositionCBFBroken:
    """
    Legacy illustrative surrogate (NOT forward-invariant under closed-loop QP).

    Kept for regression: documents the pre-0.4.4 bug surface. Prefer PositionCBF.
    """
    def __init__(self, p_max: float, k1: float = 1.0, k2: float = 1.0, alpha: float = 1.0):
        self.p_max = p_max
        self.k1 = k1
        self.k2 = k2
        self.alpha = alpha

    def h(self, x: np.ndarray) -> float:
        p = x[0]
        return self.p_max - abs(p)

    def build(self, x: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        p, v = x
        h_x = self.h(x)
        A_cbf = np.array([[-1.0]])
        b_cbf = np.array([-(self.k1 * p + self.k2 * v - self.alpha * h_x)])
        return A_cbf, b_cbf


class PositionCBF:
    """
    Higher-order CBF for double-integrator position limit |p| <= p_max.

    Barrier: h(x) = p_max - |p| (relative degree 2).
    Enforce ψ̇ + α₁ ψ ≥ 0 with ψ = ḣ + α₀ h, which is linear in u:

      p ≥ 0:  u ≤ -(α₀+α₁)v + α₀α₁ (p_max - p)
      p < 0: -u ≤  (α₀+α₁)v + α₀α₁ (p_max + p)

    Closed-loop under the OSQP CLF-CBF QP is forward-invariant for the demo
    (sketch only — not wired into live govern()).
    """
    def __init__(self, p_max: float, alpha0: float = 2.0, alpha1: float = 2.0):
        self.p_max = p_max
        self.alpha0 = float(alpha0)
        self.alpha1 = float(alpha1)

    def h(self, x: np.ndarray) -> float:
        p = x[0]
        return self.p_max - abs(p)

    def build(self, x: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        p, v = float(x[0]), float(x[1])
        a0, a1 = self.alpha0, self.alpha1
        if p >= 0.0:
            # u <= -(a0+a1)*v + a0*a1*(p_max - p)
            A_cbf = np.array([[1.0]])
            b_cbf = np.array([-(a0 + a1) * v + (a0 * a1) * (self.p_max - p)])
        else:
            # -u <= (a0+a1)*v + a0*a1*(p_max + p)
            A_cbf = np.array([[-1.0]])
            b_cbf = np.array([(a0 + a1) * v + (a0 * a1) * (self.p_max + p)])
        return A_cbf, b_cbf

class QuadraticCLF:
    """
    CLF: V(x) = 0.5 * (p^2 + v^2)
    Dynamics: x_dot = [v, u]
    LfV = grad V · f(x) = [p, v] · [v, 0] = p*v
    LgV = grad V · g(x) = [p, v] · [0, 1] = v
    """
    def __init__(self, alpha: float = 1.0):
        self.alpha = alpha

    def build(self, x: np.ndarray) -> Tuple[float, float, np.ndarray]:
        p, v = x
        V_x = 0.5 * (p**2 + v**2)
        lf_v = p * v
        lg_v = np.array([v])
        return V_x, lf_v, lg_v

def nominal_pd_controller(x: np.ndarray,
                          kp: float = 2.0,
                          kd: float = 1.0) -> np.ndarray:
    """
    Simple PD controller: u_nom = -kp * p - kd * v
    """
    p, v = x
    u_nom = -kp * p - kd * v
    return np.array([u_nom])

# ----------------------------------------------------------------------
# Example main: unify everything
# ----------------------------------------------------------------------
if __name__ == "__main__":
    dim_u = 1
    max_cbf = 4
    engine = GovernanceEngine(dim_u=dim_u, max_cbf_constraints=max_cbf)

    dynamics = DoubleIntegratorDynamics(dt=0.01)
    cbf = PositionCBF(p_max=1.0, alpha0=2.0, alpha1=2.0)
    clf = QuadraticCLF(alpha=1.0)

    x0 = np.array([0.8, 0.0])  # start near boundary
    steps = 500

    traj = run_closed_loop(
        engine=engine,
        dynamics=dynamics,
        cbf=cbf,
        clf=clf,
        nominal_controller=nominal_pd_controller,
        x0=x0,
        steps=steps,
    )

    safe = forward_invariant(traj, cbf.h, tol=1e-4)
    margin = min_barrier_value(traj, cbf.h)

    print("Forward invariant:", safe)
    print("Min barrier value:", margin)
    print("Final state:", traj.xs[-1])
    print("Statuses (unique):", set(traj.statuses))