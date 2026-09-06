#!/usr/bin/env python3
"""Run the Phase 0 3DM imprint simulation and write artifacts."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from imprint.pipeline import run_pipeline


def main() -> int:
    artifacts = ROOT / "artifacts"
    out = run_pipeline(artifacts)
    demo = out["summary"]["demo"]
    print("Phase 0 imprint pipeline (3DM) — empirical simulation")
    print(out["summary"]["guardrail"])
    print()
    wd = out["summary"]["weight_defaults"]
    print(f"weights: α={wd['alpha']}  β={wd['beta']}  γ={wd['gamma']}")
    print(f"  naive:    {wd['modes']['naive']}")
    print(f"  reversed: {wd['modes']['reversed']}")
    print(f"  {wd['note']}")
    print()
    print(f"demo instance: |S|={demo['abs_S']}  |L|={demo['abs_L']}  "
          f"|L|/|S|={demo['reduction_L_over_S']:.4f}")
    print(
        f"hit P(valid): naive={demo['hit_imprint_naive']:.4f}  "
        f"reversed={demo['hit_imprint_reversed']:.4f}  "
        f"uniform={demo['hit_uniform']:.4f}  "
        f"greedy_rand={demo['hit_greedy_rand']:.4f}  "
        f"greedy_det={demo['hit_greedy_det']:.4f}"
    )
    print(f"ttfv mean draws: reversed={demo['ttfv_imprint_mean']:.2f}  "
          f"naive={demo['ttfv_naive_mean']:.2f}  "
          f"uniform={demo['ttfv_uniform_mean']:.2f}")
    print(f"soft leaked invalids (must be 0): {demo['soft_leaked_invalids']}")
    print(f"greedy_prior max on invalid (must be 0): "
          f"{demo['greedy_prior_max_on_invalid']}")
    print(f"motif filter equals L: {demo['motif_equals_L']}")
    print()
    print("per-instance (naive | reversed | uniform | greedy):")
    for row in out["rows"]:
        print(
            f"  {row['instance']:12s}  |S|={row['abs_S']:4d}  |L|={row['abs_L']:4d}  "
            f"naive={row['hit_imprint_naive']:.3f}  "
            f"reversed={row['hit_imprint_reversed']:.3f}  "
            f"uniform={row['hit_uniform']:.3f}  "
            f"greedy={row['hit_greedy_rand']:.3f}"
        )
    print()
    print(f"artifacts -> {artifacts}")
    for name in out["summary"]["artifacts"]:
        print(f"  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
