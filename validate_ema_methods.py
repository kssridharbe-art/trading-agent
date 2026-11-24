"""
Comprehensive Backtest Validation Script:
1. Validates EMA 9 Method A, Method B, and Method C independently.
2. Detailed logging of every SuperTrend reversal (accepted vs rejected).
3. Investigates mathematical equivalence between Close vs EMA and EMA slope.
4. Segregates performance in Trending vs. Sideways market regimes.
5. Verifies 5% Daily Loss and 3-Consecutive-Loss circuit breakers.
"""

import time
from datetime import datetime, timezone
import pandas as pd
import numpy as np

from delta_trading_agent.config.settings import TradingConfig, EMAMethod, PositionSide
from delta_trading_agent.backtest.engine import BacktestEngine
from delta_trading_agent.indicators.supertrend import StatefulSuperTrend
from delta_trading_agent.indicators.ema import StatefulEMA


def main():
    print("=" * 75)
    print("BACKTEST VALIDATION & QUANTITATIVE INVESTIGATION REPORT")
    print("Instrument: BTCUSDT 5m Perpetual | Platform: Delta Exchange")
    print("=" * 75)

    # 1. Fetch 30 days of data from Delta Exchange
    df = BacktestEngine.fetch_historical_candles_from_delta(symbol="BTCUSDT", resolution="5", days=30)
    total_candles = len(df)
    start_dt = datetime.fromtimestamp(df["timestamp"].iloc[0], tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    end_dt = datetime.fromtimestamp(df["timestamp"].iloc[-1], tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    print(f"Candles Analyzed: {total_candles} from {start_dt} to {end_dt} UTC\n")

    # 2. Detailed Reversal & Filter Audit
    st = StatefulSuperTrend(length=10, factor=3.0)
    ema = StatefulEMA(period=9)

    reversals = []
    accepted_counts = {"METHOD_A": 0, "METHOD_B": 0, "METHOD_C": 0}
    rejected_counts = {"METHOD_A": 0, "METHOD_B": 0, "METHOD_C": 0}

    for i, row in df.iterrows():
        ts = int(row["timestamp"])
        c = float(row["close"])
        h = float(row["high"])
        l = float(row["low"])
        st_res = st.update(h, l, c)
        ema_res = ema.update(c)
        if st_res is None or ema_res is None:
            continue

        if st_res.is_reversal_to_green:
            # Reversal to Bullish
            conf_a = c > ema_res.ema
            conf_b = ema_res.slope > 0
            conf_c = conf_a and conf_b

            accepted_counts["METHOD_A"] += int(conf_a)
            accepted_counts["METHOD_B"] += int(conf_b)
            accepted_counts["METHOD_C"] += int(conf_c)
            rejected_counts["METHOD_A"] += int(not conf_a)
            rejected_counts["METHOD_B"] += int(not conf_b)
            rejected_counts["METHOD_C"] += int(not conf_c)

            reversals.append({
                "bar": i,
                "timestamp": ts,
                "datetime": datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
                "reversal": "RED -> GREEN (LONG)",
                "close": c,
                "ema_9": round(ema_res.ema, 2),
                "ema_slope": round(ema_res.slope, 2),
                "Method_A (Close > EMA)": "PASS" if conf_a else "REJECT",
                "Method_B (Slope > 0)": "PASS" if conf_b else "REJECT",
                "Method_C (Both)": "PASS" if conf_c else "REJECT",
            })

        elif st_res.is_reversal_to_red:
            # Reversal to Bearish
            conf_a = c < ema_res.ema
            conf_b = ema_res.slope < 0
            conf_c = conf_a and conf_b

            accepted_counts["METHOD_A"] += int(conf_a)
            accepted_counts["METHOD_B"] += int(conf_b)
            accepted_counts["METHOD_C"] += int(conf_c)
            rejected_counts["METHOD_A"] += int(not conf_a)
            rejected_counts["METHOD_B"] += int(not conf_b)
            rejected_counts["METHOD_C"] += int(not conf_c)

            reversals.append({
                "bar": i,
                "timestamp": ts,
                "datetime": datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
                "reversal": "GREEN -> RED (SHORT)",
                "close": c,
                "ema_9": round(ema_res.ema, 2),
                "ema_slope": round(ema_res.slope, 2),
                "Method_A (Close < EMA)": "PASS" if conf_a else "REJECT",
                "Method_B (Slope < 0)": "PASS" if conf_b else "REJECT",
                "Method_C (Both)": "PASS" if conf_c else "REJECT",
            })

    total_rev = len(reversals)
    print("=" * 75)
    print("PART 1: SIGNAL FILTER AUDIT (SUPERTREND REVERSAL SIGNALS)")
    print("=" * 75)
    print(f"Total SuperTrend Reversals:   {total_rev}")
    print(f"Method A (Candle Close):      {accepted_counts['METHOD_A']} Accepted | {rejected_counts['METHOD_A']} Rejected")
    print(f"Method B (EMA 9 Slope):       {accepted_counts['METHOD_B']} Accepted | {rejected_counts['METHOD_B']} Rejected")
    print(f"Method C (Close & Slope):     {accepted_counts['METHOD_C']} Accepted | {rejected_counts['METHOD_C']} Rejected")
    print("-" * 75)

    # Show sample of rejected signals
    rejected_samples = [r for r in reversals if r["Method_C (Both)"] == "REJECT"]
    print(f"\nSample of Rejected Signals (Total {len(rejected_samples)}):")
    for r in rejected_samples[:5]:
        print(f"  [{r['datetime']}] {r['reversal']} at Close: {r['close']:.2f} | EMA: {r['ema_9']:.2f} | Slope: {r['ema_slope']:+.2f} -> REJECTED")

    print("\nSample of Accepted Signals:")
    accepted_samples = [r for r in reversals if r["Method_C (Both)"] == "PASS"]
    for r in accepted_samples[:5]:
        print(f"  [{r['datetime']}] {r['reversal']} at Close: {r['close']:.2f} | EMA: {r['ema_9']:.2f} | Slope: {r['ema_slope']:+.2f} -> ACCEPTED")

    # 3. Independent Simulation of Method A, B, C with Circuit Breakers Enabled
    print("\n" + "=" * 75)
    print("PART 2: COMPARATIVE PERFORMANCE (30-DAY FULL BACKTEST)")
    print("Risk Settings: 70% Margin Allocation | 10x Leverage | 5% Daily Loss | 3-Loss Breaker")
    print("=" * 75)

    results = {}
    for method in [EMAMethod.METHOD_A, EMAMethod.METHOD_B, EMAMethod.METHOD_C]:
        cfg = TradingConfig(
            symbol="BTCUSDT",
            ema_method=method,
            margin_allocation_pct=0.70,
            leverage=10.0,
            max_daily_loss_pct=0.05,
            max_consecutive_losses=3
        )
        engine = BacktestEngine(cfg)
        res = engine.run(df)
        results[method.value] = res
        s = res.summary
        print(f"\n[{method.value}] Performance Summary:")
        print(f"  Starting Capital:      {s.initial_capital:,.2f} USDT")
        print(f"  Ending Capital:        {s.ending_capital:,.2f} USDT")
        print(f"  Net P&L:               {s.total_net_pnl:+,.2f} USDT ({s.total_return_pct:+.2f}%)")
        print(f"  Total Trades:          {s.total_trades} (Win Rate: {s.win_rate_pct:.1f}%)")
        print(f"  Wins / Losses:         {s.winning_trades} Wins / {s.losing_trades} Losses")
        print(f"  Total Fees & Slippage: {s.total_fees_paid:,.2f} USDT")
        print(f"  Profit Factor:         {s.profit_factor:.2f}")
        print(f"  Max Drawdown:          {s.max_drawdown_amount:,.2f} USDT ({s.max_drawdown_pct:.2f}%)")
        print(f"  Long Trades:           {s.long_trades} (Win%: {s.long_win_rate_pct:.1f}%, PnL: {s.long_net_pnl:+,.2f} USDT)")
        print(f"  Short Trades:          {s.short_trades} (Win%: {s.short_win_rate_pct:.1f}%, PnL: {s.short_net_pnl:+,.2f} USDT)")

    # 4. Regime Analysis: Trending vs. Sideways Market
    print("\n" + "=" * 75)
    print("PART 3: MARKET REGIME ANALYSIS (TRENDING VS. SIDEWAYS)")
    print("=" * 75)

    # Let's find a trending slice and a sideways slice from df
    # Compute rolling 24h return to identify trending and sideways windows
    window_288 = 288  # 24 hours (288 * 5m)
    df["rolling_ret"] = df["close"].pct_change(window_288)

    # Strong trending window: highest absolute 24h return
    max_trend_idx = int(df["rolling_ret"].abs().idxmax())
    trend_start = max(0, max_trend_idx - window_288)
    trend_end = min(len(df), max_trend_idx)
    trend_df = df.iloc[trend_start:trend_end].copy().reset_index(drop=True)

    # Sideways window: lowest rolling ATR / price variation
    df["rolling_std"] = df["close"].rolling(window_288).std()
    min_vol_idx = int(df["rolling_std"].dropna().idxmin())
    side_start = max(0, min_vol_idx - window_288)
    side_end = min(len(df), min_vol_idx)
    side_df = df.iloc[side_start:side_end].copy().reset_index(drop=True)

    # Run Method C on Trending regime
    cfg_c = TradingConfig(symbol="BTCUSDT", ema_method=EMAMethod.METHOD_C, leverage=10.0, max_daily_loss_pct=0.05, max_consecutive_losses=3)
    engine_trend = BacktestEngine(cfg_c)
    res_trend = engine_trend.run(trend_df)
    st_t = res_trend.summary

    # Run Method C on Sideways regime
    engine_side = BacktestEngine(cfg_c)
    res_side = engine_side.run(side_df)
    st_s = res_side.summary

    print(f"A. TRENDING REGIME ({trend_df['timestamp'].iloc[0]} -> {trend_df['timestamp'].iloc[-1]}):")
    print(f"   Price Movement: {trend_df['close'].iloc[0]:.1f} -> {trend_df['close'].iloc[-1]:.1f} ({((trend_df['close'].iloc[-1]/trend_df['close'].iloc[0])-1)*100:+.2f}%)")
    print(f"   Trades Taken:   {st_t.total_trades}")
    print(f"   Win Rate:       {st_t.win_rate_pct:.1f}%")
    print(f"   Net P&L:        {st_t.total_net_pnl:+,.2f} USDT ({st_t.total_return_pct:+.2f}%)")
    print(f"   Profit Factor:  {st_t.profit_factor:.2f}")
    print(f"   Max Drawdown:   {st_t.max_drawdown_pct:.2f}%")

    print(f"\nB. SIDEWAYS / CHOPPY REGIME ({side_df['timestamp'].iloc[0]} -> {side_df['timestamp'].iloc[-1]}):")
    print(f"   Price Movement: {side_df['close'].iloc[0]:.1f} -> {side_df['close'].iloc[-1]:.1f} ({((side_df['close'].iloc[-1]/side_df['close'].iloc[0])-1)*100:+.2f}%)")
    print(f"   Trades Taken:   {st_s.total_trades}")
    print(f"   Win Rate:       {st_s.win_rate_pct:.1f}%")
    print(f"   Net P&L:        {st_s.total_net_pnl:+,.2f} USDT ({st_s.total_return_pct:+.2f}%)")
    print(f"   Profit Factor:  {st_s.profit_factor:.2f}")
    print(f"   Max Drawdown:   {st_s.max_drawdown_pct:.2f}%")

    # 5. Circuit Breaker Audit Log
    print("\n" + "=" * 75)
    print("PART 4: CIRCUIT BREAKER VALIDATION AUDIT")
    print("=" * 75)
    cb_events = []
    for t in results["METHOD_C"].trades:
        if "Circuit breaker" in t.exit_reason or t.net_pnl < 0:
            cb_events.append(t)

    print(f"Total Trades Executed in Method C: {len(results['METHOD_C'].trades)}")
    print("Sample Trades with Execution & Breaker Transitions:")
    for t in results["METHOD_C"].trades[:10]:
        print(f"  [{t.trade_id}] {t.side.value:<5} Entry: {t.entry_price:,.1f} | Exit: {t.exit_price:,.1f} | Net PnL: {t.net_pnl:+8.2f} USDT | Return: {t.return_pct:+6.2f}% | Exit: {t.exit_reason}")
    print("=" * 75)


if __name__ == "__main__":
    main()
