"""
CLI Runner for Paper Trading on Live Delta Exchange Market Data.
"""

import time
import signal
import sys
from delta_trading_agent.config.settings import CONFIG, EMAMethod
from delta_trading_agent.paper.paper_trader import PaperTrader
from delta_trading_agent.storage.database import DatabaseManager


def main():
    print("=" * 65)
    print("STARTING PAPER TRADING AGENT (DELTA EXCHANGE LIVE FEED)")
    print("=" * 65)
    print(f"Symbol:           {CONFIG.symbol}")
    print(f"Timeframe:        5 minutes")
    print(f"Leverage:         {CONFIG.leverage}x")
    print(f"EMA 9 Method:     {CONFIG.ema_method.value}")
    print(f"Margin Cap:       70% per position (30% unallocated cash)")
    print("Press Ctrl+C to terminate.")
    print("=" * 65)

    db = DatabaseManager(CONFIG.db_path)
    trader = PaperTrader(CONFIG, db)
    trader.start_polling_loop()

    def handle_exit(sig, frame):
        print("\nStopping paper trader...")
        trader.stop_polling_loop()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_exit)

    try:
        while True:
            time.sleep(10)
            state = trader.get_dashboard_state()
            acc = state["account"]
            mkt = state["market"]
            pos = state["position"]
            sig = state["latest_signal"]
            print(f"[STATUS] BTC: {mkt['live_price']:.1f} USDT | SuperTrend: {mkt['supertrend_trend']} | Pos: {pos['side']} ({pos['btc_size']:.3f} BTC) | Equity: {acc['total_equity']:.2f} USDT | Signal: {sig['action']}")
    except KeyboardInterrupt:
        trader.stop_polling_loop()


if __name__ == "__main__":
    main()
