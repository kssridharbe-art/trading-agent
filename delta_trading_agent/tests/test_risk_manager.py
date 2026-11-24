"""
Unit Tests for Risk Management Module.
Verifies 70% margin allocation, contract sizing, leverage calculations,
liquidation pricing, daily loss limits, and consecutive loss circuit breakers.
"""

import unittest
from delta_trading_agent.config.settings import TradingConfig, PositionSide
from delta_trading_agent.risk.risk_manager import RiskManager


class TestRiskManager(unittest.TestCase):
    def setUp(self):
        self.config = TradingConfig(
            initial_capital=10000.0,
            margin_allocation_pct=0.70,
            unallocated_cash_pct=0.30,
            leverage=10.0,
            max_daily_loss_pct=0.05,  # 5% max loss = 500 USDT
            max_consecutive_losses=3
        )
        self.rm = RiskManager(self.config)

    def test_70_percent_margin_allocation_sizing(self):
        # Capital 10,000 USDT, Price 50,000 USDT
        # Margin allocation: 70% of 10,000 = 7,000 USDT
        # Max notional: 7,000 * 10 = 70,000 USDT
        # 1 contract = 0.001 BTC = 50 USDT
        # Contracts = 70,000 // 50 = 1400 contracts = 1.400 BTC
        sizing = self.rm.calculate_position_size(
            current_capital=10000.0,
            price=50000.0,
            side=PositionSide.LONG
        )
        self.assertTrue(sizing.allowed)
        self.assertEqual(sizing.contracts, 1400)
        self.assertEqual(sizing.btc_size, 1.400)
        self.assertAlmostEqual(sizing.margin_required, 7000.0, places=2)
        self.assertAlmostEqual(sizing.notional_value, 70000.0, places=2)
        self.assertAlmostEqual(sizing.effective_leverage, 7.0, places=2)

    def test_liquidation_price_estimation(self):
        # Entry 50000, leverage 10x, MMR 0.005 (0.5%)
        # Long Liq = 50000 * (1 - 0.1 + 0.005) = 50000 * 0.905 = 45250
        # Short Liq = 50000 * (1 + 0.1 - 0.005) = 50000 * 1.095 = 54750
        liq_long = self.rm.calculate_liquidation_price(50000.0, PositionSide.LONG, 10.0)
        liq_short = self.rm.calculate_liquidation_price(50000.0, PositionSide.SHORT, 10.0)

        self.assertAlmostEqual(liq_long, 45250.0, places=1)
        self.assertAlmostEqual(liq_short, 54750.0, places=1)
        self.assertTrue(liq_long < 50000.0)
        self.assertTrue(liq_short > 50000.0)

    def test_daily_loss_circuit_breaker(self):
        capital = 10000.0
        # Trade 1: -300 USDT
        self.rm.record_trade_result(-300.0, capital - 300.0)
        self.assertFalse(self.rm.circuit_breaker_active)

        # Trade 2: -250 USDT -> total daily loss = -550 USDT > 500 USDT (5%)
        self.rm.record_trade_result(-250.0, capital - 550.0)
        self.assertTrue(self.rm.circuit_breaker_active)
        self.assertIn("Hit daily loss limit", self.rm.circuit_breaker_reason)

        # Subsequent sizing must be blocked
        sizing = self.rm.calculate_position_size(9450.0, 50000.0, PositionSide.LONG)
        self.assertFalse(sizing.allowed)
        self.assertIn("Circuit breaker active", sizing.reason)

    def test_consecutive_losses_circuit_breaker(self):
        capital = 10000.0
        # 3 small losses (each -50 USDT, well below daily loss limit)
        self.rm.record_trade_result(-50.0, capital - 50.0)
        self.rm.record_trade_result(-50.0, capital - 100.0)
        self.assertFalse(self.rm.circuit_breaker_active)

        self.rm.record_trade_result(-50.0, capital - 150.0)
        self.assertTrue(self.rm.circuit_breaker_active)
        self.assertIn("Hit maximum consecutive losing trades", self.rm.circuit_breaker_reason)


if __name__ == "__main__":
    unittest.main()
