"""
Higher-Timeframe (HTF) Trend Module (Phase 4).
Calculates 4-Hour EMA 50 and EMA 200 from 1-hour candle stream without look-ahead bias.

Mechanism:
- Aggregates completed 1-hour candles into 4-hour bars (00:00, 04:00, 08:00, 12:00, 16:00, 20:00 UTC).
- Updates EMA 50 and EMA 200 strictly upon 4-hour bar completion.
- Provides trend state for 1-hour signal evaluation:
  - Bullish: 4h EMA 50 > 4h EMA 200 (Long entries allowed)
  - Bearish: 4h EMA 50 < 4h EMA 200 (Short entries allowed)
"""

from dataclasses import dataclass
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
import pandas as pd
import numpy as np


@dataclass
class HTFTrendResult:
    timestamp_4h: int
    ema_fast: float
    ema_slow: float
    is_bullish: bool
    is_bearish: bool
    trend_direction: str  # "BULLISH", "BEARISH", "NEUTRAL"


class StatefulHTFTrend:
    """Stateful 4-Hour Trend tracker built on 1-hour candles."""
    def __init__(self, fast_period: int = 50, slow_period: int = 200):
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.alpha_fast = 2.0 / (fast_period + 1.0)
        self.alpha_slow = 2.0 / (slow_period + 1.0)

        self.current_4h_block: List[Dict[str, float]] = []
        self.ema_fast: Optional[float] = None
        self.ema_slow: Optional[float] = None
        self.last_result: Optional[HTFTrendResult] = None
        self.completed_4h_closes: List[float] = []

    def update(self, timestamp: int, open_p: float, high: float, low: float, close: float) -> Optional[HTFTrendResult]:
        """
        Processes a completed 1-hour candle.
        4h boundaries occur when UTC hour % 4 == 3 (closing bar of 00-03, 04-07, 08-11, 12-15, 16-19, 20-23).
        """
        dt = datetime.fromtimestamp(timestamp, timezone.utc)
        self.current_4h_block.append({
            "timestamp": timestamp,
            "open": open_p,
            "high": high,
            "low": low,
            "close": close
        })

        # Check if this 1h bar completes a 4h block
        # On a 1h chart, hour 3, 7, 11, 15, 19, 23 marks completion of a 4-hour window
        is_4h_close = (dt.hour % 4 == 3) or (len(self.current_4h_block) >= 4)

        if is_4h_close and self.current_4h_block:
            close_4h = close
            self.completed_4h_closes.append(close_4h)

            # Update EMAs
            if self.ema_fast is None:
                self.ema_fast = close_4h
            else:
                self.ema_fast = self.alpha_fast * close_4h + (1.0 - self.alpha_fast) * self.ema_fast

            if self.ema_slow is None:
                self.ema_slow = close_4h
            else:
                self.ema_slow = self.alpha_slow * close_4h + (1.0 - self.alpha_slow) * self.ema_slow

            is_bull = self.ema_fast > self.ema_slow
            is_bear = self.ema_fast < self.ema_slow

            self.last_result = HTFTrendResult(
                timestamp_4h=timestamp,
                ema_fast=round(self.ema_fast, 2),
                ema_slow=round(self.ema_slow, 2),
                is_bullish=is_bull,
                is_bearish=is_bear,
                trend_direction="BULLISH" if is_bull else ("BEARISH" if is_bear else "NEUTRAL")
            )
            self.current_4h_block = []

        return self.last_result
