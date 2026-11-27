"""
Phase 4: Market Regime Detection and Strategy Robustness Research Suite.
Platform: Delta Exchange | Instrument: BTCUSDT Perpetual | Timeframe: 1-Hour

Executes comprehensive 12-Month (8,760 bars) and 24-Month (17,520 bars) validation:
1. Baseline Configuration:
   - BTCUSDT perpetual
   - 1-hour timeframe
   - SuperTrend: (10, 3)
   - EMA 9: Method C
   - 10% Margin Allocation
   - 10x Leverage
   - Market execution
   - Active circuit breaker emergency closure (close_on_circuit_breaker=True)
2. Independent Regime Filter Evaluation:
   - Baseline: NONE (Unconstrained)
   - Filter A: BOTH (ADX 14 > 20 + ATR% > 0.15%)
   - Filter B: ADX_RISING (ADX > 20 and rising over completed candles)
   - Filter C: HTF_TREND (4-Hour EMA 50 > EMA 200 for Long, < EMA 200 for Short)
   - Filter D: CHOP_FILTER (Choppiness Index < 61.8)
   - Filter E: ST_SEPARATION (|Close - ST| / ATR >= 0.50)
3. Emergency Circuit Breaker Audit:
   - Evaluates baseline with close_on_circuit_breaker=False vs True to confirm daily loss containment.
4. Liquidation Model:
   - Delta Exchange Tier 1-4 MMR schedule + 0.5% fee penalty buffer + intrabar adverse movement check.
5. Non-overlapping chronological partitions:
   - In-Sample (60%)
   - Validation (20%)
   - Out-of-Sample (20%)
6. Produces All Phase 4 Deliverables:
   - phase4_comparison_report.csv
   - phase4_partition_reconciliation.csv
   - phase4_trade_by_trade_audit.csv
   - phase4_monthly_performance.csv
   - phase4_regime_robustness.csv
   - equity_curves_phase4/ (<config_id>_equity_curve.csv)
"""

import os
import sys
import time
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple
import pandas as pd
import numpy as np

from delta_trading_agent.config.settings import (
    TradingConfig, EMAMethod, RegimeFilterType, ExecutionType, PositionSide
)
from delta_trading_agent.backtest.engine import BacktestEngine, BacktestResult
from delta_trading_agent.backtest.performance import calculate_performance, PerformanceSummary
from delta_trading_agent.execution.strategy_executor import TradeRecord

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
DATA_12M = os.path.join(DATA_DIR, "candles_BTCUSDT_1h_365d.csv")
DATA_24M = os.path.join(DATA_DIR, "candles_BTCUSDT_1h_730d.csv")
EQUITY_DIR = os.path.join(os.path.dirname(__file__), "equity_curves_phase4")


def load_candles(filepath: str) -> pd.DataFrame:
    df = pd.read_csv(filepath)
    df["timestamp"] = df["timestamp"].astype(int)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df.sort_values(by="timestamp", inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df


def get_partition_boundaries(df: pd.DataFrame) -> Dict[str, Any]:
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
        "oos_candles": n - i_val_end,
        "dt_start": datetime.fromtimestamp(ts_start, timezone.utc).strftime("%Y-%m-%d %H:%M"),
        "dt_is_end": datetime.fromtimestamp(ts_is_end, timezone.utc).strftime("%Y-%m-%d %H:%M"),
        "dt_val_end": datetime.fromtimestamp(ts_val_end, timezone.utc).strftime("%Y-%m-%d %H:%M"),
        "dt_end": datetime.fromtimestamp(ts_end, timezone.utc).strftime("%Y-%m-%d %H:%M"),
    }


