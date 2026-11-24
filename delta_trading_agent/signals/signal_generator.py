"""
Signal Generation Module.
Evaluates SuperTrend reversals and EMA 9 directional confirmation.
Implements Methods A, B, and C as required by the specification.
Guarantees single-signal per candle timestamp and zero lookahead bias.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any, List
import pandas as pd

from ..config.settings import EMAMethod, PositionSide
from ..indicators.supertrend import SuperTrendResult
from ..indicators.ema import EMAResult


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


class SignalGenerator:
    """
    Stateful signal generator evaluating completed candles.
    """
    def __init__(self, ema_method: EMAMethod = EMAMethod.METHOD_A):
        self.ema_method = ema_method
        self.last_processed_timestamp: Optional[int] = None
        self.signal_history: List[TradingSignal] = []

    def set_ema_method(self, method: EMAMethod):
        """Allows dynamic switching of the EMA confirmation method."""
        self.ema_method = method

    def check_ema_bullish(self, close: float, ema_res: EMAResult) -> bool:
        """
        Validates whether EMA 9 confirms a bullish entry based on chosen method.
        """
        if self.ema_method == EMAMethod.METHOD_A:
            # Candle close above EMA 9
            return close > ema_res.ema
        elif self.ema_method == EMAMethod.METHOD_B:
            # EMA 9 slope upward
            return ema_res.slope > 0
        elif self.ema_method == EMAMethod.METHOD_C:
            # Both candle position and slope upward
            return (close > ema_res.ema) and (ema_res.slope > 0)
        return False

    def check_ema_bearish(self, close: float, ema_res: EMAResult) -> bool:
        """
        Validates whether EMA 9 confirms a bearish entry based on chosen method.
        """
        if self.ema_method == EMAMethod.METHOD_A:
            # Candle close below EMA 9
            return close < ema_res.ema
        elif self.ema_method == EMAMethod.METHOD_B:
            # EMA 9 slope downward
            return ema_res.slope < 0
        elif self.ema_method == EMAMethod.METHOD_C:
            # Both candle position and slope downward
            return (close < ema_res.ema) and (ema_res.slope < 0)
        return False

    def evaluate(
        self,
        candle_timestamp: int,
        close: float,
        st_res: SuperTrendResult,
        ema_res: EMAResult,
        current_position: PositionSide = PositionSide.NONE
    ) -> TradingSignal:
        """
        Evaluates a newly closed candle.
        Rejects repeated timestamps to avoid duplicate signals on the same candle.
        """
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

        # Check SuperTrend reversals
        if st_res.is_reversal_to_green:
            # SuperTrend flipped RED -> GREEN
            is_bullish_confirmed = self.check_ema_bullish(close, ema_res)

            if current_position == PositionSide.SHORT:
                # Core Rule: Close SHORT, then reverse to LONG if EMA confirms
                if is_bullish_confirmed:
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
                        reason=f"SuperTrend reversed to GREEN; SHORT closed and EMA {self.ema_method.value} confirmed bullish reversal"
                    )
                else:
                    sig = TradingSignal(
                        timestamp=candle_timestamp,
                        signal_type=SignalType.EXIT_SHORT,
                        price=close,
                        supertrend_val=st_res.supertrend,
                        supertrend_direction=st_dir,
                        ema_val=ema_res.ema,
                        ema_slope=ema_res.slope,
                        ema_confirmed=False,
                        ema_method_used=self.ema_method,
                        position_at_signal=current_position,
                        action="CLOSE_SHORT",
                        reason=f"SuperTrend reversed to GREEN; SHORT closed. EMA {self.ema_method.value} failed to confirm LONG, staying in CASH"
                    )
                self.signal_history.append(sig)
                return sig

            elif current_position == PositionSide.NONE:
                # Flat: Open LONG if EMA confirms
                if is_bullish_confirmed:
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
                        reason=f"SuperTrend reversed RED->GREEN and EMA {self.ema_method.value} confirmed bullish entry"
                    )
                else:
                    sig = TradingSignal(
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
                        reason=f"SuperTrend reversed RED->GREEN but EMA {self.ema_method.value} filter rejected bullish entry"
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
                    reason="SuperTrend GREEN reversal detected but already in LONG position"
                )
                self.signal_history.append(sig)
                return sig

        elif st_res.is_reversal_to_red:
            # SuperTrend flipped GREEN -> RED
            is_bearish_confirmed = self.check_ema_bearish(close, ema_res)

            if current_position == PositionSide.LONG:
                # Core Rule: Close LONG, then reverse to SHORT if EMA confirms
                if is_bearish_confirmed:
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
                        reason=f"SuperTrend reversed to RED; LONG closed and EMA {self.ema_method.value} confirmed bearish reversal"
                    )
                else:
                    sig = TradingSignal(
                        timestamp=candle_timestamp,
                        signal_type=SignalType.EXIT_LONG,
                        price=close,
                        supertrend_val=st_res.supertrend,
                        supertrend_direction=st_dir,
                        ema_val=ema_res.ema,
                        ema_slope=ema_res.slope,
                        ema_confirmed=False,
                        ema_method_used=self.ema_method,
                        position_at_signal=current_position,
                        action="CLOSE_LONG",
                        reason=f"SuperTrend reversed to RED; LONG closed. EMA {self.ema_method.value} failed to confirm SHORT, staying in CASH"
                    )
                self.signal_history.append(sig)
                return sig

            elif current_position == PositionSide.NONE:
                # Flat: Open SHORT if EMA confirms
                if is_bearish_confirmed:
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
                        reason=f"SuperTrend reversed GREEN->RED and EMA {self.ema_method.value} confirmed bearish entry"
                    )
                else:
                    sig = TradingSignal(
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
                        reason=f"SuperTrend reversed GREEN->RED but EMA {self.ema_method.value} filter rejected bearish entry"
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
                    reason="SuperTrend RED reversal detected but already in SHORT position"
                )
                self.signal_history.append(sig)
                return sig

        # No reversal occurred on this candle: maintain current position
        sig = TradingSignal(
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
            reason=f"No reversal on candle; SuperTrend remains {st_dir}"
        )
        return sig
