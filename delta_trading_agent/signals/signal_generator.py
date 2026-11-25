"""
Signal Generation Module (Phase 2 Enhanced).
Evaluates SuperTrend reversals, EMA 9 directional confirmation (Methods A, B, C),
and optional Market Regime Filters (ADX Trend Strength & ATR Volatility Threshold).
Guarantees single-signal per candle timestamp and zero lookahead bias.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any, List, Tuple
import pandas as pd

from ..config.settings import EMAMethod, PositionSide, RegimeFilterType
from ..indicators.supertrend import SuperTrendResult
from ..indicators.ema import EMAResult
from ..indicators.adx import ADXResult
from ..indicators.atr import ATRResult


class SignalType(str, Enum):
    NONE = "NONE"
    BUY_LONG = "BUY_LONG"
    SELL_SHORT = "SELL_SHORT"
    EXIT_LONG = "EXIT_LONG"
    EXIT_SHORT = "EXIT_SHORT"
    REVERSE_TO_SHORT = "REVERSE_TO_SHORT"
    REVERSE_TO_LONG = "REVERSE_TO_LONG"


@dataclass
class TradingSignal:
    timestamp: int                     # Epoch timestamp of candle close
    signal_type: SignalType
    price: float                       # Candle close price
    supertrend_val: float
    supertrend_direction: str          # "GREEN" or "RED"
    ema_val: float
    ema_slope: float
    ema_confirmed: bool
    ema_method_used: EMAMethod
    position_at_signal: PositionSide
    action: str                        # "OPEN_LONG", "OPEN_SHORT", "CLOSE_LONG", "CLOSE_SHORT", "REVERSE", "HOLD"
    reason: str
    regime_passed: bool = True
    adx_val: Optional[float] = None
    atr_pct_val: Optional[float] = None


class SignalGenerator:
    """
    Stateful signal generator evaluating completed candles with optional regime filters.
    """
    def __init__(
        self,
        ema_method: EMAMethod = EMAMethod.METHOD_C,
        regime_filter: RegimeFilterType = RegimeFilterType.NONE
    ):
        self.ema_method = ema_method
        self.regime_filter = regime_filter
        self.last_processed_timestamp: Optional[int] = None
        self.signal_history: List[TradingSignal] = []

    def set_ema_method(self, method: EMAMethod):
        self.ema_method = method

    def set_regime_filter(self, regime_filter: RegimeFilterType):
        self.regime_filter = regime_filter

    def check_ema_bullish(self, close: float, ema_res: EMAResult) -> bool:
        if self.ema_method == EMAMethod.METHOD_A:
            return close > ema_res.ema
        elif self.ema_method == EMAMethod.METHOD_B:
            return ema_res.slope > 0
        elif self.ema_method == EMAMethod.METHOD_C:
            return (close > ema_res.ema) and (ema_res.slope > 0)
        return False

    def check_ema_bearish(self, close: float, ema_res: EMAResult) -> bool:
        if self.ema_method == EMAMethod.METHOD_A:
            return close < ema_res.ema
        elif self.ema_method == EMAMethod.METHOD_B:
            return ema_res.slope < 0
        elif self.ema_method == EMAMethod.METHOD_C:
            return (close < ema_res.ema) and (ema_res.slope < 0)
        return False

    def check_regime_filter(
        self,
        adx_res: Optional[ADXResult] = None,
        atr_res: Optional[ATRResult] = None
    ) -> Tuple[bool, str]:
        """Validates market regime filters (ADX & ATR) without lookahead."""
        if self.regime_filter == RegimeFilterType.NONE:
            return True, "Baseline (No filter)"

        adx_ok = True
        atr_ok = True
        reasons = []

        if self.regime_filter in [RegimeFilterType.ADX_ONLY, RegimeFilterType.BOTH]:
            if adx_res is not None:
                adx_ok = adx_res.is_trending
                if not adx_ok:
                    reasons.append(f"ADX {adx_res.adx:.1f} < threshold")
            else:
                adx_ok = True

        if self.regime_filter in [RegimeFilterType.ATR_ONLY, RegimeFilterType.BOTH]:
            if atr_res is not None:
                atr_ok = atr_res.is_sufficient_vol
                if not atr_ok:
                    reasons.append(f"ATR% {atr_res.atr_pct:.3f}% < threshold")
            else:
                atr_ok = True

        passed = adx_ok and atr_ok
        reason_str = "Regime validated" if passed else " & ".join(reasons)
        return passed, reason_str

    def evaluate(
        self,
        candle_timestamp: int,
        close: float,
        st_res: SuperTrendResult,
        ema_res: EMAResult,
        current_position: PositionSide = PositionSide.NONE,
        adx_res: Optional[ADXResult] = None,
        atr_res: Optional[ATRResult] = None
    ) -> TradingSignal:
        if self.last_processed_timestamp is not None and candle_timestamp <= self.last_processed_timestamp:
            return TradingSignal(
                timestamp=candle_timestamp,
                signal_type=SignalType.NONE,
                price=close,
                supertrend_val=st_res.supertrend,
                supertrend_direction="GREEN" if st_res.trend == 1 else "RED",
                ema_val=ema_res.ema,
                ema_slope=ema_res.slope,
                ema_confirmed=False,
                ema_method_used=self.ema_method,
                position_at_signal=current_position,
                action="HOLD",
                reason=f"Duplicate candle timestamp {candle_timestamp} already evaluated"
            )

        self.last_processed_timestamp = candle_timestamp
        st_dir = "GREEN" if st_res.trend == 1 else "RED"

        regime_ok, regime_reason = self.check_regime_filter(adx_res, atr_res)
        adx_val = adx_res.adx if adx_res else None
        atr_pct_val = atr_res.atr_pct if atr_res else None

        # 1. SuperTrend flipped RED -> GREEN
        if st_res.is_reversal_to_green:
            is_bullish_confirmed = self.check_ema_bullish(close, ema_res)
            entry_allowed = is_bullish_confirmed and regime_ok

            if current_position == PositionSide.SHORT:
                # Core Rule: Close SHORT, then reverse to LONG if both EMA and regime confirm
                if entry_allowed:
                    sig = TradingSignal(
                        timestamp=candle_timestamp,
                        signal_type=SignalType.REVERSE_TO_LONG,
                        price=close,
                        supertrend_val=st_res.supertrend,
                        supertrend_direction=st_dir,
                        ema_val=ema_res.ema,
                        ema_slope=ema_res.slope,
                        ema_confirmed=True,
                        ema_method_used=self.ema_method,
                        position_at_signal=current_position,
                        action="REVERSE",
                        reason=f"SuperTrend GREEN reversal; SHORT closed & EMA/Regime confirmed reversal ({regime_reason})",
                        regime_passed=regime_ok,
                        adx_val=adx_val,
                        atr_pct_val=atr_pct_val
                    )
                else:
                    filter_fail_reason = f"EMA {self.ema_method.value} failed" if not is_bullish_confirmed else regime_reason
                    sig = TradingSignal(
                        timestamp=candle_timestamp,
                        signal_type=SignalType.EXIT_SHORT,
                        price=close,
                        supertrend_val=st_res.supertrend,
                        supertrend_direction=st_dir,
                        ema_val=ema_res.ema,
                        ema_slope=ema_res.slope,
                        ema_confirmed=is_bullish_confirmed,
                        ema_method_used=self.ema_method,
                        position_at_signal=current_position,
                        action="CLOSE_SHORT",
                        reason=f"SuperTrend GREEN reversal; SHORT closed to CASH. Entry rejected: {filter_fail_reason}",
                        regime_passed=regime_ok,
                        adx_val=adx_val,
                        atr_pct_val=atr_pct_val
                    )
                self.signal_history.append(sig)
                return sig

            elif current_position == PositionSide.NONE:
                if entry_allowed:
                    sig = TradingSignal(
                        timestamp=candle_timestamp,
                        signal_type=SignalType.BUY_LONG,
                        price=close,
                        supertrend_val=st_res.supertrend,
                        supertrend_direction=st_dir,
                        ema_val=ema_res.ema,
                        ema_slope=ema_res.slope,
                        ema_confirmed=True,
                        ema_method_used=self.ema_method,
                        position_at_signal=current_position,
                        action="OPEN_LONG",
                        reason=f"SuperTrend GREEN & EMA/Regime confirmed bullish entry ({regime_reason})",
                        regime_passed=regime_ok,
                        adx_val=adx_val,
                        atr_pct_val=atr_pct_val
                    )
                else:
                    filter_fail_reason = f"EMA {self.ema_method.value} rejected" if not is_bullish_confirmed else regime_reason
                    sig = TradingSignal(
                        timestamp=candle_timestamp,
                        signal_type=SignalType.NONE,
                        price=close,
                        supertrend_val=st_res.supertrend,
                        supertrend_direction=st_dir,
                        ema_val=ema_res.ema,
                        ema_slope=ema_res.slope,
                        ema_confirmed=is_bullish_confirmed,
                        ema_method_used=self.ema_method,
                        position_at_signal=current_position,
                        action="HOLD",
                        reason=f"SuperTrend GREEN detected but filtered out: {filter_fail_reason}",
                        regime_passed=regime_ok,
                        adx_val=adx_val,
                        atr_pct_val=atr_pct_val
                    )
                self.signal_history.append(sig)
                return sig

            else:
                # Already LONG
                sig = TradingSignal(
                    timestamp=candle_timestamp,
                    signal_type=SignalType.NONE,
                    price=close,
                    supertrend_val=st_res.supertrend,
                    supertrend_direction=st_dir,
                    ema_val=ema_res.ema,
                    ema_slope=ema_res.slope,
                    ema_confirmed=is_bullish_confirmed,
                    ema_method_used=self.ema_method,
                    position_at_signal=current_position,
                    action="HOLD",
                    reason="SuperTrend GREEN detected but already in LONG position",
                    regime_passed=regime_ok,
                    adx_val=adx_val,
                    atr_pct_val=atr_pct_val
                )
                self.signal_history.append(sig)
                return sig

        # 2. SuperTrend flipped GREEN -> RED
        elif st_res.is_reversal_to_red:
            is_bearish_confirmed = self.check_ema_bearish(close, ema_res)
            entry_allowed = is_bearish_confirmed and regime_ok

            if current_position == PositionSide.LONG:
                # Core Rule: Close LONG, then reverse to SHORT if both EMA and regime confirm
                if entry_allowed:
                    sig = TradingSignal(
                        timestamp=candle_timestamp,
                        signal_type=SignalType.REVERSE_TO_SHORT,
                        price=close,
                        supertrend_val=st_res.supertrend,
                        supertrend_direction=st_dir,
                        ema_val=ema_res.ema,
                        ema_slope=ema_res.slope,
                        ema_confirmed=True,
                        ema_method_used=self.ema_method,
                        position_at_signal=current_position,
                        action="REVERSE",
                        reason=f"SuperTrend RED reversal; LONG closed & EMA/Regime confirmed reversal ({regime_reason})",
                        regime_passed=regime_ok,
                        adx_val=adx_val,
                        atr_pct_val=atr_pct_val
                    )
                else:
                    filter_fail_reason = f"EMA {self.ema_method.value} failed" if not is_bearish_confirmed else regime_reason
                    sig = TradingSignal(
                        timestamp=candle_timestamp,
                        signal_type=SignalType.EXIT_LONG,
                        price=close,
                        supertrend_val=st_res.supertrend,
                        supertrend_direction=st_dir,
                        ema_val=ema_res.ema,
                        ema_slope=ema_res.slope,
                        ema_confirmed=is_bearish_confirmed,
                        ema_method_used=self.ema_method,
                        position_at_signal=current_position,
                        action="CLOSE_LONG",
                        reason=f"SuperTrend RED reversal; LONG closed to CASH. Entry rejected: {filter_fail_reason}",
                        regime_passed=regime_ok,
                        adx_val=adx_val,
                        atr_pct_val=atr_pct_val
                    )
                self.signal_history.append(sig)
                return sig

            elif current_position == PositionSide.NONE:
                if entry_allowed:
                    sig = TradingSignal(
                        timestamp=candle_timestamp,
                        signal_type=SignalType.SELL_SHORT,
                        price=close,
                        supertrend_val=st_res.supertrend,
                        supertrend_direction=st_dir,
                        ema_val=ema_res.ema,
                        ema_slope=ema_res.slope,
                        ema_confirmed=True,
                        ema_method_used=self.ema_method,
                        position_at_signal=current_position,
                        action="OPEN_SHORT",
                        reason=f"SuperTrend RED & EMA/Regime confirmed bearish entry ({regime_reason})",
                        regime_passed=regime_ok,
                        adx_val=adx_val,
                        atr_pct_val=atr_pct_val
                    )
                else:
                    filter_fail_reason = f"EMA {self.ema_method.value} rejected" if not is_bearish_confirmed else regime_reason
                    sig = TradingSignal(
                        timestamp=candle_timestamp,
                        signal_type=SignalType.NONE,
                        price=close,
                        supertrend_val=st_res.supertrend,
                        supertrend_direction=st_dir,
                        ema_val=ema_res.ema,
                        ema_slope=ema_res.slope,
                        ema_confirmed=is_bearish_confirmed,
                        ema_method_used=self.ema_method,
                        position_at_signal=current_position,
                        action="HOLD",
                        reason=f"SuperTrend RED detected but filtered out: {filter_fail_reason}",
                        regime_passed=regime_ok,
                        adx_val=adx_val,
                        atr_pct_val=atr_pct_val
                    )
                self.signal_history.append(sig)
                return sig

            else:
                # Already SHORT
                sig = TradingSignal(
                    timestamp=candle_timestamp,
                    signal_type=SignalType.NONE,
                    price=close,
                    supertrend_val=st_res.supertrend,
                    supertrend_direction=st_dir,
                    ema_val=ema_res.ema,
                    ema_slope=ema_res.slope,
                    ema_confirmed=is_bearish_confirmed,
                    ema_method_used=self.ema_method,
                    position_at_signal=current_position,
                    action="HOLD",
                    reason="SuperTrend RED detected but already in SHORT position",
                    regime_passed=regime_ok,
                    adx_val=adx_val,
                    atr_pct_val=atr_pct_val
                )
                self.signal_history.append(sig)
                return sig

        # No reversal on this candle
        return TradingSignal(
            timestamp=candle_timestamp,
            signal_type=SignalType.NONE,
            price=close,
            supertrend_val=st_res.supertrend,
            supertrend_direction=st_dir,
            ema_val=ema_res.ema,
            ema_slope=ema_res.slope,
            ema_confirmed=False,
            ema_method_used=self.ema_method,
            position_at_signal=current_position,
            action="HOLD",
            reason=f"No reversal on candle; SuperTrend remains {st_dir}",
            regime_passed=regime_ok,
            adx_val=adx_val,
            atr_pct_val=atr_pct_val
        )
