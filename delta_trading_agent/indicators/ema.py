"""
Exponential Moving Average (EMA) Indicator Implementation.
Calculates EMA with period 9 (multiplier = 2 / (N + 1) = 0.2).
Provides candle position check and slope direction analysis.
Supports both batch calculation on pandas Series and stateful incremental updates.
"""

from dataclasses import dataclass
from typing import Optional, List
import numpy as np
import pandas as pd


@dataclass
class EMAResult:
    ema: float                  # Current EMA value
    prev_ema: Optional[float]   # Previous EMA value
    slope: float                # Difference: ema - prev_ema
    slope_pct: float            # Percentage change
    is_price_above: bool        # Close > EMA
    is_price_below: bool        # Close < EMA
    is_slope_up: bool           # slope > 0
    is_slope_down: bool         # slope < 0


def calculate_ema(
    df: pd.DataFrame,
    period: int = 9,
    close_col: str = "close"
) -> pd.DataFrame:
    """
    Computes EMA on a pandas DataFrame.
    Returns DataFrame with columns:
      - 'ema_{period}'
      - 'ema_slope'
      - 'price_above_ema'
      - 'ema_slope_up'
    """
    df = df.copy()
    close = df[close_col]
    ema_series = close.ewm(span=period, adjust=False).mean()
    slope_series = ema_series.diff()

    col_name = f"ema_{period}"
    df[col_name] = ema_series
    df["ema_slope"] = slope_series
    df["price_above_ema"] = df[close_col] > ema_series
    df["price_below_ema"] = df[close_col] < ema_series
    df["ema_slope_up"] = slope_series > 0
    df["ema_slope_down"] = slope_series < 0

    return df


class StatefulEMA:
    """
    Incremental EMA tracker for streaming candles.
    Matches pandas ewm(span=period, adjust=False) exactly.
    """
    def __init__(self, period: int = 9):
        self.period = period
        self.alpha = 2.0 / (period + 1.0)
        self.ema: Optional[float] = None
        self.prev_ema: Optional[float] = None
        self.count = 0

    def update(self, close: float) -> Optional[EMAResult]:
        """
        Feed a completed candle close price.
        Returns EMAResult on each bar.
        """
        self.count += 1

        if self.count == 1:
            self.ema = close
            self.prev_ema = close
            return EMAResult(
                ema=self.ema,
                prev_ema=self.prev_ema,
                slope=0.0,
                slope_pct=0.0,
                is_price_above=False,
                is_price_below=False,
                is_slope_up=False,
                is_slope_down=False
            )

        self.prev_ema = self.ema
        self.ema = close * self.alpha + self.prev_ema * (1.0 - self.alpha)
        slope = self.ema - self.prev_ema
        slope_pct = (slope / self.prev_ema * 100.0) if self.prev_ema != 0 else 0.0

        return EMAResult(
            ema=self.ema,
            prev_ema=self.prev_ema,
            slope=slope,
            slope_pct=slope_pct,
            is_price_above=close > self.ema,
            is_price_below=close < self.ema,
            is_slope_up=slope > 0,
            is_slope_down=slope < 0
        )
