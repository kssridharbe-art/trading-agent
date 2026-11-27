"""
Unit Tests for Phase 2 & Phase 2.1: Strategy Optimization, Risk Hardening, and P&L Accounting.
Covers:
1. ADX and ATR indicators (batch/stateful parity, warmup, thresholds).
2. Hardened Risk Management (unrealized floating loss, total drawdown breaker, SQLite persistence).
3. Regime Filters in Signal Generation (ADX_ONLY, ATR_ONLY, BOTH, unconditional exit).
4. Execution Simulator (tick rounding, limit entry touch/cancellation, limit exit timeout, maker/taker fee accuracy).
5. Exact Financial Accounting & Reconciliation (Gross - Fees == Net PnL).
"""

import os
import tempfile
import unittest
import numpy as np
import pandas as pd

from delta_trading_agent.config.settings import (
    TradingConfig, EMAMethod, PositionSide, RegimeFilterType, ExecutionType
)
from delta_trading_agent.indicators.adx import calculate_adx, StatefulADX, ADXResult
from delta_trading_agent.indicators.atr import calculate_atr, StatefulATR, ATRResult
from delta_trading_agent.indicators.supertrend import SuperTrendResult
from delta_trading_agent.indicators.ema import EMAResult
from delta_trading_agent.signals.signal_generator import SignalGenerator, SignalType, TradingSignal
from delta_trading_agent.risk.risk_manager import RiskManager
from delta_trading_agent.storage.database import DatabaseManager
from delta_trading_agent.execution.strategy_executor import StrategyExecutor, TradeRecord
from delta_trading_agent.execution.execution_model import ExecutionModel
from delta_trading_agent.backtest.performance import calculate_performance


class TestPhase2Indicators(unittest.TestCase):
    def setUp(self):
        np.random.seed(42)
        n = 80
        base = 60000.0
        wave = np.sin(np.linspace(0, 3 * np.pi, n)) * 2000.0
        close = base + wave + np.random.normal(0, 100, n)
        high = close + np.random.uniform(50, 300, n)
        low = close - np.random.uniform(50, 300, n)
        open_p = (high + low) / 2.0

        self.df = pd.DataFrame({
            "timestamp": np.arange(1700000000, 1700000000 + n * 300, 300),
            "open": open_p,
            "high": high,
            "low": low,
            "close": close,
            "volume": np.random.uniform(10, 50, n)
        })

    def test_adx_batch_and_stateful_parity(self):
        period = 14
        threshold = 20.0
        batch_df = calculate_adx(self.df, period=period, threshold=threshold)
        stateful = StatefulADX(period=period, threshold=threshold)

        warmup_idx = period * 2 - 2  # 14 * 2 - 2 = 26 (27th candle)
        for i, row in self.df.iterrows():
            res = stateful.update(row["high"], row["low"], row["close"])
            if i < warmup_idx:
                self.assertIsNone(res)
            else:
                self.assertIsNotNone(res)
                self.assertGreaterEqual(res.adx, 0.0)
                self.assertAlmostEqual(res.adx, batch_df.loc[i, "adx"], places=1)

    def test_atr_batch_and_stateful_parity(self):
        period = 14
        min_pct = 0.15
        batch_df = calculate_atr(self.df, period=period, min_pct_threshold=min_pct)
        stateful = StatefulATR(period=period, min_pct_threshold=min_pct)

        for i, row in self.df.iterrows():
            res = stateful.update(row["high"], row["low"], row["close"])
            if i < period - 1:
                self.assertIsNone(res)
            else:
                self.assertIsNotNone(res)
                self.assertGreaterEqual(res.atr, 0.0)
                self.assertAlmostEqual(res.atr, batch_df.loc[i, "atr_filter"], places=2)
                self.assertAlmostEqual(res.atr_pct, batch_df.loc[i, "atr_pct"], places=2)


