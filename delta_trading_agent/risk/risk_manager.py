"""
Harden Risk Management Module (Phase 2).
Implements:
- 70% margin allocation limit per trade (leaving 30% unallocated capital)
- Configurable leverage sizing
- Position quantity calculation for Delta Exchange (0.001 BTC per contract)
- Maximum daily loss circuit breaker (including both realized and unrealized floating P&L)
- Maximum consecutive losing trades circuit breaker
- Maximum portfolio equity drawdown circuit breaker (max_total_drawdown_pct)
- SQLite persistent risk state (restored on application restart)
- Liquidation price estimation
- Exchange minimum order validation
"""

from dataclasses import dataclass
from datetime import datetime, date, timezone
from typing import Optional, Tuple, Any
from ..config.settings import PositionSide, TradingConfig


@dataclass
class SizingResult:
    allowed: bool
    contracts: int               # Number of Delta contracts (1 contract = 0.001 BTC)
    btc_size: float              # btc quantity = contracts * contract_value
    margin_required: float       # Margin in USDT
    notional_value: float        # Total notional in USDT
    effective_leverage: float
    liquidation_price: float
    reason: str


class RiskManager:
    def __init__(self, config: TradingConfig, db: Optional[Any] = None):
        self.config = config
        self.db = db
        self.contract_value = 0.001  # 1 contract = 0.001 BTC
        self.maintenance_margin_rate = 0.005  # 0.5%
        
        # Risk tracking state
        self.current_day: Optional[date] = None
        self.daily_start_capital: float = config.initial_capital
        self.daily_realized_pnl: float = 0.0
        self.consecutive_losses: int = 0
        self.circuit_breaker_active: bool = False
        self.circuit_breaker_reason: str = ""
        self.peak_equity: float = config.initial_capital

        # Restore persistent state from database if available
        if self.db is not None:
            self._restore_state_from_db()

    def _persist_state(self):
        """Saves current risk state to SQLite database."""
        if self.db is not None and hasattr(self.db, "save_risk_state"):
            d_str = self.current_day.isoformat() if self.current_day else datetime.now(timezone.utc).date().isoformat()
            self.db.save_risk_state(
                trading_date=d_str,
                daily_start_capital=self.daily_start_capital,
                daily_realized_pnl=self.daily_realized_pnl,
                consecutive_losses=self.consecutive_losses,
                circuit_breaker_active=self.circuit_breaker_active,
                circuit_breaker_reason=self.circuit_breaker_reason,
                peak_equity=self.peak_equity
            )

    def _restore_state_from_db(self):
        """Restores risk state from SQLite to prevent bypassing circuit breakers on restart."""
        try:
            state = self.db.load_risk_state()
            if state:
                self.peak_equity = float(state.get("peak_equity", self.config.initial_capital))
                if state.get("trading_date"):
                    try:
                        self.current_day = date.fromisoformat(state["trading_date"])
                    except Exception:
                        self.current_day = datetime.now(timezone.utc).date()
                self.daily_start_capital = float(state.get("daily_start_capital", self.config.initial_capital))
                self.daily_realized_pnl = float(state.get("daily_realized_pnl", 0.0))
                self.consecutive_losses = int(state.get("consecutive_losses", 0))
                self.circuit_breaker_active = bool(state.get("circuit_breaker_active", False))
                self.circuit_breaker_reason = str(state.get("circuit_breaker_reason", ""))
        except Exception:
            pass

    def reset_day_if_needed(self, current_capital: float, timestamp: Optional[int] = None):
        """Resets daily PnL counter when UTC day flips (using candle timestamp or system clock)."""
        if timestamp is not None:
            now_date = datetime.fromtimestamp(timestamp, tz=timezone.utc).date()
        else:
            now_date = datetime.now(timezone.utc).date()

        if self.current_day is None or now_date > self.current_day:
            self.current_day = now_date
            self.daily_start_capital = current_capital
            self.daily_realized_pnl = 0.0
            # Reset daily loss breaker on new UTC day if it was solely a daily loss breaker
            if "daily loss limit" in self.circuit_breaker_reason.lower():
                self.circuit_breaker_active = False
                self.circuit_breaker_reason = ""
            self._persist_state()

    def update_equity_and_check_drawdown(
        self,
        current_capital: float,
        unrealized_pnl: float,
        timestamp: Optional[int] = None
    ) -> bool:
        """
        Monitors floating intraday loss and overall portfolio equity drawdown.
        Returns True if a circuit breaker was triggered.
        """
        self.reset_day_if_needed(current_capital, timestamp)
        total_equity = current_capital + unrealized_pnl

        # 1. Update peak equity
        if total_equity > self.peak_equity:
            self.peak_equity = total_equity

        # 2. Maximum Total Equity Drawdown Protection
        if self.peak_equity > 0:
            total_drawdown_pct = (self.peak_equity - total_equity) / self.peak_equity
            if total_drawdown_pct >= self.config.max_total_drawdown_pct:
                self.circuit_breaker_active = True
                self.circuit_breaker_reason = (
                    f"Hit maximum portfolio drawdown protection ({total_drawdown_pct*100:.2f}% >= {self.config.max_total_drawdown_pct*100:.2f}%)"
                )
                self._persist_state()
                return True

        # 3. Floating Intraday Daily Loss Check (Including Unrealized PnL)
        if self.config.include_unrealized_in_daily_loss:
            effective_daily_pnl = self.daily_realized_pnl + min(0.0, unrealized_pnl)
            max_daily_loss = self.daily_start_capital * self.config.max_daily_loss_pct
            if effective_daily_pnl <= -max_daily_loss:
                self.circuit_breaker_active = True
                self.circuit_breaker_reason = (
                    f"Hit daily loss limit including floating loss ({effective_daily_pnl:.2f} USDT <= -{max_daily_loss:.2f} USDT)"
                )
                self._persist_state()
                return True

        return self.circuit_breaker_active

    def calculate_position_size(
        self,
        current_capital: float,
        price: float,
        side: PositionSide,
        timestamp: Optional[int] = None
    ) -> SizingResult:
        """
        Calculates allowed contract quantity based on 70% margin allocation.
        Formula:
          Allocated Margin = current_capital * margin_allocation_pct (0.70)
          Max Notional = Allocated Margin * leverage
          Contracts = floor(Max Notional / (price * contract_value))
        """
        self.reset_day_if_needed(current_capital, timestamp)

        # Check circuit breakers
        if self.circuit_breaker_active:
            return SizingResult(
                allowed=False,
                contracts=0,
                btc_size=0.0,
                margin_required=0.0,
                notional_value=0.0,
                effective_leverage=0.0,
                liquidation_price=0.0,
                reason=f"Circuit breaker active: {self.circuit_breaker_reason}"
            )

        if current_capital <= 0 or price <= 0:
            return SizingResult(
                allowed=False,
                contracts=0,
                btc_size=0.0,
                margin_required=0.0,
                notional_value=0.0,
                effective_leverage=0.0,
                liquidation_price=0.0,
                reason="Invalid capital or price"
            )

        # 70% margin allocation
        allocated_margin = current_capital * self.config.margin_allocation_pct
        max_notional = allocated_margin * self.config.leverage

        # Contract value in USDT at current price
        cost_per_contract_notional = price * self.contract_value
        if cost_per_contract_notional <= 0:
            return SizingResult(
                allowed=False,
                contracts=0,
                btc_size=0.0,
                margin_required=0.0,
                notional_value=0.0,
                effective_leverage=0.0,
                liquidation_price=0.0,
                reason="Invalid contract cost"
            )

        contracts = int(max_notional // cost_per_contract_notional)
        if contracts < 1:
            return SizingResult(
                allowed=False,
                contracts=0,
                btc_size=0.0,
                margin_required=0.0,
                notional_value=0.0,
                effective_leverage=0.0,
                liquidation_price=0.0,
                reason=f"Insufficient capital ({current_capital:.2f} USDT) for minimum 1 contract at price {price:.2f}"
            )

        notional_value = contracts * cost_per_contract_notional
        margin_required = notional_value / self.config.leverage
        btc_size = round(contracts * self.contract_value, 6)
        effective_leverage = notional_value / current_capital

        # Liquidation price estimation
        liq_price = self.calculate_liquidation_price(
            entry_price=price,
            side=side,
            leverage=self.config.leverage
        )

        return SizingResult(
            allowed=True,
            contracts=contracts,
            btc_size=btc_size,
            margin_required=round(margin_required, 4),
            notional_value=round(notional_value, 4),
            effective_leverage=round(effective_leverage, 2),
            liquidation_price=round(liq_price, 2),
            reason="Size validated against 70% margin allocation and risk rules"
        )

    def calculate_liquidation_price(
        self,
        entry_price: float,
        side: PositionSide,
        leverage: float
    ) -> float:
        """
        Estimates liquidation price based on isolated leverage & maintenance margin.
        For LONG: Entry * (1 - 1/leverage + MMR)
        For SHORT: Entry * (1 + 1/leverage - MMR)
        """
        if leverage <= 0:
            return 0.0

        if side == PositionSide.LONG:
            # Drop that wipes out initial margin minus maintenance buffer
            liq = entry_price * (1.0 - (1.0 / leverage) + self.maintenance_margin_rate)
            return max(liq, 0.0)
        elif side == PositionSide.SHORT:
            liq = entry_price * (1.0 + (1.0 / leverage) - self.maintenance_margin_rate)
            return liq
        return 0.0

    def record_trade_result(self, net_pnl: float, current_capital: float, timestamp: Optional[int] = None):
        """
        Updates consecutive loss count and daily PnL limit checks.
        """
        self.reset_day_if_needed(current_capital, timestamp)
        self.daily_realized_pnl += net_pnl

        # Track consecutive losses
        if net_pnl < 0:
            self.consecutive_losses += 1
            if self.consecutive_losses >= self.config.max_consecutive_losses:
                self.circuit_breaker_active = True
                self.circuit_breaker_reason = (
                    f"Hit maximum consecutive losing trades ({self.consecutive_losses} >= {self.config.max_consecutive_losses})"
                )
        else:
            self.consecutive_losses = 0

        # Track daily loss limit
        max_daily_loss = self.daily_start_capital * self.config.max_daily_loss_pct
        if self.daily_realized_pnl <= -max_daily_loss:
            self.circuit_breaker_active = True
            self.circuit_breaker_reason = (
                f"Hit daily loss limit ({self.daily_realized_pnl:.2f} USDT <= -{max_daily_loss:.2f} USDT)"
            )

        # Update peak equity
        if current_capital > self.peak_equity:
            self.peak_equity = current_capital

        self._persist_state()
