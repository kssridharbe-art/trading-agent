"""
Dashboard Server Runner.
Initializes Paper Trader on live Delta Exchange market data
and serves the real-time web dashboard on http://localhost:5000.
"""

import os
import argparse
from delta_trading_agent.config.settings import CONFIG, EMAMethod
from delta_trading_agent.paper.paper_trader import PaperTrader
from delta_trading_agent.storage.database import DatabaseManager
from delta_trading_agent.dashboard.app import create_app


def main():
    parser = argparse.ArgumentParser(description="Delta Exchange Trading Dashboard")
    parser.add_argument("--port", type=int, default=5000, help="Web dashboard port (default: 5000)")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Host bind address (default: 127.0.0.1)")
    parser.add_argument("--method", type=str, default="METHOD_C", choices=["METHOD_A", "METHOD_B", "METHOD_C"],
                        help="Initial EMA confirmation method (default: METHOD_C)")
    parser.add_argument("--leverage", type=float, default=10.0, help="Initial leverage (default: 10)")
    args = parser.parse_args()

    CONFIG.ema_method = EMAMethod(args.method)
    CONFIG.leverage = args.leverage

    print("=" * 65)
    print("DELTA EXCHANGE - BTC/USDT AUTOMATED TRADING AGENT & DASHBOARD")
    print("=" * 65)
    print(f"Server URL:         http://{args.host}:{args.port}")
    print(f"Symbol:             {CONFIG.symbol}")
    print(f"Timeframe:          {CONFIG.resolution} (5 minutes)")
    print(f"Initial Capital:    {CONFIG.initial_capital:,.2f} USDT")
    print(f"Margin Allocation:  70% ({CONFIG.initial_capital * 0.70:,.2f} USDT max margin per position)")
    print(f"Cash Buffer:        30% ({CONFIG.initial_capital * 0.30:,.2f} USDT unallocated)")
    print(f"EMA 9 Method:       {CONFIG.ema_method.value}")
    print(f"Leverage:           {CONFIG.leverage:.0f}x")
    print("=" * 65)

    db = DatabaseManager(CONFIG.db_path)
    paper_trader = PaperTrader(CONFIG, db)
    paper_trader.start_polling_loop()

    app = create_app(paper_trader, db)
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
