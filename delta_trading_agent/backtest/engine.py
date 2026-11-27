"""
Historical Backtesting Engine for BTCUSD / BTCUSDT Perpetual on Delta Exchange.
Phase 2.1 Audit Corrected:
- Strict P&L reconciliation: Gross PnL - Total Fees & Funding = Net PnL.
- Discrete modeling of Exit orders vs Entry orders.
- Realistic Limit execution: touch validation, unfilled entry cancellations, timeout taker exits.
- Maker fees (0.02%) applied exclusively to confirmed maker fills.
- Multi-timeframe support: 5m, 15m, 1h.
- Per-bar unrealized drawdown monitoring.
- Zero lookahead bias.
- Live trading strictly disabled (live_trading_authorized = False).
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
from ..execution.execution_model import ExecutionModel, OrderFillResult
from .performance import calculate_performance, PerformanceSummary


@dataclass
class BacktestResult:
    config: TradingConfig
    summary: PerformanceSummary
    trades: List[TradeRecord]
    equity_curve: List[Dict[str, Any]]
    signals_log: List[Dict[str, Any]]
    candle_count: int
    start_date: str
    end_date: str
    regime_filter: str
    execution_type: str
    timeframe: str


_RESOLUTION_SECONDS: Dict[str, int] = {
    "5": 300,
    "15": 900,
    "60": 3600,
}


class BacktestEngine:
    def __init__(self, config: Optional[TradingConfig] = None):
        self.config = config or TradingConfig()
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
        """Fetches historical candles from Delta Exchange chart/history API."""
        now = int(time.time())
        from_time = now - (days * 86400)
        all_candles = []
        bar_secs = _RESOLUTION_SECONDS.get(resolution, 300)
        chunk_seconds = bar_secs * 1000

        cur_to = now
        label = {"5": "5-minute", "15": "15-minute", "60": "1-hour"}.get(resolution, resolution)
        print(f"Fetching {days} days of {symbol} {label} candles from Delta Exchange...")

        while cur_to > from_time:
            cur_from = max(cur_to - chunk_seconds, from_time)
            url = f"{api_base}/v2/chart/history?symbol={symbol}&resolution={resolution}&from={cur_from}&to={cur_to}"
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
            raise RuntimeError(f"Failed to fetch historical candles for {symbol} (resolution={resolution})")

        df = pd.concat(all_candles, ignore_index=True)
        df.drop_duplicates(subset=["timestamp"], inplace=True)
        df.sort_values(by="timestamp", inplace=True)
        df.reset_index(drop=True, inplace=True)

        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df.dropna(subset=["close"], inplace=True)
        df.reset_index(drop=True, inplace=True)
        return df

    def run(self, df: pd.DataFrame) -> BacktestResult:
        """Runs the bar-by-bar backtest across completed candles."""
        self._init_components()

        capital = self.config.initial_capital
        equity_records: List[Dict[str, Any]] = []
        equity_values: List[float] = [capital]
        signals_log: List[Dict[str, Any]] = []

        pending_exit: Optional[Dict[str, Any]] = None
        pending_entry: Optional[Dict[str, Any]] = None

        for i, row in df.iterrows():
            ts = int(row["timestamp"])
            high = float(row["high"])
            low = float(row["low"])
            close = float(row["close"])

            # ── 1. Process Pending Limit Exit (Priority: must close existing pos first) ──
            if pending_exit is not None:
                fill_res = self.execution_model.check_limit_exit_fill(
                    is_closing_long=pending_exit["is_closing_long"],
                    limit_price=pending_exit["limit_price"],
                    bar_high=high,
                    bar_low=low,
                    bar_close=close,
                    bars_waiting=pending_exit["bars_waiting"]
                )
                if fill_res.filled:
                    trade_record, capital = self.executor.close_current_position(
                        exit_price=fill_res.fill_price,
                        exit_timestamp=ts,
                        exit_reason=pending_exit["reason"],
                        current_capital=capital,
                        fee_rate=fill_res.fee_rate,
                        fill_type=fill_res.fill_type
                    )
                    self.risk_manager.record_trade_result(trade_record.net_pnl, capital, ts)

                    # If this exit was the first leg of a reversal, now activate the followup entry
                    if pending_exit.get("followup_entry"):
                        f_entry = pending_exit["followup_entry"]
                        f_entry["limit_price"] = self.execution_model.calc_entry_limit_price(
                            is_buy=f_entry["is_buy"], close=close
                        )
                        f_entry["bars_waiting"] = 0
                        f_entry["placed_ts"] = ts
                        pending_entry = f_entry

                    pending_exit = None
                else:
                    pending_exit["bars_waiting"] += 1

            # ── 2. Process Pending Limit Entry (Only when flat) ─────────────────────────
            elif pending_entry is not None and self.executor.position.side == PositionSide.NONE:
                fill_res = self.execution_model.check_limit_entry_fill(
                    is_buy=pending_entry["is_buy"],
                    limit_price=pending_entry["limit_price"],
                    bar_high=high,
                    bar_low=low,
                    bar_close=close,
                    bars_waiting=pending_entry["bars_waiting"]
                )
                if fill_res.filled:
                    sizing = self.risk_manager.calculate_position_size(
                        capital, fill_res.fill_price, pending_entry["side"], ts
                    )
                    if sizing.allowed:
                        self.executor.open_position(
                            side=pending_entry["side"],
                            price=fill_res.fill_price,
                            timestamp=ts,
                            sizing=sizing,
                            fill_fraction=fill_res.fill_fraction,
                            fill_type=fill_res.fill_type,
                            fee_rate=fill_res.fee_rate
                        )
                    pending_entry = None
                elif fill_res.cancelled:
                    # Unfilled entry timed out and cancelled! Remain flat in cash
                    signals_log.append({
                        "timestamp": ts,
                        "action": "CANCELLED_UNFILLED",
                        "side": pending_entry["side"].value,
                        "limit_price": pending_entry["limit_price"],
                        "reason": "Entry limit order timed out without price touch; cancelled"
                    })
                    pending_entry = None
                else:
                    pending_entry["bars_waiting"] += 1

            # ── 3. Update Indicators on Completed Candle (Zero Lookahead) ───────────────
            st_res = self.supertrend.update(high, low, close)
            ema_res = self.ema.update(close)
            adx_res = self.adx.update(high, low, close)
            atr_res = self.atr.update(high, low, close)

            if st_res is None or ema_res is None:
                continue

            # ── 4. Per-Bar Floating Drawdown Check ──────────────────────────────────────
            self.executor.update_market_price(close)
            unrealized = self.executor.position.unrealized_pnl
            self.risk_manager.update_equity_and_check_drawdown(capital, unrealized, ts)

            # ── 5. Evaluate Completed Candle Signal ─────────────────────────────────────
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

            # ── 6. Process Evaluated Signal ─────────────────────────────────────────────
            if signal.action not in ("HOLD",) and signal.signal_type != SignalType.NONE:
                sig_record = {
                    "timestamp": ts,
                    "signal_type": signal.signal_type.value,
                    "action": signal.action,
                    "price": close,
                    "supertrend_direction": signal.supertrend_direction,
                    "ema_confirmed": signal.ema_confirmed,
                    "regime_passed": signal.regime_passed,
                    "reason": signal.reason,
                    "execution_status": "PROCESSED"
                }

                # Case A: Market Execution Model
                if self.config.execution_type == ExecutionType.MARKET:
                    if signal.action == "OPEN_LONG":
                        fill = self.execution_model.execute_market_order(is_buy=True, close=close)
                        sizing = self.risk_manager.calculate_position_size(capital, fill.fill_price, PositionSide.LONG, ts)
                        if sizing.allowed:
                            self.executor.open_position(PositionSide.LONG, fill.fill_price, ts, sizing, fee_rate=fill.fee_rate, fill_type=fill.fill_type)
                            sig_record["execution_status"] = "FILLED_MARKET"
                        else:
                            sig_record["execution_status"] = f"REJECTED: {sizing.reason}"

                    elif signal.action == "OPEN_SHORT":
                        fill = self.execution_model.execute_market_order(is_buy=False, close=close)
                        sizing = self.risk_manager.calculate_position_size(capital, fill.fill_price, PositionSide.SHORT, ts)
                        if sizing.allowed:
                            self.executor.open_position(PositionSide.SHORT, fill.fill_price, ts, sizing, fee_rate=fill.fee_rate, fill_type=fill.fill_type)
                            sig_record["execution_status"] = "FILLED_MARKET"
                        else:
                            sig_record["execution_status"] = f"REJECTED: {sizing.reason}"

                    elif signal.action in ("CLOSE_LONG", "CLOSE_SHORT"):
                        is_closing_long = (signal.action == "CLOSE_LONG")
                        fill = self.execution_model.execute_market_order(is_buy=(not is_closing_long), close=close)
                        trade, capital = self.executor.close_current_position(
                            exit_price=fill.fill_price, exit_timestamp=ts, exit_reason=signal.reason,
                            current_capital=capital, fee_rate=fill.fee_rate, fill_type=fill.fill_type
                        )
                        self.risk_manager.record_trade_result(trade.net_pnl, capital, ts)
                        sig_record["execution_status"] = "CLOSED_MARKET"

                    elif signal.action == "REVERSE":
                        # Reversal: close existing first -> confirm flat -> open reverse
                        is_closing_long = (self.executor.position.side == PositionSide.LONG)
                        fill_exit = self.execution_model.execute_market_order(is_buy=(not is_closing_long), close=close)
                        trade, capital = self.executor.close_current_position(
                            exit_price=fill_exit.fill_price, exit_timestamp=ts, exit_reason=signal.reason,
                            current_capital=capital, fee_rate=fill_exit.fee_rate, fill_type=fill_exit.fill_type
                        )
                        self.risk_manager.record_trade_result(trade.net_pnl, capital, ts)

                        new_side = PositionSide.SHORT if is_closing_long else PositionSide.LONG
                        fill_entry = self.execution_model.execute_market_order(is_buy=(new_side == PositionSide.LONG), close=close)
                        sizing = self.risk_manager.calculate_position_size(capital, fill_entry.fill_price, new_side, ts)
                        if sizing.allowed:
                            self.executor.open_position(new_side, fill_entry.fill_price, ts, sizing, fee_rate=fill_entry.fee_rate, fill_type=fill_entry.fill_type)
                            sig_record["execution_status"] = "REVERSED_MARKET"
                        else:
                            sig_record["execution_status"] = f"REVERSE_ENTRY_BLOCKED: {sizing.reason}"

                # Case B: Post-Only Limit Execution Model
                elif self.config.execution_type == ExecutionType.POST_ONLY_LIMIT:
                    if signal.action == "OPEN_LONG":
                        limit_p = self.execution_model.calc_entry_limit_price(is_buy=True, close=close)
                        pending_entry = {"is_buy": True, "side": PositionSide.LONG, "limit_price": limit_p, "bars_waiting": 0, "ts": ts}
                        sig_record["execution_status"] = f"PLACED_LIMIT_ENTRY @ {limit_p}"

                    elif signal.action == "OPEN_SHORT":
                        limit_p = self.execution_model.calc_entry_limit_price(is_buy=False, close=close)
                        pending_entry = {"is_buy": False, "side": PositionSide.SHORT, "limit_price": limit_p, "bars_waiting": 0, "ts": ts}
                        sig_record["execution_status"] = f"PLACED_LIMIT_ENTRY @ {limit_p}"

                    elif signal.action in ("CLOSE_LONG", "CLOSE_SHORT"):
                        is_closing_long = (signal.action == "CLOSE_LONG")
                        limit_exit_p = self.execution_model.calc_exit_limit_price(is_closing_long=is_closing_long, close=close)
                        pending_exit = {"is_closing_long": is_closing_long, "limit_price": limit_exit_p, "reason": signal.reason, "bars_waiting": 0, "ts": ts}
                        sig_record["execution_status"] = f"PLACED_LIMIT_EXIT @ {limit_exit_p}"

                    elif signal.action == "REVERSE":
                        # Exit order placed first; followup entry staged
                        is_closing_long = (self.executor.position.side == PositionSide.LONG)
                        limit_exit_p = self.execution_model.calc_exit_limit_price(is_closing_long=is_closing_long, close=close)
                        new_side = PositionSide.SHORT if is_closing_long else PositionSide.LONG
                        followup = {"is_buy": (new_side == PositionSide.LONG), "side": new_side, "limit_price": 0.0, "bars_waiting": 0, "ts": ts}
                        pending_exit = {
                            "is_closing_long": is_closing_long,
                            "limit_price": limit_exit_p,
                            "reason": signal.reason,
                            "bars_waiting": 0,
                            "ts": ts,
                            "followup_entry": followup
                        }
                        sig_record["execution_status"] = f"PLACED_LIMIT_REVERSAL_EXIT @ {limit_exit_p}"

                signals_log.append(sig_record)

            # ── 7. Record Equity Curve Snapshot ─────────────────────────────────────────
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

        # ── 8. Final Liquidation at Backtest End ────────────────────────────────────────
        if self.executor.position.side != PositionSide.NONE:
            last_row = df.iloc[-1]
            final_trade, capital = self.executor.close_current_position(
                exit_price=float(last_row["close"]),
                exit_timestamp=int(last_row["timestamp"]),
                exit_reason="End of Backtest Period – Final Liquidation",
                current_capital=capital,
                fee_rate=self.config.taker_fee_rate,
                fill_type="FINAL_LIQUIDATION"
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
            signals_log=signals_log,
            candle_count=len(df),
            start_date=start_date,
            end_date=end_date,
            regime_filter=self.config.regime_filter.value,
            execution_type=self.config.execution_type.value,
            timeframe=self.config.resolution
        )
