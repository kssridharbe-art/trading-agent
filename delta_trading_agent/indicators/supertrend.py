"""
SuperTrend Indicator Implementation.
Calculates SuperTrend with given ATR Length and Multiplier (Factor).
Implements standard Wilder's smoothing (RMA) for ATR to match TradingView standards.
Supports both batch DataFrame calculation and stateful incremental calculation.
"""

from dataclasses import dataclass
from typing import Optional, List, Tuple
import numpy as np
import pandas as pd


@dataclass
class SuperTrendResult:
    trend: int           # +1 for Bullish (Green), -1 for Bearish (Red)
    supertrend: float    # Value of the active SuperTrend line (LB when bullish, UB when bearish)
    upper_band: float    # Final upper band
    lower_band: float    # Final lower band
    atr: float           # Current Average True Range
    is_reversal_to_green: bool # True if flipped from -1 to +1 on this candle
    is_reversal_to_red: bool   # True if flipped from +1 to -1 on this candle


def calculate_supertrend(
    df: pd.DataFrame,
    length: int = 10,
    factor: float = 3.0,
    high_col: str = "high",
    low_col: str = "low",
    close_col: str = "close"
) -> pd.DataFrame:
    """
    Computes SuperTrend on a pandas DataFrame.
    Expected columns: high, low, close.
    Returns DataFrame with columns:
      - 'atr'
      - 'upper_band'
      - 'lower_band'
      - 'supertrend'
      - 'trend' (+1 or -1)
      - 'reversal_green' (bool)
      - 'reversal_red' (bool)
    """
    if len(df) < length:
        raise ValueError(f"DataFrame length ({len(df)}) is less than SuperTrend length ({length})")

    df = df.copy()
    high = df[high_col].values.astype(float)
    low = df[low_col].values.astype(float)
    close = df[close_col].values.astype(float)
    n = len(df)

    # 1. True Range calculation
    tr = np.zeros(n, dtype=float)
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        tr[i] = max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i] - close[i - 1])
        )

    # 2. Wilder's RMA for ATR
    atr = np.zeros(n, dtype=float)
    # Seed with SMA of first 'length' bars
    atr[length - 1] = np.mean(tr[:length])
    for i in range(length, n):
        atr[i] = (atr[i - 1] * (length - 1) + tr[i]) / length

    # 3. Basic Bands
    hl2 = (high + low) / 2.0
    basic_upper = hl2 + factor * atr
    basic_lower = hl2 - factor * atr

    # 4. Final Bands and Trend
    final_upper = np.zeros(n, dtype=float)
    final_lower = np.zeros(n, dtype=float)
    supertrend = np.zeros(n, dtype=float)
    trend = np.zeros(n, dtype=int)  # +1 = Bullish/Green, -1 = Bearish/Red
    reversal_green = np.zeros(n, dtype=bool)
    reversal_red = np.zeros(n, dtype=bool)

    # Initialize at index = length - 1
    start_idx = length - 1
    final_upper[start_idx] = basic_upper[start_idx]
    final_lower[start_idx] = basic_lower[start_idx]
    # Default initial trend based on close vs upper band
    trend[start_idx] = 1 if close[start_idx] >= basic_lower[start_idx] else -1
    supertrend[start_idx] = final_lower[start_idx] if trend[start_idx] == 1 else final_upper[start_idx]

    for i in range(start_idx + 1, n):
        # Calculate Final Upper Band
        if basic_upper[i] < final_upper[i - 1] or close[i - 1] > final_upper[i - 1]:
            final_upper[i] = basic_upper[i]
        else:
            final_upper[i] = final_upper[i - 1]

        # Calculate Final Lower Band
        if basic_lower[i] > final_lower[i - 1] or close[i - 1] < final_lower[i - 1]:
            final_lower[i] = basic_lower[i]
        else:
            final_lower[i] = final_lower[i - 1]

        # Trend Decision
        prev_trend = trend[i - 1]
        if prev_trend == 1:
            if close[i] < final_lower[i]:
                trend[i] = -1
                supertrend[i] = final_upper[i]
                reversal_red[i] = True
            else:
                trend[i] = 1
                supertrend[i] = final_lower[i]
        else:  # prev_trend == -1
            if close[i] > final_upper[i]:
                trend[i] = 1
                supertrend[i] = final_lower[i]
                reversal_green[i] = True
            else:
                trend[i] = -1
                supertrend[i] = final_upper[i]

    df["atr"] = atr
    df["upper_band"] = final_upper
    df["lower_band"] = final_lower
    df["supertrend"] = supertrend
    df["trend"] = trend
    df["reversal_green"] = reversal_green
    df["reversal_red"] = reversal_red

    return df


