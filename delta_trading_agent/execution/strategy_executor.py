"""
Strategy Execution Module.
Manages position state transitions, order sequencing, and trade lifecycles:
1. Entry execution on valid signal (Flat -> LONG or Flat -> SHORT).
2. Opposite SuperTrend exit execution.
3. Reversal sequencing: fully close existing position FIRST, verify flat, then open opposing position if EMA confirms.
4. If EMA fails post-exit, remain in cash buffer.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple
from ..config.settings import PositionSide, TradingConfig, EMAMethod
from ..signals.signal_generator import TradingSignal, SignalType
from ..risk.risk_manager import RiskManager, SizingResult


@dataclass
class TradeRecord:
    trade_id: str
    symbol: str
    side: PositionSide               # LONG or SHORT
    entry_timestamp: int
    exit_timestamp: int
    entry_price: float
    exit_price: float
    contracts: int
    btc_size: float
    margin_used: float
    leverage: float
    gross_pnl: float
    entry_fee: float
    exit_fee: float
    funding_fee: float
    net_pnl: float
    return_pct: float                # Net PnL / Margin Used * 100
    exit_reason: str
    ema_method_used: str


@dataclass
class ActivePosition:
    side: PositionSide = PositionSide.NONE
    entry_price: float = 0.0
    contracts: int = 0
    btc_size: float = 0.0
    margin_used: float = 0.0
    entry_timestamp: int = 0
    leverage: float = 10.0
    liquidation_price: float = 0.0
    current_price: float = 0.0
    unrealized_pnl: float = 0.0


class StrategyExecutor:
    def __init__(self, config: TradingConfig, risk_manager: RiskManager):
        self.config = config
        self.risk_manager = risk_manager
        self.position = ActivePosition()
        self.trade_history: List[TradeRecord] = []
        self.trade_counter = 0

    def get_position_state(self) -> PositionSide:
        return self.position.side

    def update_market_price(self, price: float):
        """Updates unrealized PnL based on live/current candle price."""
        if self.position.side == PositionSide.NONE or self.position.contracts == 0:
            self.position.unrealized_pnl = 0.0
            self.position.current_price = price
            return

        self.position.current_price = price
        contract_btc = self.position.contracts * self.risk_manager.contract_value
        if self.position.side == PositionSide.LONG:
            self.position.unrealized_pnl = contract_btc * (price - self.position.entry_price)
        elif self.position.side == PositionSide.SHORT:
            self.position.unrealized_pnl = contract_btc * (self.position.entry_price - price)

    def close_current_position(
        self,
        exit_price: float,
        exit_timestamp: int,
        exit_reason: str,
        current_capital: float
    ) -> Tuple[TradeRecord, float]:
        """
        Closes existing position and records trade details.
        Returns (TradeRecord, updated_capital).
        """
        if self.position.side == PositionSide.NONE or self.position.contracts == 0:
            raise ValueError("No active position to close.")

        self.trade_counter += 1
        pos = self.position
        btc_size = pos.contracts * self.risk_manager.contract_value

        # Calculate gross PnL
        if pos.side == PositionSide.LONG:
            gross_pnl = btc_size * (exit_price - pos.entry_price)
        else:
            gross_pnl = btc_size * (pos.entry_price - exit_price)

        # Fees
        entry_notional = btc_size * pos.entry_price
        exit_notional = btc_size * exit_price
        entry_fee = entry_notional * self.config.taker_fee_rate
        exit_fee = exit_notional * self.config.taker_fee_rate

        # Estimated funding fee based on duration (every 8 hours)
        duration_hours = max((exit_timestamp - pos.entry_timestamp) / 3600.0, 0.0)
        funding_periods = duration_hours / 8.0
        funding_fee = entry_notional * self.config.est_funding_fee_8h * funding_periods

        net_pnl = gross_pnl - entry_fee - exit_fee - funding_fee
        return_pct = (net_pnl / pos.margin_used * 100.0) if pos.margin_used > 0 else 0.0
        new_capital = current_capital + net_pnl

        record = TradeRecord(
            trade_id=f"TRD-{self.trade_counter:04d}",
            symbol=self.config.symbol,
            side=pos.side,
            entry_timestamp=pos.entry_timestamp,
            exit_timestamp=exit_timestamp,
            entry_price=round(pos.entry_price, 2),
            exit_price=round(exit_price, 2),
            contracts=pos.contracts,
            btc_size=btc_size,
            margin_used=round(pos.margin_used, 4),
            leverage=pos.leverage,
            gross_pnl=round(gross_pnl, 4),
            entry_fee=round(entry_fee, 4),
            exit_fee=round(exit_fee, 4),
            funding_fee=round(funding_fee, 4),
            net_pnl=round(net_pnl, 4),
            return_pct=round(return_pct, 2),
            exit_reason=exit_reason,
            ema_method_used=self.config.ema_method.value
        )

        self.trade_history.append(record)
        self.risk_manager.record_trade_result(net_pnl, new_capital, exit_timestamp)

        # Reset active position to flat
        self.position = ActivePosition()
        return record, new_capital

    def open_position(
        self,
        side: PositionSide,
        price: float,
        timestamp: int,
        sizing: SizingResult
    ):
        """Opens a new position using validated sizing."""
        self.position = ActivePosition(
            side=side,
            entry_price=price,
            contracts=sizing.contracts,
            btc_size=sizing.btc_size,
            margin_used=sizing.margin_required,
            entry_timestamp=timestamp,
            leverage=self.config.leverage,
            liquidation_price=sizing.liquidation_price,
            current_price=price,
            unrealized_pnl=0.0
        )

    def process_signal(
        self,
        signal: TradingSignal,
        current_capital: float
    ) -> Tuple[Optional[TradeRecord], Optional[str], float]:
        """
        Processes a trading signal:
        - Executes entry, exit, or reversal.
        - Enforces strict reversal sequence: Exit first -> verify flat -> open opposite.
        Returns: (closed_trade_record, log_message, updated_capital)
        """
        trade_record: Optional[TradeRecord] = None
        action_msg: Optional[str] = None
        capital = current_capital

        if signal.action == "HOLD" or signal.signal_type == SignalType.NONE:
            self.update_market_price(signal.price)
            return None, None, capital

        # 1. Standard Entry when Flat
        if signal.action == "OPEN_LONG" and self.position.side == PositionSide.NONE:
            sizing = self.risk_manager.calculate_position_size(capital, signal.price, PositionSide.LONG, signal.timestamp)
            if sizing.allowed:
                self.open_position(PositionSide.LONG, signal.price, signal.timestamp, sizing)
                action_msg = f"OPENED LONG: {sizing.contracts} contracts ({sizing.btc_size} BTC) at {signal.price:.2f}"
            else:
                action_msg = f"REJECTED LONG: {sizing.reason}"

        elif signal.action == "OPEN_SHORT" and self.position.side == PositionSide.NONE:
            sizing = self.risk_manager.calculate_position_size(capital, signal.price, PositionSide.SHORT, signal.timestamp)
            if sizing.allowed:
                self.open_position(PositionSide.SHORT, signal.price, signal.timestamp, sizing)
                action_msg = f"OPENED SHORT: {sizing.contracts} contracts ({sizing.btc_size} BTC) at {signal.price:.2f}"
            else:
                action_msg = f"REJECTED SHORT: {sizing.reason}"

        # 2. Exits without Reversal (EMA did not confirm reversal or pure exit)
        elif signal.action == "CLOSE_LONG" and self.position.side == PositionSide.LONG:
            trade_record, capital = self.close_current_position(
                exit_price=signal.price,
                exit_timestamp=signal.timestamp,
                exit_reason="SuperTrend changed to RED; EMA rejected SHORT (Flat in Cash)",
                current_capital=capital
            )
            action_msg = f"CLOSED LONG at {signal.price:.2f} (Net PnL: {trade_record.net_pnl:.2f} USDT). Remaining in Cash."

        elif signal.action == "CLOSE_SHORT" and self.position.side == PositionSide.SHORT:
            trade_record, capital = self.close_current_position(
                exit_price=signal.price,
                exit_timestamp=signal.timestamp,
                exit_reason="SuperTrend changed to GREEN; EMA rejected LONG (Flat in Cash)",
                current_capital=capital
            )
            action_msg = f"CLOSED SHORT at {signal.price:.2f} (Net PnL: {trade_record.net_pnl:.2f} USDT). Remaining in Cash."

        # 3. Automatic Reversal (SuperTrend flipped opposite AND EMA confirmed)
        elif signal.action == "REVERSE":
            if signal.signal_type == SignalType.REVERSE_TO_SHORT and self.position.side == PositionSide.LONG:
                # Step 1: Fully close LONG
                trade_record, capital = self.close_current_position(
                    exit_price=signal.price,
                    exit_timestamp=signal.timestamp,
                    exit_reason="SuperTrend flipped RED & EMA confirmed bearish (Automatic Reversal)",
                    current_capital=capital
                )
                # Step 2: Verify flat, then size and open SHORT
                sizing = self.risk_manager.calculate_position_size(capital, signal.price, PositionSide.SHORT, signal.timestamp)
                if sizing.allowed:
                    self.open_position(PositionSide.SHORT, signal.price, signal.timestamp, sizing)
                    action_msg = (
                        f"REVERSED: Closed LONG at {signal.price:.2f} (PnL: {trade_record.net_pnl:.2f} USDT) "
                        f"-> Opened SHORT: {sizing.contracts} contracts ({sizing.btc_size} BTC)"
                    )
                else:
                    action_msg = f"CLOSED LONG at {signal.price:.2f}, but reversal to SHORT blocked: {sizing.reason}"

            elif signal.signal_type == SignalType.REVERSE_TO_LONG and self.position.side == PositionSide.SHORT:
                # Step 1: Fully close SHORT
                trade_record, capital = self.close_current_position(
                    exit_price=signal.price,
                    exit_timestamp=signal.timestamp,
                    exit_reason="SuperTrend flipped GREEN & EMA confirmed bullish (Automatic Reversal)",
                    current_capital=capital
                )
                # Step 2: Verify flat, then size and open LONG
                sizing = self.risk_manager.calculate_position_size(capital, signal.price, PositionSide.LONG, signal.timestamp)
                if sizing.allowed:
                    self.open_position(PositionSide.LONG, signal.price, signal.timestamp, sizing)
                    action_msg = (
                        f"REVERSED: Closed SHORT at {signal.price:.2f} (PnL: {trade_record.net_pnl:.2f} USDT) "
                        f"-> Opened LONG: {sizing.contracts} contracts ({sizing.btc_size} BTC)"
                    )
                else:
                    action_msg = f"CLOSED SHORT at {signal.price:.2f}, but reversal to LONG blocked: {sizing.reason}"

        return trade_record, action_msg, capital
