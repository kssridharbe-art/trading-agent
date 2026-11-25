"""
Phase 2: Comparative Performance Analyzer.
Executes multi-timeframe backtests across:
- Timeframes: 5-minute, 15-minute, 1-hour
- Regime Filters: NONE (Baseline), ADX_ONLY, ATR_ONLY, BOTH
- Execution Models: MARKET, POST_ONLY_LIMIT
- Datasets: Full 30-Day, In-Sample (Days 1-20), Validation (Days 21-25), Out-of-Sample (Days 26-30)

Outputs:
- phase2_comparison_report.csv (complete metrics per configuration)
- Formatted summary tables for report generation
- Cached candles in data/ directory
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


DATA_DIR = os.path.join(os.path.dirname(__file__), "data")


def ensure_candles(symbol: str = "BTCUSDT", days: int = 30) -> Dict[str, pd.DataFrame]:
    """
    Retrieves and caches 30 days of candles for 5m, 15m, and 1h resolutions.
    """
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


def split_dataset(df: pd.DataFrame) -> Dict[str, pd.DataFrame]:
    """
    Chronological split:
    - in_sample: Days 1-20 (~66.7%)
    - validation: Days 21-25 (~16.7%)
    - out_of_sample: Days 26-30 (~16.7%)
    - full: Days 1-30 (100%)
    """
    n = len(df)
    i_val_start = int(n * (20.0 / 30.0))
    i_oos_start = int(n * (25.0 / 30.0))

    return {
        "full": df.copy(),
        "in_sample": df.iloc[:i_val_start].reset_index(drop=True),
        "validation": df.iloc[i_val_start:i_oos_start].reset_index(drop=True),
        "out_of_sample": df.iloc[i_oos_start:].reset_index(drop=True)
    }


def calculate_max_daily_loss(trades: List[Any], equity_curve: List[Dict[str, Any]]) -> float:
    """Calculates the largest single UTC day loss in USDT."""
    if not equity_curve:
        return 0.0
    
    df_eq = pd.DataFrame(equity_curve)
    df_eq["date"] = pd.to_datetime(df_eq["timestamp"], unit="s", utc=True).dt.date
    daily_start_end = df_eq.groupby("date")["total_equity"].agg(["first", "min"])
    daily_drawdowns = daily_start_end["first"] - daily_start_end["min"]
    max_d = daily_drawdowns.max() if not daily_drawdowns.empty else 0.0
    return round(float(max(max_d, 0.0)), 2)


def run_single_config(
    cfg: TradingConfig,
    df_splits: Dict[str, pd.DataFrame]
) -> Dict[str, Any]:
    """
    Runs backtest for a specific configuration across Full, In-Sample, and Out-of-Sample datasets.
    """
    engine = BacktestEngine(cfg)

    # 1. Full 30-Day Run
    full_res = engine.run(df_splits["full"])
    summ = full_res.summary

    # 2. In-Sample Run (Days 1-20)
    engine_is = BacktestEngine(cfg)
    is_res = engine_is.run(df_splits["in_sample"])

    # 3. Validation Run (Days 21-25)
    engine_val = BacktestEngine(cfg)
    val_res = engine_val.run(df_splits["validation"])

    # 4. Out-of-Sample Run (Days 26-30)
    engine_oos = BacktestEngine(cfg)
    oos_res = engine_oos.run(df_splits["out_of_sample"])

    # Trade duration calculation
    durations = []
    for t in full_res.trades:
        dur_hrs = (t.exit_timestamp - t.entry_timestamp) / 3600.0
        durations.append(dur_hrs)
    avg_dur = round(float(np.mean(durations)), 2) if durations else 0.0

    max_daily_loss = calculate_max_daily_loss(full_res.trades, full_res.equity_curve)
    gross_pnl = round(summ.gross_profit - summ.gross_loss, 2)

    return {
        "timeframe": cfg.resolution,
        "regime_filter": cfg.regime_filter.value,
        "execution_type": cfg.execution_type.value,
        "adx_threshold": cfg.adx_threshold,
        "atr_min_pct": cfg.atr_min_pct,
        # Full 30-Day Metrics
        "total_trades": summ.total_trades,
        "win_rate_pct": summ.win_rate_pct,
        "gross_pnl": gross_pnl,
        "total_fees_and_funding": summ.total_fees_paid,
        "net_pnl": summ.total_net_pnl,
        "net_return_pct": summ.total_return_pct,
        "profit_factor": summ.profit_factor,
        "max_drawdown_pct": summ.max_drawdown_pct,
        "max_drawdown_usdt": summ.max_drawdown_amount,
        "max_daily_loss_usdt": max_daily_loss,
        "avg_trade_duration_hrs": avg_dur,
        # Long vs Short
        "long_trades": summ.long_trades,
        "long_win_rate_pct": summ.long_win_rate_pct,
        "long_net_pnl": summ.long_net_pnl,
        "short_trades": summ.short_trades,
        "short_win_rate_pct": summ.short_win_rate_pct,
        "short_net_pnl": summ.short_net_pnl,
        # Segment Splits
        "in_sample_trades": is_res.summary.total_trades,
        "in_sample_net_pnl": is_res.summary.total_net_pnl,
        "in_sample_profit_factor": is_res.summary.profit_factor,
        "validation_trades": val_res.summary.total_trades,
        "validation_net_pnl": val_res.summary.total_net_pnl,
        "validation_profit_factor": val_res.summary.profit_factor,
        "out_of_sample_trades": oos_res.summary.total_trades,
        "out_of_sample_net_pnl": oos_res.summary.total_net_pnl,
        "out_of_sample_profit_factor": oos_res.summary.profit_factor,
    }


def run_all_comparisons() -> pd.DataFrame:
    """
    Executes comparative analysis over:
    - 3 Timeframes: 5m, 15m, 1h
    - 4 Regime Filters: NONE, ADX_ONLY, ATR_ONLY, BOTH
    - 2 Execution Types: MARKET, POST_ONLY_LIMIT
    Total = 24 configurations + specific variations.
    """
    print("=" * 80)
    print("PHASE 2: COMPREHENSIVE STRATEGY OPTIMIZATION & RISK HARDENING AUDIT")
    print("=" * 80)

    datasets = ensure_candles(symbol="BTCUSDT", days=30)
    splits_by_tf = {tf: split_dataset(df) for tf, df in datasets.items()}

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

    all_results: List[Dict[str, Any]] = []
    total_configs = len(timeframes) * len(regime_filters) * len(execution_types)
    cfg_idx = 0

    for tf in timeframes:
        for rf in regime_filters:
            for ex in execution_types:
                cfg_idx += 1
                config_name = f"[{cfg_idx:02d}/{total_configs}] TF={tf} | Filter={rf.value} | Exec={ex.value}"
                print(f"Running {config_name}...")

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
                    live_trading_authorized=False
                )

                res_dict = run_single_config(cfg, splits_by_tf[tf])
                all_results.append(res_dict)

    df_report = pd.DataFrame(all_results)
    output_csv = "phase2_comparison_report.csv"
    df_report.to_csv(output_csv, index=False)
    print(f"\n[OK] Comparative performance report saved to {output_csv}")
    return df_report


if __name__ == "__main__":
    df_results = run_all_comparisons()
    # Print high-level preview
    cols_to_print = [
        "timeframe", "regime_filter", "execution_type",
        "total_trades", "win_rate_pct", "gross_pnl", "total_fees_and_funding",
        "net_pnl", "profit_factor", "max_drawdown_pct", "out_of_sample_net_pnl"
    ]
    print("\n" + "=" * 110)
    print("PHASE 2 SUMMARY MATRIX (30-Day Comparative Backtest)")
    print("=" * 110)
    print(df_results[cols_to_print].to_string(index=False))
