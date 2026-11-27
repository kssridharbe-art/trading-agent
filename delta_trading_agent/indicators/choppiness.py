"""
Choppiness Index (CHOP) Indicator Module (Phase 4).
Calculates the Choppiness Index developed by E.W. Dreiss:
CHOP = 100 * LOG10( SUM(TrueRange, n) / (MaxHigh(n) - MinLow(n)) ) / LOG10(n)

Interpretation:
- Values above 61.8 indicate a choppy, sideways market (consolidation).
- Values below 38.2 indicate a strong directional trend.
- Values between 38.2 and 61.8 indicate a developing or transitional trend.

Both batch and stateful (streaming) implementations are provided for exact parity.
"""

from dataclasses import dataclass
from typing import Optional, List
import math
import numpy as np
import pandas as pd


@dataclass
class ChoppinessResult:
    chop: float
    is_choppy: bool
    is_trending: bool
    threshold: float


class StatefulChoppiness:
    """Stateful Choppiness Index calculator with rolling window."""
    def __init__(self, period: int = 14, threshold: float = 61.8):
        self.period = period
        self.threshold = threshold
        self.highs: List[float] = []
        self.lows: List[float] = []
        self.closes: List[float] = []
        self.tr_list: List[float] = []
        self.log_period = math.log10(period)

    def update(self, high: float, low: float, close: float) -> Optional[ChoppinessResult]:
        if self.closes:
            prev_close = self.closes[-1]
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        else:
            tr = high - low

        self.highs.append(high)
        self.lows.append(low)
        self.closes.append(close)
        self.tr_list.append(tr)

        if len(self.closes) > self.period:
            self.highs.pop(0)
            self.lows.pop(0)
            self.closes.pop(0)
            self.tr_list.pop(0)

        if len(self.closes) < self.period:
            return None

        tr_sum = sum(self.tr_list)
        hh = max(self.highs)
        ll = min(self.lows)
        diff = hh - ll

        if diff <= 0:
            chop = 50.0
        else:
            ratio = tr_sum / diff
            if ratio <= 0:
                chop = 50.0
            else:
                chop = 100.0 * (math.log10(ratio) / self.log_period)
                chop = max(0.0, min(100.0, chop))

        is_choppy = chop >= self.threshold
        is_trending = chop <= 38.2

        return ChoppinessResult(
            chop=round(chop, 2),
            is_choppy=is_choppy,
            is_trending=is_trending,
            threshold=self.threshold
        )


def calculate_choppiness(
    df: pd.DataFrame,
    period: int = 14,
    threshold: float = 61.8
) -> pd.DataFrame:
    """Batch calculation of Choppiness Index."""
    df = df.copy()
    high = df["high"]
    low = df["low"]
    close = df["close"]
    prev_close = close.shift(1)

    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    tr_sum = tr.rolling(window=period).sum()
    hh = high.rolling(window=period).max()
    ll = low.rolling(window=period).min()
    price_range = hh - ll

    log_period = math.log10(period)
    ratio = tr_sum / price_range.replace(0, np.nan)
    chop = 100.0 * (np.log10(ratio.clip(lower=1e-9)) / log_period)
    chop = chop.clip(lower=0.0, upper=100.0)

    df["chop"] = chop
    df["is_choppy"] = df["chop"] >= threshold
    df["is_trending"] = df["chop"] <= 38.2
    return df
