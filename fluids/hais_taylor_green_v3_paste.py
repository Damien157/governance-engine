#!/usr/bin/env python3
"""
Dedalus v3: 3D Taylor-Green vortex with full measurement + control layer.

Features:
  - Triply periodic incompressible Navier–Stokes
  - Taylor–Green initial condition
  - CFL-adaptive timestep
  - Diagnostics: energy, enstrophy, peak vorticity, dissipation
  - Multi-objective CLF–CBF-style control:
      * energy tracking (CLF)
      * enstrophy safety (CBF)
      * proportional regulator
  - Optional adaptive Reynolds control
  - Optional ML controller hook
  - Governance-style control audit logging

SKETCH ONLY — see fluids/smoke_taylor_green.py for Dedalus-3 working smoke.
Known Dedalus 3.0.5 blockers in this raw paste:
  IVP([..., f_ctrl]), d3.sym_grad, d3.max, solver.evaluator.evaluate(...)
"""

import numpy as np
import dedalus.public as d3
import logging

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# ---------------- Parameters ----------------
N = 64
L = 2 * np.pi
Reynolds = 800
nu = 1.0 / Reynolds
dealias = 3 / 2
stop_sim_time = 20
timestepper = d3.RK222
max_timestep = 1e-2
dtype = np.float64

# --- Control parameters ---
enable_control = True

# Proportional energy regulator
target_energy = 0.25
control_gain = 0.5

# CLF-style controller
clf_gain = 0.1

# CBF-style enstrophy safety filter
enstrophy_limit = 0.5
cbf_gain = 0.05

# Multi-objective weights
w_energy = 1.0
w_enstrophy = 0.5
w_dissipation = 0.2

# Adaptive Reynolds control
enable_adaptive_Re = False
Re_min, Re_max = 400, 2000
Re_target_enstrophy = 0.6

# ML controller hook
enable_ml_controller = False
ml_controller = None  # set to callable: ml_controller(state_dict) -> forcing array

# ---------------- Bases: triply periodic ----------------
coords = d3.CartesianCoordinates('x', 'y', 'z')
dist = d3.Distributor(coords, dtype=dtype)
xbasis = d3.RealFourier(coords['x'], size=N, bounds=(0, L), dealias=dealias)
ybasis = d3.RealFourier(coords['y'], size=N, bounds=(0, L), dealias=dealias)
zbasis = d3.RealFourier(coords['z'], size=N, bounds=(0, L), dealias=dealias)

# ---------------- Fields ----------------
p = dist.Field(name='p', bases=(xbasis, ybasis, zbasis))
u = dist.VectorField(coords, name='u', bases=(xbasis, ybasis, zbasis))
tau_p = dist.Field(name='tau_p')
f_ctrl = dist.VectorField(coords, name='f_ctrl', bases=(xbasis, ybasis, zbasis))

x, y, z = dist.local_grids(xbasis, ybasis, zbasis)

# ---------------- Problem: incompressible Navier-Stokes + control ----------------
problem = d3.IVP([u, p, tau_p, f_ctrl], namespace=locals())
problem.add_equation("dt(u) + grad(p) - nu*lap(u) = - u@grad(u) + f_ctrl")
problem.add_equation("div(u) + tau_p = 0")
problem.add_equation("integ(p) = 0")

# ---------------- Solver ----------------
solver = problem.build_solver(timestepper)
solver.stop_sim_time = stop_sim_time

# ---------------- Initial condition: Taylor-Green vortex ----------------
u['g'][0] = np.sin(x) * np.cos(y) * np.cos(z)
u['g'][1] = -np.cos(x) * np.sin(y) * np.cos(z)
u['g'][2] = 0

# ---------------- Diagnostics: energy, enstrophy, vorticity, dissipation ----------------
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

snapshots = solver.evaluator.add_file_handler('tg_snapshots', sim_dt=0.2, max_writes=100)
snapshots.add_task(vorticity, name='vorticity')
snapshots.add_task(energy_density, name='energy_density')
snapshots.add_task(omega_mag, name='omega_mag')

