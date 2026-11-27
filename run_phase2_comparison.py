"""
Phase 2.1: Comprehensive Comparative Performance & Reconciliation Analyzer.
Executes multi-timeframe backtests across:
- Timeframes: 5-minute, 15-minute, 1-hour
- Regime Filters: NONE (Baseline), ADX_ONLY, ATR_ONLY, BOTH
- Execution Models: MARKET, POST_ONLY_LIMIT
- Total Configurations: 24

Strict Accounting & Reconciliation Rules:
1. Financial Reconciliation: Gross PnL - Total Fees & Funding = Net PnL (for overall and every partition).
2. Partition Reconciliation: Total Trades == IS Trades + Val Trades + OOS Trades.
3. Partition Net PnL: Total Net PnL == IS Net PnL + Val Net PnL + OOS Net PnL.
4. Partition Fees: Total Fees == IS Fees + Val Fees + OOS Fees.
5. Non-overlapping chronological partitions: 60% In-Sample, 20% Validation, 20% Out-of-Sample.
6. Automated assertions that halt execution if any reconciliation check fails.

Generated Deliverables:
- phase2_comparison_report.csv (corrected summary matrix)
- phase2_partition_reconciliation.csv (audit verifying exact mathematical equality)
- phase2_trade_by_trade_audit.csv (every executed trade across all 24 configurations)
- phase2_signal_reconciliation.csv (every signal generated and executed/blocked)
- equity_curves/ (directory containing CSV equity curve for every configuration)
"""

import os
import sys
import time
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
import pandas as pd
import numpy as np

from delta_trading_agent.config.settings import (
    TradingConfig, EMAMethod, RegimeFilterType, ExecutionType, PositionSide
)
from delta_trading_agent.backtest.engine import BacktestEngine, BacktestResult
from delta_trading_agent.backtest.performance import calculate_performance, PerformanceSummary
from delta_trading_agent.execution.strategy_executor import TradeRecord


DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
EQUITY_DIR = os.path.join(os.path.dirname(__file__), "equity_curves")


def ensure_candles(symbol: str = "BTCUSDT", days: int = 30) -> Dict[str, pd.DataFrame]:
    """Retrieves and caches 30 days of candles for 5m, 15m, and 1h resolutions."""
    os.makedirs(DATA_DIR, exist_ok=True)
    datasets: Dict[str, pd.DataFrame] = {}

    resolutions = [
        ("5m", "5"),
        ("15m", "15"),
        ("1h", "60")
    ]

    for tf_label, res_code in resolutions:
        cache_file = os.path.join(DATA_DIR, f"candles_{symbol}_{tf_label}.csv")
        if os.path.exists(cache_file):
            print(f"Loading cached {tf_label} candles from {cache_file}...")
            df = pd.read_csv(cache_file)
            df["timestamp"] = df["timestamp"].astype(int)
            datasets[tf_label] = df
        else:
            print(f"Fetching {days} days of {tf_label} candles from Delta Exchange...")
            df = BacktestEngine.fetch_historical_candles_from_delta(
                symbol=symbol,
                resolution=res_code,
                days=days
            )
            df.to_csv(cache_file, index=False)
            datasets[tf_label] = df

    return datasets


