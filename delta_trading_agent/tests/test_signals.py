"""
Unit Tests for Signal Generation Logic.
Verifies SuperTrend reversal detection, EMA methods (A, B, C),
duplicate timestamp prevention, and exit/reversal transitions.
"""

import unittest
from delta_trading_agent.config.settings import EMAMethod, PositionSide
from delta_trading_agent.indicators.supertrend import SuperTrendResult
from delta_trading_agent.indicators.ema import EMAResult
from delta_trading_agent.signals.signal_generator import SignalGenerator, SignalType


class TestSignals(unittest.TestCase):
    def setUp(self):
        self.generator_a = SignalGenerator(ema_method=EMAMethod.METHOD_A)
        self.generator_b = SignalGenerator(ema_method=EMAMethod.METHOD_B)
        self.generator_c = SignalGenerator(ema_method=EMAMethod.METHOD_C)

    def test_long_entry_method_a(self):
        # SuperTrend flips to GREEN
        st = SuperTrendResult(
            trend=1,
            supertrend=50000.0,
            upper_band=52000.0,
            lower_band=50000.0,
            atr=500.0,
            is_reversal_to_green=True,
            is_reversal_to_red=False
        )
        # Price 51000 > EMA 50800, but slope is downward (-10)
        ema = EMAResult(
            ema=50800.0,
            prev_ema=50810.0,
            slope=-10.0,
            slope_pct=-0.02,
            is_price_above=True,
            is_price_below=False,
            is_slope_up=False,
            is_slope_down=True
        )

        # Method A: close (51000) > ema (50800) -> should trigger BUY_LONG
        sig_a = self.generator_a.evaluate(
            candle_timestamp=1700000300,
            close=51000.0,
            st_res=st,
            ema_res=ema,
            current_position=PositionSide.NONE
        )
        self.assertEqual(sig_a.signal_type, SignalType.BUY_LONG)
        self.assertEqual(sig_a.action, "OPEN_LONG")

        # Method B: slope is negative -> should NOT trigger BUY_LONG
        sig_b = self.generator_b.evaluate(
            candle_timestamp=1700000300,
            close=51000.0,
            st_res=st,
            ema_res=ema,
            current_position=PositionSide.NONE
        )
        self.assertEqual(sig_b.signal_type, SignalType.NONE)
        self.assertEqual(sig_b.action, "HOLD")

    def test_short_entry_and_reversal_rules(self):
        # Holding LONG position, SuperTrend flips RED
        st_red = SuperTrendResult(
            trend=-1,
            supertrend=52000.0,
            upper_band=52000.0,
            lower_band=49000.0,
            atr=600.0,
            is_reversal_to_green=False,
            is_reversal_to_red=True
        )
        # Price 49500 < EMA 50500, slope is downward (-50) -> confirms bearish
        ema_bearish = EMAResult(
            ema=50500.0,
            prev_ema=50550.0,
            slope=-50.0,
            slope_pct=-0.1,
            is_price_above=False,
            is_price_below=True,
            is_slope_up=False,
            is_slope_down=True
        )

        # Holding LONG: must close LONG and REVERSE to SHORT
        sig = self.generator_c.evaluate(
            candle_timestamp=1700000600,
            close=49500.0,
            st_res=st_red,
            ema_res=ema_bearish,
            current_position=PositionSide.LONG
        )
        self.assertEqual(sig.signal_type, SignalType.REVERSE_TO_SHORT)
        self.assertEqual(sig.action, "REVERSE")

    def test_reversal_without_ema_confirmation_exits_to_cash(self):
        # Holding LONG, SuperTrend flips RED, but EMA is bullish (e.g. price > EMA)
        st_red = SuperTrendResult(
            trend=-1,
            supertrend=52000.0,
            upper_band=52000.0,
            lower_band=49000.0,
            atr=600.0,
            is_reversal_to_green=False,
            is_reversal_to_red=True
        )
        ema_bullish = EMAResult(
            ema=49000.0,
            prev_ema=48900.0,
            slope=100.0,
            slope_pct=0.2,
            is_price_above=True,
            is_price_below=False,
            is_slope_up=True,
            is_slope_down=False
        )

        sig = self.generator_a.evaluate(
            candle_timestamp=1700000900,
            close=49500.0,
            st_res=st_red,
            ema_res=ema_bullish,
            current_position=PositionSide.LONG
        )
        # Must close LONG, but NOT enter SHORT because EMA failed
        self.assertEqual(sig.signal_type, SignalType.EXIT_LONG)
        self.assertEqual(sig.action, "CLOSE_LONG")

    def test_duplicate_candle_rejection(self):
        st = SuperTrendResult(trend=1, supertrend=50000, upper_band=52000, lower_band=50000, atr=500, is_reversal_to_green=True, is_reversal_to_red=False)
        ema = EMAResult(ema=50000, prev_ema=49900, slope=100, slope_pct=0.2, is_price_above=True, is_price_below=False, is_slope_up=True, is_slope_down=False)

        # First evaluation
        sig1 = self.generator_a.evaluate(1700001200, 51000.0, st, ema, PositionSide.NONE)
        self.assertEqual(sig1.signal_type, SignalType.BUY_LONG)

        # Repeated evaluation on identical timestamp
        sig2 = self.generator_a.evaluate(1700001200, 51000.0, st, ema, PositionSide.NONE)
        self.assertEqual(sig2.signal_type, SignalType.NONE)
        self.assertIn("Duplicate candle timestamp", sig2.reason)


if __name__ == "__main__":
    unittest.main()
