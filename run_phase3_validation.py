"""
Phase 3: Extended Historical Validation & Risk Optimization Analyzer.
Platform: Delta Exchange | Instrument: BTCUSDT Perpetual | Timeframe: 1-Hour

Executes 12-Month Historical Validation across 8,760 candles:
1. Candidate: BTCUSDT_1H_ADX_ATR_RESEARCH
2. Filter Comparison:
   - A: NONE (Baseline)
   - B: ADX_ONLY (ADX > 20)
   - C: ATR_ONLY (ATR% > 0.15%)
   - D: BOTH (ADX > 20 and ATR% > 0.15%)
3. Risk Optimization (Margin Allocations):
   - 10%, 20%, 30%, 40%, and 70% (Original Baseline)
   - Initial leverage: 10x
4. Comprehensive Risk Metrics:
   - Maximum Equity Drawdown (% and USDT)
   - Maximum Daily Loss (USDT)
   - Maximum Adverse Excursion (MAE) (USDT and %)
   - Liquidation Distance (% and USDT)
   - Exposure (Long/Short Notional and Margin)
   - Risk per Trade (Average loss, Max loss)
   - Consecutive Losses (Max losing streak)
5. Additional Robustness Tests across Market Regimes:
   - Strong Uptrend (> +10% 30d return)
   - Strong Downtrend (< -10% 30d return)
   - Sideways Market (-10% to +10% 30d return)
   - High Volatility (> median ATR%)
   - Low Volatility (<= median ATR%)
6. Non-Overlapping Chronological Partitions:
   - In-Sample (60%): Days 1 to 219 (5,256 candles)
   - Validation (20%): Days 220 to 292 (1,752 candles)
   - Out-of-Sample (20%): Days 293 to 365 (1,752 candles)
7. Deliverables Generated:
   - phase3_comparison_report.csv
   - phase3_partition_reconciliation.csv
   - phase3_trade_by_trade_audit.csv
   - phase3_monthly_performance.csv
   - phase3_regime_robustness.csv
   - equity_curves_phase3/ (20 CSV files with full equity and drawdown curves)
"""

import os
import sys
import time
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple
import pandas as pd
import numpy as np

from delta_trading_agent.config.settings import (
    TradingConfig, EMAMethod, RegimeFilterType, ExecutionType, PositionSide,
    BTCUSDT_1H_ADX_ATR_RESEARCH
)
from delta_trading_agent.backtest.engine import BacktestEngine, BacktestResult
from delta_trading_agent.backtest.performance import calculate_performance, PerformanceSummary
from delta_trading_agent.execution.strategy_executor import TradeRecord


DATA_FILE = os.path.join(os.path.dirname(__file__), "data", "candles_BTCUSDT_1h_365d.csv")
EQUITY_DIR = os.path.join(os.path.dirname(__file__), "equity_curves_phase3")


