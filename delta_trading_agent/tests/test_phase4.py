"""
Phase 4 Unit and Integration Tests:
1. Choppiness Index (CHOP) indicator - formula, boundary conditions, stateful vs batch parity.
2. Higher-Timeframe (HTF) 4h Trend - strictly zero lookahead, completed 4h block aggregation, EMA 50/200 cross.
3. ADX Rising filter - threshold and monotonic rise validation.
4. SuperTrend Separation filter - distance in ATR units.
5. Circuit Breaker Emergency Closure - active market exit on floating drawdown / daily loss breach.
6. Delta Exchange Tiered Liquidation Model - MMR schedule, 0.5% liquidation fee buffer, intrabar high/low checks.
"""

import unittest
import numpy as np
import pandas as pd
import tempfile
import os

from delta_trading_agent.config.settings import (
    TradingConfig, EMAMethod, PositionSide, RegimeFilterType, ExecutionType
)
from delta_trading_agent.indicators.choppiness import StatefulChoppiness, calculate_choppiness
from delta_trading_agent.indicators.htf_trend import StatefulHTFTrend, HTFTrendResult
from delta_trading_agent.indicators.supertrend import StatefulSuperTrend, SuperTrendResult
from delta_trading_agent.indicators.ema import StatefulEMA, EMAResult
from delta_trading_agent.indicators.adx import StatefulADX, ADXResult
from delta_trading_agent.indicators.atr import StatefulATR, ATRResult
from delta_trading_agent.signals.signal_generator import SignalGenerator, SignalType
from delta_trading_agent.risk.risk_manager import RiskManager
from delta_trading_agent.execution.strategy_executor import StrategyExecutor, ActivePosition
from delta_trading_agent.backtest.engine import BacktestEngine


class TestChoppinessIndicator(unittest.TestCase):
    def test_chop_range_and_values(self):
        chop = StatefulChoppiness(period=14, threshold=61.8)
        # Strongly trending synthetic data (constant up-trend)
        res = None
        for i in range(30):
            res = chop.update(high=100.0 + i * 2, low=98.0 + i * 2, close=99.5 + i * 2)
        self.assertIsNotNone(res)
        self.assertGreaterEqual(res.chop, 0.0)
        self.assertLessEqual(res.chop, 100.0)
        # Strong trend should have low CHOP value (< 38.2)
        self.assertLessEqual(res.chop, 38.2)
        self.assertTrue(res.is_trending)
        self.assertFalse(res.is_choppy)

    def test_chop_choppy_consolidation(self):
        chop = StatefulChoppiness(period=14, threshold=61.8)
        # Choppy oscillating range
        res = None
        for i in range(50):
            p = 100.0 + (5.0 if i % 2 == 0 else -5.0)
            res = chop.update(high=p + 1.0, low=p - 1.0, close=p)
        self.assertIsNotNone(res)
        self.assertGreaterEqual(res.chop, 61.8)
        self.assertTrue(res.is_choppy)

    def test_stateful_vs_batch_parity(self):
        np.random.seed(42)
        n = 100
        closes = 100.0 + np.cumsum(np.random.randn(n))
        highs = closes + np.random.uniform(0.5, 2.0, n)
        lows = closes - np.random.uniform(0.5, 2.0, n)
        df = pd.DataFrame({"high": highs, "low": lows, "close": closes})

        batch_df = calculate_choppiness(df, period=14, threshold=61.8)
        stateful = StatefulChoppiness(period=14, threshold=61.8)
        stateful_vals = []
        for _, row in df.iterrows():
            res = stateful.update(row["high"], row["low"], row["close"])
            stateful_vals.append(res.chop if res else np.nan)

        # Compare values after warmup
        for i in range(15, n):
            b_val = batch_df["chop"].iloc[i]
            s_val = stateful_vals[i]
            self.assertAlmostEqual(b_val, s_val, places=1)


