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


# Every tests/test_*.py must appear in exactly one of these two lists;
# tests/test_runner_coverage.py (run below) and check_coverage() enforce it.
UNITTEST_MODULES = [
    "tests.test_all_works",
    "tests.test_unified",
    "tests.test_governed_stack",
    "tests.test_action_bus",
    "tests.test_connectors",
    "tests.test_lint_no_bypass",
    "tests.test_governed_mail",
    "tests.test_governed_calendar",
    "tests.test_governed_social",
    "tests.test_governed_tool",
    "tests.test_runtime_bridge",
    "tests.test_invariant_topology",
    "tests.test_osqp_governance_engine",
    "tests.test_hais_osqp_clf_cbf_engine",
    "tests.test_hais_audit_review_flag",
    "tests.test_control_logic",
    "tests.test_math_cores",
    "tests.test_review_path",
    "tests.test_review_ops",
    "tests.test_key_providers",
    "tests.test_observability",
    "tests.test_contracts",
    "tests.test_sidecar",
    "tests.test_audit_soak",
    # MCP-server test skips (documented) unless the optional `mcp` extra is installed.
    "tests.test_sealed_mail",
    "tests.test_runner_coverage",
]

# pytest-style files (fixtures / parametrize / xfail) that unittest cannot run.
# pytest picks up pythonpath from [tool.pytest.ini_options].
PYTEST_FILES = [
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

# Sub-project test folders, each run as its own pytest invocation.
# (hais_tests ZK slow tests skip unless ZK_RUN_SLOW=1.)
EXTRA_PYTEST_DIRS = [
    "haven2/tests",
    "imprint/tests",
    "hais/hais_tests",
]


def coverage_problems(root: Path = ROOT) -> list[str]:
    """Return human-readable problems: tests/test_*.py listed in neither or both lists."""
    on_disk = {p.stem for p in (root / "tests").glob("test_*.py")}
    in_ut = {m.rsplit(".", 1)[-1] for m in UNITTEST_MODULES}
    in_pt = {Path(f).stem for f in PYTEST_FILES}
    problems = []
    for name in sorted(on_disk - in_ut - in_pt):
        problems.append(
            f"tests/{name}.py is in neither UNITTEST_MODULES nor PYTEST_FILES "
            "in scripts/run_all_works_tests.py; CI would never run it"
        )
    for name in sorted(in_ut & in_pt):
        problems.append(f"tests/{name}.py is in BOTH UNITTEST_MODULES and PYTEST_FILES")
    for name in sorted((in_ut | in_pt) - on_disk):
        problems.append(f"tests/{name}.py is listed but does not exist")
    return problems


def main() -> int:
    problems = coverage_problems()
    if problems:
        for p in problems:
            print("RUNNER COVERAGE ERROR:", p, file=sys.stderr)
        return 2
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
    modules = UNITTEST_MODULES
    cmd = [sys.executable, "-m", "unittest", *modules, "-v"]
    print("Running:", " ".join(modules))
    proc = subprocess.run(cmd, cwd=str(ROOT), env=env)

    pytest_files = PYTEST_FILES
    py_cmd = [sys.executable, "-m", "pytest", "-q", *pytest_files]
    print("Running (pytest):", " ".join(pytest_files), flush=True)
    py_proc = subprocess.run(py_cmd, cwd=str(ROOT), env=env)

    extra_rc = 0
    for d in EXTRA_PYTEST_DIRS:
        print("Running (pytest):", d, flush=True)
        r = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", d],
            cwd=str(ROOT),
            env=env,
        )
        extra_rc = extra_rc or r.returncode

    return proc.returncode or py_proc.returncode or extra_rc


if __name__ == "__main__":
    raise SystemExit(main())
