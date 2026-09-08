# Governed controller NN — Evolutionary Synthesis (sketch charter)

**Title vibe:** Evolutionary Synthesis — a small feed-forward controller that
maps joint/sensor inputs to XYZ rotation commands for one controlled joint.

This document **grounds** the diagram as a **governed** controller under the
Algorithm gate. It does **not** place any neural net on the live ALLOW path.
Any code under `docs/examples/` or `solvers/` remains **sketch-tier**.

## Purpose

Produce joint **X / Y / Z rotation** commands (`O1`, `O2`, `O3`) for the
controlled joint from the current sensor snapshot.

## Input sensors (diagram)

| Sensor | Role |
|--------|------|
| Touch | Contact / grasp cue |
| Index of joint | Which joint is under control |
| Position of joint | Current pose |
| Last joint | Upstream / previous joint pose or id |
| High / Low / Y / Z angular limits | Soft/hard stop envelope |
| Timer | Step / dwell clock |

**Hidden neurons** — internal layers (size/topology unspecified here; training
and evolution stay off the live gate).

**Outputs** — `O1`, `O2`, `O3` = commanded joint rotations about x / y / z.

## Cost

| Cost axis | What to record on the Algorithm scan |
|-----------|--------------------------------------|
| Time | Forward-pass latency (e.g. `~1ms` or `O(H)` for hidden width H) |
| Space | Parameter footprint / activation buffer |
| Energy | Estimate only — map to HAIS `capability_cap` / energy_cost field; **not** a joulemeter |
| Speedup | Claimed vs PID / QP baseline (Cost claim, not a proof) |

Live HAIS still applies its own `capability_cap` throttle (`CAP_THROTTLE=0.25`)
independent of any NN claim.

## Risk

| Risk | Notes |
|------|-------|
| Limit violations | Commanded angles vs High/Low/Y/Z limits — prefer CBF/QP filter **after** NN |
| Instability `I` | QUANTUM / HAIS instability; high `I` → REVIEW or BLOCK |
| Command channel | Authenticate / authorize actuator path; no secrets in scan intent |

Security of the command channel lives under **Risk** (not Cost).

## Authority

Who may **deploy** or **run** the controller:

- Token / role via `GovernedStack` (same JWT path as other live gates).
- Prefer operator / `admin` role for deploy; `user` may be enough for dry-run
  scans depending on policy.
- Never bypass `GovernedAlgorithm.require_allow` on the deploy path.

## Audit

Every control step (or deploy decision) should retain:

1. **QUANTUM line** + structured `quantum` (see [QUANTUM_LINE.md](QUANTUM_LINE.md))
2. Last **sensor / actuator snapshot** (inputs + `O1/O2/O3`)
3. Stack `entry_id` / reasons from the Algorithm gate

## Example — gate before a control step

```python
from governed_stack import GovernedAlgorithm
from governed_stack.quantum_line import decode_quantum_line

gate = GovernedAlgorithm()
result = gate.check_sync(
    purpose="joint_xyz_controller",
    summary=(
        "Evolutionary Synthesis NN: sensors "
        "(touch, joint_index, position, last_joint, angular_limits, timer) "
        "→ hidden → O1/O2/O3 joint xyz rotation; dry-run scan before step"
    ),
    time_cost="~1ms forward",
    space_cost="O(H) activations",
    energy_cost="low",          # Cost estimate; HAIS cap still authoritative
    speedup="vs PID baseline (claim)",
    risk_notes=(
        "limit envelope High/Low/Y/Z; post-filter with CLF-CBF-QP; "
        "command channel authenticated; monitor instability I"
    ),
    security_margin="standard",
    user="damien",
    role="user",
)
if not result["ok"]:
    raise SystemExit(f"{result['decision']}: {result.get('reasons')}")

# Audit payload for this step
quantum_line = result["quantum_line"]
quantum = result["quantum"]
assert len(quantum_line) == 150
_ = decode_quantum_line(quantum_line)

# Only after ALLOW: run the sketch NN out of band (not via govern solvers).
# sensors = {...}; o1, o2, o3 = sketch_forward(sensors)
```

Optional sketch (off ALLOW path): `docs/examples/joint_xyz_controller_sketch.py`.

## Spine

- [ALGORITHM_GOVERNANCE_MAIN.md](ALGORITHM_GOVERNANCE_MAIN.md) — Purpose→Cost→Risk→Authority→Audit
- [QUANTUM_LINE.md](QUANTUM_LINE.md) — fused HAIS audit string
- Live control solver (separate): `action=control` → CLF-CBF-QP, **not** this NN