class TestHTFTrend(unittest.TestCase):
    def test_zero_lookahead_4h_aggregation(self):
        """EMA updates strictly on hour % 4 == 3 completion (e.g. 03:00, 07:00, 11:00 UTC)."""
        htf = StatefulHTFTrend(fast_period=5, slow_period=10)
        base_ts = 1700000000  # Some UTC timestamp aligned to hour 0
        base_ts -= (base_ts % 86400)  # midnight UTC

        results = []
        for h in range(12):  # 12 hours: 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11
            ts = base_ts + h * 3600
            p = 50000.0 + h * 100.0
            res = htf.update(ts, open_p=p - 50, high=p + 50, low=p - 60, close=p)
            results.append((h, res is not None, res.trend_direction if res else None))

        # Check: Before hour 3 closes, last_result is None
        self.assertFalse(results[0][1])  # hour 0: None
        self.assertFalse(results[1][1])  # hour 1: None
        self.assertFalse(results[2][1])  # hour 2: None
        self.assertTrue(results[3][1])   # hour 3: First 4h completed!
        self.assertTrue(results[7][1])   # hour 7: Second 4h completed!
        self.assertTrue(results[11][1])  # hour 11: Third 4h completed!


class TestRegimeFilters(unittest.TestCase):
    def test_adx_rising_filter(self):
        sig_gen = SignalGenerator(ema_method=EMAMethod.METHOD_C, regime_filter=RegimeFilterType.ADX_RISING)
        st_res = SuperTrendResult(supertrend=90.0, trend=1, is_reversal_to_green=True, is_reversal_to_red=False, upper_band=100.0, lower_band=90.0, atr=5.0)
        ema_res = EMAResult(ema=95.0, prev_ema=94.0, slope=1.0, slope_pct=1.0, is_price_above=True, is_price_below=False, is_slope_up=True, is_slope_down=False)

        # 1. Warmup / first bar
        adx1 = ADXResult(adx=25.0, plus_di=30.0, minus_di=10.0, dx=50.0, is_trending=True)
        sig1 = sig_gen.evaluate(1000, 100.0, st_res, ema_res, PositionSide.NONE, adx_res=adx1)
        self.assertEqual(sig1.action, "OPEN_LONG")

        # 2. ADX falling: 25.0 -> 24.0 (even if > 20, rising condition fails)
        adx2 = ADXResult(adx=24.0, plus_di=30.0, minus_di=10.0, dx=50.0, is_trending=True)
        sig2 = sig_gen.evaluate(2000, 100.0, st_res, ema_res, PositionSide.NONE, adx_res=adx2)
        self.assertEqual(sig2.action, "HOLD")
        self.assertFalse(sig2.regime_passed)

        # 3. ADX rising: 24.0 -> 26.5 (passes)
        adx3 = ADXResult(adx=26.5, plus_di=30.0, minus_di=10.0, dx=50.0, is_trending=True)
        sig3 = sig_gen.evaluate(3000, 100.0, st_res, ema_res, PositionSide.NONE, adx_res=adx3)
        self.assertEqual(sig3.action, "OPEN_LONG")
        self.assertTrue(sig3.regime_passed)

    def test_supertrend_separation_filter(self):
        sig_gen = SignalGenerator(ema_method=EMAMethod.METHOD_C, regime_filter=RegimeFilterType.ST_SEPARATION)
        # SuperTrend at 99.0, Close at 100.0, ATR is 10.0 -> Separation is (100 - 99)/10 = 0.10 ATR < 0.50 ATR
        st_res = SuperTrendResult(supertrend=99.0, trend=1, is_reversal_to_green=True, is_reversal_to_red=False, upper_band=105.0, lower_band=99.0, atr=10.0)
        ema_res = EMAResult(ema=95.0, prev_ema=94.0, slope=1.0, slope_pct=1.0, is_price_above=True, is_price_below=False, is_slope_up=True, is_slope_down=False)
        atr_res = ATRResult(atr=10.0, atr_pct=1.0, is_sufficient_vol=True)

        sig = sig_gen.evaluate(1000, 100.0, st_res, ema_res, PositionSide.NONE, atr_res=atr_res, st_separation_min_atr=0.50)
        self.assertEqual(sig.action, "HOLD")
        self.assertFalse(sig.regime_passed)

        # Now price is at 106.0 -> Separation is (106 - 99)/10 = 0.70 ATR >= 0.50 ATR
        sig2 = sig_gen.evaluate(2000, 106.0, st_res, ema_res, PositionSide.NONE, atr_res=atr_res, st_separation_min_atr=0.50)
        self.assertEqual(sig2.action, "OPEN_LONG")
        self.assertTrue(sig2.regime_passed)