def load_12m_candles() -> pd.DataFrame:
    """Loads 12 months (8,760 candles) of BTCUSDT 1h data."""
    if not os.path.exists(DATA_FILE):
        print(f"Candle data not found at {DATA_FILE}. Fetching from Delta Exchange...")
        df = BacktestEngine.fetch_historical_candles_from_delta(
            symbol="BTCUSDT",
            resolution="60",
            days=365
        )
        df.to_csv(DATA_FILE, index=False)
    else:
        df = pd.read_csv(DATA_FILE)
    df["timestamp"] = df["timestamp"].astype(int)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df.sort_values(by="timestamp", inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df


def get_partition_boundaries(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Chronological 60% / 20% / 20% splits:
    - In-Sample: 0 to 60%
    - Validation: 60% to 80%
    - Out-of-Sample: 80% to 100%
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
        "oos_candles": n - i_val_end,
        "dt_start": datetime.fromtimestamp(ts_start, timezone.utc).strftime("%Y-%m-%d %H:%M"),
        "dt_is_end": datetime.fromtimestamp(ts_is_end, timezone.utc).strftime("%Y-%m-%d %H:%M"),
        "dt_val_end": datetime.fromtimestamp(ts_val_end, timezone.utc).strftime("%Y-%m-%d %H:%M"),
        "dt_end": datetime.fromtimestamp(ts_end, timezone.utc).strftime("%Y-%m-%d %H:%M"),
    }


def calculate_max_daily_loss(equity_curve: List[Dict[str, Any]]) -> float:
    """Calculates the largest single UTC day loss in USDT."""
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
    Partitions trades and equity curves into In-Sample, Validation, and Out-of-Sample.
    Trades are attributed by exit_timestamp.
    """
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
    """Calculates month-by-month trade count, win rate, gross PnL, fees, net PnL, return, and drawdown."""
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

        # Trades closed in this month
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
    """
    Classifies candles into market regimes:
    1. Trend: 30-day (720 bars) rolling return
       - Strong Uptrend: > +10%
       - Strong Downtrend: < -10%
       - Sideways: -10% to +10%
    2. Volatility: ATR% (14-period)
       - High Volatility: > median ATR%
       - Low Volatility: <= median ATR%
    """
    df = df.copy()
    # 30-day rolling return (720 1-hour candles)
    df["rolling_ret_30d"] = (df["close"] - df["close"].shift(720)) / df["close"].shift(720) * 100.0
    # Fill early warm-up with available shift
    df["rolling_ret_30d"] = df["rolling_ret_30d"].fillna(
        (df["close"] - df["close"].iloc[0]) / df["close"].iloc[0] * 100.0
    )

    # 14-period ATR
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift(1)).abs()
    low_close = (df["low"] - df["close"].shift(1)).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    df["atr_pct"] = (atr / df["close"]) * 100.0

    median_atr_pct = float(df["atr_pct"].median())

    def get_trend_regime(ret: float) -> str:
        if ret > 10.0:
            return "STRONG_UPTREND"
        elif ret < -10.0:
            return "STRONG_DOWNTREND"
        else:
            return "SIDEWAYS"

    def get_vol_regime(atr_val: float) -> str:
        return "HIGH_VOLATILITY" if atr_val > median_atr_pct else "LOW_VOLATILITY"

    df["trend_regime"] = df["rolling_ret_30d"].apply(get_trend_regime)
    df["vol_regime"] = df["atr_pct"].apply(get_vol_regime)
    return df, median_atr_pct


def evaluate_regime_robustness(
    trades: List[TradeRecord],
    df_regimes: pd.DataFrame,
    config_id: str
) -> List[Dict[str, Any]]:
    """Evaluates strategy performance across Trend and Volatility market regimes."""
    ts_to_trend = dict(zip(df_regimes["timestamp"], df_regimes["trend_regime"]))
    ts_to_vol = dict(zip(df_regimes["timestamp"], df_regimes["vol_regime"]))

    # Tag each trade with entry regime
    regime_buckets: Dict[str, List[TradeRecord]] = {
        "STRONG_UPTREND": [],
        "STRONG_DOWNTREND": [],
        "SIDEWAYS": [],
        "HIGH_VOLATILITY": [],
        "LOW_VOLATILITY": []
    }

    for t in trades:
        t_trend = ts_to_trend.get(t.entry_timestamp, "SIDEWAYS")
        t_vol = ts_to_vol.get(t.entry_timestamp, "LOW_VOLATILITY")
        regime_buckets[t_trend].append(t)
        regime_buckets[t_vol].append(t)

    robustness_rows = []
    for regime_name, r_trades in regime_buckets.items():
        n = len(r_trades)
        wins = [t for t in r_trades if t.net_pnl > 0]
        losses = [t for t in r_trades if t.net_pnl < 0]
        win_rate = round(len(wins) / n * 100.0, 2) if n > 0 else 0.0

        gross_pnl = round(sum(t.gross_pnl for t in r_trades), 2)
        fees = round(sum(t.entry_fee + t.exit_fee + t.funding_fee for t in r_trades), 2)
        net_pnl = round(gross_pnl - fees, 2)

        profit = sum(t.net_pnl for t in wins)
        loss = abs(sum(t.net_pnl for t in losses))
        pf = round(profit / loss, 2) if loss > 0 else (99.99 if profit > 0 else 0.0)

        robustness_rows.append({
            "config_id": config_id,
            "regime": regime_name,
            "trades": n,
            "wins": len(wins),
            "losses": len(losses),
            "win_rate_pct": win_rate,
            "gross_pnl": gross_pnl,
            "total_fees": fees,
            "net_pnl": net_pnl,
            "profit_factor": pf,
            "is_profitable": net_pnl > 0
        })

    return robustness_rows


def calculate_risk_metrics(trades: List[TradeRecord], initial_capital: float) -> Dict[str, Any]:
    """Calculates extensive risk and exposure metrics across trades."""
    if not trades:
        return {
            "avg_mae_usdt": 0.0,
            "max_mae_usdt": 0.0,
            "avg_mae_pct": 0.0,
            "max_mae_pct": 0.0,
            "avg_liq_distance_pct": 0.0,
            "min_liq_distance_pct": 0.0,
            "avg_notional_exposure": 0.0,
            "max_notional_exposure": 0.0,
            "avg_margin_used": 0.0,
            "max_margin_used": 0.0,
            "max_consecutive_losses": 0,
            "avg_loss_usdt": 0.0,
            "max_trade_loss_usdt": 0.0,
            "max_trade_loss_pct": 0.0,
            "avg_win_usdt": 0.0,
            "max_trade_win_usdt": 0.0,
            "win_loss_payout_ratio": 0.0
        }

    mae_usdts = [getattr(t, "max_adverse_excursion_usdt", 0.0) for t in trades]
    mae_pcts = [getattr(t, "max_adverse_excursion_pct", 0.0) for t in trades]
    liq_dists = [getattr(t, "liquidation_distance_pct", 0.0) for t in trades]
    notionals = [getattr(t, "notional_exposure", 0.0) for t in trades]
    margins = [t.margin_used for t in trades]

    # Losses
    losses = [t.net_pnl for t in trades if t.net_pnl < 0]
    wins = [t.net_pnl for t in trades if t.net_pnl > 0]

    # Consecutive losses streak
    max_streak = 0
    cur_streak = 0
    for t in trades:
        if t.net_pnl < 0:
            cur_streak += 1
            if cur_streak > max_streak:
                max_streak = cur_streak
        else:
            cur_streak = 0

    avg_loss = round(float(np.mean(losses)), 2) if losses else 0.0
    max_loss = round(float(min(losses)), 2) if losses else 0.0
    avg_win = round(float(np.mean(wins)), 2) if wins else 0.0
    max_win = round(float(max(wins)), 2) if wins else 0.0
    payout_ratio = round(abs(avg_win / avg_loss), 2) if avg_loss != 0 else 0.0

    return {
        "avg_mae_usdt": round(float(np.mean(mae_usdts)), 2),
        "max_mae_usdt": round(float(max(mae_usdts)), 2),
        "avg_mae_pct": round(float(np.mean(mae_pcts)), 2),
        "max_mae_pct": round(float(max(mae_pcts)), 2),
        "avg_liq_distance_pct": round(float(np.mean(liq_dists)), 2),
        "min_liq_distance_pct": round(float(min(liq_dists)), 2),
        "avg_notional_exposure": round(float(np.mean(notionals)), 2),
        "max_notional_exposure": round(float(max(notionals)), 2),
        "avg_margin_used": round(float(np.mean(margins)), 2),
        "max_margin_used": round(float(max(margins)), 2),
        "max_consecutive_losses": max_streak,
        "avg_loss_usdt": avg_loss,
        "max_trade_loss_usdt": max_loss,
        "max_trade_loss_pct": round(abs(max_loss) / initial_capital * 100.0, 2),
        "avg_win_usdt": avg_win,
        "max_trade_win_usdt": max_win,
        "win_loss_payout_ratio": payout_ratio
    }


def run_phase3_validation():
    print("=" * 105)
    print("PHASE 3: EXTENDED HISTORICAL VALIDATION OF 1-HOUR BTCUSDT STRATEGY (12 MONTHS)")
    print("Evaluating: Filter Variations x Margin Allocations (10%, 20%, 30%, 40%, 70%)")
    print("Strict Accounting: Gross - Fees = Net PnL | Partitions: 60% IS, 20% Val, 20% OOS")
    print("=" * 105)

    df_candles = load_12m_candles()
    boundaries = get_partition_boundaries(df_candles)
    df_regimes, median_atr_pct = classify_market_regimes(df_candles)

    print(f"\n[Dataset Overview]")
    print(f"Total Candles: {boundaries['total_candles']} (365 days / 12 months)")
    print(f"Date Range: {boundaries['dt_start']} to {boundaries['dt_end']} UTC")
    print(f"In-Sample (60%):  {boundaries['is_candles']} candles ({boundaries['dt_start']} to {boundaries['dt_is_end']})")
    print(f"Validation (20%): {boundaries['val_candles']} candles ({boundaries['dt_is_end']} to {boundaries['dt_val_end']})")
    print(f"Out-of-Sample (20%): {boundaries['oos_candles']} candles ({boundaries['dt_val_end']} to {boundaries['dt_end']})")
    print(f"Median ATR% Threshold for Volatility: {median_atr_pct:.3f}%")

    # 4 Filters
    regime_filters = [
        ("NONE", RegimeFilterType.NONE),
        ("ADX_ONLY", RegimeFilterType.ADX_ONLY),
        ("ATR_ONLY", RegimeFilterType.ATR_ONLY),
        ("BOTH", RegimeFilterType.BOTH)
    ]

    # 5 Margin Allocations
    margin_allocations = [0.10, 0.20, 0.30, 0.40, 0.70]

    all_comparison_rows: List[Dict[str, Any]] = []
    all_reconciliation_rows: List[Dict[str, Any]] = []
    all_trades: List[Dict[str, Any]] = []
    all_monthly_rows: List[Dict[str, Any]] = []
    all_robustness_rows: List[Dict[str, Any]] = []

    total_runs = len(regime_filters) * len(margin_allocations)
    run_idx = 0

    os.makedirs(EQUITY_DIR, exist_ok=True)

    for rf_label, rf_enum in regime_filters:
        for margin_pct in margin_allocations:
            run_idx += 1
            cfg_id = f"1h_{rf_label}_margin_{int(margin_pct*100)}pct"
            print(f"\n[{run_idx:02d}/{total_runs}] Running {cfg_id} (Filter: {rf_label}, Margin: {int(margin_pct*100)}%, Lev: 10x)...")

            cfg = TradingConfig(
                symbol="BTCUSDT",
                resolution="1h",
                timeframe_seconds=3600,
                supertrend_length=10,
                supertrend_factor=3.0,
                ema_period=9,
                ema_method=EMAMethod.METHOD_C,
                regime_filter=rf_enum,
                adx_period=14,
                adx_threshold=20.0,
                atr_filter_period=14,
                atr_min_pct=0.15,
                execution_type=ExecutionType.MARKET,
                leverage=10.0,
                margin_allocation_pct=margin_pct,
                unallocated_cash_pct=round(1.0 - margin_pct, 2),
                initial_capital=10000.0,
                live_trading_authorized=False,
                max_total_drawdown_pct=0.60  # Allow deep research run over 1 year
            )

            engine = BacktestEngine(cfg)
            res = engine.run(df_candles)
            summ = res.summary

            # Partition trades and equity
            parts = partition_trades_and_equity(
                trades=res.trades,
                equity_curve=res.equity_curve,
                boundaries=boundaries,
                initial_capital=cfg.initial_capital
            )

            is_summ = parts["is_summary"]
            val_summ = parts["val_summary"]
            oos_summ = parts["oos_summary"]

            # ── AUTOMATED RECONCILIATION ASSERTIONS ──────────────────────
            # 1. Trade count
            assert summ.total_trades == (is_summ.total_trades + val_summ.total_trades + oos_summ.total_trades), (
                f"[{cfg_id}] Trade count mismatch: Overall {summ.total_trades} != "
                f"IS {is_summ.total_trades} + Val {val_summ.total_trades} + OOS {oos_summ.total_trades}"
            )

            # 2. Accounting: Gross - Fees == Net
            gross_minus_fees = round(summ.total_gross_pnl - summ.total_fees_paid, 2)
            assert abs(gross_minus_fees - summ.total_net_pnl) < 0.05, (
                f"[{cfg_id}] Accounting mismatch: Gross ({summ.total_gross_pnl}) - "
                f"Fees ({summ.total_fees_paid}) = {gross_minus_fees} != Net ({summ.total_net_pnl})"
            )

            # 3. Partition Net PnL sum
            sum_part_net = round(is_summ.total_net_pnl + val_summ.total_net_pnl + oos_summ.total_net_pnl, 2)
            assert abs(summ.total_net_pnl - sum_part_net) < 0.05, (
                f"[{cfg_id}] Partition Net mismatch: Overall ({summ.total_net_pnl}) != Sum ({sum_part_net})"
            )

            # 4. Partition Gross PnL sum
            sum_part_gross = round(is_summ.total_gross_pnl + val_summ.total_gross_pnl + oos_summ.total_gross_pnl, 2)
            assert abs(summ.total_gross_pnl - sum_part_gross) < 0.05, (
                f"[{cfg_id}] Partition Gross mismatch: Overall ({summ.total_gross_pnl}) != Sum ({sum_part_gross})"
            )

            # 5. Partition Fees sum
            sum_part_fees = round(is_summ.total_fees_paid + val_summ.total_fees_paid + oos_summ.total_fees_paid, 2)
            assert abs(summ.total_fees_paid - sum_part_fees) < 0.05, (
                f"[{cfg_id}] Partition Fees mismatch: Overall ({summ.total_fees_paid}) != Sum ({sum_part_fees})"
            )

            # Additional Risk Metrics
            max_daily_loss = calculate_max_daily_loss(res.equity_curve)
            risk_metrics = calculate_risk_metrics(res.trades, cfg.initial_capital)

            durations = [(t.exit_timestamp - t.entry_timestamp) / 3600.0 for t in res.trades]
            avg_dur = round(float(np.mean(durations)), 2) if durations else 0.0

            # Monthly performance breakdown
            monthly = calculate_monthly_breakdown(res.trades, res.equity_curve, cfg.initial_capital, cfg_id)
            all_monthly_rows.extend(monthly)

            # Regime robustness
            robustness = evaluate_regime_robustness(res.trades, df_regimes, cfg_id)
            all_robustness_rows.extend(robustness)

            # Export Equity & Drawdown Curve
            df_eq_curve = pd.DataFrame(res.equity_curve)
            peak_eq = df_eq_curve["total_equity"].cummax()
            df_eq_curve["drawdown_usdt"] = peak_eq - df_eq_curve["total_equity"]
            df_eq_curve["drawdown_pct"] = (df_eq_curve["drawdown_usdt"] / peak_eq * 100.0).round(2)
            df_eq_curve["datetime"] = pd.to_datetime(df_eq_curve["timestamp"], unit="s", utc=True).dt.strftime("%Y-%m-%d %H:%M")
            eq_file = os.path.join(EQUITY_DIR, f"equity_curve_{cfg_id}.csv")
            df_eq_curve.to_csv(eq_file, index=False)

            # Tag trades
            for t in parts["is_trades"]:
                t.__dict__["config_id"] = cfg_id
                t.__dict__["partition"] = "IN_SAMPLE"
            for t in parts["val_trades"]:
                t.__dict__["config_id"] = cfg_id
                t.__dict__["partition"] = "VALIDATION"
            for t in parts["oos_trades"]:
                t.__dict__["config_id"] = cfg_id
                t.__dict__["partition"] = "OUT_OF_SAMPLE"

            for t in res.trades:
                all_trades.append(dict(t.__dict__))

            # Partition reconciliation row
            all_reconciliation_rows.append({
                "config_id": cfg_id,
                "regime_filter": rf_label,
                "margin_allocation_pct": margin_pct,
                "overall_trades": summ.total_trades,
                "is_trades": is_summ.total_trades,
                "val_trades": val_summ.total_trades,
                "oos_trades": oos_summ.total_trades,
                "trade_count_diff": summ.total_trades - (is_summ.total_trades + val_summ.total_trades + oos_summ.total_trades),
                "overall_gross_pnl": summ.total_gross_pnl,
                "is_gross_pnl": is_summ.total_gross_pnl,
                "val_gross_pnl": val_summ.total_gross_pnl,
                "oos_gross_pnl": oos_summ.total_gross_pnl,
                "gross_pnl_diff": round(summ.total_gross_pnl - sum_part_gross, 4),
                "overall_fees": summ.total_fees_paid,
                "is_fees": is_summ.total_fees_paid,
                "val_fees": val_summ.total_fees_paid,
                "oos_fees": oos_summ.total_fees_paid,
                "fees_diff": round(summ.total_fees_paid - sum_part_fees, 4),
                "overall_net_pnl": summ.total_net_pnl,
                "is_net_pnl": is_summ.total_net_pnl,
                "val_net_pnl": val_summ.total_net_pnl,
                "oos_net_pnl": oos_summ.total_net_pnl,
                "net_pnl_diff": round(summ.total_net_pnl - sum_part_net, 4),
                "gross_minus_fees": gross_minus_fees,
                "accounting_pnl_diff": round(gross_minus_fees - summ.total_net_pnl, 4),
                "reconciliation_status": "EXACT_RECONCILED"
            })

            # Full comparison row
            all_comparison_rows.append({
                "config_id": cfg_id,
                "timeframe": "1h",
                "regime_filter": rf_label,
                "margin_allocation_pct": margin_pct,
                "leverage": 10.0,
                # Overall Performance
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
                # Drawdown & Duration
                "max_drawdown_pct": summ.max_drawdown_pct,
                "max_drawdown_usdt": summ.max_drawdown_amount,
                "max_daily_loss_usdt": max_daily_loss,
                "avg_trade_duration_hrs": avg_dur,
                # Long vs Short
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
                # Risk & Exposure Metrics
                **risk_metrics,
                "reconciliation_passed": True
            })

    # ── EXPORT ARTIFACTS ─────────────────────────────────────────────
    # 1. Comparison Report CSV
    df_comp = pd.DataFrame(all_comparison_rows)
    df_comp.to_csv("phase3_comparison_report.csv", index=False)
    print("\n[OK] Phase 3 Comparison report saved to phase3_comparison_report.csv")

    # 2. Partition Reconciliation CSV
    df_rec = pd.DataFrame(all_reconciliation_rows)
    df_rec.to_csv("phase3_partition_reconciliation.csv", index=False)
    print("[OK] Partition reconciliation audit saved to phase3_partition_reconciliation.csv")

    # 3. Complete Trade-by-Trade Audit Log
    df_trades = pd.DataFrame(all_trades)
    df_trades.to_csv("phase3_trade_by_trade_audit.csv", index=False)
    print(f"[OK] Trade-by-trade audit log ({len(df_trades)} trades) saved to phase3_trade_by_trade_audit.csv")

    # 4. Monthly Performance Breakdown CSV
    df_monthly = pd.DataFrame(all_monthly_rows)
    df_monthly.to_csv("phase3_monthly_performance.csv", index=False)
    print(f"[OK] Monthly performance breakdown ({len(df_monthly)} rows) saved to phase3_monthly_performance.csv")

    # 5. Market Regime Robustness CSV
    df_robust = pd.DataFrame(all_robustness_rows)
    df_robust.to_csv("phase3_regime_robustness.csv", index=False)
    print(f"[OK] Regime robustness breakdown ({len(df_robust)} rows) saved to phase3_regime_robustness.csv")

    # Summary Print
    print("\n" + "=" * 115)
    print("PHASE 3: EXTENDED 12-MONTH VALIDATION SUMMARY")
    print("=" * 115)
    cols = [
        "config_id", "total_trades", "win_rate_pct", "gross_pnl", "total_fees_and_funding", "net_pnl",
        "net_return_pct", "profit_factor", "max_drawdown_pct", "is_net_pnl", "val_net_pnl", "oos_net_pnl"
    ]
    print(df_comp[cols].to_string(index=False))

    return df_comp, df_rec, df_trades, df_monthly, df_robust


if __name__ == "__main__":
    run_phase3_validation()
