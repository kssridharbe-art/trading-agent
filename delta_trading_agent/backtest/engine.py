"""
Historical Backtesting Engine for BTCUSD / BTCUSDT Perpetual on Delta Exchange.
Phase 2 Enhanced:
- SuperTrend (10, 3) + EMA 9 (Method A/B/C)
- Optional ADX Trend-Strength and ATR Volatility filters
- Multi-timeframe support: 5m, 15m, 1h (resolution="5","15","60")
- Hardened risk manager with per-bar unrealized loss check
- Execution model: MARKET or POST_ONLY_LIMIT with partial fills
- Tick-size rounding (0.5 USDT)
- Correct directional slippage per signal type (REVERSE_TO_LONG vs REVERSE_TO_SHORT)
- Zero lookahead bias – indicators update on completed candle only
- Live trading ALWAYS disabled (live_trading_authorized = False)
"""

from dataclasses import dataclass
from typing import List, Dict, Any, Optional
import time
import requests
import pandas as pd
import numpy as np

from ..config.settings import TradingConfig, EMAMethod, PositionSide, RegimeFilterType, ExecutionType
from ..indicators.supertrend import calculate_supertrend, StatefulSuperTrend
from ..indicators.ema import calculate_ema, StatefulEMA
from ..indicators.adx import StatefulADX
from ..indicators.atr import StatefulATR
from ..signals.signal_generator import SignalGenerator, TradingSignal, SignalType
from ..risk.risk_manager import RiskManager
from ..execution.strategy_executor import StrategyExecutor, TradeRecord
from ..execution.execution_model import ExecutionModel, FilledOrder
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
    regime_filter: str
    execution_type: str
    timeframe: str


# Resolution string -> seconds per bar
_RESOLUTION_SECONDS: Dict[str, int] = {
    "5": 300,
    "15": 900,
    "60": 3600,
}


