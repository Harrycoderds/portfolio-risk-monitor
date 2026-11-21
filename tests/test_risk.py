"""
Sanity tests with hand-checkable answers.
Run:  python -m unittest tests/test_risk.py     (or  python tests/test_risk.py)
"""
import os
import sys
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.risk import (basel_zone, expected_shortfall, hist_var, kupiec_pof,  # noqa: E402
                      rolling_var_es)


class TestVaRES(unittest.TestCase):
    def setUp(self):
        # Returns of -1%, -2%, ..., -100%: a sorted list we can reason about by hand.
        self.r = -np.arange(1, 101) / 100

    def test_var_matches_percentile(self):
        # 5th percentile (Excel PERCENTILE.INC) of -1.00..-0.01:
        # position = 0.05 * 99 = 4.95 -> between the 5th (-0.96) and 6th (-0.95) smallest
        # = -0.96 + 0.95 * 0.01 = -0.9505  -> VaR95 = 0.9505
        self.assertAlmostEqual(hist_var(self.r, 0.95), 0.9505, places=10)
        # 1st percentile: position 0.99 -> -1.00 + 0.99*0.01 = -0.9901
        self.assertAlmostEqual(hist_var(self.r, 0.99), 0.9901, places=10)

    def test_es_is_average_of_tail(self):
        # Returns at or below -0.9505 are -1.00..-0.96 (5 values) -> mean -0.98
        self.assertAlmostEqual(expected_shortfall(self.r, 0.95), 0.98, places=10)
        # At 99% only -1.00 is at or below -0.9901
        self.assertAlmostEqual(expected_shortfall(self.r, 0.99), 1.00, places=10)

    def test_small_array(self):
        # 20 daily returns (in %); 5th percentile position = 0.05*19 = 0.95
        r = np.array([-5, -3, -2, -1, 0, 1, 2, 1, 0, -1, 2, 3, 1, 0, -2, 1, 2, 0, 1, 4]) / 100
        # sorted: -5, -3, ... -> -0.05 + 0.95*(-0.03 - -0.05) = -0.031
        self.assertAlmostEqual(hist_var(r, 0.95), 0.031, places=10)
        self.assertAlmostEqual(expected_shortfall(r, 0.95), 0.05, places=10)

    def test_ordering(self):
        rng = np.random.default_rng(0)
        r = rng.standard_t(df=4, size=1000) / 100
        self.assertGreater(hist_var(r, 0.99), hist_var(r, 0.95))
        for a in (0.95, 0.99):
            self.assertGreaterEqual(expected_shortfall(r, a), hist_var(r, a))


class TestKupiec(unittest.TestCase):
    def test_perfect_calibration(self):
        # 2 exceptions in 200 days at 1% = exactly the expected rate -> LR = 0
        k = kupiec_pof(200, 2, 0.01)
        self.assertAlmostEqual(k["lr_stat"], 0.0, places=10)
        self.assertFalse(k["reject_5pct"])

    def test_too_many_exceptions(self):
        # n=250, x=10, p=0.01. By hand:
        # log L0 = 240*ln(0.99) + 10*ln(0.01) = -2.41208 - 46.05170 = -48.46378
        # log L1 = 240*ln(0.96) + 10*ln(0.04) = -9.79728 - 32.18876 = -41.98604
        # LR = -2*(-48.46378 + 41.98604) = 12.9555 > 3.84 (chi2 5% critical) -> reject
        k = kupiec_pof(250, 10, 0.01)
        self.assertAlmostEqual(k["lr_stat"], 12.9555, places=3)
        self.assertTrue(k["reject_5pct"])

    def test_zero_exceptions(self):
        # x=0: LR = -2*250*ln(0.99) = 5.025 -> also rejects (too FEW exceptions)
        k = kupiec_pof(250, 0, 0.01)
        self.assertAlmostEqual(k["lr_stat"], -2 * 250 * np.log(0.99), places=10)


class TestBaselAndLookAhead(unittest.TestCase):
    def test_basel_zone_boundaries(self):
        self.assertEqual(basel_zone(0), "GREEN")
        self.assertEqual(basel_zone(4), "GREEN")
        self.assertEqual(basel_zone(5), "YELLOW")
        self.assertEqual(basel_zone(9), "YELLOW")
        self.assertEqual(basel_zone(10), "RED")

    def test_no_look_ahead(self):
        # Changing day t's return must not change the VaR forecast for day t.
        rng = np.random.default_rng(1)
        r = pd.Series(rng.normal(0, 0.01, 400))
        var_before, _ = rolling_var_es(r, 0.99, window=250)
        r2 = r.copy()
        r2.iloc[300] = -0.50          # a huge crash on day 300
        var_after, _ = rolling_var_es(r2, 0.99, window=250)
        self.assertEqual(var_before.iloc[300], var_after.iloc[300])   # same forecast
        self.assertGreater(var_after.iloc[301], var_before.iloc[301])  # next day reacts
        self.assertTrue(np.isnan(var_before.iloc[249]))   # no forecast without 250 days


if __name__ == "__main__":
    unittest.main()