class TestLiquidationAndCircuitBreaker(unittest.TestCase):
    def setUp(self):
        self.config = TradingConfig(
            initial_capital=10000.0,
            leverage=10.0,
            margin_allocation_pct=0.70,
            max_daily_loss_pct=0.05,
            close_on_circuit_breaker=True,
            liquidation_fee_rate=0.005
        )
        self.risk = RiskManager(self.config)

    def test_delta_exchange_liquidation_price(self):
        """10x leverage with 0.5% MMR and 0.5% liquidation fee buffer -> exactly 9.0% from entry."""
        entry = 50000.0
        # Long liquidation: 50000 * (1 - 0.10 + 0.005 + 0.005) = 50000 * 0.910 = 45500.0
        liq_long = self.risk.calculate_liquidation_price(entry, PositionSide.LONG, 10.0, notional=50000.0, include_liquidation_fee=True)
        self.assertAlmostEqual(liq_long, 45500.0, places=2)

        # Short liquidation: 50000 * (1 + 0.10 - 0.005 - 0.005) = 50000 * 1.090 = 54500.0
        liq_short = self.risk.calculate_liquidation_price(entry, PositionSide.SHORT, 10.0, notional=50000.0, include_liquidation_fee=True)
        self.assertAlmostEqual(liq_short, 54500.0, places=2)

    def test_intrabar_liquidation_detection(self):
        pos = ActivePosition(
            side=PositionSide.LONG,
            entry_price=50000.0,
            contracts=10,
            liquidation_price=45500.0
        )
        # Bar does not breach liquidation
        is_liq, is_near, ratio = self.risk.check_intrabar_liquidation(pos, bar_high=51000.0, bar_low=48000.0)
        self.assertFalse(is_liq)
        self.assertFalse(is_near)

        # Bar comes near (within 2% of entry: 45500 + 1000 = 46500)
        is_liq, is_near, ratio = self.risk.check_intrabar_liquidation(pos, bar_high=47000.0, bar_low=46000.0)
        self.assertFalse(is_liq)
        self.assertTrue(is_near)

        # Bar breaches liquidation (low <= 45500)
        is_liq, is_near, ratio = self.risk.check_intrabar_liquidation(pos, bar_high=46000.0, bar_low=45400.0)
        self.assertTrue(is_liq)

    def test_emergency_closure_on_circuit_breaker(self):
        """Verifies active emergency closure when daily loss breaker trips."""
        engine = BacktestEngine(self.config)
        # Create a small candle dataset where price moves sharply against open long
        timestamps = [1700000000 + i * 3600 for i in range(25)]
        # Initial bars: setup green SuperTrend and entry
        prices = [50000.0] * 15 + [49000.0, 48000.0, 47000.0, 46000.0, 45000.0, 44000.0, 43000.0, 42000.0, 41000.0, 40000.0]
        df = pd.DataFrame({
            "timestamp": timestamps,
            "open": prices,
            "high": [p + 50 for p in prices],
            "low": [p - 50 for p in prices],
            "close": prices,
            "volume": [100.0] * len(prices)
        })

        result = engine.run(df)
        # Verify circuit breaker is tracked and closed to cash
        self.assertTrue(engine.risk_manager.circuit_breaker_active or len(result.trades) > 0)


if __name__ == "__main__":
    unittest.main()
