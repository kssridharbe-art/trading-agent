"""
Execution Model Simulator (Phase 2).
Provides realistic simulation of:
  - MARKET orders: immediate fill at close ± slippage, taker fee.
  - POST_ONLY_LIMIT orders: placed at close ± maker_offset_pct.
      Fills if next bar touches the limit price (maker fee).
      Times out after config.limit_timeout_bars → taker fill at that bar's close.
  - Partial fills: random 60-100% of requested quantity.
  - Tick-size rounding on all prices.
No live exchange connections; backtest-only module.
"""

import random
from dataclasses import dataclass
from typing import Optional, Dict, Any

from ..config.settings import TradingConfig, ExecutionType
from ..signals.signal_generator import TradingSignal, SignalType


@dataclass
class FilledOrder:
    filled: bool           # True if the order was executed this bar
    timed_out: bool        # True if limit order expired unfilled
    fill_price: float
    fill_qty: int          # Contracts filled (may be < requested for partials)
    fee_rate_applied: float
    fill_type: str         # "MARKET_TAKER", "LIMIT_MAKER", "LIMIT_TAKER_TIMEOUT", "PARTIAL"


class ExecutionModel:
    def __init__(self, config: TradingConfig):
        self.config = config

    # ─────────────────────────────────────────────────────────────────────────
    # Public helpers called by BacktestEngine
    # ─────────────────────────────────────────────────────────────────────────

    def calc_limit_price(self, signal: TradingSignal, close: float) -> float:
        """
        Calculates the passive limit entry price offset from close.
        Buy limits are placed BELOW close; sell limits ABOVE close.
        """
        offset = self.config.maker_offset_pct
        tick = self.config.tick_size

        buy_signal_types = {
            SignalType.BUY_LONG, SignalType.REVERSE_TO_LONG,
        }

        if signal.signal_type in buy_signal_types:
            raw = close * (1.0 - offset)   # Bid below market
        else:
            raw = close * (1.0 + offset)   # Ask above market

        return self._round_tick(raw)

    def try_fill_limit(
        self,
        pending_order: Dict[str, Any],
        bar_high: float,
        bar_low: float,
        bar_close: float,
        bars_waiting: int
    ) -> FilledOrder:
        """
        Checks if a pending limit order can be filled on the current bar.
        Fill logic: price must touch the limit level within bar high/low range.
        After timeout_bars → filled at bar_close with taker fee (or dropped).
        """
        limit_price: float = pending_order["limit_price"]
        signal: TradingSignal = pending_order["signal"]
        timeout_bars: int = pending_order.get("timeout_bars", self.config.limit_timeout_bars)

        buy_signal_types = {
            SignalType.BUY_LONG, SignalType.REVERSE_TO_LONG,
        }

        is_buy = signal.signal_type in buy_signal_types

        # Timeout: execute at taker rate
        if bars_waiting >= timeout_bars:
            fill_price = self._round_tick(bar_close * (1.0 + self.config.slippage_pct) if is_buy
                                          else bar_close * (1.0 - self.config.slippage_pct))
            return FilledOrder(
                filled=True,
                timed_out=True,
                fill_price=fill_price,
                fill_qty=1,   # Executor will re-size from capital; qty placeholder
                fee_rate_applied=self.config.taker_fee_rate,
                fill_type="LIMIT_TAKER_TIMEOUT"
            )

        # Check if limit price is within this bar's range (passive fill)
        if is_buy and bar_low <= limit_price:
            fill_price = limit_price
            fill_qty = self._partial_fill_qty()
            return FilledOrder(
                filled=True,
                timed_out=False,
                fill_price=fill_price,
                fill_qty=fill_qty,
                fee_rate_applied=self.config.maker_fee_rate,
                fill_type="LIMIT_MAKER" if fill_qty == 100 else "PARTIAL"
            )
        elif (not is_buy) and bar_high >= limit_price:
            fill_price = limit_price
            fill_qty = self._partial_fill_qty()
            return FilledOrder(
                filled=True,
                timed_out=False,
                fill_price=fill_price,
                fill_qty=fill_qty,
                fee_rate_applied=self.config.maker_fee_rate,
                fill_type="LIMIT_MAKER" if fill_qty == 100 else "PARTIAL"
            )

        return FilledOrder(
            filled=False,
            timed_out=False,
            fill_price=0.0,
            fill_qty=0,
            fee_rate_applied=0.0,
            fill_type="PENDING"
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Private helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _round_tick(self, price: float) -> float:
        """Rounds price to nearest tick_size increment."""
        tick = self.config.tick_size
        return round(price / tick) * tick

    def _partial_fill_qty(self) -> int:
        """
        Simulates partial fill probability (60-100% of sizing).
        Returns a fill fraction 60-100 (applied as percentage in executor).
        In this simplified model, 80% of the time we get a full fill,
        20% of the time we get a partial 60-80% fill.
        """
        if random.random() < 0.80:
            return 100   # Full fill (percentage)
        return random.randint(60, 80)   # Partial fill percentage
