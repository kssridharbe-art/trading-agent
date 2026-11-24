"""
Unit Tests for Strategy Execution and Automatic Reversals.
Verifies position state machine, exit sequencing, reversal handling,
and fee accounting.
"""

import unittest
from delta_trading_agent.config.settings import TradingConfig, PositionSide, EMAMethod
from delta_trading_agent.risk.risk_manager import RiskManager
from delta_trading_agent.execution.strategy_executor import StrategyExecutor
from delta_trading_agent.signals.signal_generator import TradingSignal, SignalType


class TestStrategyExecution(unittest.TestCase):
    def setUp(self):
        self.config = TradingConfig(
            initial_capital=10000.0,
            margin_allocation_pct=0.70,
            leverage=10.0,
            taker_fee_rate=0.0005,
            slippage_pct=0.0
        )
        self.risk_manager = RiskManager(self.config)
        self.executor = StrategyExecutor(self.config, self.risk_manager)

    def test_open_long_execution(self):
        sig = TradingSignal(
            timestamp=1700000000,
            signal_type=SignalType.BUY_LONG,
            price=50000.0,
            supertrend_val=49000.0,
            supertrend_direction="GREEN",
            ema_val=49500.0,
            ema_slope=50.0,
            ema_confirmed=True,
            ema_method_used=EMAMethod.METHOD_A,
            position_at_signal=PositionSide.NONE,
            action="OPEN_LONG",
            reason="Confirmed entry"
        )
        trade, msg, capital = self.executor.process_signal(sig, 10000.0)
        self.assertIsNone(trade)
        self.assertEqual(self.executor.position.side, PositionSide.LONG)
        self.assertEqual(self.executor.position.contracts, 1400)
        self.assertEqual(self.executor.position.entry_price, 50000.0)

    def test_automatic_reversal_long_to_short(self):
        # 1. Open LONG at 50,000
        sig_open = TradingSignal(
            timestamp=1700000000,
            signal_type=SignalType.BUY_LONG,
            price=50000.0,
            supertrend_val=49000.0,
            supertrend_direction="GREEN",
            ema_val=49500.0,
            ema_slope=50.0,
            ema_confirmed=True,
            ema_method_used=EMAMethod.METHOD_A,
            position_at_signal=PositionSide.NONE,
            action="OPEN_LONG",
            reason="Confirmed entry"
        )
        _, _, capital = self.executor.process_signal(sig_open, 10000.0)

        # 2. SuperTrend flips RED at 52,000, EMA confirms bearish -> Reversal
        sig_rev = TradingSignal(
            timestamp=1700000600,
            signal_type=SignalType.REVERSE_TO_SHORT,
            price=52000.0,
            supertrend_val=53000.0,
            supertrend_direction="RED",
            ema_val=52500.0,
            ema_slope=-30.0,
            ema_confirmed=True,
            ema_method_used=EMAMethod.METHOD_A,
            position_at_signal=PositionSide.LONG,
            action="REVERSE",
            reason="Automatic reversal to SHORT"
        )
        trade_record, msg, capital = self.executor.process_signal(sig_rev, capital)

        # Closed trade should be the LONG trade
        self.assertIsNotNone(trade_record)
        self.assertEqual(trade_record.side, PositionSide.LONG)
        self.assertEqual(trade_record.entry_price, 50000.0)
        self.assertEqual(trade_record.exit_price, 52000.0)
        self.assertTrue(trade_record.net_pnl > 0)

        # New position should be SHORT
        self.assertEqual(self.executor.position.side, PositionSide.SHORT)
        self.assertEqual(self.executor.position.entry_price, 52000.0)

    def test_exit_to_cash_when_ema_rejects_reversal(self):
        # 1. Open LONG at 50,000
        sig_open = TradingSignal(
            timestamp=1700000000,
            signal_type=SignalType.BUY_LONG,
            price=50000.0,
            supertrend_val=49000.0,
            supertrend_direction="GREEN",
            ema_val=49500.0,
            ema_slope=50.0,
            ema_confirmed=True,
            ema_method_used=EMAMethod.METHOD_A,
            position_at_signal=PositionSide.NONE,
            action="OPEN_LONG",
            reason="Confirmed entry"
        )
        _, _, capital = self.executor.process_signal(sig_open, 10000.0)

        # 2. SuperTrend flips RED, but EMA rejected SHORT -> Exit to CASH
        sig_exit = TradingSignal(
            timestamp=1700000600,
            signal_type=SignalType.EXIT_LONG,
            price=49000.0,
            supertrend_val=50500.0,
            supertrend_direction="RED",
            ema_val=48800.0,
            ema_slope=20.0,
            ema_confirmed=False,
            ema_method_used=EMAMethod.METHOD_A,
            position_at_signal=PositionSide.LONG,
            action="CLOSE_LONG",
            reason="Exit to cash"
        )
        trade_record, msg, capital = self.executor.process_signal(sig_exit, capital)

        self.assertIsNotNone(trade_record)
        self.assertEqual(trade_record.side, PositionSide.LONG)
        # Position must be flat in cash
        self.assertEqual(self.executor.position.side, PositionSide.NONE)
        self.assertEqual(self.executor.position.contracts, 0)


if __name__ == "__main__":
    unittest.main()
