#!/usr/bin/env python3
"""
Dedalus v3: 3D Taylor–Green vortex with CLF–CBF control — FIXED sketch.

FIXES applied (vs fluids/hais_taylor_green_v3_paste.py):
  1. IVP([u, p, tau_p]) only — f_ctrl is an external forcing Field, not an unknown
     (including f_ctrl in IVP without an equation → non-square system).
  2. nu_field Field for adaptive Re; Reynolds clamped to [Re_min, Re_max].
     Viscous term on RHS so nu_field can change after build.
  3. Margin CBF (cbf_margin) — engage before hard barrier violation (h < margin).
  4. GlobalFlowProperty for E/Ens/Diss (not solver.evaluator.evaluate).
     Intended volume_average → volume_integral/L**3 (see remaining notes).

REMAINING Dedalus 3.0.5 blockers / smoke-compatible workarounds:
  - d3.sym_grad does NOT exist — dissipation uses placeholder
    dissipation_density = 2 * nu_field * enstrophy_density
    (incompressible periodic: ε ≈ ν|ω|² = 2ν·Ω). Do not use S@S via sym_grad.
  - d3.max does NOT exist — peak_vorticity omitted from scalar file handlers;
    use flow.max('enstrophy') for a related peak diagnostic instead.
  - flow.volume_average is NotImplemented (missing hypervolume) in 3.0.5 —
    use flow.volume_integral(name) / L**3 (with precompute_integral=True).

SKETCH ONLY — off live govern path. See fluids/smoke_taylor_green.py for a
working tiny Dedalus-3 smoke. Control-law math also lives in fluids/control_law.py
(tested by tests/test_control_logic.py). Do NOT run N=64 as a default CI step.
"""

from __future__ import annotations

import logging

import numpy as np
import dedalus.public as d3

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# ---------------- Parameters ----------------
N = 64
L = 2 * np.pi
Reynolds = 800.0
nu = 1.0 / Reynolds
dealias = 3 / 2
stop_sim_time = 20.0
timestepper = d3.RK222
max_timestep = 1e-2
dtype = np.float64

# --- Control parameters ---
enable_control = True

# Proportional energy regulator + CLF
target_energy = 0.25
control_gain = 0.5
clf_gain = 0.1

# CBF-style enstrophy safety filter (margin form)
enstrophy_limit = 0.5
cbf_gain = 0.05
cbf_margin = 0.1  # engage when h_enst < margin (not only when h < 0)

# Multi-objective weights
w_energy = 1.0
w_enstrophy = 0.5
w_dissipation = 0.2

# Adaptive Reynolds control (nu_field + clamp)
enable_adaptive_Re = False
Re_min, Re_max = 400.0, 2000.0
Re_target_enstrophy = 0.6

# ML controller hook
enable_ml_controller = False
ml_controller = None  # callable: ml_controller(state_dict) -> forcing array


# ---- control-law helpers (match fluids/control_law.py / test_control_logic) ----

def cbf_correction(h_enst, vorticity_arr, cbf_gain_, cbf_margin_):
    if h_enst < cbf_margin_:
        deficit = cbf_margin_ - h_enst
        return -cbf_gain_ * deficit * vorticity_arr
    return 0 * vorticity_arr


def adaptive_re_update_clamped(Ens, Reynolds_, Re_target_enstrophy_, Re_min_, Re_max_):
    if Ens < Re_target_enstrophy_:
        Reynolds_ *= 1.01
    elif Ens > Re_target_enstrophy_:
        Reynolds_ *= 0.99
    return min(max(Reynolds_, Re_min_), Re_max_)


def energy_clf_terms(E, target_energy_, u_g, control_gain_, clf_gain_):
    V_energy = (E - target_energy_) ** 2
    f_base = control_gain_ * (target_energy_ - E) * u_g
    dV_du = 2 * (E - target_energy_) * u_g
    f_clf = -clf_gain_ * dV_du
    return V_energy, f_base, f_clf