class BacktestEngine:
    def __init__(self, config: Optional[TradingConfig] = None):
        self.config = config or TradingConfig()
        # Safety: backtest never touches live orders
        self.config.live_trading_authorized = False
        self._init_components()

    def _init_components(self):
        self.risk_manager = RiskManager(self.config)
        self.signal_generator = SignalGenerator(
            ema_method=self.config.ema_method,
            regime_filter=self.config.regime_filter
        )
        self.executor = StrategyExecutor(self.config, self.risk_manager)
        self.execution_model = ExecutionModel(self.config)
        self.supertrend = StatefulSuperTrend(
            length=self.config.supertrend_length,
            factor=self.config.supertrend_factor
        )
        self.ema = StatefulEMA(period=self.config.ema_period)
        self.adx = StatefulADX(
            period=self.config.adx_period,
            threshold=self.config.adx_threshold
        )
        self.atr = StatefulATR(
            period=self.config.atr_filter_period,
            min_pct_threshold=self.config.atr_min_pct
        )

    @staticmethod
    def fetch_historical_candles_from_delta(
        symbol: str = "BTCUSDT",
        resolution: str = "5",
        days: int = 30,
        api_base: str = "https://api.delta.exchange"
    ) -> pd.DataFrame:
        """
        Fetches historical candles from Delta Exchange chart/history API.
        resolution: "5" (5m), "15" (15m), "60" (1h)
        Paginates backwards to retrieve up to `days` of historical candles.
        """
        now = int(time.time())
        from_time = now - (days * 86400)
        all_candles = []

        bar_secs = _RESOLUTION_SECONDS.get(resolution, 300)
        # Fetch ~1000 bars per request
        chunk_seconds = bar_secs * 1000

        cur_to = now
        label = {
            "5": "5-minute", "15": "15-minute", "60": "1-hour"
        }.get(resolution, resolution)
        print(f"Fetching {days} days of {symbol} {label} candles from Delta Exchange...")

        while cur_to > from_time:
            cur_from = max(cur_to - chunk_seconds, from_time)
            url = (
                f"{api_base}/v2/chart/history"
                f"?symbol={symbol}&resolution={resolution}&from={cur_from}&to={cur_to}"
            )
            try:
                res = requests.get(url, timeout=15)
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
                    print(f"Delta API warning: {res.status_code} {res.text[:200]}")
            except Exception as e:
                print(f"Error fetching candles chunk: {e}")
            cur_to = cur_from

        if not all_candles:
            raise RuntimeError(
                f"Failed to fetch historical candles for {symbol} (resolution={resolution}) from {api_base}"
            )

        df = pd.concat(all_candles, ignore_index=True)
        df.drop_duplicates(subset=["timestamp"], inplace=True)
        df.sort_values(by="timestamp", inplace=True)
        df.reset_index(drop=True, inplace=True)

        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df.dropna(subset=["close"], inplace=True)
        df.reset_index(drop=True, inplace=True)

        print(f"Retrieved {len(df)} {label} candles ({df['timestamp'].iloc[0]} -> {df['timestamp'].iloc[-1]})")
        return df

    def run(self, df: pd.DataFrame) -> BacktestResult:
        """
        Runs the backtest bar-by-bar on a completed-candle DataFrame.
        Expected columns: timestamp, open, high, low, close (volume optional).
        """
        # Re-initialize all stateful objects so the engine is reusable
        self._init_components()

        capital = self.config.initial_capital
        equity_records: List[Dict[str, Any]] = []
        equity_values: List[float] = [capital]

        # Pending limit order state
        pending_limit: Optional[Dict[str, Any]] = None
        limit_bars_waiting = 0

        for i, row in df.iterrows():
            ts = int(row["timestamp"])
            high = float(row["high"])
            low = float(row["low"])
            close = float(row["close"])

            # ── 1. Try to fill any outstanding limit order on this bar ──────
            if pending_limit is not None:
                fill = self.execution_model.try_fill_limit(
                    pending_order=pending_limit,
                    bar_high=high,
                    bar_low=low,
                    bar_close=close,
                    bars_waiting=limit_bars_waiting
                )
                if fill.filled:
                    # Apply the pending action with the actual fill price
                    _signal = pending_limit["signal"]
                    _signal.price = fill.fill_price
                    closed_trade, _, capital = self.executor.process_signal(
                        _signal, capital, fill_qty=fill.fill_qty
                    )
                    if closed_trade:
                        self.risk_manager.record_trade_result(closed_trade.net_pnl, capital, ts)
                    pending_limit = None
                    limit_bars_waiting = 0
                elif fill.timed_out:
                    pending_limit = None
                    limit_bars_waiting = 0
                else:
                    limit_bars_waiting += 1

            # ── 2. Update indicators (completed candle, no lookahead) ────────
            st_res = self.supertrend.update(high, low, close)
            ema_res = self.ema.update(close)
            adx_res = self.adx.update(high, low, close)
            atr_res = self.atr.update(high, low, close)

            if st_res is None or ema_res is None:
                continue   # Still warming up SuperTrend or EMA

            # ── 3. Per-bar unrealized drawdown check ─────────────────────────
            self.executor.update_market_price(close)
            unrealized = self.executor.position.unrealized_pnl
            self.risk_manager.update_equity_and_check_drawdown(capital, unrealized, ts)

            # ── 4. Generate signal on completed candle ───────────────────────
            current_pos_side = self.executor.get_position_state()
            signal = self.signal_generator.evaluate(
                candle_timestamp=ts,
                close=close,
                st_res=st_res,
                ema_res=ema_res,
                current_position=current_pos_side,
                adx_res=adx_res,
                atr_res=atr_res
            )

            # ── 5. Execute signal through execution model ────────────────────
            if signal.action not in ("HOLD",) and signal.signal_type != SignalType.NONE:
                if self.config.execution_type == ExecutionType.MARKET:
                    # Market order: immediate fill with correct directional slippage
                    exec_price = self._apply_market_slippage(signal, close)
                    signal.price = exec_price
                    closed_trade, action_msg, capital = self.executor.process_signal(signal, capital)
                    if closed_trade:
                        self.risk_manager.record_trade_result(closed_trade.net_pnl, capital, ts)

                elif self.config.execution_type == ExecutionType.POST_ONLY_LIMIT:
                    # Post-only limit: place and queue; will try to fill next bar(s)
                    limit_price = self.execution_model.calc_limit_price(signal, close)
                    signal.price = limit_price
                    pending_limit = {
                        "signal": signal,
                        "limit_price": limit_price,
                        "side": signal.signal_type,
                        "timeout_bars": self.config.limit_timeout_bars,
                        "timestamp": ts
                    }
                    limit_bars_waiting = 0

            # ── 6. Record equity snapshot ────────────────────────────────────
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
                "ema": round(ema_res.ema, 2),
                "adx": round(adx_res.adx, 2) if adx_res else None,
                "atr_pct": round(atr_res.atr_pct, 4) if atr_res else None,
                "signal_action": signal.action,
                "regime_passed": signal.regime_passed
            })

        # ── 7. Force-close any open position at last bar close ───────────────
        if self.executor.position.side != PositionSide.NONE:
            last_row = df.iloc[-1]
            final_trade, capital = self.executor.close_current_position(
                exit_price=float(last_row["close"]),
                exit_timestamp=int(last_row["timestamp"]),
                exit_reason="End of Backtest Period – Forced Liquidation",
                current_capital=capital
            )
            equity_values.append(capital)

        summary = calculate_performance(
            trades=self.executor.trade_history,
            initial_capital=self.config.initial_capital,
            equity_curve=equity_values
        )

        start_date = pd.to_datetime(df["timestamp"].iloc[0], unit="s").strftime("%Y-%m-%d %H:%M")
        end_date = pd.to_datetime(df["timestamp"].iloc[-1], unit="s").strftime("%Y-%m-%d %H:%M")

        return BacktestResult(
            config=self.config,
            summary=summary,
            trades=self.executor.trade_history,
            equity_curve=equity_records,
            candle_count=len(df),
            start_date=start_date,
            end_date=end_date,
            regime_filter=self.config.regime_filter.value,
            execution_type=self.config.execution_type.value,
            timeframe=self.config.resolution
        )

    def _apply_market_slippage(self, signal: TradingSignal, close: float) -> float:
        """
        Applies directional slippage per signal type.
        LONG entry / REVERSE_TO_LONG → buy at close + slippage (worse fill going up)
        SHORT entry / REVERSE_TO_SHORT → buy at close - slippage (worse fill going down)
        Closes on opposite side: same logic.
        """
        s = self.config.slippage_pct
        tick = self.config.tick_size

        buy_signal_types = {
            SignalType.BUY_LONG,
            SignalType.REVERSE_TO_LONG,
        }
        sell_signal_types = {
            SignalType.SELL_SHORT,
            SignalType.REVERSE_TO_SHORT,
        }
        # For exits: LONG is closed via sell (gets lower), SHORT via buy (gets higher)
        exit_long_types = {SignalType.EXIT_LONG}
        exit_short_types = {SignalType.EXIT_SHORT}

        if signal.signal_type in buy_signal_types:
            raw = close * (1.0 + s)
        elif signal.signal_type in sell_signal_types:
            raw = close * (1.0 - s)
        elif signal.signal_type in exit_long_types:
            raw = close * (1.0 - s)   # Selling long: adverse fill = lower price
        elif signal.signal_type in exit_short_types:
            raw = close * (1.0 + s)   # Buying back short: adverse fill = higher price
        else:
            # REVERSE: determine direction from signal_type already set before this call
            # (should not reach here, but safe fallback)
            raw = close

        # Tick-size rounding
        return round(raw / tick, 0) * tick
