# trading-agent

# DELTA EXCHANGE – BTCUSD AUTOMATED TRADING AGENT
## Modular Quantitative Trading System & Execution Engine

Automated algorithmic trading application for **BTCUSD / BTCUSDT Perpetual Contracts on Delta Exchange** using **SuperTrend (10, 3)** and **EMA 9 (Method C)** with strict 70% margin allocation, 30% unallocated cash buffer, and dual-layer risk circuit breakers.

---

### 1. Strategy Overview

- **Asset**: BTCUSDT Linear Perpetual (`contract_value = 0.001 BTC`, `tick_size = 0.5 USDT`)
- **Timeframe**: 5-Minute Candles (Completed bars only)
- **Primary Trend & Exit Driver**: SuperTrend (Length: 10, Multiplier: 3.0)
- **Confirmation Filter**: EMA 9 (Method C: Both Candle Close & Slope Confirmation)
- **Capital Allocation**: 70% Max Margin Allocation per trade (30% Cash Buffer)
- **Leverage**: 10x Isolated (Configurable 1x–100x)

#### Rules of Engagement
- **LONG Entry**: Completed 5m candle confirms SuperTrend reversal from RED to GREEN + Close > EMA 9 + EMA 9 higher than previous candle ($Slope > 0$).
- **SHORT Entry**: Completed 5m candle confirms SuperTrend reversal from GREEN to RED + Close < EMA 9 + EMA 9 lower than previous candle ($Slope < 0$).
- **Exit & Reversal**:
  - Close existing position first on opposite SuperTrend signal.
  - Verify exchange position is completely flat.
  - Check EMA 9 confirmation before opening the opposite position. If EMA fails, remain flat in Cash.

---

### 2. Project Architecture

```
trading-agent/
├── delta_trading_agent/
│   ├── config/          # Central configuration & parameters (settings.py)
│   ├── indicators/      # SuperTrend (10,3) & EMA 9 engines (supertrend.py, ema.py)
│   ├── signals/         # Reversal & EMA confirmation state machine (signal_generator.py)
│   ├── risk/            # 70% margin, leverage, 5% daily loss & 3-loss breaker (risk_manager.py)
│   ├── execution/       # Position lifecycle & reversal sequencing (strategy_executor.py)
│   ├── exchange/        # Delta REST client (HMAC-SHA256) & WebSocket ticker/candle feed
│   ├── backtest/        # Historical bar-by-bar backtest engine & metrics reporter
│   ├── paper/           # Simulated live execution engine on real-time data
│   ├── storage/         # SQLite persistent database (database.py)
│   ├── dashboard/       # Dark terminal web UI & Flask REST server
│   └── tests/           # Full unit test suite (14 passing tests)
├── run_backtest.py      # CLI backtesting runner
├── run_dashboard.py     # CLI web dashboard runner (port 5000)
├── run_paper.py         # CLI paper trading runner
├── run_full_audit.py    # Independent strategy & financial audit suite
├── validate_ema_methods.py # EMA Methods A/B/C comparative validator
└── .env.example         # Example API credentials
```

---

### 3. Quickstart Guide

#### 1. Run Unit Tests (14/14 Passing)
```bash
python -m pytest delta_trading_agent/tests
```

#### 2. Run Historical Backtest on Delta Exchange Candles
```bash
python run_backtest.py --days 30 --method METHOD_C --leverage 10
```

#### 3. Launch Live Web Trading Dashboard
```bash
python run_dashboard.py --port 5000
```
Open **`http://localhost:5000`** in your browser to view the real-time Account, Market, and Position cards, trade history, emergency close controls, and interactive backtest simulator.

#### 4. Run Full Strategy Audit Suite
```bash
python run_full_audit.py
```
Outputs:
- `signal_reconciliation_audit.csv`: Complete bar-by-bar audit of all 576 reversals.
- `backtest_trade_log_audit.csv`: Complete financial trade-by-trade audit with fees, slippage, and gross/net P&L.

---

### 4. Risk Disclosures & Operational Notice
- **Live Trading Safety**: Live execution is disabled by default (`live_trading_authorized = False`). Live execution requires explicit API credentials and user authorization.
- **Transaction Costs**: On a 5-minute chart, taker fees (0.05%) and slippage (0.02%) represent significant transaction drag. Higher timeframes (15m, 1h) or maker limit orders are recommended for lower turnover.