class StatefulSuperTrend:
    """
    Incremental SuperTrend tracker for streaming candles.
    Ensures identical calculation to batch mode bar-by-bar.
    """
    def __init__(self, length: int = 10, factor: float = 3.0):
        self.length = length
        self.factor = factor
        self.history_tr: List[float] = []
        self.prev_close: Optional[float] = None
        self.prev_atr: Optional[float] = None
        self.prev_upper: Optional[float] = None
        self.prev_lower: Optional[float] = None
        self.prev_trend: Optional[int] = None
        self.count = 0

    def update(self, high: float, low: float, close: float) -> Optional[SuperTrendResult]:
        """
        Feed a completed candle (high, low, close).
        Returns SuperTrendResult once warmup is complete (>= length bars).
        """
        self.count += 1
        
        # Calculate True Range
        if self.prev_close is None:
            tr = high - low
        else:
            tr = max(high - low, abs(high - self.prev_close), abs(low - self.prev_close))
        
        self.history_tr.append(tr)

        # Warmup phase
        if self.count < self.length:
            self.prev_close = close
            return None

        # First bar at count == length
        if self.count == self.length:
            atr = float(np.mean(self.history_tr[:self.length]))
            hl2 = (high + low) / 2.0
            upper = hl2 + self.factor * atr
            lower = hl2 - self.factor * atr
            trend = 1 if close >= lower else -1
            st_val = lower if trend == 1 else upper
            
            self.prev_atr = atr
            self.prev_upper = upper
            self.prev_lower = lower
            self.prev_trend = trend
            self.prev_close = close

            return SuperTrendResult(
                trend=trend,
                supertrend=st_val,
                upper_band=upper,
                lower_band=lower,
                atr=atr,
                is_reversal_to_green=False,
                is_reversal_to_red=False
            )

        # Subsequent bars
        atr = (self.prev_atr * (self.length - 1) + tr) / self.length
        hl2 = (high + low) / 2.0
        basic_upper = hl2 + self.factor * atr
        basic_lower = hl2 - self.factor * atr

        # Final upper
        if basic_upper < self.prev_upper or self.prev_close > self.prev_upper:
            final_upper = basic_upper
        else:
            final_upper = self.prev_upper

        # Final lower
        if basic_lower > self.prev_lower or self.prev_close < self.prev_lower:
            final_lower = basic_lower
        else:
            final_lower = self.prev_lower

        # Trend & reversal
        reversal_green = False
        reversal_red = False

        if self.prev_trend == 1:
            if close < final_lower:
                trend = -1
                st_val = final_upper
                reversal_red = True
            else:
                trend = 1
                st_val = final_lower
        else:
            if close > final_upper:
                trend = 1
                st_val = final_lower
                reversal_green = True
            else:
                trend = -1
                st_val = final_upper

        # Update state
        self.prev_atr = atr
        self.prev_upper = final_upper
        self.prev_lower = final_lower
        self.prev_trend = trend
        self.prev_close = close

        return SuperTrendResult(
            trend=trend,
            supertrend=st_val,
            upper_band=final_upper,
            lower_band=final_lower,
            atr=atr,
            is_reversal_to_green=reversal_green,
            is_reversal_to_red=reversal_red
        )
