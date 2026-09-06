"""Smoke test for diagnostics/invariant_topology harness (seed=2026)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from diagnostics.invariant_topology import run_rigorous_diagnostics_test


class TestInvariantTopology(unittest.TestCase):
    def test_seed_2026_flags_barriers_and_circular(self):
        results = run_rigorous_diagnostics_test(seed=2026)
        barrier_count = sum(1 for r in results.values() if r.barrier_suspected)
        circular_hits = sum(1 for r in results.values() if r.circular_invariants)
        self.assertGreater(barrier_count, 0)
        self.assertGreater(circular_hits, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
