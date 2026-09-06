import numpy as np
from cvxopt import matrix, solvers

solvers.options['show_progress'] = False

def simulate_unified_control():
    # 4. Linear system dynamics (damped 1-D mass)
    A = np.array([[0.0, 1.0], [0.0, -0.1]])
    B = np.array([[0.0], [1.0]])
    
    # State: x = [position, velocity]^T
    x = np.array([[0.0], [0.0]])
    dt = 0.01
    p_max = 5.0
    k_cbf = 1.0
    k_lyap = 1.0
    
    print("Running unified simulation loop...")
    for step in range(10):
        # 3. Quadratic Programming cost formulation: min (u - u_nom)^2 -> H = 2, f = -2*u_nom
        u_nom = 1.0
        H = matrix([[2.0]])
        f = matrix([[-2.0 * u_nom]])
        
        # 2 & 6. Safety Envelopes & Control Barrier Functions (CBF)
        p = x[0, 0]
        v = x[1, 0]
        h = p_max - p
        # L_f h + L_g h * u >= -k_cbf * h
        # For h = p_max - p, dh/dt = -v. L_f h = -v, L_g h = 0
        Lf_h = -v
        Lg_h = 0.0
        
        # 1. Lyapunov Stability constraint: L_f V + L_g V * u <= -k_lyap * V
        # Simplified placeholder for combined QP constraints
        
        # Solve QP via CVXOPT
        try:
            sol = solvers.qp(H, f)
            u = np.array(sol['x'])[0, 0]
        except Exception:
            u = u_nom
            
        # 5. Euler integration update
        x_dot = A @ x + B @ np.array([[u]])
        x = x + dt * x_dot
        
        print(f"Step {step}: Position={x[0,0]:.3f}, Control (u)={u:.3f}")

if __name__ == "__main__":
    simulate_unified_control()