class TestPhase2RiskManager(unittest.TestCase):
    def setUp(self):
        self.tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp_db.close()
        self.db = DatabaseManager(self.tmp_db.name)
        self.config = TradingConfig(
            initial_capital=10000.0,
            margin_allocation_pct=0.70,
            leverage=10.0,
            max_daily_loss_pct=0.05,        # 5% = 500 USDT
            max_consecutive_losses=3,
            max_total_drawdown_pct=0.20,    # 20% = 2000 USDT from peak
            include_unrealized_in_daily_loss=True
        )

    def tearDown(self):
        try:
            os.remove(self.tmp_db.name)
        except OSError:
            pass

    def test_unrealized_floating_loss_trips_circuit_breaker(self):
        rm = RiskManager(self.config, db=self.db)
        # Capital 10,000, unrealized loss = -600 (exceeds 500 max daily loss)
        tripped = rm.update_equity_and_check_drawdown(
            current_capital=10000.0,
            unrealized_pnl=-600.0,
            timestamp=1700000300
        )
        self.assertTrue(tripped)
        self.assertTrue(rm.circuit_breaker_active)
        self.assertIn("Hit daily loss limit including floating loss", rm.circuit_breaker_reason)

        # Sizing should now be rejected
        sizing = rm.calculate_position_size(10000.0, 50000.0, PositionSide.LONG, 1700000300)
        self.assertFalse(sizing.allowed)

    def test_max_total_drawdown_protection(self):
        rm = RiskManager(self.config, db=self.db)
        # Peak equity rises to 12,000
        rm.update_equity_and_check_drawdown(12000.0, 0.0, 1700000300)
        self.assertEqual(rm.peak_equity, 12000.0)

        # Equity drops to 9,000 -> Drawdown = (12000 - 9000)/12000 = 25% >= 20%
        tripped = rm.update_equity_and_check_drawdown(9000.0, 0.0, 1700000600)
        self.assertTrue(tripped)
        self.assertTrue(rm.circuit_breaker_active)
        self.assertIn("maximum portfolio drawdown", rm.circuit_breaker_reason)

    def test_sqlite_persistence_and_restore(self):
        # 1. Initialize RiskManager, record 2 consecutive losses
        rm1 = RiskManager(self.config, db=self.db)
        rm1.record_trade_result(-100.0, 9900.0, 1700000300)
        rm1.record_trade_result(-100.0, 9800.0, 1700000600)
        self.assertEqual(rm1.consecutive_losses, 2)
        self.assertAlmostEqual(rm1.daily_realized_pnl, -200.0)

        # 2. Simulate application restart: new RiskManager with same db
        rm2 = RiskManager(self.config, db=self.db)
        # Should restore consecutive losses and realized pnl
        self.assertEqual(rm2.consecutive_losses, 2)
        self.assertAlmostEqual(rm2.daily_realized_pnl, -200.0)

        # 3. Third loss trips breaker
        rm2.record_trade_result(-100.0, 9700.0, 1700000900)
        self.assertTrue(rm2.circuit_breaker_active)

        # 4. Another restart: circuit breaker CANNOT be bypassed
        rm3 = RiskManager(self.config, db=self.db)
        self.assertTrue(rm3.circuit_breaker_active)
        sizing = rm3.calculate_position_size(9700.0, 50000.0, PositionSide.LONG, 1700001200)
        self.assertFalse(sizing.allowed)


class TestPhase2RegimeFilters(unittest.TestCase):
    def setUp(self):
        self.st_green = SuperTrendResult(
            trend=1, supertrend=50000.0, upper_band=52000.0, lower_band=50000.0,
            atr=500.0, is_reversal_to_green=True, is_reversal_to_red=False
        )
        self.st_red = SuperTrendResult(
            trend=-1, supertrend=52000.0, upper_band=52000.0, lower_band=49000.0,
            atr=500.0, is_reversal_to_green=False, is_reversal_to_red=True
        )
        self.ema_bull = EMAResult(
            ema=50500.0, prev_ema=50400.0, slope=100.0, slope_pct=0.2,
            is_price_above=True, is_price_below=False, is_slope_up=True, is_slope_down=False
        )
        self.ema_bear = EMAResult(
            ema=50500.0, prev_ema=50600.0, slope=-100.0, slope_pct=-0.2,
            is_price_above=False, is_price_below=True, is_slope_up=False, is_slope_down=True
        )

    def test_adx_filter_blocks_entry_when_below_threshold(self):
        sig_gen = SignalGenerator(ema_method=EMAMethod.METHOD_C, regime_filter=RegimeFilterType.ADX_ONLY)
        adx_weak = ADXResult(adx=15.0, plus_di=20.0, minus_di=10.0, dx=33.3, is_trending=False)

        sig = sig_gen.evaluate(
            candle_timestamp=1700000300,
            close=51000.0,
            st_res=self.st_green,
            ema_res=self.ema_bull,
            current_position=PositionSide.NONE,
            adx_res=adx_weak
        )
        self.assertEqual(sig.action, "HOLD")
        self.assertFalse(sig.regime_passed)
        self.assertIn("ADX", sig.reason)

    def test_adx_filter_allows_entry_when_above_threshold(self):
        sig_gen = SignalGenerator(ema_method=EMAMethod.METHOD_C, regime_filter=RegimeFilterType.ADX_ONLY)
        adx_strong = ADXResult(adx=28.0, plus_di=30.0, minus_di=12.0, dx=42.0, is_trending=True)

        sig = sig_gen.evaluate(
            candle_timestamp=1700000300,
            close=51000.0,
            st_res=self.st_green,
            ema_res=self.ema_bull,
            current_position=PositionSide.NONE,
            adx_res=adx_strong
        )
        self.assertEqual(sig.action, "OPEN_LONG")
        self.assertTrue(sig.regime_passed)

    def test_atr_filter_blocks_entry_when_low_volatility(self):
        sig_gen = SignalGenerator(ema_method=EMAMethod.METHOD_C, regime_filter=RegimeFilterType.ATR_ONLY)
        atr_low = ATRResult(atr=40.0, atr_pct=0.08, is_sufficient_vol=False)  # < 0.15%

        sig = sig_gen.evaluate(
            candle_timestamp=1700000300,
            close=51000.0,
            st_res=self.st_green,
            ema_res=self.ema_bull,
            current_position=PositionSide.NONE,
            atr_res=atr_low
        )
        self.assertEqual(sig.action, "HOLD")
        self.assertFalse(sig.regime_passed)

    def test_unconditional_exit_even_when_regime_fails(self):
        sig_gen = SignalGenerator(ema_method=EMAMethod.METHOD_C, regime_filter=RegimeFilterType.BOTH)
        adx_weak = ADXResult(adx=10.0, plus_di=10.0, minus_di=10.0, dx=0.0, is_trending=False)
        atr_low = ATRResult(atr=30.0, atr_pct=0.06, is_sufficient_vol=False)

        sig = sig_gen.evaluate(
            candle_timestamp=1700000300,
            close=50000.0,
            st_res=self.st_red,
            ema_res=self.ema_bear,
            current_position=PositionSide.LONG,
            adx_res=adx_weak,
            atr_res=atr_low
        )
        self.assertEqual(sig.action, "CLOSE_LONG")
        self.assertEqual(sig.signal_type, SignalType.EXIT_LONG)


