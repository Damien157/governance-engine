#!/usr/bin/env python3
"""Unified smoke for the useful live path (algorithm + control + optional sketch).

PASS requires: algorithm ok, quantum_line length 150, spectrum available.
No NP⊆P / consciousness / ket claims — useful live stack only.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "hais"))
sys.path.insert(0, str(ROOT / "imprint" / "src"))
sys.path.insert(0, str(ROOT / "haven2" / "src"))

from certified_governance_unified import CryptoEngine  # noqa: E402
from governed_stack import GovernedAlgorithm, GovernedStack, import_check, live_ok  # noqa: E402


def _section(title: str) -> None:
    print(f"\n=== {title} ===")


def _build_algo(tmp: Path) -> GovernedAlgorithm:
    db_path = str(tmp / "useful.db")
    key_path = str(tmp / "signing_key.pem")
    crypto = CryptoEngine(private_key_path=key_path)
    stack = GovernedStack(
        config={"db_path": db_path, "signing_key_path": key_path, "log_level": 40},
        crypto=crypto,
    )
    return GovernedAlgorithm(stack=stack)


def main() -> int:
    print("USEFUL LIVE PATH ONLY — no NP⊆P / consciousness / ket claims")

    # --- 2. import_check / live_ok ---
    _section("import_check / live_ok")
    check = import_check()
    ok_live = live_ok(check)
    print(json.dumps(check, indent=2, default=str))
    print("live_ok:", ok_live)

    tmp = Path(tempfile.mkdtemp(prefix="unified_useful_", dir="/tmp"))
    algo = _build_algo(tmp)

    # --- 3. GovernedAlgorithm.check_sync ---
    _section("GovernedAlgorithm.check_sync(purpose=unified_useful_test)")
    result = algo.check_sync(
        purpose="unified_useful_test",
        summary="Smoke: Purpose→Cost→Risk scan for live useful stack",
        time_cost="O(1) gate + QP step",
        space_cost="O(1)",
        energy_cost="low",
        speedup="n/a smoke",
        risk_notes="read-only audit fields; control gated by transistor",
        security_margin="standard",
        user="damien",
        role="user",
    )

    # --- 4. Key fields ---
    _section("algorithm result")
    hais = result.get("hais") or {}
    haven2 = result.get("haven2") or {}
    qline = result.get("quantum_line") or ""
    spectrum = result.get("spectrum") or {}
    print("decision:", result.get("decision"))
    print("entry_id:", result.get("entry_id"))
    print(
        "hais:",
        {
            "cap": hais.get("cap"),
            "risk": hais.get("risk"),
            "S": hais.get("S"),
            "I": hais.get("instability"),
        },
    )
    print("haven2:", {"realm": haven2.get("realm"), "open": haven2.get("open")})
    print("quantum_line len:", len(qline))
    if qline:
        print("quantum_line first20:", repr(qline[:20]))
        print("quantum_line last20:", repr(qline[-20:]))
    print(
        "spectrum:",
        {
            "Z_E": spectrum.get("Z_E"),
            "Z_R": spectrum.get("Z_R"),
            "Z_C": spectrum.get("Z_C"),
            "Z_H": spectrum.get("Z_H"),
            "source": spectrum.get("source"),
            "available": spectrum.get("available"),
        },
    )

    # --- 5. control via stack.govern if algorithm ALLOW ---
    _section("control (action=control)")
    if result.get("decision") == "ALLOW" and result.get("ok"):
        token = algo.issue_token("damien", "user")

        async def _control():
            return await algo.stack.govern(
                {"action": "control", "u_nom": 1.0, "payload": {"text": "useful control smoke"}},
                token,
            )

        ctrl = asyncio.run(_control())
        print("decision:", ctrl.get("decision"))
        print("reasons:", ctrl.get("reasons"))
        solver = ctrl.get("solver")
        if solver:
            print("solver:", {k: solver.get(k) for k in ("kind", "u", "u_nom", "h", "feasible", "status", "error") if k in solver or solver.get(k) is not None})
        else:
            notes = ctrl.get("notes") or []
            why = ctrl.get("reasons") or notes or ["no solver (likely transistor_closed / latch)"]
            print("control not available / BLOCK:", why)
            if notes:
                print("notes:", notes)
    else:
        print(
            "skipped control: algorithm decision was",
            result.get("decision"),
            "| reasons:",
            result.get("reasons"),
        )

    # --- 6. optional joint sketch (ONLY if algorithm ALLOW) ---
    if result.get("ok") and result.get("decision") == "ALLOW":
        _section("SKETCH joint_xyz_controller_sketch.forward")
        try:
            from docs.examples.joint_xyz_controller_sketch import sketch_forward

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
            print(f"SKETCH O1/O2/O3 = ({o1:.4f}, {o2:.4f}, {o3:.4f})")
        except Exception as exc:
            print(f"SKETCH skipped: {exc}")

    # --- 7. summary ---
    reasons_fail: list[str] = []
    if not result.get("ok") or result.get("decision") != "ALLOW":
        reasons_fail.append(f"algorithm not ok (decision={result.get('decision')})")
    if len(qline) != 150:
        reasons_fail.append(f"quantum_line len {len(qline)} != 150")
    if not spectrum.get("available"):
        reasons_fail.append("spectrum not available")
    if not ok_live:
        reasons_fail.append("live_ok=False (imports)")

    passed = not reasons_fail
    _section("SUMMARY")
    if passed:
        print("+------------------+")
        print("| PASS             |")
        print("+------------------+")
        print("algorithm ok; quantum_line len 150; spectrum available; live imports ok")
    else:
        print("+------------------+")
        print("| FAIL             |")
        print("+------------------+")
        for r in reasons_fail:
            print(" -", r)

    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
