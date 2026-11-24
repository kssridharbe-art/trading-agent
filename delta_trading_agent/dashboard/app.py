"""
Trading Dashboard Web Application (Flask).
Serves real-time trading metrics, position management controls,
EMA confirmation method configuration, and backtest execution viewer.
"""

from flask import Flask, render_template, jsonify, request
from typing import Optional
import os
import json

from ..config.settings import CONFIG, TradingConfig, EMAMethod, PositionSide, TradingMode
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
        if "ema_method" in data:
            method_str = data["ema_method"]
            if method_str in [m.value for m in EMAMethod]:
                new_method = EMAMethod(method_str)
                trader.config.ema_method = new_method
                trader.signal_generator.set_ema_method(new_method)

        if "leverage" in data:
            try:
                lev = float(data["leverage"])
                if 1.0 <= lev <= 100.0:
                    trader.config.leverage = lev
            except ValueError:
                pass

        if "margin_allocation_pct" in data:
            try:
                pct = float(data["margin_allocation_pct"]) / 100.0 if float(data["margin_allocation_pct"]) > 1.0 else float(data["margin_allocation_pct"])
                if 0.1 <= pct <= 0.95:
                    trader.config.margin_allocation_pct = pct
                    trader.config.unallocated_cash_pct = 1.0 - pct
            except ValueError:
                pass

        return jsonify({
            "success": True,
            "ema_method": trader.config.ema_method.value,
            "leverage": trader.config.leverage,
            "margin_allocation_pct": trader.config.margin_allocation_pct
        })

    @app.route("/api/close_position", methods=["POST"])
    def close_position():
        res = trader.manual_close_position()
        return jsonify(res)

    @app.route("/api/backtest", methods=["POST"])
    def run_backtest_api():
        data = request.json or {}
        days = int(data.get("days", 14))
        method_str = data.get("ema_method", trader.config.ema_method.value)
        leverage = float(data.get("leverage", trader.config.leverage))

        cfg = TradingConfig(
            symbol=trader.config.symbol,
            initial_capital=trader.config.initial_capital,
            margin_allocation_pct=trader.config.margin_allocation_pct,
            unallocated_cash_pct=trader.config.unallocated_cash_pct,
            leverage=leverage,
            ema_method=EMAMethod(method_str)
        )

        try:
            df = BacktestEngine.fetch_historical_candles_from_delta(
                symbol=cfg.symbol,
                resolution="5",
                days=days,
                api_base=cfg.api_base_url
            )
            engine = BacktestEngine(cfg)
            res = engine.run(df)
            return jsonify({
                "success": True,
                "summary": res.summary.to_dict(),
                "trades": [t.__dict__ for t in res.trades[-50:]],
                "candle_count": res.candle_count,
                "start_date": res.start_date,
                "end_date": res.end_date,
                "equity_sample": res.equity_curve[::max(1, len(res.equity_curve) // 100)]
            })
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    return app