def calculate_max_daily_loss(equity_curve: List[Dict[str, Any]]) -> float:
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
    ts_is_end = boundaries["ts_is_end"]
    ts_val_end = boundaries["ts_val_end"]

    is_trades = [t for t in trades if t.exit_timestamp < ts_is_end]
    val_trades = [t for t in trades if ts_is_end <= t.exit_timestamp < ts_val_end]
    oos_trades = [t for t in trades if t.exit_timestamp >= ts_val_end]

    is_eq_records = [e for e in equity_curve if e["timestamp"] < ts_is_end]
    val_eq_records = [e for e in equity_curve if ts_is_end <= e["timestamp"] < ts_val_end]
    oos_eq_records = [e for e in equity_curve if e["timestamp"] >= ts_val_end]

    is_eq_vals = [e["total_equity"] for e in is_eq_records] or [initial_capital]
    is_ending_cap = is_eq_vals[-1] if is_eq_vals else initial_capital

    val_eq_vals = [e["total_equity"] for e in val_eq_records] or [is_ending_cap]
    val_ending_cap = val_eq_vals[-1] if val_eq_vals else is_ending_cap

    oos_eq_vals = [e["total_equity"] for e in oos_eq_records] or [val_ending_cap]

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


def calculate_monthly_breakdown(
    trades: List[TradeRecord],
    equity_curve: List[Dict[str, Any]],
    initial_capital: float,
    config_id: str
) -> List[Dict[str, Any]]:
    if not equity_curve:
        return []

    df_eq = pd.DataFrame(equity_curve)
    df_eq["dt"] = pd.to_datetime(df_eq["timestamp"], unit="s", utc=True)
    df_eq["month"] = df_eq["dt"].dt.strftime("%Y-%m")

    months = sorted(df_eq["month"].unique())
    monthly_rows = []

    for m in months:
        m_eq = df_eq[df_eq["month"] == m]
        m_start_cap = float(m_eq["total_equity"].iloc[0])
        m_end_cap = float(m_eq["total_equity"].iloc[-1])
        m_peak = float(m_eq["total_equity"].cummax().max())
        m_min = float(m_eq["total_equity"].min())
        m_dd_pct = round(((m_peak - m_min) / m_peak * 100.0) if m_peak > 0 else 0.0, 2)

        m_trades = [
            t for t in trades
            if datetime.fromtimestamp(t.exit_timestamp, timezone.utc).strftime("%Y-%m") == m
        ]
        n_trades = len(m_trades)
        wins = [t for t in m_trades if t.net_pnl > 0]
        losses = [t for t in m_trades if t.net_pnl < 0]
        win_rate = round(len(wins) / n_trades * 100.0, 2) if n_trades > 0 else 0.0

        gross_pnl = round(sum(t.gross_pnl for t in m_trades), 2)
        entry_fees = sum(t.entry_fee for t in m_trades)
        exit_fees = sum(t.exit_fee for t in m_trades)
        funding = sum(t.funding_fee for t in m_trades)
        fees = round(entry_fees + exit_fees + funding, 2)
        net_pnl = round(gross_pnl - fees, 2)

        profit = sum(t.net_pnl for t in wins)
        loss = abs(sum(t.net_pnl for t in losses))
        pf = round(profit / loss, 2) if loss > 0 else (99.99 if profit > 0 else 0.0)
        ret_pct = round((net_pnl / m_start_cap * 100.0) if m_start_cap > 0 else 0.0, 2)

        monthly_rows.append({
            "config_id": config_id,
            "month": m,
            "trades": n_trades,
            "wins": len(wins),
            "losses": len(losses),
            "win_rate_pct": win_rate,
            "gross_pnl": gross_pnl,
            "fees_and_funding": fees,
            "net_pnl": net_pnl,
            "return_pct": ret_pct,
            "profit_factor": pf,
            "max_drawdown_pct": m_dd_pct,
            "starting_capital": round(m_start_cap, 2),
            "ending_capital": round(m_end_cap, 2)
        })

    return monthly_rows


