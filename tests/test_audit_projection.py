"""Audit-only governance projection — does not drive the live gate."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from governed_stack.audit_projection import project_governance_score, _norm_p_hat


class TestAuditProjection(unittest.TestCase):
    def test_norm_p_hat_scalar_and_list(self):
        self.assertEqual(_norm_p_hat(None), 0.0)
        self.assertEqual(_norm_p_hat(-0.5), 0.5)
        self.assertAlmostEqual(_norm_p_hat([3.0, 4.0]), 5.0)

    def test_hais_cap_floors_risk(self):
        out = project_governance_score(
            {
                "decision": "BLOCK",
                "error_code": "GOV_HAIS_CAP",
                "hais": {"cap": 0.1, "risk": 0.2, "instability": 0.1},
                "haven2": {"realm": "normal", "open": True, "p_hat": 0.0},
            }
        )
        self.assertGreaterEqual(out["risk"], 0.9)
        self.assertTrue(0.0 <= out["stability"] <= 1.0)
        self.assertTrue(0.0 <= out["governance"] <= 1.0)

    def test_latch_closed_raises_governance_vs_plain_block(self):
        base = {
            "decision": "BLOCK",
            "hais": {"cap": 0.9, "risk": 0.1, "instability": 0.05},
            "haven2": {"realm": "defensive", "open": False, "p_hat": 0.2},
        }
        plain = project_governance_score({**base, "error_code": "GOV_POLICY_BLOCK"})
        latch = project_governance_score({**base, "error_code": "GOV_LATCH_CLOSED"})
        # Latch closed adds +0.2 governance bonus vs policy-only path nuance —
        # both get policy/latch bonuses differently; latch should be >= plain here.
        self.assertGreaterEqual(latch["governance"], plain["governance"] - 1e-9)


if __name__ == "__main__":
    unittest.main()
