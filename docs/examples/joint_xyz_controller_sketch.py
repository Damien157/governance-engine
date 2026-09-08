"""SKETCH ONLY — Evolutionary Synthesis joint XYZ controller stub.

Off the live ALLOW / govern / solver path. Do not import from GovernedStack
decision code. Demonstrates sensor → hidden → O1/O2/O3 mapping and how a
caller would gate with GovernedAlgorithm before a step.

See docs/GOVERNED_CONTROLLER_NN.md.
"""

from __future__ import annotations

from typing import Dict, Mapping, Sequence, Tuple


def sketch_forward(
    sensors: Mapping[str, float],
    *,
    weights: Sequence[Sequence[float]] | None = None,
) -> Tuple[float, float, float]:
    """Tiny deterministic stub: weighted sum of sensors → (ox, oy, oz).

    Not trained, not safe, not on the ALLOW path. Limits are *reported*
    by the caller; this function does not enforce CBF/QP.
    """
    keys = (
        "touch",
        "joint_index",
        "position",
        "last_joint",
        "limit_high",
        "limit_low",
        "limit_y",
        "limit_z",
        "timer",
    )
    x = [float(sensors.get(k, 0.0)) for k in keys]
    # Default "hidden" row: equal mix into three outputs (sketch).
    if weights is None:
        w = (
            (0.1, 0.2, 0.4, 0.1, 0.05, -0.05, 0.0, 0.0, 0.01),
            (0.1, 0.1, 0.3, 0.2, 0.0, 0.0, 0.1, -0.1, 0.01),
            (0.05, 0.1, 0.25, 0.15, 0.0, 0.0, -0.1, 0.1, 0.01),
        )
    else:
        w = weights  # type: ignore[assignment]
    outs = []
    for row in w:
        outs.append(sum(a * b for a, b in zip(x, row)))
    return float(outs[0]), float(outs[1]), float(outs[2])


def example_gated_step() -> Dict[str, object]:
    """Illustrative: scan Algorithm gate, then run sketch forward if ALLOW."""
    from governed_stack import GovernedAlgorithm

    gate = GovernedAlgorithm()
    result = gate.check_sync(
        purpose="joint_xyz_controller",
        summary="sketch forward only; not live control",
        time_cost="~1ms forward",
        space_cost="O(1)",
        energy_cost="low",
        speedup="n/a sketch",
        risk_notes="limits not enforced here; use QP filter in production",
        security_margin="standard",
    )
    out: Dict[str, object] = {
        "decision": result.get("decision"),
        "quantum_line": result.get("quantum_line"),
    }
    if not result.get("ok"):
        out["blocked"] = True
        return out
    sensors = {
        "touch": 0.0,
        "joint_index": 2.0,
        "position": 0.1,
        "last_joint": 1.0,
        "limit_high": 1.0,
        "limit_low": -1.0,
        "limit_y": 0.5,
        "limit_z": 0.5,
        "timer": 0.0,
    }
    o1, o2, o3 = sketch_forward(sensors)
    out["actuators"] = {"O1": o1, "O2": o2, "O3": o3}
    out["sensors"] = sensors
    return out


if __name__ == "__main__":
    print(example_gated_step())
