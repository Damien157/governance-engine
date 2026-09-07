#!/usr/bin/env python3
"""Full bug-finding harness: live gates + maths + sketches + external tools.

Usage:
  python scripts/test_harness.py

Sections: LIVE / MATH / SKETCH / EXTERNAL
Exit non-zero on any failure.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

KNOWN_DOCUMENTED_ISSUES = [
    "adaptive Re overshoot without clamp (use adaptive_re_update_clamped)",
    "OSQP PositionCBF closed-loop is NOT forward-invariant (sketch honesty)",
    "TG Dedalus: no sym_grad (fluids smoke skips / incomplete without Dedalus)",
]


def _env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [
            str(ROOT),
            str(ROOT / "src"),
            str(ROOT / "hais"),
            str(ROOT / "imprint" / "src"),
            str(ROOT / "haven2" / "src"),
            env.get("PYTHONPATH", ""),
        ]
    )
    return env


@dataclass
class SectionResult:
    name: str
    ok: bool
    detail: str = ""
    tests_run: int = 0
    failures: int = 0
    errors: int = 0
    skipped: int = 0
    lines: list[str] = field(default_factory=list)


def _parse_unittest_counts(text: str) -> tuple[int, int, int, int]:
    run = fail = err = skip = 0
    m = re.search(r"Ran (\d+) tests? in ", text)
    if m:
        run = int(m.group(1))
    m2 = re.search(r"failures=(\d+)", text)
    if m2:
        fail = int(m2.group(1))
    m3 = re.search(r"errors=(\d+)", text)
    if m3:
        err = int(m3.group(1))
    m4 = re.search(r"skipped=(\d+)", text)
    if m4:
        skip = int(m4.group(1))
    return run, fail, err, skip


def _parse_pytest_counts(text: str) -> tuple[int, int, int, int]:
    passed = failed = errors = skipped = 0
    m = re.search(r"(\d+)\s+passed\b", text)
    if m:
        passed = int(m.group(1))
    m = re.search(r"(\d+)\s+failed\b", text)
    if m:
        failed = int(m.group(1))
    m = re.search(r"(\d+)\s+errors?\b", text)
    if m:
        errors = int(m.group(1))
    m = re.search(r"(\d+)\s+skipped\b", text)
    if m:
        skipped = int(m.group(1))
    run = passed + failed + errors + skipped
    return run, failed, errors, skipped


def run_unittest(modules: list[str], label: str) -> SectionResult:
    cmd = [sys.executable, "-m", "unittest", *modules, "-v"]
    proc = subprocess.run(
        cmd,
        cwd=str(ROOT),
        env=_env(),
        capture_output=True,
        text=True,
    )
    text = (proc.stdout or "") + (proc.stderr or "")
    run, fail, err, skip = _parse_unittest_counts(text)
    ok = proc.returncode == 0
    return SectionResult(
        name=label,
        ok=ok,
        detail=f"unittest {' '.join(modules)}",
        tests_run=run,
        failures=fail,
        errors=err,
        skipped=skip,
        lines=text.splitlines()[-40:],
    )


def run_pytest(files: list[str], label: str) -> SectionResult:
    cmd = [sys.executable, "-m", "pytest", *files, "-v", "--tb=short"]
    proc = subprocess.run(
        cmd,
        cwd=str(ROOT),
        env=_env(),
        capture_output=True,
        text=True,
    )
    text = (proc.stdout or "") + (proc.stderr or "")
    run, fail, err, skip = _parse_pytest_counts(text)
    ok = proc.returncode == 0
    return SectionResult(
        name=label,
        ok=ok,
        detail=f"pytest {' '.join(files)}",
        tests_run=run,
        failures=fail,
        errors=err,
        skipped=skip,
        lines=text.splitlines()[-40:],
    )


def print_section(title: str, results: list[SectionResult]) -> None:
    bar = "=" * 60
    print(f"\n{bar}")
    print(f"  {title}")
    print(bar)
    for r in results:
        status = "PASS" if r.ok else "FAIL"
        print(
            f"  [{status}] {r.detail}  "
            f"(ran={r.tests_run} fail={r.failures} err={r.errors} skip={r.skipped})"
        )
        if not r.ok:
            for line in r.lines[-20:]:
                print(f"    | {line}")


def main() -> int:
    print("Governance-engine bug-finding harness")
    print(f"ROOT={ROOT}")
    print(f"python={sys.executable}")

    live_modules = [
        "tests.test_all_works",
        "tests.test_unified",
        "tests.test_governed_stack",
        "tests.test_governed_mail",
        "tests.test_governed_calendar",
        "tests.test_governed_social",
        "tests.test_runtime_bridge",
        "tests.test_review_path",
        "tests.test_review_ops",
        "tests.test_key_providers",
        "tests.test_observability",
        "tests.test_contracts",
        "tests.test_sidecar",
        "tests.test_audit_soak",
    ]
    math_modules = [
        "tests.test_math_cores",
        "tests.test_control_logic",
    ]
    sketch_modules = [
        "tests.test_osqp_governance_engine",
        "tests.test_hais_osqp_clf_cbf_engine",
        "tests.test_invariant_topology",
    ]
    pytest_files = [
        "tests/test_lie_and_constraints.py",
        "tests/test_safety_sim.py",
    ]

    live_ut = run_unittest(live_modules, "LIVE-unittest")
    live_py = run_pytest(pytest_files, "LIVE-pytest-math")
    math_ut = run_unittest(math_modules, "MATH-unittest")
    sketch_ut = run_unittest(sketch_modules, "SKETCH-unittest")
    external_ut = run_unittest(["tests.test_math_cores.TestCDCL"], "EXTERNAL-cdcl")

    print_section("LIVE", [live_ut, live_py])
    print_section("MATH", [math_ut])
    print_section("SKETCH", [sketch_ut])
    print_section("EXTERNAL", [external_ut])

    all_results = [live_ut, live_py, math_ut, sketch_ut, external_ut]
    total_run = sum(r.tests_run for r in all_results)
    total_fail = sum(r.failures + r.errors for r in all_results)
    unique_note = (
        "Note: TestCDCL also runs under MATH; EXTERNAL re-runs that class for the report."
    )

    print("\n" + "=" * 60)
    print("  SUMMARY")
    print("=" * 60)
    print(
        f"  Section test counts (sum, with CDCL double-count): "
        f"ran={total_run} failures+errors={total_fail}"
    )
    print(f"  {unique_note}")
    any_fail = any(not r.ok for r in all_results)
    print(f"  Overall: {'FAIL' if any_fail else 'PASS'}")

    print("\nKnown documented issues")
    for issue in KNOWN_DOCUMENTED_ISSUES:
        print(f"  - {issue}")

    return 1 if any_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
