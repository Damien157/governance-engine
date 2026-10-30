#!/usr/bin/env python3
"""Run EVERY test in the repo with one command, then print a per-area summary.

    python scripts/run_all_works_tests.py             # everything
    python scripts/run_all_works_tests.py --area mail # one area (repeatable)
    python scripts/run_all_works_tests.py --list      # show file -> area map

Auto-discovers tests/test_*.py (no hand-maintained list, so a new test file is
never silently skipped) and runs them with pytest (which runs both unittest- and
pytest-style tests). Sub-project test folders (EXTRA_PYTEST_DIRS) run as their
own pytest invocations. Extra args after ``--`` go to pytest. Exit code is
non-zero if any invocation fails.

Coverage guard: every discovered tests/test_*.py must classify into a *named*
area in AREAS. A file that matches no area is reported by coverage_problems(),
which makes main() exit 2 and fails tests/test_runner_coverage.py, so a test
file can never be silently unlisted/unclassified.

Repo root is put ahead of hais/ so ``tests`` resolves to ./tests.
(hais/tests was renamed to hais/hais_tests to avoid package shadowing.)
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# First matching pattern wins. A tests/test_*.py matching none is a coverage
# problem (see coverage_problems()): add an area/pattern here for new files.
AREAS: list[tuple[str, str]] = [
    # Mathematics suite (docs/MATH_TESTS.md); listed first so it wins over the generic patterns below.
    ("mathematics", r"test_math_(?!cores)"),
    ("sealed_mail", r"sealed_mail"),
    ("mail", r"governed_mail"),
    ("calendar", r"calendar"),
    ("social_posts", r"social"),
    ("tool_agent_0.7", r"governed_tool|action_bus|connectors|lint_no_bypass"),
    ("bio_judge_kernel_overlay", r"bio_"),
    ("audit", r"audit|spectral|observability|review"),
    ("control_loop_qp", r"osqp|clf_cbf|control_logic|math_cores|lie_and|safety_sim|qp_controller|invariant"),
    ("governance_gate", r"governed_stack|stack_qp|contracts|key_providers|sidecar|runtime_bridge|governed_algorithm|runner_coverage"),
    ("unified_all_works", r"unified|all_works|quantum_line"),
]

# Sub-project test folders, each run as its own pytest invocation and reported
# as its own area. (hais_tests ZK slow tests skip unless ZK_RUN_SLOW=1.)
EXTRA_PYTEST_DIRS = [
    "haven2/tests",
    "imprint/tests",
    "hais/hais_tests",
]

UNCLASSIFIED = "other"


def area_of(name: str) -> str:
    for area, pat in AREAS:
        if re.search(pat, name):
            return area
    return UNCLASSIFIED


def discover(root: Path = ROOT) -> list[Path]:
    return sorted((root / "tests").glob("test_*.py"))


def coverage_problems(root: Path = ROOT) -> list[str]:
    """Human-readable problems: test files that would not be run or classified."""
    problems = []
    for f in discover(root):
        if area_of(f.stem) == UNCLASSIFIED:
            problems.append(
                f"tests/{f.name} matches no area in AREAS in "
                "scripts/run_all_works_tests.py; add a pattern so it is classified"
            )
    for d in EXTRA_PYTEST_DIRS:
        if not (root / d).is_dir():
            problems.append(f"{d} is in EXTRA_PYTEST_DIRS but does not exist")
    return problems


def _run_pytest(targets: list[str], env: dict, extra: list[str], area_for) -> tuple[int, dict]:
    """Run pytest once; return (exit code, {area: {passed/failed/skipped: n}})."""
    fd, xml_name = tempfile.mkstemp(suffix=".xml")
    os.close(fd)
    xml_path = Path(xml_name)
    cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
           f"--junitxml={xml_path}", *targets, *extra]
    rc = subprocess.run(cmd, cwd=str(ROOT), env=env).returncode
    stats: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    try:
        for tc in ET.parse(xml_path).iter("testcase"):
            kind = "passed"
            for child in tc:
                if child.tag in ("failure", "error"):
                    kind = "failed"
                elif child.tag == "skipped":
                    kind = "skipped"
            stats[area_for(tc)][kind] += 1
    except (ET.ParseError, FileNotFoundError):
        print("(no junit report; pytest probably failed to start)")
        rc = rc or 1
    finally:
        xml_path.unlink(missing_ok=True)
    return rc, stats


def _tests_area(tc: ET.Element) -> str:
    parts = (tc.get("classname") or "").split(".")
    stem = next((p for p in parts if p.startswith("test_")), tc.get("file", UNCLASSIFIED))
    return area_of(stem)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--area", action="append", help="only run this area (repeatable)")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("pytest_args", nargs="*")
    a = ap.parse_args(argv)

    problems = coverage_problems()
    if problems:
        for p in problems:
            print("RUNNER COVERAGE ERROR:", p, file=sys.stderr)
        return 2

    by_area: dict[str, list[Path]] = defaultdict(list)
    for f in discover():
        by_area[area_of(f.stem)].append(f)
    extra_areas = {Path(d).parts[0]: d for d in EXTRA_PYTEST_DIRS}
    if a.list:
        for area in sorted(by_area):
            print(f"{area}:")
            for f in by_area[area]:
                print(f"  {f.relative_to(ROOT)}")
        for area, d in extra_areas.items():
            print(f"{area}:\n  {d}/")
        return 0

    files = [f for fs in by_area.values() for f in fs]
    extra_dirs = dict(extra_areas)
    if a.area:
        unknown = set(a.area) - set(by_area) - set(extra_areas)
        if unknown:
            print(f"unknown area(s): {sorted(unknown)}; known: {sorted(set(by_area) | set(extra_areas))}")
            return 2
        files = [f for ar in a.area for f in by_area.get(ar, [])]
        extra_dirs = {k: v for k, v in extra_areas.items() if k in a.area}

    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [str(ROOT), str(ROOT / "src"), str(ROOT / "hais"), str(ROOT / "imprint" / "src"),
         str(ROOT / "haven2" / "src"), env.get("PYTHONPATH", "")]
    )

    all_stats: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    rcs = []

    def merge(stats: dict) -> None:
        for area, s in stats.items():
            for k, v in s.items():
                all_stats[area][k] += v

    if files:
        print(f"Running {len(files)} test files via pytest", flush=True)
        rc, st = _run_pytest([str(f.relative_to(ROOT)) for f in files], env,
                             a.pytest_args, _tests_area)
        rcs.append(rc)
        merge(st)
    for area, d in extra_dirs.items():
        print(f"Running (pytest): {d}", flush=True)
        rc, st = _run_pytest([d], env, a.pytest_args, lambda _tc, _a=area: _a)
        rcs.append(rc)
        merge(st)
    rc = next((r for r in rcs if r), 0)

    print("\nPer-area summary")
    print(f"{'area':28} {'passed':>7} {'failed':>7} {'skipped':>8}")
    tot: dict[str, int] = defaultdict(int)
    for area in sorted(all_stats):
        s = all_stats[area]
        for k in ("passed", "failed", "skipped"):
            tot[k] += s[k]
        flag = "  <-- FAIL" if s["failed"] else ""
        print(f"{area:28} {s['passed']:7} {s['failed']:7} {s['skipped']:8}{flag}")
    print(f"{'TOTAL':28} {tot['passed']:7} {tot['failed']:7} {tot['skipped']:8}")
    print("RESULT:", "PASS" if rc == 0 else f"FAIL (pytest exit {rc})")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