scalars = solver.evaluator.add_file_handler('tg_scalars', sim_dt=0.02, max_writes=2000)
scalars.add_task(mean_energy, name='mean_energy')
scalars.add_task(mean_enstrophy, name='mean_enstrophy')
scalars.add_task(peak_vorticity, name='peak_vorticity')
scalars.add_task(mean_dissipation, name='mean_dissipation')

# ---------------- CFL-adaptive timestep ----------------
CFL = d3.CFL(solver, initial_dt=max_timestep, cadence=10, safety=0.4, threshold=0.1,
             max_change=1.5, min_change=0.5, max_dt=max_timestep)
CFL.add_velocity(u)

flow = d3.GlobalFlowProperty(solver, cadence=10)
flow.add_property(enstrophy_density, name='enstrophy')

# ---------------- Main loop ----------------
try:
    logger.info('Starting main loop -- Reynolds=%g, N=%d^3' % (Reynolds, N))
    while solver.proceed:
        timestep = CFL.compute_timestep()

        # --- Measurements ---
        E = solver.evaluator.evaluate(mean_energy)['g'][0, 0, 0]
        Ens = solver.evaluator.evaluate(mean_enstrophy)['g'][0, 0, 0]
        Diss = solver.evaluator.evaluate(mean_dissipation)['g'][0, 0, 0]

        # --- CLF: energy tracking ---
        V_energy = (E - target_energy)**2

        # --- CBF: enstrophy safety ---
        h_enst = enstrophy_limit - Ens  # h >= 0 safe

        # --- Multi-objective scalar ---
        J = (w_energy * V_energy
             + w_enstrophy * max(0.0, -h_enst)**2
             + w_dissipation * Diss)

        # --- Base forcing: proportional energy regulator ---
        f_base = control_gain * (target_energy - E) * u['g']

        # --- CLF-style correction (drive V down) ---
        dV_du = 2 * (E - target_energy) * u['g']  # crude gradient
        f_clf = -clf_gain * dV_du

        # --- CBF-style safety correction (push against high enstrophy) ---
        if h_enst < 0:
            f_cbf = -cbf_gain * vorticity['g']
        else:
            f_cbf = 0 * u['g']

        # --- Combine control terms ---
        if enable_control:
            f_ctrl['g'] = f_base + f_clf + f_cbf
        else:
            f_ctrl['g'] = 0 * u['g']

        # --- Adaptive Reynolds control (optional) ---
        if enable_adaptive_Re:
            if Ens < Re_target_enstrophy and Reynolds < Re_max:
                Reynolds *= 1.01
            elif Ens > Re_target_enstrophy and Reynolds > Re_min:
                Reynolds *= 0.99
            nu = 1.0 / Reynolds
            problem.parameters['nu'] = nu

        # --- ML controller hook (optional) ---
        if enable_ml_controller and ml_controller is not None:
            state = {
                "energy": E,
                "enstrophy": Ens,
                "dissipation": Diss,
                "Re": Reynolds,
                "time": solver.sim_time,
            }
            f_ml = ml_controller(state)  # expected shape like u['g']
            f_ctrl['g'] += f_ml

        # Step solver
        solver.step(timestep)

        # Logging
        if (solver.iteration - 1) % 20 == 0:
            max_ens = flow.max('enstrophy')
            logger.info(
                "iter=%i  t=%.3f  dt=%.4f  E=%.5f  Ens=%.5f  Diss=%.5f  h_enst=%.5f  J=%.5f  Re=%.1f  Ens(max)=%.5f"
                % (solver.iteration, solver.sim_time, timestep,
                   E, Ens, Diss, h_enst, J, Reynolds, max_ens)
            )

except Exception:
    logger.error('Exception raised, stopping.')
    raise
finally:
    solver.log_stats()