def build_and_run(
    N_=N,
    stop_sim_time_=stop_sim_time,
    enable_control_=enable_control,
    enable_adaptive_Re_=enable_adaptive_Re,
    max_timestep_=max_timestep,
):
    """Build Dedalus TG + control problem and run until stop_sim_time_."""
    global Reynolds, nu

    # ---------------- Bases: triply periodic ----------------
    coords = d3.CartesianCoordinates('x', 'y', 'z')
    dist = d3.Distributor(coords, dtype=dtype)
    xbasis = d3.RealFourier(coords['x'], size=N_, bounds=(0, L), dealias=dealias)
    ybasis = d3.RealFourier(coords['y'], size=N_, bounds=(0, L), dealias=dealias)
    zbasis = d3.RealFourier(coords['z'], size=N_, bounds=(0, L), dealias=dealias)

    # ---------------- Fields ----------------
    p = dist.Field(name='p', bases=(xbasis, ybasis, zbasis))
    u = dist.VectorField(coords, name='u', bases=(xbasis, ybasis, zbasis))
    tau_p = dist.Field(name='tau_p')

    # FIX: f_ctrl external (not IVP unknown)
    f_ctrl = dist.VectorField(coords, name='f_ctrl', bases=(xbasis, ybasis, zbasis))
    f_ctrl['g'] = 0

    # FIX: nu_field for adaptive Re (updated in-loop; viscous term on RHS)
    nu_field = dist.Field(name='nu')
    nu_field['g'] = nu

    x, y, z = dist.local_grids(xbasis, ybasis, zbasis)

    # ---------------- Problem: IVP unknowns = [u, p, tau_p] only ----------------
    problem = d3.IVP([u, p, tau_p], namespace=locals())
    # Viscous term on RHS so nu_field can change after build_solver
    problem.add_equation("dt(u) + grad(p) = nu_field*lap(u) - u@grad(u) + f_ctrl")
    problem.add_equation("div(u) + tau_p = 0")
    problem.add_equation("integ(p) = 0")

    solver = problem.build_solver(timestepper)
    solver.stop_sim_time = stop_sim_time_

    # ---------------- Initial condition: Taylor–Green vortex ----------------
    u['g'][0] = np.sin(x) * np.cos(y) * np.cos(z)
    u['g'][1] = -np.cos(x) * np.sin(y) * np.cos(z)
    u['g'][2] = 0

    # ---------------- Diagnostics ----------------
    vorticity = d3.curl(u)
    energy_density = 0.5 * (u @ u)
    enstrophy_density = 0.5 * (vorticity @ vorticity)
    omega_mag = d3.sqrt(vorticity @ vorticity)

    # REMAINING: d3.sym_grad absent — smoke-compatible dissipation placeholder
    # (do NOT call d3.sym_grad / S@S here)
    dissipation_density = 2 * nu_field * enstrophy_density

    mean_energy = d3.integ(energy_density) / L**3
    mean_enstrophy = d3.integ(enstrophy_density) / L**3
    mean_dissipation = d3.integ(dissipation_density) / L**3
    # REMAINING: d3.max absent — peak_vorticity omitted from handlers

    snapshots = solver.evaluator.add_file_handler(
        'tg_fixed_snapshots', sim_dt=0.2, max_writes=100)
    snapshots.add_task(vorticity, name='vorticity')
    snapshots.add_task(energy_density, name='energy_density')
    snapshots.add_task(omega_mag, name='omega_mag')

    scalars = solver.evaluator.add_file_handler(
        'tg_fixed_scalars', sim_dt=0.02, max_writes=2000)
    scalars.add_task(mean_energy, name='mean_energy')
    scalars.add_task(mean_enstrophy, name='mean_enstrophy')
    # peak_vorticity omitted (d3.max missing)
    scalars.add_task(mean_dissipation, name='mean_dissipation')

    # ---------------- CFL + GlobalFlowProperty ----------------
    CFL = d3.CFL(
        solver, initial_dt=max_timestep_, cadence=10, safety=0.4, threshold=0.1,
        max_change=1.5, min_change=0.5, max_dt=max_timestep_,
    )
    CFL.add_velocity(u)

    # cadence=1 so volume_average is fresh each step for the control loop
    flow = d3.GlobalFlowProperty(solver, cadence=1)
    flow.add_property(energy_density, name='E', precompute_integral=True)
    flow.add_property(enstrophy_density, name='Ens', precompute_integral=True)
    flow.add_property(dissipation_density, name='Diss', precompute_integral=True)
    flow.add_property(enstrophy_density, name='enstrophy')  # for flow.max

    # Seed measurements once before the first step (flow updates during step)
    def _scalarize(op):
        val = op.evaluate()
        return float(np.asarray(val['g']).ravel()[0])

    E = _scalarize(mean_energy)
    Ens = _scalarize(mean_enstrophy)
    Diss = _scalarize(mean_dissipation)

    Re_local = float(Reynolds)

    try:
        logger.info(
            'Starting FIXED TG control loop -- Re=%g, N=%d^3, stop=%g'
            % (Re_local, N_, stop_sim_time_)
        )
        while solver.proceed:
            timestep = CFL.compute_timestep()

            # --- Measurements: prefer GlobalFlowProperty after first step ---
            if solver.iteration > 0:
                # volume_average NotImplemented in Dedalus 3.0.5
                E = flow.volume_integral('E') / L**3
                Ens = flow.volume_integral('Ens') / L**3
                Diss = flow.volume_integral('Diss') / L**3

            # --- CLF: energy tracking ---
            V_energy, f_base, f_clf = energy_clf_terms(
                E, target_energy, u['g'], control_gain, clf_gain)

            # --- CBF: margin safety (h = limit - Ens; safe when h >= 0) ---
            h_enst = enstrophy_limit - Ens

            J = (
                w_energy * V_energy
                + w_enstrophy * max(0.0, -h_enst) ** 2
                + w_dissipation * Diss
            )

            # --- Combine control terms ---
            if enable_control_:
                u.change_scales(dealias)
                f_ctrl.change_scales(dealias)
                ug = u['g']
                V_energy, f_base, f_clf = energy_clf_terms(
                    E, target_energy, ug, control_gain, clf_gain)
                vort_f = vorticity.evaluate()
                vort_f.change_scales(dealias)
                f_cbf = cbf_correction(h_enst, vort_f['g'], cbf_gain, cbf_margin)
                f_ctrl['g'] = f_base + f_clf + f_cbf
            else:
                f_ctrl.change_scales(dealias)
                f_ctrl['g'] = 0

            # --- Adaptive Reynolds via nu_field + clamp ---
            if enable_adaptive_Re_:
                Re_local = adaptive_re_update_clamped(
                    Ens, Re_local, Re_target_enstrophy, Re_min, Re_max)
                nu_local = 1.0 / Re_local
                nu_field['g'] = nu_local

            # --- ML controller hook (optional) ---
            if enable_ml_controller and ml_controller is not None:
                state = {
                    "energy": E,
                    "enstrophy": Ens,
                    "dissipation": Diss,
                    "Re": Re_local,
                    "time": solver.sim_time,
                }
                f_ml = ml_controller(state)
                f_ctrl['g'] += f_ml

            solver.step(timestep)

            if (solver.iteration - 1) % 20 == 0:
                max_ens = flow.max('enstrophy')
                logger.info(
                    "iter=%i  t=%.3f  dt=%.4f  E=%.5f  Ens=%.5f  Diss=%.5f  "
                    "h_enst=%.5f  J=%.5f  Re=%.1f  Ens(max)=%.5f"
                    % (
                        solver.iteration, solver.sim_time, timestep,
                        E, Ens, Diss, h_enst, J, Re_local, max_ens,
                    )
                )

    except Exception:
        logger.error('Exception raised, stopping.')
        raise
    finally:
        try:
            solver.log_stats()
        except Exception as exc:
            logger.info('log_stats skipped: %s', exc)

    return solver


if __name__ == "__main__":
    build_and_run()
