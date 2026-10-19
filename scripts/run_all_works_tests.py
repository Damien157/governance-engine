#!/usr/bin/env python3
"""Run the unified all-works suite (+ core live adapter tests).

Phase 1: unittest over a fixed module list (output unchanged).
Phase 2: pytest over the pytest-style files unittest cannot run.
Exit code is non-zero if either phase fails.

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
        "tests.test_connectors",
        "tests.test_lint_no_bypass",
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

    # pytest-style files (fixtures / parametrize / xfail) that unittest cannot
    # run. Every tests/test_*.py must be in exactly one of the two lists.
    # pytest picks up pythonpath from [tool.pytest.ini_options].
    pytest_files = [
        "tests/test_audit_projection.py",
        "tests/test_bio_authority_jwt.py",
        "tests/test_bio_governance.py",
        "tests/test_bio_semantic.py",
        "tests/test_bio_semantic_judge.py",
        "tests/test_bio_semantic_overlay.py",
        "tests/test_governed_algorithm.py",
        "tests/test_lie_and_constraints.py",
        "tests/test_quantum_line.py",
        "tests/test_safety_sim.py",
        "tests/test_spectral_audit.py",
    ]
    py_cmd = [sys.executable, "-m", "pytest", "-q", *pytest_files]
    print("Running (pytest):", " ".join(pytest_files), flush=True)
    py_proc = subprocess.run(py_cmd, cwd=str(ROOT), env=env)

    return proc.returncode or py_proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
