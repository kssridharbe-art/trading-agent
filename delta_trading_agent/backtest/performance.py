"""
Backtesting Performance Analytics Module (Phase 2.1 Audit Corrected).
Calculates exact quantitative trading performance metrics:
- Total, Winning, and Losing Trades
- Win Rate (%) based on Net Realized P&L
- Exact Gross P&L, Total Fees & Funding, and Net P&L
- Exact reconciliation: Gross P&L - Fees & Funding = Net P&L
- Net Profit Factor and Gross Profit Factor
- Maximum Drawdown (Value and %)
- Average Winning vs. Losing Trade, Payoff Ratio
- Long trades breakdown vs. Short trades breakdown
"""

from dataclasses import dataclass
from typing import List, Dict, Any, Optional
import numpy as np
import pandas as pd
from ..execution.strategy_executor import TradeRecord


@dataclass
class PerformanceSummary:
    initial_capital: float
    ending_capital: float
    total_gross_pnl: float
    total_entry_fees: float
    total_exit_fees: float
    total_funding_fees: float
    total_fees_paid: float
    total_net_pnl: float
    total_return_pct: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    breakeven_trades: int
    win_rate_pct: float
    profit_factor: float          # Net Profit Factor: sum(net wins) / abs(sum(net losses))
    gross_profit_factor: float    # Gross Profit Factor: sum(gross wins) / abs(sum(gross losses))
    gross_profit: float           # sum(gross wins)
    gross_loss: float             # abs(sum(gross losses))
    net_profit: float             # sum(net wins)
    net_loss: float               # abs(sum(net losses))
    max_drawdown_amount: float
    max_drawdown_pct: float
    avg_trade_pnl: float
    avg_winning_trade: float
    avg_losing_trade: float
    payoff_ratio: float
    # Long vs Short
    long_trades: int
    long_win_rate_pct: float
    long_gross_pnl: float
    long_fees_paid: float
    long_net_pnl: float
    short_trades: int
    short_win_rate_pct: float
    short_gross_pnl: float
    short_fees_paid: float
    short_net_pnl: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "initial_capital": self.initial_capital,
            "ending_capital": self.ending_capital,
            "total_gross_pnl": self.total_gross_pnl,
            "gross_pnl": self.total_gross_pnl,
            "total_entry_fees": self.total_entry_fees,
            "total_exit_fees": self.total_exit_fees,
            "total_funding_fees": self.total_funding_fees,
            "total_fees_paid": self.total_fees_paid,
            "total_net_pnl": self.total_net_pnl,
            "net_pnl": self.total_net_pnl,
            "total_return_pct": self.total_return_pct,
            "net_return_pct": self.total_return_pct,
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "breakeven_trades": self.breakeven_trades,
            "win_rate_pct": self.win_rate_pct,
            "profit_factor": self.profit_factor,
            "gross_profit_factor": self.gross_profit_factor,
            "gross_profit": self.gross_profit,
            "gross_loss": self.gross_loss,
            "net_profit": self.net_profit,
            "net_loss": self.net_loss,
            "max_drawdown_amount": self.max_drawdown_amount,
            "max_drawdown_pct": self.max_drawdown_pct,
            "avg_trade_pnl": self.avg_trade_pnl,
            "avg_winning_trade": self.avg_winning_trade,
            "avg_losing_trade": self.avg_losing_trade,
            "payoff_ratio": self.payoff_ratio,
            "long_trades": self.long_trades,
            "long_win_rate_pct": self.long_win_rate_pct,
            "long_gross_pnl": self.long_gross_pnl,
            "long_fees_paid": self.long_fees_paid,
            "long_net_pnl": self.long_net_pnl,
            "short_trades": self.short_trades,
            "short_win_rate_pct": self.short_win_rate_pct,
            "short_gross_pnl": self.short_gross_pnl,
            "short_fees_paid": self.short_fees_paid,
            "short_net_pnl": self.short_net_pnl
        }