def classify_market_regimes(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    rolling_30d = 720  # 30 days * 24 hours
    df["return_30d"] = (df["close"] - df["close"].shift(rolling_30d)) / df["close"].shift(rolling_30d) * 100.0

    high = df["high"]
    low = df["low"]
    close = df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()
    df["atr_pct"] = (atr / close) * 100.0
    median_vol = df["atr_pct"].median()

    conditions = [
        df["return_30d"] > 10.0,
        df["return_30d"] < -10.0,
        df["return_30d"].between(-10.0, 10.0)
    ]
    choices = ["Strong Uptrend", "Strong Downtrend", "Sideways"]
    df["trend_regime"] = np.select(conditions, choices, default="Warming Up")
    df["vol_regime"] = np.where(df["atr_pct"] > median_vol, "High Volatility", "Low Volatility")
    return df


def calculate_regime_robustness(
    trades: List[TradeRecord],
    df_classified: pd.DataFrame,
    config_id: str
) -> List[Dict[str, Any]]:
    ts_to_trend = dict(zip(df_classified["timestamp"], df_classified["trend_regime"]))
    ts_to_vol = dict(zip(df_classified["timestamp"], df_classified["vol_regime"]))

    regime_trades: Dict[str, List[TradeRecord]] = {
        "Strong Uptrend (> +10%)": [],
        "Strong Downtrend (< -10%)": [],
        "Sideways (-10% to +10%)": [],
        "High Volatility (> median ATR%)": [],
        "Low Volatility (<= median ATR%)": []
    }

    for t in trades:
        t_regime = ts_to_trend.get(t.entry_timestamp, "Unknown")
        v_regime = ts_to_vol.get(t.entry_timestamp, "Unknown")

        if t_regime == "Strong Uptrend":
            regime_trades["Strong Uptrend (> +10%)"].append(t)
        elif t_regime == "Strong Downtrend":
            regime_trades["Strong Downtrend (< -10%)"].append(t)
        elif t_regime == "Sideways":
            regime_trades["Sideways (-10% to +10%)"].append(t)

        if v_regime == "High Volatility":
            regime_trades["High Volatility (> median ATR%)"].append(t)
        elif v_regime == "Low Volatility":
            regime_trades["Low Volatility (<= median ATR%)"].append(t)

    rows = []
    for regime_name, t_list in regime_trades.items():
        n = len(t_list)
        wins = [t for t in t_list if t.net_pnl > 0]
        losses = [t for t in t_list if t.net_pnl < 0]
        win_rate = round(len(wins) / n * 100.0, 2) if n > 0 else 0.0

        gross_pnl = round(sum(t.gross_pnl for t in t_list), 2)
        entry_fees = sum(t.entry_fee for t in t_list)
        exit_fees = sum(t.exit_fee for t in t_list)
        funding = sum(t.funding_fee for t in t_list)
        fees = round(entry_fees + exit_fees + funding, 2)
        net_pnl = round(gross_pnl - fees, 2)

        profit = sum(t.net_pnl for t in wins)
        loss = abs(sum(t.net_pnl for t in losses))
        pf = round(profit / loss, 2) if loss > 0 else (99.99 if profit > 0 else 0.0)

        rows.append({
            "config_id": config_id,
            "market_regime": regime_name,
            "trades": n,
            "wins": len(wins),
            "losses": len(losses),
            "win_rate_pct": win_rate,
            "gross_pnl": gross_pnl,
            "fees_and_funding": fees,
            "net_pnl": net_pnl,
            "profit_factor": pf
        })

    return rows


def build_comparison_row(
    config_id: str,
    dataset_period: str,
    filter_label: str,
    filter_type: RegimeFilterType,
    margin_alloc: float,
    close_on_cb: bool,
    result: BacktestResult,
    partition_res: Dict[str, Any],
    max_daily_loss: float
) -> Dict[str, Any]:
    s = result.summary
    is_s = partition_res["is_summary"]
    val_s = partition_res["val_summary"]
    oos_s = partition_res["oos_summary"]

    return {
        "config_id": config_id,
        "dataset_period": dataset_period,
        "filter_name": filter_label,
        "filter_type": filter_type.value,
        "margin_allocation_pct": round(margin_alloc * 100.0, 1),
        "leverage": result.config.leverage,
        "close_on_circuit_breaker": close_on_cb,
        "total_trades": s.total_trades,
        "win_rate_pct": s.win_rate_pct,
        "gross_pnl": s.total_gross_pnl,
        "fees_and_funding": s.total_fees_paid,
        "net_pnl": s.total_net_pnl,
        "net_return_pct": s.total_return_pct,
        "profit_factor": s.profit_factor,
        "max_drawdown_amount": s.max_drawdown_amount,
        "max_drawdown_pct": s.max_drawdown_pct,
        "max_daily_loss": max_daily_loss,
        "liquidation_events": result.liquidation_events,
        "near_liquidation_events": result.near_liquidation_events,
        "avg_trade_pnl": s.avg_trade_pnl,
        "long_trades": s.long_trades,
        "long_win_rate_pct": s.long_win_rate_pct,
        "long_net_pnl": s.long_net_pnl,
        "short_trades": s.short_trades,
        "short_win_rate_pct": s.short_win_rate_pct,
        "short_net_pnl": s.short_net_pnl,
        "in_sample_net_pnl": is_s.total_net_pnl,
        "validation_net_pnl": val_s.total_net_pnl,
        "out_of_sample_net_pnl": oos_s.total_net_pnl
    }


def build_reconciliation_rows(
    config_id: str,
    result: BacktestResult,
    partition_res: Dict[str, Any],
    boundaries: Dict[str, Any]
) -> List[Dict[str, Any]]:
    rows = []
    partitions = [
        ("OVERALL", boundaries["total_candles"], result.summary, result.trades),
        ("IN_SAMPLE", boundaries["is_candles"], partition_res["is_summary"], partition_res["is_trades"]),
        ("VALIDATION", boundaries["val_candles"], partition_res["val_summary"], partition_res["val_trades"]),
        ("OUT_OF_SAMPLE", boundaries["oos_candles"], partition_res["oos_summary"], partition_res["oos_trades"]),
    ]

    for part_name, candle_count, s, trade_list in partitions:
        reconciles_pnl = abs(s.total_gross_pnl - s.total_fees_paid - s.total_net_pnl) < 0.05
        rows.append({
            "config_id": config_id,
            "partition": part_name,
            "candles": candle_count,
            "trades": s.total_trades,
            "wins": s.winning_trades,
            "losses": s.losing_trades,
            "win_rate_pct": s.win_rate_pct,
            "gross_pnl": s.total_gross_pnl,
            "fees_and_funding": s.total_fees_paid,
            "net_pnl": s.total_net_pnl,
            "net_return_pct": s.total_return_pct,
            "profit_factor": s.profit_factor,
            "max_drawdown_pct": s.max_drawdown_pct,
            "reconciles_trades": True,
            "reconciles_pnl": reconciles_pnl
        })

    # Assert trade count and PnL partition equality
    tot_trades = result.summary.total_trades
    sum_part_trades = (
        partition_res["is_summary"].total_trades +
        partition_res["val_summary"].total_trades +
        partition_res["oos_summary"].total_trades
    )
    assert tot_trades == sum_part_trades, (
        f"Trade partition mismatch in {config_id}: {tot_trades} overall != {sum_part_trades} partitions"
    )

    return rows


def build_trade_rows(config_id: str, trades: List[TradeRecord]) -> List[Dict[str, Any]]:
    rows = []
    for t in trades:
        en_dt = datetime.fromtimestamp(t.entry_timestamp, timezone.utc).strftime("%Y-%m-%d %H:%M")
        ex_dt = datetime.fromtimestamp(t.exit_timestamp, timezone.utc).strftime("%Y-%m-%d %H:%M")
        dur_h = round((t.exit_timestamp - t.entry_timestamp) / 3600.0, 2)
        tot_fee = round(t.entry_fee + t.exit_fee + t.funding_fee, 4)

        rows.append({
            "trade_id": t.trade_id,
            "config_id": config_id,
            "symbol": t.symbol,
            "side": t.side.value,
            "entry_timestamp": t.entry_timestamp,
            "exit_timestamp": t.exit_timestamp,
            "entry_datetime": en_dt,
            "exit_datetime": ex_dt,
            "duration_hours": dur_h,
            "entry_price": t.entry_price,
            "exit_price": t.exit_price,
            "contracts": t.contracts,
            "btc_size": t.btc_size,
            "margin_used": t.margin_used,
            "leverage": t.leverage,
            "gross_pnl": t.gross_pnl,
            "entry_fee": t.entry_fee,
            "exit_fee": t.exit_fee,
            "funding_fee": t.funding_fee,
            "total_fees": tot_fee,
            "net_pnl": t.net_pnl,
            "return_pct": t.return_pct,
            "exit_reason": t.exit_reason,
            "regime_filter": t.regime_filter,
            "execution_type": t.execution_type,
            "fill_type": t.fill_type,
            "max_adverse_excursion_usdt": t.max_adverse_excursion_usdt,
            "max_adverse_excursion_pct": t.max_adverse_excursion_pct,
            "liquidation_price": t.liquidation_price,
            "liquidation_distance_pct": t.liquidation_distance_pct,
            "notional_exposure": t.notional_exposure
        })
    return rows


def save_equity_curve_csv(config_id: str, equity_curve: List[Dict[str, Any]]):
    os.makedirs(EQUITY_DIR, exist_ok=True)
    out_path = os.path.join(EQUITY_DIR, f"{config_id}_equity_curve.csv")

    df_eq = pd.DataFrame(equity_curve)
    if df_eq.empty:
        return

    df_eq["datetime"] = pd.to_datetime(df_eq["timestamp"], unit="s", utc=True).dt.strftime("%Y-%m-%d %H:%M")
    cum_max = df_eq["total_equity"].cummax()
    df_eq["drawdown_pct"] = np.where(cum_max > 0, (cum_max - df_eq["total_equity"]) / cum_max * 100.0, 0.0)
    df_eq["drawdown_pct"] = df_eq["drawdown_pct"].round(2)

    cols = [
        "timestamp", "datetime", "price", "capital", "unrealized_pnl", "total_equity",
        "drawdown_pct", "position", "supertrend_trend", "ema", "adx", "atr_pct",
        "chop", "htf_trend", "signal_action", "regime_passed"
    ]
    present_cols = [c for c in cols if c in df_eq.columns]
    df_eq[present_cols].to_csv(out_path, index=False)


def main():
    print("=" * 80)
    print("PHASE 4: MARKET REGIME DETECTION & STRATEGY ROBUSTNESS RESEARCH")
    print("Platform: Delta Exchange | BTCUSDT Perpetual | 1-Hour Timeframe")
    print("=" * 80)

    # 1. Load historical datasets
    print(f"\nLoading 12-Month candles from {DATA_12M}...")
    df_12m = load_candles(DATA_12M)
    bounds_12m = get_partition_boundaries(df_12m)
    print(f"Loaded 12M: {len(df_12m)} bars ({bounds_12m['dt_start']} to {bounds_12m['dt_end']})")

    print(f"\nLoading 24-Month candles from {DATA_24M}...")
    df_24m = load_candles(DATA_24M)
    bounds_24m = get_partition_boundaries(df_24m)
    print(f"Loaded 24M: {len(df_24m)} bars ({bounds_24m['dt_start']} to {bounds_24m['dt_end']})")

    df_12m_classified = classify_market_regimes(df_12m)
    df_24m_classified = classify_market_regimes(df_24m)

    # 2. Define Filter Matrix for Research
    # Filters to evaluate independently:
    # A: NONE (Baseline)
    # B: Filter A: BOTH (Existing ADX 14 > 20 + ATR% > 0.15%)
    # C: Filter B: ADX_RISING (ADX > 20 and rising over completed candles)
    # D: Filter C: HTF_TREND (4h EMA 50 > EMA 200 for Long, < EMA 200 for Short)
    # E: Filter D: CHOP_FILTER (Choppiness Index < 61.8)
    # F: Filter E: ST_SEPARATION (|Close - ST| / ATR >= 0.50)
    filter_definitions = [
        ("Baseline_NONE", "Baseline (No filter)", RegimeFilterType.NONE),
        ("FilterA_BOTH", "Filter A: ADX + ATR", RegimeFilterType.BOTH),
        ("FilterB_ADX_RISING", "Filter B: ADX Rising", RegimeFilterType.ADX_RISING),
        ("FilterC_HTF_TREND", "Filter C: 4h HTF Trend", RegimeFilterType.HTF_TREND),
        ("FilterD_CHOP_FILTER", "Filter D: Choppiness Index", RegimeFilterType.CHOP_FILTER),
        ("FilterE_ST_SEPARATION", "Filter E: ST Separation", RegimeFilterType.ST_SEPARATION),
    ]

    all_comparison_rows: List[Dict[str, Any]] = []
    all_reconciliation_rows: List[Dict[str, Any]] = []
    all_trade_rows: List[Dict[str, Any]] = []
    all_monthly_rows: List[Dict[str, Any]] = []
    all_robustness_rows: List[Dict[str, Any]] = []

    # ── Test Suite 1: 12-Month Historical Validation (365 Days / 8,760 Bars) ──
    print("\n" + "#" * 80)
    print("SUITE 1: 12-MONTH HISTORICAL VALIDATION (8,760 BARS)")
    print("#" * 80)

    for filter_id, filter_label, f_type in filter_definitions:
        config_id = f"1h_12m_{filter_id}_margin_10pct"
        print(f"\nExecuting: {config_id} [{filter_label}] on 12-Month dataset...")

        cfg = TradingConfig(
            symbol="BTCUSDT",
            resolution="60",
            timeframe_seconds=3600,
            supertrend_length=10,
            supertrend_factor=3.0,
            ema_period=9,
            ema_method=EMAMethod.METHOD_C,
            regime_filter=f_type,
            adx_period=14,
            adx_threshold=20.0,
            atr_filter_period=14,
            atr_min_pct=0.15,
            chop_period=14,
            chop_threshold=61.8,
            st_separation_min_atr=0.50,
            htf_ema_fast=50,
            htf_ema_slow=200,
            execution_type=ExecutionType.MARKET,
            leverage=10.0,
            margin_allocation_pct=0.10,
            unallocated_cash_pct=0.90,
            initial_capital=10000.0,
            max_daily_loss_pct=0.05,
            max_total_drawdown_pct=0.50,
            close_on_circuit_breaker=True,
            liquidation_fee_rate=0.005,
            live_trading_authorized=False
        )

        engine = BacktestEngine(cfg)
        res = engine.run(df_12m)
        part_res = partition_trades_and_equity(res.trades, res.equity_curve, bounds_12m, cfg.initial_capital)
        max_daily_loss = calculate_max_daily_loss(res.equity_curve)

        # Audit financial identity: Gross - Fees = Net
        s = res.summary
        assert abs(s.total_gross_pnl - s.total_fees_paid - s.total_net_pnl) < 0.05, (
            f"Financial reconciliation failed in {config_id}: {s.total_gross_pnl} - {s.total_fees_paid} != {s.total_net_pnl}"
        )

        print(f"  -> Trades: {s.total_trades} | Win Rate: {s.win_rate_pct:.1f}% | Net PnL: ${s.total_net_pnl:.2f} ({s.total_return_pct:.2f}%)")
        print(f"  -> Max Drawdown: {s.max_drawdown_pct:.2f}% | Max Daily Loss: ${max_daily_loss:.2f} | Liquidations: {res.liquidation_events}")
        print(f"  -> Partitions: IS Net=${part_res['is_summary'].total_net_pnl:.2f} | Val Net=${part_res['val_summary'].total_net_pnl:.2f} | OOS Net=${part_res['oos_summary'].total_net_pnl:.2f}")

        comp_row = build_comparison_row(
            config_id, "12-Month", filter_label, f_type, 0.10, True, res, part_res, max_daily_loss
        )
        all_comparison_rows.append(comp_row)

        recon_rows = build_reconciliation_rows(config_id, res, part_res, bounds_12m)
        all_reconciliation_rows.extend(recon_rows)

        trade_rows = build_trade_rows(config_id, res.trades)
        all_trade_rows.extend(trade_rows)

        monthly_rows = calculate_monthly_breakdown(res.trades, res.equity_curve, cfg.initial_capital, config_id)
        all_monthly_rows.extend(monthly_rows)

        robust_rows = calculate_regime_robustness(res.trades, df_12m_classified, config_id)
        all_robustness_rows.extend(robust_rows)

        save_equity_curve_csv(config_id, res.equity_curve)

    # ── Test Suite 2: 24-Month Extended Historical Validation (17,520 Bars) ──
    print("\n" + "#" * 80)
    print("SUITE 2: 24-MONTH EXTENDED HISTORICAL VALIDATION (17,520 BARS)")
    print("#" * 80)

    for filter_id, filter_label, f_type in filter_definitions:
        config_id = f"1h_24m_{filter_id}_margin_10pct"
        print(f"\nExecuting: {config_id} [{filter_label}] on 24-Month dataset...")

        cfg = TradingConfig(
            symbol="BTCUSDT",
            resolution="60",
            timeframe_seconds=3600,
            supertrend_length=10,
            supertrend_factor=3.0,
            ema_period=9,
            ema_method=EMAMethod.METHOD_C,
            regime_filter=f_type,
            adx_period=14,
            adx_threshold=20.0,
            atr_filter_period=14,
            atr_min_pct=0.15,
            chop_period=14,
            chop_threshold=61.8,
            st_separation_min_atr=0.50,
            htf_ema_fast=50,
            htf_ema_slow=200,
            execution_type=ExecutionType.MARKET,
            leverage=10.0,
            margin_allocation_pct=0.10,
            unallocated_cash_pct=0.90,
            initial_capital=10000.0,
            max_daily_loss_pct=0.05,
            max_total_drawdown_pct=0.50,
            close_on_circuit_breaker=True,
            liquidation_fee_rate=0.005,
            live_trading_authorized=False
        )

        engine = BacktestEngine(cfg)
        res = engine.run(df_24m)
        part_res = partition_trades_and_equity(res.trades, res.equity_curve, bounds_24m, cfg.initial_capital)
        max_daily_loss = calculate_max_daily_loss(res.equity_curve)

        s = res.summary
        assert abs(s.total_gross_pnl - s.total_fees_paid - s.total_net_pnl) < 0.05, (
            f"Financial reconciliation failed in {config_id}: {s.total_gross_pnl} - {s.total_fees_paid} != {s.total_net_pnl}"
        )

        print(f"  -> Trades: {s.total_trades} | Win Rate: {s.win_rate_pct:.1f}% | Net PnL: ${s.total_net_pnl:.2f} ({s.total_return_pct:.2f}%)")
        print(f"  -> Max Drawdown: {s.max_drawdown_pct:.2f}% | Max Daily Loss: ${max_daily_loss:.2f} | Liquidations: {res.liquidation_events}")
        print(f"  -> Partitions: IS Net=${part_res['is_summary'].total_net_pnl:.2f} | Val Net=${part_res['val_summary'].total_net_pnl:.2f} | OOS Net=${part_res['oos_summary'].total_net_pnl:.2f}")

        comp_row = build_comparison_row(
            config_id, "24-Month", filter_label, f_type, 0.10, True, res, part_res, max_daily_loss
        )
        all_comparison_rows.append(comp_row)

        recon_rows = build_reconciliation_rows(config_id, res, part_res, bounds_24m)
        all_reconciliation_rows.extend(recon_rows)

        trade_rows = build_trade_rows(config_id, res.trades)
        all_trade_rows.extend(trade_rows)

        monthly_rows = calculate_monthly_breakdown(res.trades, res.equity_curve, cfg.initial_capital, config_id)
        all_monthly_rows.extend(monthly_rows)

        robust_rows = calculate_regime_robustness(res.trades, df_24m_classified, config_id)
        all_robustness_rows.extend(robust_rows)

        save_equity_curve_csv(config_id, res.equity_curve)

    # ── Test Suite 3: Circuit Breaker Emergency Closure Comparison ──
    # Compare Baseline Filter A (ADX+ATR) with close_on_circuit_breaker=False vs True
    print("\n" + "#" * 80)
    print("SUITE 3: CIRCUIT BREAKER EMERGENCY CLOSURE AUDIT")
    print("#" * 80)
    for cb_flag in [False, True]:
        cb_label = "Active_Emergency_Close" if cb_flag else "Legacy_Block_Only"
        config_id = f"1h_12m_FilterA_BOTH_CB_{cb_label}"
        print(f"\nExecuting: {config_id} (close_on_circuit_breaker={cb_flag})...")

        cfg = TradingConfig(
            symbol="BTCUSDT",
            resolution="60",
            timeframe_seconds=3600,
            supertrend_length=10,
            supertrend_factor=3.0,
            ema_period=9,
            ema_method=EMAMethod.METHOD_C,
            regime_filter=RegimeFilterType.BOTH,
            adx_period=14,
            adx_threshold=20.0,
            atr_filter_period=14,
            atr_min_pct=0.15,
            execution_type=ExecutionType.MARKET,
            leverage=10.0,
            margin_allocation_pct=0.10,
            unallocated_cash_pct=0.90,
            initial_capital=10000.0,
            max_daily_loss_pct=0.05,
            max_total_drawdown_pct=0.50,
            close_on_circuit_breaker=cb_flag,
            liquidation_fee_rate=0.005,
            live_trading_authorized=False
        )

        engine = BacktestEngine(cfg)
        res = engine.run(df_12m)
        part_res = partition_trades_and_equity(res.trades, res.equity_curve, bounds_12m, cfg.initial_capital)
        max_daily_loss = calculate_max_daily_loss(res.equity_curve)

        s = res.summary
        print(f"  -> {cb_label}: Max Daily Loss: ${max_daily_loss:.2f} | Net PnL: ${s.total_net_pnl:.2f} | Max DD: {s.max_drawdown_pct:.2f}%")

        comp_row = build_comparison_row(
            config_id, "12-Month", f"ADX+ATR ({cb_label})", RegimeFilterType.BOTH, 0.10, cb_flag, res, part_res, max_daily_loss
        )
        all_comparison_rows.append(comp_row)

        recon_rows = build_reconciliation_rows(config_id, res, part_res, bounds_12m)
        all_reconciliation_rows.extend(recon_rows)

        trade_rows = build_trade_rows(config_id, res.trades)
        all_trade_rows.extend(trade_rows)

        monthly_rows = calculate_monthly_breakdown(res.trades, res.equity_curve, cfg.initial_capital, config_id)
        all_monthly_rows.extend(monthly_rows)

        robust_rows = calculate_regime_robustness(res.trades, df_12m_classified, config_id)
        all_robustness_rows.extend(robust_rows)

        save_equity_curve_csv(config_id, res.equity_curve)

    # ── 3. Export All Deliverables ──
    print("\n" + "=" * 80)
    print("SAVING DELIVERABLES")
    print("=" * 80)

    # Deliverable 1: phase4_comparison_report.csv
    df_comp = pd.DataFrame(all_comparison_rows)
    df_comp.to_csv("phase4_comparison_report.csv", index=False)
    print(f"Saved: phase4_comparison_report.csv ({len(df_comp)} rows)")

    # Deliverable 2: phase4_partition_reconciliation.csv
    df_recon = pd.DataFrame(all_reconciliation_rows)
    df_recon.to_csv("phase4_partition_reconciliation.csv", index=False)
    print(f"Saved: phase4_partition_reconciliation.csv ({len(df_recon)} rows)")

    # Deliverable 3: phase4_trade_by_trade_audit.csv
    df_trades = pd.DataFrame(all_trade_rows)
    df_trades.to_csv("phase4_trade_by_trade_audit.csv", index=False)
    print(f"Saved: phase4_trade_by_trade_audit.csv ({len(df_trades)} rows)")

    # Deliverable 4: phase4_monthly_performance.csv
    df_monthly = pd.DataFrame(all_monthly_rows)
    df_monthly.to_csv("phase4_monthly_performance.csv", index=False)
    print(f"Saved: phase4_monthly_performance.csv ({len(df_monthly)} rows)")

    # Deliverable 5: phase4_regime_robustness.csv
    df_robust = pd.DataFrame(all_robustness_rows)
    df_robust.to_csv("phase4_regime_robustness.csv", index=False)
    print(f"Saved: phase4_regime_robustness.csv ({len(df_robust)} rows)")

    print(f"Saved: equity_curves_phase4/ ({len(all_comparison_rows)} curve files)")

    print("\nPhase 4 Research Backtest Pipeline Complete!")


if __name__ == "__main__":
    main()