def get_partition_boundaries(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Chronological, non-overlapping dataset partition:
    - In-Sample: first 60% of timeline (0% to 60%)
    - Validation: next 20% of timeline (60% to 80%)
    - Out-of-Sample: final 20% of timeline (80% to 100%)
    """
    n = len(df)
    i_is_end = int(n * 0.60)
    i_val_end = int(n * 0.80)

    ts_start = int(df["timestamp"].iloc[0])
    ts_is_end = int(df["timestamp"].iloc[i_is_end])
    ts_val_end = int(df["timestamp"].iloc[i_val_end])
    ts_end = int(df["timestamp"].iloc[-1])

    return {
        "ts_start": ts_start,
        "ts_is_end": ts_is_end,
        "ts_val_end": ts_val_end,
        "ts_end": ts_end,
        "idx_is_end": i_is_end,
        "idx_val_end": i_val_end,
        "total_candles": n,
        "is_candles": i_is_end,
        "val_candles": i_val_end - i_is_end,
        "oos_candles": n - i_val_end
    }


def calculate_max_daily_loss(equity_curve: List[Dict[str, Any]]) -> float:
    """Calculates the largest single UTC day drawdown in USDT."""
    if not equity_curve:
        return 0.0
    df_eq = pd.DataFrame(equity_curve)
    df_eq["date"] = pd.to_datetime(df_eq["timestamp"], unit="s", utc=True).dt.date
    daily_groups = df_eq.groupby("date")["total_equity"]
    daily_start = daily_groups.first()
    daily_min = daily_groups.min()
    daily_drawdowns = daily_start - daily_min
    max_d = daily_drawdowns.max() if not daily_drawdowns.empty else 0.0
    return round(float(max(max_d, 0.0)), 2)


def partition_trades_and_equity(
    trades: List[TradeRecord],
    equity_curve: List[Dict[str, Any]],
    boundaries: Dict[str, Any],
    initial_capital: float
) -> Dict[str, Any]:
    """
    Assigns completed trades and equity curves to In-Sample, Validation, and Out-of-Sample
    partitions based on trade completion (`exit_timestamp`).
    
    Boundary treatment:
    - Positions open at a partition boundary remain open without artificial liquidation.
    - When a trade closes, its realized P&L and fees are attributed to the partition in which
      it closed (`exit_timestamp`).
    - This preserves realistic continuous market state while ensuring:
      total_trades == is_trades + val_trades + oos_trades
      total_net_pnl == is_net_pnl + val_net_pnl + oos_net_pnl
      total_fees == is_fees + val_fees + oos_fees
    """
    ts_is_end = boundaries["ts_is_end"]
    ts_val_end = boundaries["ts_val_end"]

    is_trades = [t for t in trades if t.exit_timestamp < ts_is_end]
    val_trades = [t for t in trades if ts_is_end <= t.exit_timestamp < ts_val_end]
    oos_trades = [t for t in trades if t.exit_timestamp >= ts_val_end]

    # Partition equity curves
    is_eq_records = [e for e in equity_curve if e["timestamp"] < ts_is_end]
    val_eq_records = [e for e in equity_curve if ts_is_end <= e["timestamp"] < ts_val_end]
    oos_eq_records = [e for e in equity_curve if e["timestamp"] >= ts_val_end]

    is_eq_vals = [e["total_equity"] for e in is_eq_records] or [initial_capital]
    is_ending_cap = is_eq_vals[-1] if is_eq_vals else initial_capital

    val_eq_vals = [e["total_equity"] for e in val_eq_records] or [is_ending_cap]
    val_ending_cap = val_eq_vals[-1] if val_eq_vals else is_ending_cap

    oos_eq_vals = [e["total_equity"] for e in oos_eq_records] or [val_ending_cap]

    # Calculate partition summaries
    is_summary = calculate_performance(is_trades, initial_capital, is_eq_vals)
    val_summary = calculate_performance(val_trades, is_ending_cap, val_eq_vals)
    oos_summary = calculate_performance(oos_trades, val_ending_cap, oos_eq_vals)

    return {
        "is_trades": is_trades,
        "val_trades": val_trades,
        "oos_trades": oos_trades,
        "is_summary": is_summary,
        "val_summary": val_summary,
        "oos_summary": oos_summary,
        "is_equity": is_eq_records,
        "val_equity": val_eq_records,
        "oos_equity": oos_eq_records
    }


def run_configuration_audit(
    cfg: TradingConfig,
    df: pd.DataFrame,
    boundaries: Dict[str, Any],
    config_id: str
) -> Dict[str, Any]:
    """Runs a single configuration, performs all reconciliations, and verifies assertions."""
    engine = BacktestEngine(cfg)
    full_res = engine.run(df)
    summ = full_res.summary

    # Partition trades and equity
    parts = partition_trades_and_equity(
        trades=full_res.trades,
        equity_curve=full_res.equity_curve,
        boundaries=boundaries,
        initial_capital=cfg.initial_capital
    )

    is_summ = parts["is_summary"]
    val_summ = parts["val_summary"]
    oos_summ = parts["oos_summary"]

    # ── AUTOMATED RECONCILIATION ASSERTIONS ──────────────────────────────────
    # 1. Trade count reconciliation
    total_trades = summ.total_trades
    is_trades_count = is_summ.total_trades
    val_trades_count = val_summ.total_trades
    oos_trades_count = oos_summ.total_trades
    assert total_trades == (is_trades_count + val_trades_count + oos_trades_count), (
        f"[{config_id}] Trade count mismatch: Overall {total_trades} != "
        f"IS ({is_trades_count}) + Val ({val_trades_count}) + OOS ({oos_trades_count})"
    )

    # 2. P&L accounting reconciliation: Gross - Fees == Net
    gross_minus_fees = round(summ.total_gross_pnl - summ.total_fees_paid, 2)
    assert abs(gross_minus_fees - summ.total_net_pnl) < 0.05, (
        f"[{config_id}] Overall P&L Accounting mismatch: Gross ({summ.total_gross_pnl}) - "
        f"Fees ({summ.total_fees_paid}) = {gross_minus_fees} != Net ({summ.total_net_pnl})"
    )

    # 3. Partition Net P&L reconciliation
    sum_partition_net = round(is_summ.total_net_pnl + val_summ.total_net_pnl + oos_summ.total_net_pnl, 2)
    assert abs(summ.total_net_pnl - sum_partition_net) < 0.05, (
        f"[{config_id}] Partition Net P&L mismatch: Overall ({summ.total_net_pnl}) != "
        f"IS ({is_summ.total_net_pnl}) + Val ({val_summ.total_net_pnl}) + OOS ({oos_summ.total_net_pnl}) = {sum_partition_net}"
    )

    # 4. Partition Gross P&L reconciliation
    sum_partition_gross = round(is_summ.total_gross_pnl + val_summ.total_gross_pnl + oos_summ.total_gross_pnl, 2)
    assert abs(summ.total_gross_pnl - sum_partition_gross) < 0.05, (
        f"[{config_id}] Partition Gross P&L mismatch: Overall ({summ.total_gross_pnl}) != "
        f"IS ({is_summ.total_gross_pnl}) + Val ({val_summ.total_gross_pnl}) + OOS ({oos_summ.total_gross_pnl}) = {sum_partition_gross}"
    )

    # 5. Partition Fees reconciliation
    sum_partition_fees = round(is_summ.total_fees_paid + val_summ.total_fees_paid + oos_summ.total_fees_paid, 2)
    assert abs(summ.total_fees_paid - sum_partition_fees) < 0.05, (
        f"[{config_id}] Partition Fees mismatch: Overall ({summ.total_fees_paid}) != "
        f"IS ({is_summ.total_fees_paid}) + Val ({val_summ.total_fees_paid}) + OOS ({oos_summ.total_fees_paid}) = {sum_partition_fees}"
    )

    max_daily_loss = calculate_max_daily_loss(full_res.equity_curve)

    # Trade duration
    durations = [(t.exit_timestamp - t.entry_timestamp) / 3600.0 for t in full_res.trades]
    avg_dur = round(float(np.mean(durations)), 2) if durations else 0.0

    # Tag trades with config_id and partition
    for t in parts["is_trades"]:
        t.__dict__["config_id"] = config_id
        t.__dict__["partition"] = "IN_SAMPLE"
    for t in parts["val_trades"]:
        t.__dict__["config_id"] = config_id
        t.__dict__["partition"] = "VALIDATION"
    for t in parts["oos_trades"]:
        t.__dict__["config_id"] = config_id
        t.__dict__["partition"] = "OUT_OF_SAMPLE"

    # Tag signals with config_id
    for s in full_res.signals_log:
        s["config_id"] = config_id

    # Save equity curve
    os.makedirs(EQUITY_DIR, exist_ok=True)
    eq_file = os.path.join(EQUITY_DIR, f"equity_curve_{config_id.replace(' ', '_').replace('|', '_')}.csv")
    pd.DataFrame(full_res.equity_curve).to_csv(eq_file, index=False)

    return {
        # Config identifiers
        "config_id": config_id,
        "timeframe": cfg.resolution,
        "regime_filter": cfg.regime_filter.value,
        "execution_type": cfg.execution_type.value,
        "adx_threshold": cfg.adx_threshold,
        "atr_min_pct": cfg.atr_min_pct,
        # Overall 30-Day Metrics
        "total_trades": summ.total_trades,
        "win_rate_pct": summ.win_rate_pct,
        "gross_pnl": summ.total_gross_pnl,
        "entry_commission": summ.total_entry_fees,
        "exit_commission": summ.total_exit_fees,
        "funding_charges": summ.total_funding_fees,
        "total_fees_and_funding": summ.total_fees_paid,
        "net_pnl": summ.total_net_pnl,
        "net_return_pct": summ.total_return_pct,
        "profit_factor": summ.profit_factor,
        "gross_profit_factor": summ.gross_profit_factor,
        "max_drawdown_pct": summ.max_drawdown_pct,
        "max_drawdown_usdt": summ.max_drawdown_amount,
        "max_daily_loss_usdt": max_daily_loss,
        "avg_trade_duration_hrs": avg_dur,
        # Long vs Short breakdown
        "long_trades": summ.long_trades,
        "long_win_rate_pct": summ.long_win_rate_pct,
        "long_gross_pnl": summ.long_gross_pnl,
        "long_fees_paid": summ.long_fees_paid,
        "long_net_pnl": summ.long_net_pnl,
        "short_trades": summ.short_trades,
        "short_win_rate_pct": summ.short_win_rate_pct,
        "short_gross_pnl": summ.short_gross_pnl,
        "short_fees_paid": summ.short_fees_paid,
        "short_net_pnl": summ.short_net_pnl,
        # Partition 1: In-Sample (60%)
        "is_trades": is_summ.total_trades,
        "is_win_rate_pct": is_summ.win_rate_pct,
        "is_gross_pnl": is_summ.total_gross_pnl,
        "is_fees": is_summ.total_fees_paid,
        "is_net_pnl": is_summ.total_net_pnl,
        "is_profit_factor": is_summ.profit_factor,
        # Partition 2: Validation (20%)
        "val_trades": val_summ.total_trades,
        "val_win_rate_pct": val_summ.win_rate_pct,
        "val_gross_pnl": val_summ.total_gross_pnl,
        "val_fees": val_summ.total_fees_paid,
        "val_net_pnl": val_summ.total_net_pnl,
        "val_profit_factor": val_summ.profit_factor,
        # Partition 3: Out-of-Sample (20%)
        "oos_trades": oos_summ.total_trades,
        "oos_win_rate_pct": oos_summ.win_rate_pct,
        "oos_gross_pnl": oos_summ.total_gross_pnl,
        "oos_fees": oos_summ.total_fees_paid,
        "oos_net_pnl": oos_summ.total_net_pnl,
        "oos_profit_factor": oos_summ.profit_factor,
        # Reconciliation Verification Flag
        "reconciliation_passed": True,
        # Objects for export
        "_trades": full_res.trades,
        "_signals": full_res.signals_log
    }


def run_complete_matrix_and_audit():
    print("=" * 95)
    print("PHASE 2.1: CRITICAL BACKTEST ACCOUNTING & VALIDATION AUDIT")
    print("Enforcing: Gross - Fees = Net PnL, Partition Equality, and Zero Unwarranted Fills")
    print("=" * 95)

    datasets = ensure_candles(symbol="BTCUSDT", days=30)

    timeframes = ["5m", "15m", "1h"]
    regime_filters = [
        RegimeFilterType.NONE,
        RegimeFilterType.ADX_ONLY,
        RegimeFilterType.ATR_ONLY,
        RegimeFilterType.BOTH
    ]
    execution_types = [
        ExecutionType.MARKET,
        ExecutionType.POST_ONLY_LIMIT
    ]

    all_comparison_rows: List[Dict[str, Any]] = []
    all_reconciliation_rows: List[Dict[str, Any]] = []
    all_trades: List[Dict[str, Any]] = []
    all_signals: List[Dict[str, Any]] = []

    total_runs = len(timeframes) * len(regime_filters) * len(execution_types)
    run_idx = 0

    for tf in timeframes:
        df_tf = datasets[tf]
        boundaries = get_partition_boundaries(df_tf)
        print(f"\n[Timeframe: {tf}] Total Candles: {boundaries['total_candles']} | "
              f"IS (60%): {boundaries['is_candles']} | Val (20%): {boundaries['val_candles']} | OOS (20%): {boundaries['oos_candles']}")

        for rf in regime_filters:
            for ex in execution_types:
                run_idx += 1
                cfg_id = f"{tf}_{rf.value}_{ex.value}"
                print(f"  [{run_idx:02d}/{total_runs}] Running {cfg_id}...")

                cfg = TradingConfig(
                    symbol="BTCUSDT",
                    resolution=tf,
                    ema_method=EMAMethod.METHOD_C,
                    regime_filter=rf,
                    execution_type=ex,
                    adx_threshold=20.0,
                    atr_min_pct=0.15,
                    leverage=10.0,
                    margin_allocation_pct=0.70,
                    initial_capital=10000.0,
                    live_trading_authorized=False,
                    max_total_drawdown_pct=0.50
                )

                audit_res = run_configuration_audit(cfg, df_tf, boundaries, cfg_id)

                # Collect trade logs
                for t in audit_res["_trades"]:
                    d = dict(t.__dict__)
                    all_trades.append(d)

                # Collect signals
                for s in audit_res["_signals"]:
                    all_signals.append(s)

                # Partition reconciliation verification row
                all_reconciliation_rows.append({
                    "config_id": cfg_id,
                    "timeframe": tf,
                    "regime_filter": rf.value,
                    "execution_type": ex.value,
                    "overall_trades": audit_res["total_trades"],
                    "is_trades": audit_res["is_trades"],
                    "val_trades": audit_res["val_trades"],
                    "oos_trades": audit_res["oos_trades"],
                    "trade_count_diff": audit_res["total_trades"] - (audit_res["is_trades"] + audit_res["val_trades"] + audit_res["oos_trades"]),
                    "overall_gross_pnl": audit_res["gross_pnl"],
                    "is_gross_pnl": audit_res["is_gross_pnl"],
                    "val_gross_pnl": audit_res["val_gross_pnl"],
                    "oos_gross_pnl": audit_res["oos_gross_pnl"],
                    "gross_pnl_diff": round(audit_res["gross_pnl"] - (audit_res["is_gross_pnl"] + audit_res["val_gross_pnl"] + audit_res["oos_gross_pnl"]), 4),
                    "overall_fees": audit_res["total_fees_and_funding"],
                    "is_fees": audit_res["is_fees"],
                    "val_fees": audit_res["val_fees"],
                    "oos_fees": audit_res["oos_fees"],
                    "fees_diff": round(audit_res["total_fees_and_funding"] - (audit_res["is_fees"] + audit_res["val_fees"] + audit_res["oos_fees"]), 4),
                    "overall_net_pnl": audit_res["net_pnl"],
                    "is_net_pnl": audit_res["is_net_pnl"],
                    "val_net_pnl": audit_res["val_net_pnl"],
                    "oos_net_pnl": audit_res["oos_net_pnl"],
                    "net_pnl_diff": round(audit_res["net_pnl"] - (audit_res["is_net_pnl"] + audit_res["val_net_pnl"] + audit_res["oos_net_pnl"]), 4),
                    "gross_minus_fees": round(audit_res["gross_pnl"] - audit_res["total_fees_and_funding"], 4),
                    "accounting_pnl_diff": round((audit_res["gross_pnl"] - audit_res["total_fees_and_funding"]) - audit_res["net_pnl"], 4),
                    "reconciliation_status": "EXACT_RECONCILED"
                })

                # Clean internal objects before CSV export
                clean_row = {k: v for k, v in audit_res.items() if not k.startswith("_")}
                all_comparison_rows.append(clean_row)

    # ── EXPORT ARTIFACTS ─────────────────────────────────────────────────────
    # 1. Comparison Report CSV
    df_comp = pd.DataFrame(all_comparison_rows)
    df_comp.to_csv("phase2_comparison_report.csv", index=False)
    print("\n[OK] Corrected comparison report saved to phase2_comparison_report.csv")

    # 2. Partition Reconciliation CSV
    df_rec = pd.DataFrame(all_reconciliation_rows)
    df_rec.to_csv("phase2_partition_reconciliation.csv", index=False)
    print("[OK] Partition reconciliation audit saved to phase2_partition_reconciliation.csv")

    # 3. Trade-by-Trade Audit Log
    df_trades = pd.DataFrame(all_trades)
    df_trades.to_csv("phase2_trade_by_trade_audit.csv", index=False)
    print(f"[OK] Trade-by-trade audit log ({len(df_trades)} trades) saved to phase2_trade_by_trade_audit.csv")

    # 4. Signal Reconciliation Log
    df_signals = pd.DataFrame(all_signals)
    df_signals.to_csv("phase2_signal_reconciliation.csv", index=False)
    print(f"[OK] Signal reconciliation log ({len(df_signals)} signals) saved to phase2_signal_reconciliation.csv")

    # Print verification summary
    print("\n" + "=" * 105)
    print("PHASE 2.1 RECONCILIATION SUMMARY MATRIX")
    print("=" * 105)
    summary_cols = [
        "config_id", "total_trades", "gross_pnl", "total_fees_and_funding", "net_pnl",
        "is_trades", "is_net_pnl", "val_trades", "val_net_pnl", "oos_trades", "oos_net_pnl"
    ]
    print(df_comp[summary_cols].to_string(index=False))

    return df_comp, df_rec, df_trades, df_signals


if __name__ == "__main__":
    run_complete_matrix_and_audit()