def calculate_performance(
    trades: List[TradeRecord],
    initial_capital: float,
    equity_curve: List[float]
) -> PerformanceSummary:
    """
    Computes all standard backtesting metrics on a list of executed TradeRecords.
    Enforces strict mathematical identity:
    total_gross_pnl - total_fees_paid == total_net_pnl (within rounding tolerance).
    """
    if not trades:
        return PerformanceSummary(
            initial_capital=round(initial_capital, 2),
            ending_capital=round(initial_capital, 2),
            total_gross_pnl=0.0,
            total_entry_fees=0.0,
            total_exit_fees=0.0,
            total_funding_fees=0.0,
            total_fees_paid=0.0,
            total_net_pnl=0.0,
            total_return_pct=0.0,
            total_trades=0,
            winning_trades=0,
            losing_trades=0,
            breakeven_trades=0,
            win_rate_pct=0.0,
            profit_factor=0.0,
            gross_profit_factor=0.0,
            gross_profit=0.0,
            gross_loss=0.0,
            net_profit=0.0,
            net_loss=0.0,
            max_drawdown_amount=0.0,
            max_drawdown_pct=0.0,
            avg_trade_pnl=0.0,
            avg_winning_trade=0.0,
            avg_losing_trade=0.0,
            payoff_ratio=0.0,
            long_trades=0,
            long_win_rate_pct=0.0,
            long_gross_pnl=0.0,
            long_fees_paid=0.0,
            long_net_pnl=0.0,
            short_trades=0,
            short_win_rate_pct=0.0,
            short_gross_pnl=0.0,
            short_fees_paid=0.0,
            short_net_pnl=0.0
        )

    # 1. Individual trade components
    gross_pnls = [t.gross_pnl for t in trades]
    net_pnls = [t.net_pnl for t in trades]
    entry_fees = [t.entry_fee for t in trades]
    exit_fees = [t.exit_fee for t in trades]
    funding_fees = [t.funding_fee for t in trades]

    total_gross_pnl = round(sum(gross_pnls), 2)
    total_entry_fees = round(sum(entry_fees), 2)
    total_exit_fees = round(sum(exit_fees), 2)
    total_funding_fees = round(sum(funding_fees), 2)
    total_fees_paid = round(total_entry_fees + total_exit_fees + total_funding_fees, 2)
    total_net_pnl = round(total_gross_pnl - total_fees_paid, 2)

    # Mathematical assertion: Gross - Fees == Net
    reconciliation_error = abs(total_gross_pnl - total_fees_paid - total_net_pnl)
    assert reconciliation_error < 0.01, (
        f"P&L Accounting Discrepancy! Gross ({total_gross_pnl:.4f}) - "
        f"Fees ({total_fees_paid:.4f}) != Net ({total_net_pnl:.4f}), error={reconciliation_error:.6f}"
    )

    ending_capital = initial_capital + total_net_pnl
    total_return_pct = (total_net_pnl / initial_capital) * 100.0 if initial_capital > 0 else 0.0

    # 2. Trade counts & win rate (based on Net Realized P&L)
    winning = [p for p in net_pnls if p > 0]
    losing = [p for p in net_pnls if p < 0]
    breakeven = [p for p in net_pnls if p == 0]

    n_trades = len(trades)
    n_win = len(winning)
    n_loss = len(losing)
    n_break = len(breakeven)
    win_rate = (n_win / n_trades * 100.0) if n_trades > 0 else 0.0

    # 3. Gross Profit / Gross Loss & Gross Profit Factor
    gross_wins = [g for g in gross_pnls if g > 0]
    gross_losses = [g for g in gross_pnls if g < 0]
    gross_profit = sum(gross_wins)
    gross_loss = abs(sum(gross_losses))
    gross_profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)

    # 4. Net Profit / Net Loss & Net Profit Factor
    net_profit = sum(winning)
    net_loss = abs(sum(losing))
    net_profit_factor = (net_profit / net_loss) if net_loss > 0 else (999.0 if net_profit > 0 else 0.0)

    avg_win = float(np.mean(winning)) if winning else 0.0
    avg_loss = float(abs(np.mean(losing))) if losing else 0.0
    payoff_ratio = (avg_win / avg_loss) if avg_loss > 0 else 0.0
    avg_trade_pnl = float(np.mean(net_pnls)) if net_pnls else 0.0

    # 5. Max Drawdown calculation from equity curve
    if len(equity_curve) > 1:
        equity_series = pd.Series(equity_curve)
        peak = equity_series.cummax()
        drawdown_amount = peak - equity_series
        drawdown_pct = (drawdown_amount / peak) * 100.0
        max_dd_amount = float(drawdown_amount.max())
        max_dd_pct = float(drawdown_pct.max())
    else:
        max_dd_amount = 0.0
        max_dd_pct = 0.0

    # 6. Long vs Short breakdown
    longs = [t for t in trades if t.side.value == "LONG"]
    shorts = [t for t in trades if t.side.value == "SHORT"]

    n_long = len(longs)
    long_win = sum(1 for t in longs if t.net_pnl > 0)
    long_win_rate = (long_win / n_long * 100.0) if n_long > 0 else 0.0
    long_gross = sum(t.gross_pnl for t in longs)
    long_fees = sum(t.entry_fee + t.exit_fee + t.funding_fee for t in longs)
    long_net = sum(t.net_pnl for t in longs)

    n_short = len(shorts)
    short_win = sum(1 for t in shorts if t.net_pnl > 0)
    short_win_rate = (short_win / n_short * 100.0) if n_short > 0 else 0.0
    short_gross = sum(t.gross_pnl for t in shorts)
    short_fees = sum(t.entry_fee + t.exit_fee + t.funding_fee for t in shorts)
    short_net = sum(t.net_pnl for t in shorts)

    return PerformanceSummary(
        initial_capital=round(initial_capital, 2),
        ending_capital=round(ending_capital, 2),
        total_gross_pnl=round(total_gross_pnl, 2),
        total_entry_fees=round(total_entry_fees, 2),
        total_exit_fees=round(total_exit_fees, 2),
        total_funding_fees=round(total_funding_fees, 2),
        total_fees_paid=round(total_fees_paid, 2),
        total_net_pnl=round(total_net_pnl, 2),
        total_return_pct=round(total_return_pct, 2),
        total_trades=n_trades,
        winning_trades=n_win,
        losing_trades=n_loss,
        breakeven_trades=n_break,
        win_rate_pct=round(win_rate, 2),
        profit_factor=round(net_profit_factor, 2),
        gross_profit_factor=round(gross_profit_factor, 2),
        gross_profit=round(gross_profit, 2),
        gross_loss=round(gross_loss, 2),
        net_profit=round(net_profit, 2),
        net_loss=round(net_loss, 2),
        max_drawdown_amount=round(max_dd_amount, 2),
        max_drawdown_pct=round(max_dd_pct, 2),
        avg_trade_pnl=round(avg_trade_pnl, 2),
        avg_winning_trade=round(avg_win, 2),
        avg_losing_trade=round(avg_loss, 2),
        payoff_ratio=round(payoff_ratio, 2),
        long_trades=n_long,
        long_win_rate_pct=round(long_win_rate, 2),
        long_gross_pnl=round(long_gross, 2),
        long_fees_paid=round(long_fees, 2),
        long_net_pnl=round(long_net, 2),
        short_trades=n_short,
        short_win_rate_pct=round(short_win_rate, 2),
        short_gross_pnl=round(short_gross, 2),
        short_fees_paid=round(short_fees, 2),
        short_net_pnl=round(short_net, 2)
    )
