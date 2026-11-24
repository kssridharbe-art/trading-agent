"""
Unit Tests for Indicator Calculations: SuperTrend and EMA 9.
Verifies mathematical precision, band consistency, and equivalence
between batch DataFrame calculations and stateful streaming calculations.
"""

import unittest
import numpy as np
import pandas as pd

from delta_trading_agent.indicators.supertrend import calculate_supertrend, StatefulSuperTrend
from delta_trading_agent.indicators.ema import calculate_ema, StatefulEMA


class TestIndicators(unittest.TestCase):
    def setUp(self):
        # Generate synthetic OHLC dataset with known trend switches
        np.random.seed(42)
        n = 100
        base_price = 50000.0
        # Create an oscillating price path
        trend = np.sin(np.linspace(0, 4 * np.pi, n)) * 2000.0
        noise = np.random.normal(0, 100, n)
        close = base_price + trend + noise
        high = close + np.random.uniform(50, 200, n)
        low = close - np.random.uniform(50, 200, n)
        open_p = (high + low) / 2.0

        self.df = pd.DataFrame({
            "timestamp": np.arange(1700000000, 1700000000 + n * 300, 300),
            "open": open_p,
            "high": high,
            "low": low,
            "close": close,
            "volume": np.random.uniform(10, 50, n)
        })

    def test_supertrend_batch_calculation(self):
        result_df = calculate_supertrend(self.df, length=10, factor=3.0)
        self.assertIn("atr", result_df.columns)
        self.assertIn("upper_band", result_df.columns)
        self.assertIn("lower_band", result_df.columns)
        self.assertIn("supertrend", result_df.columns)
        self.assertIn("trend", result_df.columns)

        # Trends after warmup (length-1 onwards) should only be +1 (Green) or -1 (Red)
        valid_trends = set(result_df["trend"].iloc[9:].unique())
        self.assertTrue(valid_trends.issubset({1, -1}))

        # When trend is 1, supertrend must equal lower_band
        # When trend is -1, supertrend must equal upper_band
        bullish = result_df.iloc[9:][result_df.iloc[9:]["trend"] == 1]
        bearish = result_df.iloc[9:][result_df.iloc[9:]["trend"] == -1]

        np.testing.assert_allclose(bullish["supertrend"], bullish["lower_band"])
        np.testing.assert_allclose(bearish["supertrend"], bearish["upper_band"])

    def test_stateful_vs_batch_supertrend_parity(self):
        batch_df = calculate_supertrend(self.df, length=10, factor=3.0)
        stateful = StatefulSuperTrend(length=10, factor=3.0)

        for i, row in self.df.iterrows():
            res = stateful.update(row["high"], row["low"], row["close"])
            if i >= 10:  # After warmup
                self.assertIsNotNone(res)
                # Verify values match batch within float tolerances
                self.assertAlmostEqual(res.atr, batch_df.loc[i, "atr"], places=5)
                self.assertAlmostEqual(res.upper_band, batch_df.loc[i, "upper_band"], places=5)
                self.assertAlmostEqual(res.lower_band, batch_df.loc[i, "lower_band"], places=5)
                self.assertEqual(res.trend, batch_df.loc[i, "trend"])
                self.assertAlmostEqual(res.supertrend, batch_df.loc[i, "supertrend"], places=5)

    def test_ema_batch_and_stateful_parity(self):
        batch_df = calculate_ema(self.df, period=9)
        stateful = StatefulEMA(period=9)

        for i, row in self.df.iterrows():
            res = stateful.update(row["close"])
            if i >= 9:
                self.assertIsNotNone(res)
                self.assertAlmostEqual(res.ema, batch_df.loc[i, "ema_9"], places=4)
                if i > 9:
                    expected_slope = batch_df.loc[i, "ema_9"] - batch_df.loc[i - 1, "ema_9"]
                    self.assertAlmostEqual(res.slope, expected_slope, places=4)


if __name__ == "__main__":
    unittest.main()