class TestPhase2ExecutionModel(unittest.TestCase):
    def setUp(self):
        self.config = TradingConfig(
            tick_size=0.5,
            maker_offset_pct=0.0001,
            limit_timeout_bars=2,
            maker_fee_rate=0.0002,
            taker_fee_rate=0.0005,
            slippage_pct=0.0002
        )
        self.em = ExecutionModel(self.config, seed=42)

    def test_tick_size_rounding(self):
        self.assertEqual(self.em._round_tick(50000.23), 50000.0)
        self.assertEqual(self.em._round_tick(50000.26), 50000.5)
        self.assertEqual(self.em._round_tick(50000.74), 50000.5)
        self.assertEqual(self.em._round_tick(50000.76), 50001.0)

    def test_limit_entry_fill_on_touch(self):
        limit_price = self.em.calc_entry_limit_price(is_buy=True, close=50000.0)
        self.assertLess(limit_price, 50000.0)  # Buy limit placed below market

        # Next bar low dips below limit_price -> should fill passively as maker
        fill = self.em.check_limit_entry_fill(
            is_buy=True,
            limit_price=limit_price,
            bar_high=50100.0,
            bar_low=limit_price - 10.0,
            bar_close=50050.0,
            bars_waiting=0
        )
        self.assertTrue(fill.filled)
        self.assertFalse(fill.cancelled)
        self.assertEqual(fill.fill_price, limit_price)
        self.assertEqual(fill.fee_rate, 0.0002)  # Confirmed Maker fee

    def test_limit_entry_timeout_cancellation(self):
        # Entry limit order does NOT touch price within timeout -> CANCELLED unfilled
        fill = self.em.check_limit_entry_fill(
            is_buy=True,
            limit_price=49990.0,
            bar_high=50200.0,
            bar_low=50000.0,   # never touched 49990.0
            bar_close=50100.0,
            bars_waiting=2     # reached timeout
        )
        self.assertFalse(fill.filled)
        self.assertTrue(fill.cancelled)
        self.assertEqual(fill.fill_type, "UNFILLED_CANCELLED")

    def test_limit_exit_timeout_liquidation(self):
        # Exit limit order does NOT touch price within timeout -> forces liquidation as TAKER
        fill = self.em.check_limit_exit_fill(
            is_closing_long=True,
            limit_price=50100.0,
            bar_high=50050.0,   # never touched 50100.0
            bar_low=49800.0,
            bar_close=49900.0,
            bars_waiting=2     # reached timeout
        )
        self.assertTrue(fill.filled)
        self.assertTrue(fill.timed_out)
        self.assertEqual(fill.fee_rate, 0.0005)  # Taker fee applied on timeout exit
        self.assertEqual(fill.fill_type, "TIMEOUT_TAKER")

    def test_financial_pnl_accounting_identity(self):
        # Verifies that Gross PnL - Total Fees == Net PnL down to the penny
        trades = [
            TradeRecord(
                trade_id="TRD-0001",
                symbol="BTCUSDT",
                side=PositionSide.LONG,
                entry_timestamp=1700000000,
                exit_timestamp=1700003600,
                entry_price=50000.0,
                exit_price=52000.0,
                contracts=1400,
                btc_size=1.4,
                margin_used=7000.0,
                leverage=10.0,
                gross_pnl=2800.0,         # 1.4 * 2000 = 2800
                entry_fee=14.0,           # 1.4 * 50000 * 0.0002
                exit_fee=36.4,            # 1.4 * 52000 * 0.0005
                funding_fee=0.875,
                net_pnl=2748.725,         # 2800 - 14 - 36.4 - 0.875
                return_pct=39.27,
                exit_reason="Exit",
                ema_method_used="METHOD_C"
            )
        ]
        summary = calculate_performance(trades, initial_capital=10000.0, equity_curve=[10000.0, 12748.73])
        self.assertAlmostEqual(summary.total_gross_pnl - summary.total_fees_paid, summary.total_net_pnl, places=2)


if __name__ == "__main__":
    unittest.main()
