"""
Historical Backtesting Engine for BTCUSD / BTCUSDT Perpetual on Delta Exchange.
Simulates bar-by-bar execution on completed 5-minute candles:
- SuperTrend (10, 3)
- EMA 9 (Method A, B, or C)
- 70% margin allocation
- Configurable leverage
- Taker fees (0.05%), slippage (0.02%), and funding rate (0.01% / 8h)
- Strict opposite SuperTrend exit and automatic reversal
- Zero lookahead bias
"""

from dataclasses import dataclass
from typing import List, Dict, Any, Optional
import time
import requests
import pandas as pd
import numpy as np

from ..config.settings import TradingConfig, EMAMethod, PositionSide
from ..indicators.supertrend import calculate_supertrend, StatefulSuperTrend
from ..indicators.ema import calculate_ema, StatefulEMA
from ..signals.signal_generator import SignalGenerator, TradingSignal, SignalType
from ..risk.risk_manager import RiskManager
from ..execution.strategy_executor import StrategyExecutor, TradeRecord
from .performance import calculate_performance, PerformanceSummary


@dataclass
class BacktestResult:
    config: TradingConfig
    summary: PerformanceSummary
    trades: List[TradeRecord]
    equity_curve: List[Dict[str, Any]]
    candle_count: int
    start_date: str
    end_date: str


class BacktestEngine:
    def __init__(self, config: Optional[TradingConfig] = None):
        self.config = config or TradingConfig()
        self.risk_manager = RiskManager(self.config)
        self.signal_generator = SignalGenerator(ema_method=self.config.ema_method)
        self.executor = StrategyExecutor(self.config, self.risk_manager)
        self.supertrend = StatefulSuperTrend(
            length=self.config.supertrend_length,
            factor=self.config.supertrend_factor
        )
        self.ema = StatefulEMA(period=self.config.ema_period)

    @staticmethod
    def fetch_historical_candles_from_delta(
        symbol: str = "BTCUSDT",
        resolution: str = "5",
        days: int = 30,
        api_base: str = "https://api.delta.exchange"
    ) -> pd.DataFrame:
        """
        Fetches historical 5-minute candles from Delta Exchange chart/history API.
        Paginates backwards to retrieve up to `days` of historical candles.
        """
        now = int(time.time())
        from_time = now - (days * 86400)
        all_candles = []
        chunk_seconds = 86400 * 3  # 3 days per request (~864 5m candles)

        cur_to = now
        print(f"Fetching {days} days of {symbol} 5m candles from Delta Exchange...")

        while cur_to > from_time:
            cur_from = max(cur_to - chunk_seconds, from_time)
            url = f"{api_base}/v2/chart/history?symbol={symbol}&resolution={resolution}&from={cur_from}&to={cur_to}"
            try:
                res = requests.get(url, timeout=10)
                if res.status_code == 200:
                    data = res.json().get("result", {})
                    t = data.get("t", [])
                    if t:
                        chunk_df = pd.DataFrame({
                            "timestamp": data.get("t", []),
                            "open": data.get("o", []),
                            "high": data.get("h", []),
                            "low": data.get("l", []),
                            "close": data.get("c", []),
                            "volume": data.get("v", [])
                        })
                        all_candles.append(chunk_df)
                else:
                    print(f"Delta API warning: {res.status_code} {res.text}")
            except Exception as e:
                print(f"Error fetching candles chunk: {e}")
            cur_to = cur_from

        if not all_candles:
            raise RuntimeError(f"Failed to fetch historical candles for {symbol} from {api_base}")

        df = pd.concat(all_candles, ignore_index=True)
        df.drop_duplicates(subset=["timestamp"], inplace=True)
        df.sort_values(by="timestamp", inplace=True)
        df.reset_index(drop=True, inplace=True)
        print(f"Retrieved {len(df)} 5m candles from Delta Exchange.")
        return df

    def run(self, df: pd.DataFrame) -> BacktestResult:
        """
        Runs the backtest bar by bar across the historical candles DataFrame.
        Expected columns: timestamp, open, high, low, close.
        """
        capital = self.config.initial_capital
        equity_records: List[Dict[str, Any]] = []
        equity_values: List[float] = [capital]

        # Reset states
        self.risk_manager = RiskManager(self.config)
        self.signal_generator = SignalGenerator(ema_method=self.config.ema_method)
        self.executor = StrategyExecutor(self.config, self.risk_manager)
        self.supertrend = StatefulSuperTrend(
            length=self.config.supertrend_length,
            factor=self.config.supertrend_factor
        )
        self.ema = StatefulEMA(period=self.config.ema_period)

        for i, row in df.iterrows():
            ts = int(row["timestamp"])
            high = float(row["high"])
            low = float(row["low"])
            close = float(row["close"])

            # 1. Update indicators on completed candle
            st_res = self.supertrend.update(high, low, close)
            ema_res = self.ema.update(close)

            # Skip warmup
            if st_res is None or ema_res is None:
                continue

            # 2. Check current position state
            current_pos_side = self.executor.get_position_state()

            # 3. Generate Signal on completed candle
            signal = self.signal_generator.evaluate(
                candle_timestamp=ts,
                close=close,
                st_res=st_res,
                ema_res=ema_res,
                current_position=current_pos_side
            )

            # 4. Apply slippage on execution price if an order is triggered
            exec_price = close
            if signal.action in ["OPEN_LONG", "REVERSE"]:
                exec_price = close * (1.0 + self.config.slippage_pct)
            elif signal.action in ["OPEN_SHORT", "REVERSE"]:
                exec_price = close * (1.0 - self.config.slippage_pct)

            # Update signal price to execution price with slippage
            if signal.action != "HOLD":
                signal.price = exec_price

            # 5. Process signal through execution engine
            closed_trade, log_msg, capital = self.executor.process_signal(signal, capital)

            # 6. Track total equity (Cash + Unrealized PnL)
            self.executor.update_market_price(close)
            unrealized = self.executor.position.unrealized_pnl
            total_equity = capital + unrealized
            equity_values.append(total_equity)

            equity_records.append({
                "timestamp": ts,
                "price": close,
                "capital": round(capital, 2),
                "unrealized_pnl": round(unrealized, 2),
                "total_equity": round(total_equity, 2),
                "position": self.executor.position.side.value,
                "supertrend_trend": st_res.trend,
                "ema": round(ema_res.ema, 2)
            })

        # Close any lingering open position at the final candle close
        if self.executor.position.side != PositionSide.NONE:
            last_row = df.iloc[-1]
            final_trade, capital = self.executor.close_current_position(
                exit_price=float(last_row["close"]),
                exit_timestamp=int(last_row["timestamp"]),
                exit_reason="End of Backtest Period Liquidation",
                current_capital=capital
            )
            equity_values.append(capital)

        # Calculate final quantitative performance
        summary = calculate_performance(
            trades=self.executor.trade_history,
            initial_capital=self.config.initial_capital,
            equity_curve=equity_values
        )

        start_date = pd.to_datetime(df["timestamp"].iloc[0], unit="s").strftime("%Y-%m-%d %H:%M:%S")
        end_date = pd.to_datetime(df["timestamp"].iloc[-1], unit="s").strftime("%Y-%m-%d %H:%M:%S")

        return BacktestResult(
            config=self.config,
            summary=summary,
            trades=self.executor.trade_history,
            equity_curve=equity_records,
            candle_count=len(df),
            start_date=start_date,
            end_date=end_date
        )
