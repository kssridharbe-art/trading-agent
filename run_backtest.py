"""
CLI Runner for Historical Backtesting on Delta Exchange BTCUSDT / BTCUSD Perpetual.
Usage:
    python run_backtest.py --days 14 --method METHOD_A --leverage 10
"""

import argparse
import sys
from delta_trading_agent.config.settings import TradingConfig, EMAMethod
from delta_trading_agent.backtest.engine import BacktestEngine


def main():
    parser = argparse.ArgumentParser(description="Delta Exchange SuperTrend + EMA 9 Backtester")
    parser.add_argument("--symbol", type=str, default="BTCUSDT", help="Trading pair symbol (default: BTCUSDT)")
    parser.add_argument("--days", type=int, default=14, help="Historical days to backtest (default: 14)")
    parser.add_argument("--method", type=str, default="METHOD_C", choices=["METHOD_A", "METHOD_B", "METHOD_C"],
                        help="EMA confirmation method (default: METHOD_C)")
    parser.add_argument("--capital", type=float, default=10000.0, help="Initial capital in USDT (default: 10000)")
    parser.add_argument("--leverage", type=float, default=10.0, help="Leverage multiplier (default: 10)")
    parser.add_argument("--margin_pct", type=float, default=0.70, help="Margin allocation percentage (default: 0.70)")
    args = parser.parse_args()

    config = TradingConfig(
        symbol=args.symbol,
        initial_capital=args.capital,
        margin_allocation_pct=args.margin_pct,
        unallocated_cash_pct=1.0 - args.margin_pct,
        leverage=args.leverage,
        ema_method=EMAMethod(args.method)
    )

    print("=" * 60)
    print("DELTA EXCHANGE - BTC PERPETUAL HISTORICAL BACKTEST")
    print("=" * 60)
    print(f"Symbol:               {config.symbol}")
    print(f"Timeframe:            {config.resolution} (5 minutes)")
    print(f"Initial Capital:      {config.initial_capital:,.2f} USDT")
    print(f"Margin Allocation:    {config.margin_allocation_pct * 100:.0f}% ({config.initial_capital * config.margin_allocation_pct:,.2f} USDT max margin)")
    print(f"Unallocated Cash:     {config.unallocated_cash_pct * 100:.0f}% ({config.initial_capital * config.unallocated_cash_pct:,.2f} USDT buffer)")
    print(f"Leverage:             {config.leverage:.0f}x")
    print(f"SuperTrend Settings:  Length={config.supertrend_length}, Factor={config.supertrend_factor}")
    print(f"EMA 9 Settings:       Period={config.ema_period}, Method={config.ema_method.value}")
    print("=" * 60)

    try:
        # Download historical 5m candles from Delta Exchange
        df = BacktestEngine.fetch_historical_candles_from_delta(
            symbol=config.symbol,
            resolution="5",
            days=args.days,
            api_base=config.api_base_url
        )
    except Exception as e:
        print(f"Failed to fetch market data: {e}")
        sys.exit(1)

    print(f"Running simulation on {len(df)} candles from {df['timestamp'].iloc[0]} to {df['timestamp'].iloc[-1]}...")
    engine = BacktestEngine(config)
    result = engine.run(df)
    s = result.summary

    print("\n" + "=" * 60)
    print("VERIFIED BACKTEST PERFORMANCE REPORT")
    print("=" * 60)
    print(f"Backtest Period:         {result.start_date} -> {result.end_date}")
    print(f"Candles Processed:       {result.candle_count:,}")
    print("-" * 60)
    print(f"Starting Capital:        {s.initial_capital:,.2f} USDT")
    print(f"Ending Capital:          {s.ending_capital:,.2f} USDT")
    print(f"Net Profit / Loss:       {s.total_net_pnl:,.2f} USDT ({s.total_return_pct:+.2f}%)")
    print(f"Total Fees & Funding:    {s.total_fees_paid:,.2f} USDT")
    print(f"Max Drawdown:            {s.max_drawdown_amount:,.2f} USDT ({s.max_drawdown_pct:.2f}%)")
    print(f"Profit Factor:           {s.profit_factor:.2f}")
    print("-" * 60)
    print(f"Total Trades:            {s.total_trades}")
    print(f"Winning Trades:          {s.winning_trades} ({s.win_rate_pct:.1f}%)")
    print(f"Losing Trades:           {s.losing_trades}")
    print(f"Average Win:             {s.avg_winning_trade:,.2f} USDT")
    print(f"Average Loss:            {s.avg_losing_trade:,.2f} USDT")
    print(f"Payoff Ratio (Win/Loss): {s.payoff_ratio:.2f}")
    print("-" * 60)
    print(f"LONG Trades:             {s.long_trades} (Win Rate: {s.long_win_rate_pct:.1f}%, Net PnL: {s.long_net_pnl:,.2f} USDT)")
    print(f"SHORT Trades:            {s.short_trades} (Win Rate: {s.short_win_rate_pct:.1f}%, Net PnL: {s.short_net_pnl:,.2f} USDT)")
    print("=" * 60)

    if result.trades:
        print("\nRecent 5 Completed Trades:")
        for t in result.trades[-5:]:
            print(f"  [{t.trade_id}] {t.side.value:<5} Entry: {t.entry_price:.1f} Exit: {t.exit_price:.1f} | Net PnL: {t.net_pnl:+8.2f} USDT ({t.return_pct:+6.2f}%) | Reason: {t.exit_reason}")
    print("=" * 60)


if __name__ == "__main__":
    main()
