"""
Live, runnable tests of the control-law math extracted from
tg_vortex_control_fixed.py. These don't need Dedalus at all -- they
test the pure functions the control loop evaluates every step.
"""
import unittest
import numpy as np

from fluids.control_law import (
    adaptive_re_update,
    adaptive_re_update_clamped,
    adaptive_re_update_unclamped,
    cbf_correction,
)


class TestCBFCorrection(unittest.TestCase):
    def setUp(self):
        self.vort = np.array([1.0, 2.0, -3.0])
        self.gain = 0.05
        self.margin = 0.1

    def test_zero_at_and_above_margin(self):
        out = cbf_correction(self.margin, self.vort, self.gain, self.margin)
        np.testing.assert_array_equal(out, 0 * self.vort)
        out2 = cbf_correction(self.margin + 0.05, self.vort, self.gain, self.margin)
        np.testing.assert_array_equal(out2, 0 * self.vort)

    def test_continuous_across_margin_boundary(self):
        eps = 1e-6
        above = cbf_correction(self.margin + eps, self.vort, self.gain, self.margin)
        below = cbf_correction(self.margin - eps, self.vort, self.gain, self.margin)
        self.assertTrue(np.allclose(above, 0, atol=1e-9))
        self.assertTrue(np.allclose(below, 0, atol=1e-6))

    def test_engages_before_violation_not_just_after(self):
        out = cbf_correction(0.05, self.vort, self.gain, self.margin)
        self.assertFalse(np.allclose(out, 0))

    def test_magnitude_grows_monotonically_as_h_falls(self):
        hs = np.linspace(self.margin, -0.3, 20)
        mags = [np.linalg.norm(cbf_correction(h, self.vort, self.gain, self.margin))
                for h in hs]
        self.assertTrue(all(mags[i] <= mags[i + 1] for i in range(len(mags) - 1)))

    def test_direction_opposes_vorticity(self):
        out = cbf_correction(-0.2, self.vort, self.gain, self.margin)
        ratio = out / self.vort
        self.assertTrue(np.allclose(ratio, ratio[0]))
        self.assertLess(ratio[0], 0)


class TestAdaptiveReUpdate(unittest.TestCase):
    def test_moves_toward_max_when_enstrophy_low(self):
        Re = adaptive_re_update(Ens=0.1, Reynolds=800, Re_target_enstrophy=0.6,
                                 Re_min=400, Re_max=2000)
        self.assertGreater(Re, 800)

    def test_moves_toward_min_when_enstrophy_high(self):
        Re = adaptive_re_update(Ens=1.0, Reynolds=800, Re_target_enstrophy=0.6,
                                 Re_min=400, Re_max=2000)
        self.assertLess(Re, 800)

    def test_default_clamped_near_bounds(self):
        """Default adaptive_re_update is clamped — no overshoot at the edge."""
        Re_max = 2000
        Re = adaptive_re_update(Ens=0.1, Reynolds=1999, Re_target_enstrophy=0.6,
                                 Re_min=400, Re_max=Re_max)
        self.assertLessEqual(Re, Re_max)
        Re_min = 400
        Re2 = adaptive_re_update(Ens=1.0, Reynolds=401, Re_target_enstrophy=0.6,
                                  Re_min=Re_min, Re_max=2000)
        self.assertGreaterEqual(Re2, Re_min)

    def test_unclamped_opt_in_can_overshoot(self):
        """Regression: unclamped is opt-in only and can leave [Re_min, Re_max]."""
        Re_max = 2000
        Re = adaptive_re_update_unclamped(Ens=0.1, Reynolds=1999, Re_target_enstrophy=0.6,
                                          Re_min=400, Re_max=Re_max)
        self.assertGreater(Re, Re_max)
        Re_min = 400
        Re2 = adaptive_re_update_unclamped(Ens=1.0, Reynolds=401, Re_target_enstrophy=0.6,
                                           Re_min=Re_min, Re_max=2000)
        self.assertLess(Re2, Re_min)

    def test_clamped_version_stays_in_bounds(self):
        Re = 1999
        for _ in range(50):
            Re = adaptive_re_update_clamped(0.1, Re, 0.6, 400, 2000)
        self.assertLessEqual(Re, 2000)
        self.assertGreaterEqual(Re, 400)


if __name__ == "__main__":
    unittest.main(verbosity=2)
