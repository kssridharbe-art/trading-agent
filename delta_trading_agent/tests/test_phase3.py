"""
Unit Tests for Phase 3: Extended Historical Validation, Risk Metrics & Research Configuration.
Covers:
1. Research configuration preset (BTCUSDT_1H_ADX_ATR_RESEARCH).
2. Maximum Adverse Excursion (MAE) tracking on ActivePosition and TradeRecord.
3. Liquidation distance and margin scaling (10%, 20%, 30%, 40%).
4. Financial accounting identity on extended trades.
"""

import unittest
from delta_trading_agent.config.settings import (
    TradingConfig, EMAMethod, RegimeFilterType, ExecutionType, PositionSide,
    BTCUSDT_1H_ADX_ATR_RESEARCH
)
from delta_trading_agent.risk.risk_manager import RiskManager
from delta_trading_agent.execution.strategy_executor import StrategyExecutor, TradeRecord


class TestPhase3Research(unittest.TestCase):
    def test_research_configuration_preset(self):
        """Validates that BTCUSDT_1H_ADX_ATR_RESEARCH is properly initialized and preserved."""
        cfg = BTCUSDT_1H_ADX_ATR_RESEARCH
        self.assertEqual(cfg.symbol, "BTCUSDT")
        self.assertEqual(cfg.resolution, "1h")
        self.assertEqual(cfg.supertrend_length, 10)
        self.assertEqual(cfg.supertrend_factor, 3.0)
        self.assertEqual(cfg.ema_period, 9)
        self.assertEqual(cfg.ema_method, EMAMethod.METHOD_C)
        self.assertEqual(cfg.regime_filter, RegimeFilterType.BOTH)
        self.assertEqual(cfg.execution_type, ExecutionType.MARKET)
        self.assertEqual(cfg.leverage, 10.0)
        self.assertEqual(cfg.margin_allocation_pct, 0.70)
        self.assertFalse(cfg.live_trading_authorized, "Live trading MUST remain strictly unauthorized")

    def test_margin_allocation_scaling(self):
        """Validates that 10%, 20%, 30%, 40% margin allocations scale contract quantities proportionally."""
        capital = 10000.0
        price = 80000.0
        leverage = 10.0

        allocations = [0.10, 0.20, 0.30, 0.40]
        results = []

        for alloc in allocations:
            cfg = TradingConfig(
                initial_capital=capital,
                margin_allocation_pct=alloc,
                leverage=leverage
            )
            rm = RiskManager(cfg)
            sizing = rm.calculate_position_size(capital, price, PositionSide.LONG)
            self.assertTrue(sizing.allowed)
            results.append(sizing.contracts)

        # 10% margin -> 1,000 margin -> 10,000 notional -> 10,000 / (80,000 * 0.001) = 125 contracts
        # 20% margin -> 2,000 margin -> 20,000 notional -> 250 contracts
        # 30% margin -> 3,000 margin -> 30,000 notional -> 375 contracts
        # 40% margin -> 4,000 margin -> 40,000 notional -> 500 contracts
        self.assertEqual(results[0], 125)
        self.assertEqual(results[1], 250)
        self.assertEqual(results[2], 375)
        self.assertEqual(results[3], 500)

    def test_maximum_adverse_excursion_long(self):
        """Validates that MAE accurately captures worst unrealized dip during a LONG trade."""
        cfg = TradingConfig(initial_capital=10000.0, margin_allocation_pct=0.20, leverage=10.0)
        rm = RiskManager(cfg)
        executor = StrategyExecutor(cfg, rm)

        sizing = rm.calculate_position_size(10000.0, 80000.0, PositionSide.LONG)
        executor.open_position(PositionSide.LONG, 80000.0, 1000, sizing)

        # Bar 1: Price drops to 78,000 (adverse dip: 2,000)
        executor.update_market_price(price=79000.0, high=80500.0, low=78000.0)
        self.assertEqual(executor.position.max_adverse_displacement, 2000.0)

        # Bar 2: Price drops further to 77,000 (adverse dip: 3,000)
        executor.update_market_price(price=77500.0, high=79200.0, low=77000.0)
        self.assertEqual(executor.position.max_adverse_displacement, 3000.0)

        # Bar 3: Price rallies to 82,000 (adverse dip stays at 3,000)
        executor.update_market_price(price=82000.0, high=82500.0, low=79000.0)
        self.assertEqual(executor.position.max_adverse_displacement, 3000.0)

        # Close position
        trade, _ = executor.close_current_position(
            exit_price=82000.0,
            exit_timestamp=2000,
            exit_reason="Exit",
            current_capital=10000.0
        )

        # MAE should reflect the 3,000 price dip
        # 3,000 / 80,000 * 100 = 3.75%
        self.assertEqual(trade.max_adverse_excursion_pct, 3.75)
        self.assertAlmostEqual(trade.max_adverse_excursion_usdt, 3000.0 * trade.btc_size, places=2)

    def test_maximum_adverse_excursion_short(self):
        """Validates that MAE accurately captures worst unrealized spike against a SHORT trade."""
        cfg = TradingConfig(initial_capital=10000.0, margin_allocation_pct=0.20, leverage=10.0)
        rm = RiskManager(cfg)
        executor = StrategyExecutor(cfg, rm)

        sizing = rm.calculate_position_size(10000.0, 80000.0, PositionSide.SHORT)
        executor.open_position(PositionSide.SHORT, 80000.0, 1000, sizing)

        # Bar 1: Price spikes to 82,400 (adverse move: 2,400)
        executor.update_market_price(price=81000.0, high=82400.0, low=79800.0)
        self.assertEqual(executor.position.max_adverse_displacement, 2400.0)

        # Close position
        trade, _ = executor.close_current_position(
            exit_price=78000.0,
            exit_timestamp=2000,
            exit_reason="Exit",
            current_capital=10000.0
        )

        # 2,400 / 80,000 * 100 = 3.00%
        self.assertEqual(trade.max_adverse_excursion_pct, 3.0)

    def test_liquidation_distance_calculation(self):
        """Validates liquidation distance for 10x leverage (9.50% based on 0.5% MMR)."""
        cfg = TradingConfig(leverage=10.0)
        rm = RiskManager(cfg)

        entry_long = 100000.0
        liq_long = rm.calculate_liquidation_price(entry_long, PositionSide.LONG, 10.0)
        # Entry * (1 - 0.10 + 0.005) = 100,000 * 0.905 = 90,500
        self.assertEqual(liq_long, 90500.0)
        dist_long = (entry_long - liq_long) / entry_long * 100.0
        self.assertAlmostEqual(dist_long, 9.50, places=2)

        entry_short = 100000.0
        liq_short = rm.calculate_liquidation_price(entry_short, PositionSide.SHORT, 10.0)
        # Entry * (1 + 0.10 - 0.005) = 100,000 * 1.095 = 109,500
        self.assertAlmostEqual(liq_short, 109500.0, places=2)
        dist_short = (liq_short - entry_short) / entry_short * 100.0
        self.assertAlmostEqual(dist_short, 9.50, places=2)


if __name__ == "__main__":
    unittest.main()
