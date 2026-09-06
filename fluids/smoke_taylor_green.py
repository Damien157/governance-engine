#!/usr/bin/env python3
"""
SMOKE (tiny) HAIS Taylor-Green — patched copy of hais_taylor_green.py for Dedalus 3.
Original paste left intact. Fluids-only; does not touch HAIS m.
"""

import os
import numpy as np
import dedalus.public as d3
import logging
import json
import time

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

OUTDIR = "/tmp/tg_smoke"
os.makedirs(OUTDIR, exist_ok=True)

# ---------------- Parameters (SMOKE) ----------------
N = 16
L = 2 * np.pi
Reynolds = 800
nu = 1.0 / Reynolds
dealias = 3 / 2
stop_sim_time = 0.1
timestepper = d3.RK222
max_timestep = 5e-2
dtype = np.float64

# --- HAIS Safety & Control Parameters ---
enable_control = True
target_energy = 0.25
control_gain = 0.5
clf_gain = 0.1
enstrophy_limit = 0.5
cbf_gain = 0.05

w_energy = 1.0
w_enstrophy = 0.8
w_dissipation = 0.2

enable_adaptive_Re = True
Re_min, Re_max = 400, 2000
Re_target_enstrophy = 0.6

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
        action_factor = np.tanh(np.dot(self.weights, features))
        return action_factor

ml_controller = HAISSurrogateController() if enable_ml_controller else None

class HAISAuditLogger:
    def __init__(self, filename):
        self.filename = filename
        self.file = open(filename, "w")

    def log(self, record):
        self.file.write(json.dumps(record) + "\n")
        self.file.flush()

    def close(self):
        self.file.close()

audit_logger = HAISAuditLogger(os.path.join(OUTDIR, "audit.jsonl"))

def scalarize(op):
    """Dedalus 3: integ(...) evaluates to a (1,1,1) Field — not a Python float."""
    val = op.evaluate()
    return float(np.asarray(val['g']).ravel()[0])

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
# API FIX: f_ctrl must be an external forcing Field, NOT an IVP unknown
# (including it in IVP vars without an equation yields Non-square system).
f_ctrl = dist.VectorField(coords, name='f_ctrl', bases=(xbasis, ybasis, zbasis))
f_ctrl['g'] = 0

x, y, z = dist.local_grids(xbasis, ybasis, zbasis)

problem = d3.IVP([u, p, tau_p], namespace=locals())
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

# API FIX: d3.sym_grad absent; grad(u)@grad(u) is tensor-valued.
# For incompressible periodic flow, epsilon = nu * |omega|^2 = 2*nu*enstrophy_density.
dissipation_density = 2 * nu * enstrophy_density

mean_energy = d3.integ(energy_density) / L**3
mean_enstrophy = d3.integ(enstrophy_density) / L**3
# API FIX: d3.max not in Dedalus 3 public API; peak vorticity omitted in smoke
mean_dissipation = d3.integ(dissipation_density) / L**3

# SMOKE: short-lived file handlers under /tmp
snapshots = solver.evaluator.add_file_handler(
    os.path.join(OUTDIR, 'snapshots'), sim_dt=1.0, max_writes=2)
snapshots.add_task(energy_density, name='energy_density')

scalars = solver.evaluator.add_file_handler(
    os.path.join(OUTDIR, 'scalars'), sim_dt=0.05, max_writes=10)
scalars.add_task(mean_energy, name='mean_energy')
scalars.add_task(mean_enstrophy, name='mean_enstrophy')
scalars.add_task(mean_dissipation, name='mean_dissipation')

CFL = d3.CFL(solver, initial_dt=max_timestep, cadence=5, safety=0.4, threshold=0.1,
             max_change=1.5, min_change=0.5, max_dt=max_timestep)
CFL.add_velocity(u)

flow = d3.GlobalFlowProperty(solver, cadence=5)
flow.add_property(enstrophy_density, name='enstrophy')

t0 = time.time()
try:
    logger.info('SMOKE HAIS TG loop -- Re=%g, N=%d^3, stop=%g' % (Reynolds, N, stop_sim_time))
    while solver.proceed:
        timestep = CFL.compute_timestep()

        # API FIX: operator.evaluate, not solver.evaluator.evaluate
        E = scalarize(mean_energy)
        Ens = scalarize(mean_enstrophy)
        Diss = scalarize(mean_dissipation)

        V_energy = (E - target_energy)**2
        h_enst = enstrophy_limit - Ens

        J = (w_energy * V_energy
             + w_enstrophy * max(0.0, -h_enst)**2
             + w_dissipation * Diss)

        if enable_control:
            # API FIX: keep u/f_ctrl on matching grid scales (dealias vs coeff)
            u.change_scales(dealias)
            f_ctrl.change_scales(dealias)
            ug = u['g']
            f_base = control_gain * (target_energy - E) * ug
            dV_du = 2 * (E - target_energy) * ug
            f_clf = -clf_gain * dV_du

            if h_enst < 0:
                vort_f = vorticity.evaluate()
                vort_f.change_scales(dealias)
                f_cbf = -cbf_gain * vort_f['g']
                safety_override_active = True
            else:
                f_cbf = np.zeros_like(ug)
                safety_override_active = False

            f_control_applied = f_base + f_clf + f_cbf

            if enable_ml_controller and ml_controller is not None:
                state_dict = {
                    "energy": E,
                    "enstrophy": Ens,
                    "dissipation": Diss,
                    "Re": Reynolds,
                    "time": solver.sim_time,
                }
                f_ml_factor = ml_controller(state_dict)
                for i in range(3):
                    f_control_applied[i] += f_ml_factor[i]

            f_ctrl['g'] = f_control_applied
        else:
            f_ctrl.change_scales(dealias)
            f_ctrl['g'] = 0
            safety_override_active = False

        # Adaptive Re: bookkeeping only (nu baked into L at build; no problem.parameters)
        if enable_adaptive_Re:
            if Ens < Re_target_enstrophy and Reynolds < Re_max:
                Reynolds *= 1.01
            elif Ens > Re_target_enstrophy and Reynolds > Re_min:
                Reynolds *= 0.99
            nu = 1.0 / Reynolds

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

        solver.step(timestep)

        if (solver.iteration - 1) % 1 == 0:
            max_ens = flow.max('enstrophy')
            logger.info(
                "iter=%i  t=%.4f  dt=%.4f  E=%.5f  Ens=%.5f  Diss=%.5f  h_enst=%.5f  J=%.5f  Re=%.1f  SafetyOverride=%s  maxEns=%.5f"
                % (solver.iteration, solver.sim_time, timestep,
                   E, Ens, Diss, h_enst, J, Reynolds, str(safety_override_active), max_ens)
            )

except Exception as e:
    logger.error('Exception raised during execution: %s' % e)
    raise
finally:
    wall = time.time() - t0
    logger.info('SMOKE wall_time_sec=%.3f iterations=%s sim_time=%s' % (
        wall, getattr(solver, 'iteration', '?'), getattr(solver, 'sim_time', '?')))
    audit_logger.close()
    try:
        solver.log_stats()
    except Exception as e:
        logger.info('log_stats skipped: %s' % e)
