#!/usr/bin/env python3
"""
HAIS-Protected Unified Framework: 3D Taylor-Green Vortex 
with Full Measurement, Multi-Objective CLF-CBF Control, Adaptive Reynolds, 
Active ML Controller, and Governance Audit Logging.
"""

import numpy as np
import dedalus.public as d3
import logging
import json
import time

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# ---------------- Parameters & HAIS Configuration ----------------
N = 64
L = 2 * np.pi
Reynolds = 800
nu = 1.0 / Reynolds
dealias = 3 / 2
stop_sim_time = 20
timestepper = d3.RK222
max_timestep = 1e-2
dtype = np.float64

# --- HAIS Safety & Control Parameters ---
enable_control = True
target_energy = 0.25
control_gain = 0.5
clf_gain = 0.1
enstrophy_limit = 0.5
cbf_gain = 0.05

# Multi-objective weights
w_energy = 1.0
w_enstrophy = 0.8
w_dissipation = 0.2

# Adaptive Reynolds control
enable_adaptive_Re = True
Re_min, Re_max = 400, 2000
Re_target_enstrophy = 0.6

# Active ML Surrogate Controller Hook
enable_ml_controller = True

class HAISSurrogateController:
    """Lightweight neural/surrogate feedback controller module."""
    def __init__(self, input_dim=4, output_scale=0.01):
        self.weights = np.random.randn(3, input_dim) * output_scale
        
    def __call__(self, state_dict):
        features = np.array([
            state_dict["energy"],
            state_dict["enstrophy"],
            state_dict["dissipation"],
            state_dict["Re"]
        ])
        # Linear surrogate action mapping
        action_factor = np.tanh(np.dot(self.weights, features))
        return action_factor

ml_controller = HAISSurrogateController() if enable_ml_controller else None

# ---------------- Governance Audit Logger ----------------
class HAISAuditLogger:
    def __init__(self, filename="hais_audit_trail.jsonl"):
        self.filename = filename
        self.file = open(filename, "w")
        
    def log(self, record):
        self.file.write(json.dumps(record) + "\n")
        self.file.flush()
        
    def close(self):
        self.file.close()

audit_logger = HAISAuditLogger()

# ---------------- Bases: Triply Periodic ----------------
coords = d3.CartesianCoordinates('x', 'y', 'z')
dist = d3.Distributor(coords, dtype=dtype)
xbasis = d3.RealFourier(coords['x'], size=N, bounds=(0, L), dealias=dealias)
ybasis = d3.RealFourier(coords['y'], size=N, bounds=(0, L), dealias=dealias)
zbasis = d3.RealFourier(coords['z'], size=N, bounds=(0, L), dealias=dealias)

# ---------------- Fields & Problem Setup ----------------
p = dist.Field(name='p', bases=(xbasis, ybasis, zbasis))
u = dist.VectorField(coords, name='u', bases=(xbasis, ybasis, zbasis))
tau_p = dist.Field(name='tau_p')
f_ctrl = dist.VectorField(coords, name='f_ctrl', bases=(xbasis, ybasis, zbasis))

x, y, z = dist.local_grids(xbasis, ybasis, zbasis)

problem = d3.IVP([u, p, tau_p, f_ctrl], namespace=locals())
problem.add_equation("dt(u) + grad(p) - nu*lap(u) = - u@grad(u) + f_ctrl")
problem.add_equation("div(u) + tau_p = 0")
problem.add_equation("integ(p) = 0")

solver = problem.build_solver(timestepper)
solver.stop_sim_time = stop_sim_time

# Initial condition: Taylor-Green vortex
u['g'][0] = np.sin(x) * np.cos(y) * np.cos(z)
u['g'][1] = -np.cos(x) * np.sin(y) * np.cos(z)
u['g'][2] = 0

# ---------------- Diagnostics & Handlers ----------------
vorticity = d3.curl(u)
energy_density = 0.5 * (u @ u)
enstrophy_density = 0.5 * (vorticity @ vorticity)
omega_mag = d3.sqrt(vorticity @ vorticity)

S = d3.sym_grad(u)
dissipation_density = 2 * nu * (S @ S)

mean_energy = d3.integ(energy_density) / L**3
mean_enstrophy = d3.integ(enstrophy_density) / L**3
peak_vorticity = d3.max(omega_mag)
mean_dissipation = d3.integ(dissipation_density) / L**3

