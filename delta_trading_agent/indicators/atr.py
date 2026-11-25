"""
Average True Range (ATR) Volatility Filter Module.
Calculates ATR and relative volatility ratio:
ATR% = (ATR / Close) * 100%
Used to filter out low-volatility consolidation regimes.
"""

from dataclasses import dataclass
from typing import Optional, List
import numpy as np
import pandas as pd


@dataclass
class ATRResult:
    atr: float
    atr_pct: float             # (atr / close) * 100
    is_sufficient_vol: bool    # atr_pct >= threshold_pct


def calculate_atr(
    df: pd.DataFrame,
    period: int = 14,
    min_pct_threshold: float = 0.15,
    high_col: str = "high",
    low_col: str = "low",
    close_col: str = "close"
) -> pd.DataFrame:
    """Computes ATR and ATR percentage on a pandas DataFrame."""
    df = df.copy()
    high = df[high_col].values.astype(float)
    low = df[low_col].values.astype(float)
    close = df[close_col].values.astype(float)
    n = len(df)

    if n < period:
        df["atr_filter"] = 0.0
        df["atr_pct"] = 0.0
        df["atr_sufficient"] = False
        return df

    tr = np.zeros(n, dtype=float)
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        tr[i] = max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i] - close[i - 1])
        )

    atr = np.zeros(n, dtype=float)
    atr[period - 1] = np.mean(tr[:period])
    for i in range(period, n):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period

    atr_pct = np.zeros(n, dtype=float)
    for i in range(period - 1, n):
        if close[i] > 0:
            atr_pct[i] = (atr[i] / close[i]) * 100.0

    df["atr_filter"] = atr
    df["atr_pct"] = atr_pct
    df["atr_sufficient"] = atr_pct >= min_pct_threshold
    return df


class StatefulATR:
    """Incremental ATR calculator for streaming candles."""
    def __init__(self, period: int = 14, min_pct_threshold: float = 0.15):
        self.period = period
        self.min_pct_threshold = min_pct_threshold
        self.prev_close: Optional[float] = None
        self.tr_history: List[float] = []
        self.atr: Optional[float] = None
        self.count = 0

    def update(self, high: float, low: float, close: float) -> Optional[ATRResult]:
        self.count += 1
        if self.prev_close is None:
            tr = high - low
        else:
            tr = max(high - low, abs(high - self.prev_close), abs(low - self.prev_close))

        self.prev_close = close
        self.tr_history.append(tr)

        if self.count < self.period:
            return None

        if self.count == self.period:
            self.atr = float(np.mean(self.tr_history[:self.period]))
        else:
            self.atr = (self.atr * (self.period - 1) + tr) / self.period

        atr_pct = (self.atr / close * 100.0) if close > 0 else 0.0

        return ATRResult(
            atr=round(self.atr, 2),
            atr_pct=round(atr_pct, 4),
            is_sufficient_vol=atr_pct >= self.min_pct_threshold
        )
