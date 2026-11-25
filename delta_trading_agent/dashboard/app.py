"""
Trading Dashboard Web Application (Flask) – Phase 2 Enhanced.
Serves:
- Real-time trading metrics and active position status
- Configuration selector for Timeframe (5m, 15m, 1h), Regime Filter (NONE, ADX, ATR, BOTH),
  Execution Type (MARKET, POST_ONLY_LIMIT), and EMA confirmation methods (A, B, C)
- Comparative historical backtest analyzer with side-by-side baseline comparison
- Full Phase 2 comparative performance matrix viewer
"""

from flask import Flask, render_template, jsonify, request
from typing import Optional, Dict, Any
import os
import json
import pandas as pd

from ..config.settings import (
    CONFIG, TradingConfig, EMAMethod, PositionSide, TradingMode,
    RegimeFilterType, ExecutionType
)
from ..paper.paper_trader import PaperTrader
from ..storage.database import DatabaseManager
from ..backtest.engine import BacktestEngine


def create_app(paper_trader: Optional[PaperTrader] = None, db: Optional[DatabaseManager] = None) -> Flask:
    template_dir = os.path.join(os.path.dirname(__file__), "templates")
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    app = Flask(__name__, template_folder=template_dir, static_folder=static_dir)

    trader = paper_trader or PaperTrader(CONFIG)
    database = db or DatabaseManager(CONFIG.db_path)

    @app.route("/")
    def index():
        return render_template("index.html", config=trader.config)

    @app.route("/api/state", methods=["GET"])
    def get_state():
        state = trader.get_dashboard_state()
        return jsonify(state)

    @app.route("/api/config", methods=["POST"])
    def update_config():
        data = request.json or {}

        # 1. EMA Method
        if "ema_method" in data:
            method_str = data["ema_method"]
            if method_str in [m.value for m in EMAMethod]:
                new_method = EMAMethod(method_str)
                trader.config.ema_method = new_method
                trader.signal_generator.set_ema_method(new_method)

        # 2. Leverage
        if "leverage" in data:
            try:
                lev = float(data["leverage"])
                if 1.0 <= lev <= 100.0:
                    trader.config.leverage = lev
            except ValueError:
                pass

        # 3. Margin Allocation %
        if "margin_allocation_pct" in data:
            try:
                pct = float(data["margin_allocation_pct"])
                pct = pct / 100.0 if pct > 1.0 else pct
                if 0.1 <= pct <= 0.95:
                    trader.config.margin_allocation_pct = pct
                    trader.config.unallocated_cash_pct = 1.0 - pct
            except ValueError:
                pass

        # 4. Regime Filter (Phase 2)
        if "regime_filter" in data:
            rf_str = data["regime_filter"]
            if rf_str in [r.value for r in RegimeFilterType]:
                new_rf = RegimeFilterType(rf_str)
                trader.config.regime_filter = new_rf
                trader.signal_generator.set_regime_filter(new_rf)

        # 5. Execution Type (Phase 2)
        if "execution_type" in data:
            ex_str = data["execution_type"]
            if ex_str in [e.value for e in ExecutionType]:
                trader.config.execution_type = ExecutionType(ex_str)

        # 6. Thresholds (Phase 2)
        if "adx_threshold" in data:
            try:
                trader.config.adx_threshold = float(data["adx_threshold"])
            except ValueError:
                pass

        if "atr_min_pct" in data:
            try:
                trader.config.atr_min_pct = float(data["atr_min_pct"])
            except ValueError:
                pass

        # 7. Timeframe
        if "resolution" in data:
            trader.config.resolution = str(data["resolution"])

        return jsonify({
            "success": True,
            "ema_method": trader.config.ema_method.value,
            "leverage": trader.config.leverage,
            "margin_allocation_pct": trader.config.margin_allocation_pct,
            "regime_filter": trader.config.regime_filter.value,
            "execution_type": trader.config.execution_type.value,
            "adx_threshold": trader.config.adx_threshold,
            "atr_min_pct": trader.config.atr_min_pct,
            "resolution": trader.config.resolution
        })

    @app.route("/api/close_position", methods=["POST"])
    def close_position():
        res = trader.manual_close_position()
        return jsonify(res)

    @app.route("/api/phase2/configurations", methods=["GET"])
    def get_phase2_configurations():
        return jsonify({
            "timeframes": ["5m", "15m", "1h"],
            "regime_filters": [r.value for r in RegimeFilterType],
            "execution_types": [e.value for e in ExecutionType],
            "ema_methods": [m.value for m in EMAMethod],
            "current_config": {
                "resolution": trader.config.resolution,
                "ema_method": trader.config.ema_method.value,
                "regime_filter": trader.config.regime_filter.value,
                "execution_type": trader.config.execution_type.value,
                "adx_threshold": trader.config.adx_threshold,
                "atr_min_pct": trader.config.atr_min_pct,
                "leverage": trader.config.leverage,
                "margin_allocation_pct": trader.config.margin_allocation_pct
            }
        })

    @app.route("/api/phase2/comparison_results", methods=["GET"])
    def get_comparison_results():
        csv_path = "phase2_comparison_report.csv"
        if not os.path.exists(csv_path):
            return jsonify({"available": False, "rows": []})

        try:
            df = pd.read_csv(csv_path)
            # Fill NaNs
            df = df.fillna(0)
            rows = df.to_dict(orient="records")
            return jsonify({"available": True, "rows": rows, "count": len(rows)})
        except Exception as e:
            return jsonify({"available": False, "error": str(e), "rows": []})

    @app.route("/api/backtest", methods=["POST"])
    def run_backtest_api():
        data = request.json or {}
        days = int(data.get("days", 14))
        method_str = data.get("ema_method", trader.config.ema_method.value)
        tf = str(data.get("timeframe", "5m"))
        rf_str = data.get("regime_filter", "NONE")
        ex_str = data.get("execution_type", "MARKET")
        adx_thresh = float(data.get("adx_threshold", 20.0))
        atr_thresh = float(data.get("atr_min_pct", 0.15))
        leverage = float(data.get("leverage", trader.config.leverage))
        compare_baseline = bool(data.get("compare_baseline", True))

        res_code_map = {"5m": "5", "15m": "15", "1h": "60"}
        res_code = res_code_map.get(tf, "5")

        cfg = TradingConfig(
            symbol=trader.config.symbol,
            resolution=tf,
            initial_capital=trader.config.initial_capital,
            margin_allocation_pct=trader.config.margin_allocation_pct,
            unallocated_cash_pct=trader.config.unallocated_cash_pct,
            leverage=leverage,
            ema_method=EMAMethod(method_str),
            regime_filter=RegimeFilterType(rf_str),
            execution_type=ExecutionType(ex_str),
            adx_threshold=adx_thresh,
            atr_min_pct=atr_thresh,
            live_trading_authorized=False
        )

        try:
            # Check cached candles first to speed up UI response
            cache_file = os.path.join(os.path.dirname(__file__), "..", "..", "data", f"candles_BTCUSDT_{tf}.csv")
            if os.path.exists(cache_file):
                df = pd.read_csv(cache_file)
            else:
                df = BacktestEngine.fetch_historical_candles_from_delta(
                    symbol=cfg.symbol,
                    resolution=res_code,
                    days=days,
                    api_base=cfg.api_base_url
                )

            # Limit to requested days if df has more
            bar_secs = {"5": 300, "15": 900, "60": 3600}.get(res_code, 300)
            max_bars = int(days * 86400 / bar_secs)
            if len(df) > max_bars:
                df = df.iloc[-max_bars:].reset_index(drop=True)

            engine = BacktestEngine(cfg)
            res = engine.run(df)

            response_data: Dict[str, Any] = {
                "success": True,
                "summary": res.summary.to_dict(),
                "trades": [t.__dict__ for t in res.trades[-50:]],
                "candle_count": res.candle_count,
                "start_date": res.start_date,
                "end_date": res.end_date,
                "equity_sample": res.equity_curve[::max(1, len(res.equity_curve) // 100)]
            }

            # Optional: Run baseline configuration on the exact same dataset for comparison
            if compare_baseline and (rf_str != "NONE" or ex_str != "MARKET" or tf != "5m"):
                baseline_cfg = TradingConfig(
                    symbol=trader.config.symbol,
                    resolution=tf,
                    initial_capital=trader.config.initial_capital,
                    margin_allocation_pct=trader.config.margin_allocation_pct,
                    unallocated_cash_pct=trader.config.unallocated_cash_pct,
                    leverage=leverage,
                    ema_method=EMAMethod.METHOD_C,
                    regime_filter=RegimeFilterType.NONE,
                    execution_type=ExecutionType.MARKET,
                    live_trading_authorized=False
                )
                b_engine = BacktestEngine(baseline_cfg)
                b_res = b_engine.run(df)
                response_data["baseline"] = {
                    "summary": b_res.summary.to_dict(),
                    "equity_sample": b_res.equity_curve[::max(1, len(b_res.equity_curve) // 100)]
                }

            return jsonify(response_data)
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    return app
