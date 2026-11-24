"""
Trading System Configuration & Settings.
Loads parameters from environment variables with sensible defaults.
All parameters can also be updated dynamically at runtime via the dashboard.
"""

import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class EMAMethod(str, Enum):
    METHOD_A = "METHOD_A"  # Candle close above/below EMA 9
    METHOD_B = "METHOD_B"  # EMA 9 slope upward/downward
    METHOD_C = "METHOD_C"  # Both candle position and EMA slope


class TradingMode(str, Enum):
    BACKTEST = "BACKTEST"
    PAPER = "PAPER"
    LIVE = "LIVE"


class PositionSide(str, Enum):
    NONE = "NONE"
    LONG = "LONG"
    SHORT = "SHORT"


@dataclass
class TradingConfig:
    # Exchange & Environment
    exchange: str = "delta"
    api_base_url: str = os.getenv("DELTA_API_URL", "https://api.delta.exchange")
    ws_base_url: str = os.getenv("DELTA_WS_URL", "wss://socket.delta.exchange")
    api_key: str = os.getenv("DELTA_API_KEY", "")
    api_secret: str = os.getenv("DELTA_API_SECRET", "")
    
    # Instrument & Timeframe
    symbol: str = os.getenv("TRADING_SYMBOL", "BTCUSDT")  # Delta Exchange perpetual
    resolution: str = "5m"  # 5 minutes
    timeframe_seconds: int = 300
    
    # Indicators
    supertrend_length: int = 10
    supertrend_factor: float = 3.0
    ema_period: int = 9
    ema_method: EMAMethod = EMAMethod.METHOD_C  # Default: Method C (Close + Slope confirmation)
    
    # Capital & Risk Management
    initial_capital: float = 10000.0  # USD / USDT
    margin_allocation_pct: float = 0.70  # 70% max margin allocation per position
    unallocated_cash_pct: float = 0.30  # 30% unallocated capital
    leverage: float = 10.0  # Default 10x leverage (configurable 1x - 100x)
    max_daily_loss_pct: float = 0.05  # 5% max daily loss limit
    max_consecutive_losses: int = 3  # Circuit breaker after N consecutive losses
    
    # Fees & Execution assumptions (Delta Exchange standard rates)
    maker_fee_rate: float = 0.0002  # 0.02%
    taker_fee_rate: float = 0.0005  # 0.05%
    slippage_pct: float = 0.0002    # 0.02% estimated slippage
    est_funding_fee_8h: float = 0.0001 # 0.01% per 8 hours
    
    # Operating Mode
    mode: TradingMode = TradingMode.PAPER
    live_trading_authorized: bool = False  # Strict safety flag: MUST be explicitly confirmed
    
    # Database
    db_path: str = os.getenv("DB_PATH", "delta_trading.db")


# Default global configuration instance
CONFIG = TradingConfig()