snapshots = solver.evaluator.add_file_handler('hais_tg_snapshots', sim_dt=0.2, max_writes=100)
snapshots.add_task(vorticity, name='vorticity')
snapshots.add_task(energy_density, name='energy_density')

scalars = solver.evaluator.add_file_handler('hais_tg_scalars', sim_dt=0.02, max_writes=2000)
scalars.add_task(mean_energy, name='mean_energy')
scalars.add_task(mean_enstrophy, name='mean_enstrophy')
scalars.add_task(peak_vorticity, name='peak_vorticity')
scalars.add_task(mean_dissipation, name='mean_dissipation')

CFL = d3.CFL(solver, initial_dt=max_timestep, cadence=10, safety=0.4, threshold=0.1,
             max_change=1.5, min_change=0.5, max_dt=max_timestep)
CFL.add_velocity(u)

flow = d3.GlobalFlowProperty(solver, cadence=10)
flow.add_property(enstrophy_density, name='enstrophy')

# ---------------- Execution Loop with HAIS Governance ----------------
try:
    logger.info('Starting HAIS-protected unified simulation loop -- Re=%g, N=%d^3' % (Reynolds, N))
    while solver.proceed:
        timestep = CFL.compute_timestep()

        # --- Measurements ---
        E = float(solver.evaluator.evaluate(mean_energy)['g'])
        Ens = float(solver.evaluator.evaluate(mean_enstrophy)['g'])
        Diss = float(solver.evaluator.evaluate(mean_dissipation)['g'])

        # --- CLF & CBF Calculations ---
        V_energy = (E - target_energy)**2
        h_enst = enstrophy_limit - Ens  # Safety barrier constraint (h >= 0)

        # Multi-objective cost
        J = (w_energy * V_energy
             + w_enstrophy * max(0.0, -h_enst)**2
             + w_dissipation * Diss)

        # --- Control & Safety Filter Construction ---
        if enable_control:
            f_base = control_gain * (target_energy - E) * u['g']
            dV_du = 2 * (E - target_energy) * u['g']
            f_clf = -clf_gain * dV_du

            # Safety CBF Override
            if h_enst < 0:
                f_cbf = -cbf_gain * vorticity['g']
                safety_override_active = True
            else:
                f_cbf = np.zeros_like(u['g'])
                safety_override_active = False

            f_control_applied = f_base + f_clf + f_cbf
            
            # Integrate ML Hook if enabled
            if enable_ml_controller and ml_controller is not None:
                state_dict = {
                    "energy": E,
                    "enstrophy": Ens,
                    "dissipation": Diss,
                    "Re": Reynolds,
                    "time": solver.sim_time,
                }
                f_ml_factor = ml_controller(state_dict)
                # Broadcast across local spatial grid dimensions safely
                for i in range(3):
                    f_control_applied[i] += f_ml_factor[i]

            f_ctrl['g'] = f_control_applied
        else:
            f_ctrl['g'] = np.zeros_like(u['g'])
            safety_override_active = False

        # --- Adaptive Reynolds Control ---
        if enable_adaptive_Re:
            if Ens < Re_target_enstrophy and Reynolds < Re_max:
                Reynolds *= 1.01
            elif Ens > Re_target_enstrophy and Reynolds > Re_min:
                Reynolds *= 0.99
            nu = 1.0 / Reynolds
            problem.parameters['nu'] = nu

        # --- Governance Audit Logging ---
        audit_record = {
            "iteration": solver.iteration,
            "sim_time": solver.sim_time,
            "energy": E,
            "enstrophy": Ens,
            "dissipation": Diss,
            "h_enstrophy": h_enst,
            "cost_J": J,
            "Reynolds": Reynolds,
            "safety_override": safety_override_active
        }
        audit_logger.log(audit_record)

        # Step solver
        solver.step(timestep)

        # Console Logging
        if (solver.iteration - 1) % 20 == 0:
            max_ens = flow.max('enstrophy')
            logger.info(
                "iter=%i  t=%.3f  dt=%.4f  E=%.5f  Ens=%.5f  Diss=%.5f  h_enst=%.5f  J=%.5f  Re=%.1f  SafetyOverride=%s"
                % (solver.iteration, solver.sim_time, timestep,
                   E, Ens, Diss, h_enst, J, Reynolds, str(safety_override_active))
            )

except Exception as e:
    logger.error(f'Exception raised during execution: {e}')
    raise
finally:
    audit_logger.close()
    solver.log_stats()
