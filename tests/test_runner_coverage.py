"""Guard: every tests/test_*.py must be in exactly one runner list."""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_runner():
    spec = importlib.util.spec_from_file_location(
        "run_all_works_tests", ROOT / "scripts" / "run_all_works_tests.py"
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


class TestRunnerCoverage(unittest.TestCase):
    def test_every_test_file_listed_exactly_once(self) -> None:
        problems = _load_runner().coverage_problems()
        self.assertEqual(problems, [], "\n" + "\n".join(problems))


if __name__ == "__main__":
    unittest.main()
