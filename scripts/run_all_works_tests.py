#!/usr/bin/env python3
"""Run the unified all-works suite (+ core live adapter tests).

Note: put repo root ahead of hais/ so `tests` resolves to ./tests.
# (hais/tests was renamed to hais/hais_tests to avoid package shadowing.)
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    env = os.environ.copy()
    # Root first — keep ahead of hais/ (hais_tests rename + path order)
    py_path = os.pathsep.join(
        [
            str(ROOT),
            str(ROOT / "src"),
            str(ROOT / "hais"),
            str(ROOT / "imprint" / "src"),
            str(ROOT / "haven2" / "src"),
            env.get("PYTHONPATH", ""),
        ]
    )
    env["PYTHONPATH"] = py_path
    modules = [
        "tests.test_all_works",
        "tests.test_unified",
        "tests.test_governed_stack",
        "tests.test_action_bus",
        "tests.test_governed_mail",
        "tests.test_governed_calendar",
        "tests.test_governed_social",
        "tests.test_runtime_bridge",
        "tests.test_invariant_topology",
        "tests.test_osqp_governance_engine",
        "tests.test_hais_osqp_clf_cbf_engine",
        "tests.test_control_logic",
        "tests.test_math_cores",
        "tests.test_review_path",
        "tests.test_review_ops",
        "tests.test_key_providers",
        "tests.test_observability",
        "tests.test_contracts",
        "tests.test_sidecar",
        "tests.test_audit_soak",
    ]
    cmd = [sys.executable, "-m", "unittest", *modules, "-v"]
    print("Running:", " ".join(modules))
    proc = subprocess.run(cmd, cwd=str(ROOT), env=env)
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
