"""
Persistent SQLite Database Module.
Stores:
- Candles (OHLCV history)
- Signals generated (with EMA & SuperTrend status and decisions)
- Order executions and trade records
- Account balance and daily PnL snapshots
- System audit logs and circuit breaker events
"""

import sqlite3
import json
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from ..execution.strategy_executor import TradeRecord
from ..signals.signal_generator import TradingSignal


class DatabaseManager:
    def __init__(self, db_path: str = "delta_trading.db"):
        self.db_path = db_path
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # 1. Candles table
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS candles (
                timestamp INTEGER PRIMARY KEY,
                symbol TEXT NOT NULL,
                resolution TEXT NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """)

            # 2. Signals table
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                signal_type TEXT NOT NULL,
                price REAL NOT NULL,
                supertrend_val REAL NOT NULL,
                supertrend_direction TEXT NOT NULL,
                ema_val REAL NOT NULL,
                ema_slope REAL NOT NULL,
                ema_confirmed INTEGER NOT NULL,
                ema_method TEXT NOT NULL,
                position_at_signal TEXT NOT NULL,
                action TEXT NOT NULL,
                reason TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """)

            # 3. Trades table
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                trade_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                entry_timestamp INTEGER NOT NULL,
                exit_timestamp INTEGER NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL NOT NULL,
                contracts INTEGER NOT NULL,
                btc_size REAL NOT NULL,
                margin_used REAL NOT NULL,
                leverage REAL NOT NULL,
                gross_pnl REAL NOT NULL,
                entry_fee REAL NOT NULL,
                exit_fee REAL NOT NULL,
                funding_fee REAL NOT NULL,
                net_pnl REAL NOT NULL,
                return_pct REAL NOT NULL,
                exit_reason TEXT NOT NULL,
                ema_method_used TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """)

            # 4. Account balance / equity snapshot table
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS equity_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp INTEGER NOT NULL,
                capital REAL NOT NULL,
                unrealized_pnl REAL NOT NULL,
                total_equity REAL NOT NULL,
                used_margin REAL NOT NULL,
                position_side TEXT NOT NULL,
                position_size REAL NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """)

            # 5. Audit logs table
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                level TEXT NOT NULL,
                module TEXT NOT NULL,
                message TEXT NOT NULL,
                metadata TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """)

            conn.commit()

    def record_signal(self, symbol: str, signal: TradingSignal):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT INTO signals (
                timestamp, symbol, signal_type, price, supertrend_val,
                supertrend_direction, ema_val, ema_slope, ema_confirmed,
                ema_method, position_at_signal, action, reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                signal.timestamp,
                symbol,
                signal.signal_type.value,
                signal.price,
                signal.supertrend_val,
                signal.supertrend_direction,
                signal.ema_val,
                signal.ema_slope,
                1 if signal.ema_confirmed else 0,
                signal.ema_method_used.value,
                signal.position_at_signal.value,
                signal.action,
                signal.reason
            ))
            conn.commit()

    def record_trade(self, trade: TradeRecord):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT OR REPLACE INTO trades (
                trade_id, symbol, side, entry_timestamp, exit_timestamp,
                entry_price, exit_price, contracts, btc_size, margin_used,
                leverage, gross_pnl, entry_fee, exit_fee, funding_fee,
                net_pnl, return_pct, exit_reason, ema_method_used
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                trade.trade_id,
                trade.symbol,
                trade.side.value,
                trade.entry_timestamp,
                trade.exit_timestamp,
                trade.entry_price,
                trade.exit_price,
                trade.contracts,
                trade.btc_size,
                trade.margin_used,
                trade.leverage,
                trade.gross_pnl,
                trade.entry_fee,
                trade.exit_fee,
                trade.funding_fee,
                trade.net_pnl,
                trade.return_pct,
                trade.exit_reason,
                trade.ema_method_used
            ))
            conn.commit()

    def record_equity_snapshot(
        self,
        timestamp: int,
        capital: float,
        unrealized_pnl: float,
        total_equity: float,
        used_margin: float,
        position_side: str,
        position_size: float
    ):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT INTO equity_snapshots (
                timestamp, capital, unrealized_pnl, total_equity,
                used_margin, position_side, position_size
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (timestamp, capital, unrealized_pnl, total_equity, used_margin, position_side, position_size))
            conn.commit()

    def log_event(self, level: str, module: str, message: str, metadata: Optional[Dict[str, Any]] = None):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT INTO audit_logs (level, module, message, metadata)
            VALUES (?, ?, ?, ?)
            """, (level, module, message, json.dumps(metadata or {})))
            conn.commit()

    def get_recent_trades(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM trades ORDER BY exit_timestamp DESC LIMIT ?", (limit,))
            return [dict(row) for row in cursor.fetchall()]

    def get_recent_signals(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM signals ORDER BY timestamp DESC LIMIT ?", (limit,))
            return [dict(row) for row in cursor.fetchall()]

    def get_equity_history(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM equity_snapshots ORDER BY timestamp ASC LIMIT ?", (limit,))
            return [dict(row) for row in cursor.fetchall()]
