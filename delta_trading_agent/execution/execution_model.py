"""
Execution Model Simulator (Phase 2.1 Audit Corrected).
Provides realistic simulation of:
  - MARKET orders: immediate fill at close ± slippage, taker fee (0.05%).
  - POST_ONLY_LIMIT orders:
      - Modeled separately for exits and entries.
      - Limit entry placed at close ± maker_offset_pct. Fills if bar touches limit price.
        If price runs away and times out (limit_timeout_bars), order is CANCELLED (unfilled).
      - Limit exit placed at close ∓ maker_offset_pct. Fills as maker if touched.
        If it times out, executes at market close as taker to prevent holding position against SuperTrend.
      - Maker fee (0.02%) applied ONLY to confirmed maker fills.
      - Taker fee (0.05%) applied to market orders and timed-out exits.
      - Partial fills: realistic 70-100% fill fraction on limit fills.
      - Tick-size rounding (0.5 USDT) on all prices.
"""

import random
from dataclasses import dataclass
from typing import Optional, Dict, Any

from ..config.settings import TradingConfig, ExecutionType
from ..signals.signal_generator import TradingSignal, SignalType


@dataclass
class OrderFillResult:
    filled: bool                  # True if order executed
    cancelled: bool               # True if order was cancelled unfilled
    timed_out: bool               # True if timeout occurred
    fill_price: float             # Execution price with tick rounding
    fill_fraction: float          # 0.7 - 1.0 (for partial fills)
    fee_rate: float               # Exact maker (0.02%) or taker (0.05%) fee rate
    fill_type: str                # "MARKET_TAKER", "LIMIT_MAKER", "PARTIAL_MAKER", "TIMEOUT_TAKER", "UNFILLED_CANCELLED"


class ExecutionModel:
    def __init__(self, config: TradingConfig, seed: int = 42):
        self.config = config
        self.rng = random.Random(seed)

    def _round_tick(self, price: float) -> float:
        """Rounds price to nearest tick_size increment."""
        tick = self.config.tick_size
        return round(price / tick) * tick

    # ─────────────────────────────────────────────────────────────────────────
    # Limit Price Calculations
    # ─────────────────────────────────────────────────────────────────────────

    def calc_entry_limit_price(self, is_buy: bool, close: float) -> float:
        """
        Passive entry limit:
        - Buy limit placed BELOW current close.
        - Sell limit placed ABOVE current close.
        """
        offset = self.config.maker_offset_pct
        if is_buy:
            raw = close * (1.0 - offset)
        else:
            raw = close * (1.0 + offset)
        return self._round_tick(raw)

    def calc_exit_limit_price(self, is_closing_long: bool, close: float) -> float:
        """
        Passive exit limit:
        - Selling to close LONG: placed ABOVE close (trying to sell high).
        - Buying to close SHORT: placed BELOW close (trying to buy low).
        """
        offset = self.config.maker_offset_pct
        if is_closing_long:
            raw = close * (1.0 + offset)
        else:
            raw = close * (1.0 - offset)
        return self._round_tick(raw)

    # ─────────────────────────────────────────────────────────────────────────
    # Market Order Simulation
    # ─────────────────────────────────────────────────────────────────────────

    def execute_market_order(self, is_buy: bool, close: float) -> OrderFillResult:
        """Immediate fill with adverse slippage and taker fee."""
        s = self.config.slippage_pct
        if is_buy:
            raw = close * (1.0 + s)
        else:
            raw = close * (1.0 - s)
        fill_price = self._round_tick(raw)
        return OrderFillResult(
            filled=True,
            cancelled=False,
            timed_out=False,
            fill_price=fill_price,
            fill_fraction=1.0,
            fee_rate=self.config.taker_fee_rate,
            fill_type="MARKET_TAKER"
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Limit Order Simulation (Separate Entry and Exit rules)
    # ─────────────────────────────────────────────────────────────────────────

    def check_limit_entry_fill(
        self,
        is_buy: bool,
        limit_price: float,
        bar_high: float,
        bar_low: float,
        bar_close: float,
        bars_waiting: int
    ) -> OrderFillResult:
        """
        Checks whether an entry limit order fills on this bar.
        - Buy limit: low <= limit_price to fill.
        - Sell limit: high >= limit_price to fill.
        - If timeout reached without fill: CANCELLED (unfilled). Position NOT opened.
        """
        # 1. Check if price touched/penetrated limit price
        touched = (bar_low <= limit_price) if is_buy else (bar_high >= limit_price)

        if touched:
            # Filled as MAKER
            # 85% full fill, 15% partial fill (70-90%)
            if self.rng.random() < 0.85:
                fraction = 1.0
                fill_type = "LIMIT_MAKER"
            else:
                fraction = round(self.rng.uniform(0.70, 0.90), 2)
                fill_type = "PARTIAL_MAKER"

            return OrderFillResult(
                filled=True,
                cancelled=False,
                timed_out=False,
                fill_price=limit_price,
                fill_fraction=fraction,
                fee_rate=self.config.maker_fee_rate,
                fill_type=fill_type
            )

        # 2. Check if timed out
        if bars_waiting >= self.config.limit_timeout_bars:
            # Unfilled order is cancelled. No position opened.
            return OrderFillResult(
                filled=False,
                cancelled=True,
                timed_out=True,
                fill_price=0.0,
                fill_fraction=0.0,
                fee_rate=0.0,
                fill_type="UNFILLED_CANCELLED"
            )

        # Still waiting in orderbook
        return OrderFillResult(
            filled=False,
            cancelled=False,
            timed_out=False,
            fill_price=0.0,
            fill_fraction=0.0,
            fee_rate=0.0,
            fill_type="PENDING"
        )

    def check_limit_exit_fill(
        self,
        is_closing_long: bool,
        limit_price: float,
        bar_high: float,
        bar_low: float,
        bar_close: float,
        bars_waiting: int
    ) -> OrderFillResult:
        """
        Checks whether an exit limit order fills on this bar.
        - Closing LONG (selling): high >= limit_price.
        - Closing SHORT (buying): low <= limit_price.
        - If timeout reached: must liquidate at market close as TAKER to prevent holding opposite trend.
        """
        touched = (bar_high >= limit_price) if is_closing_long else (bar_low <= limit_price)

        if touched:
            # Passive maker exit
            return OrderFillResult(
                filled=True,
                cancelled=False,
                timed_out=False,
                fill_price=limit_price,
                fill_fraction=1.0,
                fee_rate=self.config.maker_fee_rate,
                fill_type="LIMIT_MAKER"
            )

        # Timeout: force close at market close as taker
        if bars_waiting >= self.config.limit_timeout_bars:
            s = self.config.slippage_pct
            # Selling long: gets lower price; buying back short: gets higher price
            fill_raw = bar_close * (1.0 - s) if is_closing_long else bar_close * (1.0 + s)
            fill_price = self._round_tick(fill_raw)
            return OrderFillResult(
                filled=True,
                cancelled=False,
                timed_out=True,
                fill_price=fill_price,
                fill_fraction=1.0,
                fee_rate=self.config.taker_fee_rate,
                fill_type="TIMEOUT_TAKER"
            )

        # Still waiting
        return OrderFillResult(
            filled=False,
            cancelled=False,
            timed_out=False,
            fill_price=0.0,
            fill_fraction=0.0,
            fee_rate=0.0,
            fill_type="PENDING"
        )
