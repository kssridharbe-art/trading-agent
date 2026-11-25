"""
Average Directional Index (ADX) Indicator Module.
Standard Wilder's formulation:
- True Range (TR)
- Plus Directional Movement (+DM) and Minus Directional Movement (-DM)
- Wilder's RMA smoothing for TR, +DM, -DM
- +DI, -DI, DX, and ADX
Supports both batch DataFrame calculation and streaming StatefulADX.
"""

from dataclasses import dataclass
from typing import Optional, List
import numpy as np
import pandas as pd


@dataclass
class ADXResult:
    adx: float
    plus_di: float
    minus_di: float
    dx: float
    is_trending: bool     # ADX >= threshold


def calculate_adx(
    df: pd.DataFrame,
    period: int = 14,
    threshold: float = 20.0,
    high_col: str = "high",
    low_col: str = "low",
    close_col: str = "close"
) -> pd.DataFrame:
    """Computes ADX on a pandas DataFrame."""
    df = df.copy()
    high = df[high_col].values.astype(float)
    low = df[low_col].values.astype(float)
    close = df[close_col].values.astype(float)
    n = len(df)

    if n < period * 2:
        df["adx"] = 0.0
        df["plus_di"] = 0.0
        df["minus_di"] = 0.0
        df["adx_trending"] = False
        return df

    tr = np.zeros(n, dtype=float)
    plus_dm = np.zeros(n, dtype=float)
    minus_dm = np.zeros(n, dtype=float)

    tr[0] = high[0] - low[0]
    for i in range(1, n):
        tr[i] = max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i] - close[i - 1])
        )
        up_move = high[i] - high[i - 1]
        down_move = low[i - 1] - low[i]

        if up_move > down_move and up_move > 0:
            plus_dm[i] = up_move
        else:
            plus_dm[i] = 0.0

        if down_move > up_move and down_move > 0:
            minus_dm[i] = down_move
        else:
            minus_dm[i] = 0.0

    # Wilder's smoothing
    atr_smooth = np.zeros(n, dtype=float)
    plus_dm_smooth = np.zeros(n, dtype=float)
    minus_dm_smooth = np.zeros(n, dtype=float)

    atr_smooth[period - 1] = np.mean(tr[:period])
    plus_dm_smooth[period - 1] = np.mean(plus_dm[:period])
    minus_dm_smooth[period - 1] = np.mean(minus_dm[:period])

    for i in range(period, n):
        atr_smooth[i] = (atr_smooth[i - 1] * (period - 1) + tr[i]) / period
        plus_dm_smooth[i] = (plus_dm_smooth[i - 1] * (period - 1) + plus_dm[i]) / period
        minus_dm_smooth[i] = (minus_dm_smooth[i - 1] * (period - 1) + minus_dm[i]) / period

    plus_di = np.zeros(n, dtype=float)
    minus_di = np.zeros(n, dtype=float)
    dx = np.zeros(n, dtype=float)

    for i in range(period - 1, n):
        if atr_smooth[i] > 0:
            plus_di[i] = (plus_dm_smooth[i] / atr_smooth[i]) * 100.0
            minus_di[i] = (minus_dm_smooth[i] / atr_smooth[i]) * 100.0
        di_sum = plus_di[i] + minus_di[i]
        if di_sum > 0:
            dx[i] = (abs(plus_di[i] - minus_di[i]) / di_sum) * 100.0

    adx = np.zeros(n, dtype=float)
    adx_start = period * 2 - 2
    if n > adx_start:
        adx[adx_start] = np.mean(dx[period - 1 : adx_start + 1])
        for i in range(adx_start + 1, n):
            adx[i] = (adx[i - 1] * (period - 1) + dx[i]) / period

    df["adx"] = adx
    df["plus_di"] = plus_di
    df["minus_di"] = minus_di
    df["adx_trending"] = adx >= threshold
    return df


class StatefulADX:
    """Incremental ADX calculator for streaming candles."""
    def __init__(self, period: int = 14, threshold: float = 20.0):
        self.period = period
        self.threshold = threshold
        self.prev_high: Optional[float] = None
        self.prev_low: Optional[float] = None
        self.prev_close: Optional[float] = None

        self.tr_history: List[float] = []
        self.plus_dm_history: List[float] = []
        self.minus_dm_history: List[float] = []
        self.dx_history: List[float] = []

        self.smooth_tr: Optional[float] = None
        self.smooth_plus_dm: Optional[float] = None
        self.smooth_minus_dm: Optional[float] = None
        self.adx: Optional[float] = None
        self.count = 0

    def update(self, high: float, low: float, close: float) -> Optional[ADXResult]:
        self.count += 1

        if self.prev_close is None:
            tr = high - low
            up_move = 0.0
            down_move = 0.0
        else:
            tr = max(high - low, abs(high - self.prev_close), abs(low - self.prev_close))
            up_move = high - self.prev_high
            down_move = self.prev_low - low

        plus_dm = up_move if (up_move > down_move and up_move > 0) else 0.0
        minus_dm = down_move if (down_move > up_move and down_move > 0) else 0.0

        self.prev_high = high
        self.prev_low = low
        self.prev_close = close

        self.tr_history.append(tr)
        self.plus_dm_history.append(plus_dm)
        self.minus_dm_history.append(minus_dm)

        if self.count < self.period:
            return None

        if self.count == self.period:
            self.smooth_tr = float(np.mean(self.tr_history[:self.period]))
            self.smooth_plus_dm = float(np.mean(self.plus_dm_history[:self.period]))
            self.smooth_minus_dm = float(np.mean(self.minus_dm_history[:self.period]))
        else:
            self.smooth_tr = (self.smooth_tr * (self.period - 1) + tr) / self.period
            self.smooth_plus_dm = (self.smooth_plus_dm * (self.period - 1) + plus_dm) / self.period
            self.smooth_minus_dm = (self.smooth_minus_dm * (self.period - 1) + minus_dm) / self.period

        plus_di = (self.smooth_plus_dm / self.smooth_tr * 100.0) if self.smooth_tr > 0 else 0.0
        minus_di = (self.smooth_minus_dm / self.smooth_tr * 100.0) if self.smooth_tr > 0 else 0.0
        di_sum = plus_di + minus_di
        dx = (abs(plus_di - minus_di) / di_sum * 100.0) if di_sum > 0 else 0.0
        self.dx_history.append(dx)

        adx_warmup = self.period * 2 - 1
        if self.count < adx_warmup:
            return None

        if self.count == adx_warmup:
            self.adx = float(np.mean(self.dx_history[:self.period]))
        else:
            self.adx = (self.adx * (self.period - 1) + dx) / self.period

        return ADXResult(
            adx=round(self.adx, 2),
            plus_di=round(plus_di, 2),
            minus_di=round(minus_di, 2),
            dx=round(dx, 2),
            is_trending=self.adx >= self.threshold
        )
