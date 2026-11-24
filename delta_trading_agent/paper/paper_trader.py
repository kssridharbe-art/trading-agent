"""
Paper Trading Module.
Simulates order execution in real-time on live Delta Exchange market data.
Maintains state, tracks simulated margin, calculates real fees/slippage,
logs trades to SQLite, and updates the web dashboard in real-time.
"""

import time
import threading
import logging
from typing import Optional, Dict, Any, Callable
import pandas as pd

from ..config.settings import TradingConfig, PositionSide, EMAMethod, TradingMode
from ..indicators.supertrend import StatefulSuperTrend
from ..indicators.ema import StatefulEMA
from ..signals.signal_generator import SignalGenerator, TradingSignal, SignalType
from ..risk.risk_manager import RiskManager
from ..execution.strategy_executor import StrategyExecutor, TradeRecord, ActivePosition
from ..exchange.delta_client import DeltaExchangeClient
from ..storage.database import DatabaseManager

logger = logging.getLogger("PaperTrader")


class PaperTrader:
    def __init__(self, config: TradingConfig, db: Optional[DatabaseManager] = None):
        self.config = config
        self.db = db or DatabaseManager(config.db_path)
        self.client = DeltaExchangeClient(config)
        self.risk_manager = RiskManager(config)
        self.signal_generator = SignalGenerator(ema_method=config.ema_method)
        self.executor = StrategyExecutor(config, self.risk_manager)
        
        self.supertrend = StatefulSuperTrend(
            length=config.supertrend_length,
            factor=config.supertrend_factor
        )
        self.ema = StatefulEMA(period=config.ema_period)

        self.current_capital: float = config.initial_capital
        self.live_price: float = 0.0
        self.latest_candle: Optional[Dict[str, Any]] = None
        self.latest_signal: Optional[TradingSignal] = None
        
        self.is_running = False
        self.worker_thread: Optional[threading.Thread] = None
        self.poll_interval = 5  # Check every 5 seconds for new candle completion
        self.last_candle_timestamp = 0

    def warm_up_indicators(self, days: int = 2):
        """Pre-loads recent historical candles to initialize SuperTrend and EMA."""
        logger.info(f"Warming up indicators with {days} days of historical 5m candles...")
        now = int(time.time())
        from_time = now - (days * 86400)
        url = f"{self.config.api_base_url}/v2/chart/history?symbol={self.config.symbol}&resolution=5&from={from_time}&to={now}"
        try:
            import requests
            res = requests.get(url, timeout=10)
            if res.status_code == 200:
                data = res.json().get("result", {})
                t_arr = data.get("t", [])
                h_arr = data.get("h", [])
                l_arr = data.get("l", [])
                c_arr = data.get("c", [])
                for i in range(len(t_arr)):
                    st_res = self.supertrend.update(float(h_arr[i]), float(l_arr[i]), float(c_arr[i]))
                    ema_res = self.ema.update(float(c_arr[i]))
                    self.last_candle_timestamp = int(t_arr[i])
                    self.live_price = float(c_arr[i])
                logger.info(f"Warmup complete. Processed {len(t_arr)} historical bars. Last price: {self.live_price:.2f}")
        except Exception as e:
            logger.error(f"Failed to warmup indicators: {e}")

    def on_live_price(self, price: float, timestamp: int):
        """Called when a live ticker price tick is received."""
        self.live_price = price
        self.executor.update_market_price(price)

    def on_completed_candle(self, high: float, low: float, close: float, candle_timestamp: int):
        """
        Called when a 5-minute candle has completed.
        Evaluates signals, checks risk limits, and executes paper orders.
        """
        self.live_price = close
        self.latest_candle = {
            "timestamp": candle_timestamp,
            "high": high,
            "low": low,
            "close": close
        }

        # 1. Update indicators
        st_res = self.supertrend.update(high, low, close)
        ema_res = self.ema.update(close)

        if st_res is None or ema_res is None:
            logger.warning(f"Indicators still warming up on candle {candle_timestamp}")
            return

        # 2. Evaluate signal
        current_pos = self.executor.get_position_state()
        signal = self.signal_generator.evaluate(
            candle_timestamp=candle_timestamp,
            close=close,
            st_res=st_res,
            ema_res=ema_res,
            current_position=current_pos
        )
        self.latest_signal = signal

        # 3. Record signal in database
        self.db.record_signal(self.config.symbol, signal)

        # 4. If action required, apply slippage and process order
        if signal.action != "HOLD":
            exec_price = close
            if signal.action in ["OPEN_LONG", "REVERSE"]:
                exec_price = close * (1.0 + self.config.slippage_pct)
            elif signal.action in ["OPEN_SHORT", "REVERSE"]:
                exec_price = close * (1.0 - self.config.slippage_pct)

            signal.price = exec_price
            closed_trade, log_msg, self.current_capital = self.executor.process_signal(signal, self.current_capital)

            if log_msg:
                logger.info(f"[PAPER EXECUTION] {log_msg}")
                self.db.log_event("INFO", "PAPER_TRADER", log_msg)

            if closed_trade:
                self.db.record_trade(closed_trade)

        # 5. Snapshot equity
        unrealized = self.executor.position.unrealized_pnl
        total_equity = self.current_capital + unrealized
        self.db.record_equity_snapshot(
            timestamp=candle_timestamp,
            capital=self.current_capital,
            unrealized_pnl=unrealized,
            total_equity=total_equity,
            used_margin=self.executor.position.margin_used,
            position_side=self.executor.position.side.value,
            position_size=self.executor.position.btc_size
        )

    def manual_close_position(self, reason: str = "Manual User Close via Dashboard"):
        """Emergency or manual close button for active position."""
        if self.executor.position.side == PositionSide.NONE or self.executor.position.contracts == 0:
            return {"success": False, "message": "No active position to close."}

        closed_trade, self.current_capital = self.executor.close_current_position(
            exit_price=self.live_price,
            exit_timestamp=int(time.time()),
            exit_reason=reason,
            current_capital=self.current_capital
        )
        self.db.record_trade(closed_trade)
        msg = f"Manual Close Executed at {self.live_price:.2f}. Net PnL: {closed_trade.net_pnl:.2f} USDT"
        self.db.log_event("WARNING", "MANUAL_CONTROL", msg)
        return {"success": True, "message": msg, "trade": closed_trade}

    def start_polling_loop(self):
        """Starts background loop to poll latest 5m candles and ticker."""
        if self.is_running:
            return
        self.is_running = True
        self.warm_up_indicators(days=2)
        self.worker_thread = threading.Thread(target=self._run_loop, daemon=True)
        self.worker_thread.start()

    def stop_polling_loop(self):
        self.is_running = False

    def _run_loop(self):
        logger.info("Paper trading background polling loop started.")
        while self.is_running:
            try:
                now = int(time.time())
                # Poll chart history for the last 2 bars
                url = f"{self.config.api_base_url}/v2/chart/history?symbol={self.config.symbol}&resolution=5&from={now - 3600}&to={now}"
                import requests
                res = requests.get(url, timeout=5)
                if res.status_code == 200:
                    data = res.json().get("result", {})
                    t_arr = data.get("t", [])
                    if t_arr:
                        # Latest completed candle is the second to last, or last if timestamp + 300 < now
                        latest_t = int(t_arr[-1])
                        # If the last candle in list has closed (at least 300s passed)
                        if latest_t > self.last_candle_timestamp and (now >= latest_t + 300):
                            idx = len(t_arr) - 1
                            self.on_completed_candle(
                                high=float(data["h"][idx]),
                                low=float(data["l"][idx]),
                                close=float(data["c"][idx]),
                                candle_timestamp=latest_t
                            )
                            self.last_candle_timestamp = latest_t
                        elif len(t_arr) >= 2:
                            prev_t = int(t_arr[-2])
                            if prev_t > self.last_candle_timestamp:
                                idx = len(t_arr) - 2
                                self.on_completed_candle(
                                    high=float(data["h"][idx]),
                                    low=float(data["l"][idx]),
                                    close=float(data["c"][idx]),
                                    candle_timestamp=prev_t
                                )
                                self.last_candle_timestamp = prev_t

                        # Update live price from the latest incomplete bar's current close
                        self.on_live_price(float(data["c"][-1]), now)
            except Exception as e:
                logger.error(f"Error in paper trader loop: {e}")

            time.sleep(self.poll_interval)

    def get_dashboard_state(self) -> Dict[str, Any]:
        """Provides full real-time snapshot for the dashboard UI."""
        pos = self.executor.position
        unrealized = pos.unrealized_pnl
        total_equity = self.current_capital + unrealized
        available_margin = max(self.current_capital * self.config.margin_allocation_pct - pos.margin_used, 0.0)

        return {
            "account": {
                "total_capital": round(self.current_capital, 2),
                "total_equity": round(total_equity, 2),
                "unrealized_pnl": round(unrealized, 2),
                "used_margin": round(pos.margin_used, 2),
                "available_margin": round(available_margin, 2),
                "unallocated_cash": round(self.current_capital * self.config.unallocated_cash_pct, 2),
                "leverage": self.config.leverage,
                "circuit_breaker": self.risk_manager.circuit_breaker_active,
                "circuit_breaker_reason": self.risk_manager.circuit_breaker_reason
            },
            "market": {
                "symbol": self.config.symbol,
                "live_price": round(self.live_price, 2),
                "supertrend_trend": self.supertrend.prev_trend or 0,
                "supertrend_val": round(self.supertrend.prev_lower if self.supertrend.prev_trend == 1 else (self.supertrend.prev_upper or 0.0), 2),
                "ema_val": round(self.ema.ema or 0.0, 2),
                "ema_slope": round(self.ema.ema - (self.ema.prev_ema or self.ema.ema), 2) if self.ema.ema else 0.0,
                "ema_method": self.config.ema_method.value
            },
            "position": {
                "side": pos.side.value,
                "entry_price": round(pos.entry_price, 2),
                "contracts": pos.contracts,
                "btc_size": pos.btc_size,
                "margin_used": round(pos.margin_used, 2),
                "unrealized_pnl": round(unrealized, 2),
                "liquidation_price": round(pos.liquidation_price, 2),
                "entry_timestamp": pos.entry_timestamp
            },
            "latest_signal": {
                "type": self.latest_signal.signal_type.value if self.latest_signal else "NONE",
                "action": self.latest_signal.action if self.latest_signal else "HOLD",
                "reason": self.latest_signal.reason if self.latest_signal else "System initializing"
            },
            "recent_trades": [t.__dict__ for t in self.executor.trade_history[-20:]]
        }
