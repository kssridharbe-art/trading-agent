# DELTA EXCHANGE – BTCUSD AUTOMATED TRADING AGENT
## Quantitative Architecture & Operational Guide

### 1. Executive Summary & Strategy Overview
This system is an enterprise-grade algorithmic trading application designed specifically for **BTCUSD / BTCUSDT Perpetual Contracts on Delta Exchange**.

The core trading strategy is governed by:
- **Primary Trend & Exit Driver**: **SuperTrend (Length: 10, Factor: 3)**
- **Directional Trend Confirmation Filter**: **Exponential Moving Average (Period: 9)**
- **Chart Timeframe**: **5-Minute Candles**
- **Capital Allocation Rule**: **70% Maximum Margin Allocation** per trade with **30% Unallocated Cash Buffer**

---

### 2. Core Trading Rules

#### Buy / Long Entry Rules
A **LONG** entry signal is generated strictly when:
1. A completed 5-minute candle confirms a SuperTrend reversal from **RED to GREEN**.
2. **EMA 9** confirms the bullish direction based on the user-selected method.
3. All entry conditions are satisfied at candle close.
4. No existing LONG position is already open.
5. Sizing is computed strictly under the 70% margin allocation limit.

#### Sell / Short Entry Rules
A **SHORT** entry signal is generated strictly when:
1. A completed 5-minute candle confirms a SuperTrend reversal from **GREEN to RED**.
2. **EMA 9** confirms the bearish direction based on the user-selected method.
3. All entry conditions are satisfied at candle close.
4. No existing SHORT position is already open.
5. Sizing is computed strictly under the 70% margin allocation limit.

#### Exit and Automatic Reversal (Core Mechanism)
- **Holding LONG**:
  - Continue holding while SuperTrend remains **GREEN**.
  - When SuperTrend changes **GREEN $\to$ RED** on a completed 5-minute candle, **completely close** the existing LONG position.
  - After verifying the position is flat, check EMA 9.
  - If EMA 9 confirms the bearish direction, open a **SHORT** position.
  - If EMA 9 confirmation fails, **remain in CASH buffer** and wait for the next reversal signal.
- **Holding SHORT**:
  - Continue holding while SuperTrend remains **RED**.
  - When SuperTrend changes **RED $\to$ GREEN** on a completed 5-minute candle, **completely close** the existing SHORT position.
  - After verifying the position is flat, check EMA 9.
  - If EMA 9 confirms the bullish direction, open a **LONG** position.
  - If EMA 9 confirmation fails, **remain in CASH buffer** and wait for the next reversal signal.

> [!IMPORTANT]
> The opposite SuperTrend signal is the strategy's primary exit trigger. The SuperTrend line is **not** used as a fixed-price stop-loss.

---

### 3. EMA 9 Confirmation Options

The system implements the 3 required selectable methods:
- **Method A**: Candle close above EMA 9 for LONG, and below EMA 9 for SHORT ($Close_t > EMA_t$ or $Close_t < EMA_t$).
- **Method B**: EMA 9 slope upward for LONG, downward for SHORT ($EMA_t > EMA_{t-1}$ or $EMA_t < EMA_{t-1}$).
- **Method C**: Both candle position and slope must confirm ($Close_t > EMA_t \land EMA_t > EMA_{t-1}$ for LONG; $Close_t < EMA_t \land EMA_t < EMA_{t-1}$ for SHORT).

These can be switched at any time via configuration or the real-time Dashboard UI.

---

### 4. Capital & Risk Management Engineering

- **70% Margin Allocation**: Maximum margin committed to any trade is:
  $$\text{Allocated Margin} = \text{Total Available Capital} \times 0.70$$
- **30% Unallocated Buffer**: Preserves capital against funding fees, slippage, and adverse liquidation shocks.
- **Contract Sizing**: On Delta Exchange, 1 contract = 0.001 BTC:
  $$\text{Contracts} = \left\lfloor \frac{\text{Allocated Margin} \times \text{Leverage}}{\text{Price} \times 0.001} \right\rfloor$$
- **Circuit Breakers**:
  - Maximum daily loss limit (default: 5% of starting day capital).
  - Maximum consecutive losing trades (default: 3 trades).
  - Once triggered, all further trade sizing is blocked until the risk condition clears.
- **Reconciliation & Duplicate Protection**:
  - Checks candle timestamps to prevent duplicate signals on the same bar.
  - Reconciles exchange position state before order submission.

---

### 5. Project Layout

```
Local_AI_Agent/
├── delta_trading_agent/
│   ├── config/
│   │   ├── __init__.py
│   │   └── settings.py          # Centralized configuration dataclass & env loader
│   ├── indicators/
│   │   ├── __init__.py
│   │   ├── supertrend.py        # SuperTrend (10, 3) batch & streaming calculation
│   │   └── ema.py               # EMA 9 calculation & slope analyzer
│   ├── signals/
│   │   ├── __init__.py
│   │   └── signal_generator.py  # Reversal & EMA confirmation state machine
│   ├── risk/
│   │   ├── __init__.py
│   │   └── risk_manager.py      # 70% margin, leverage, circuit breakers, liquidation guard
│   ├── execution/
│   │   ├── __init__.py
│   │   └── strategy_executor.py # Reversal state transitions, sequencing, and fee tracking
│   ├── exchange/
│   │   ├── __init__.py
│   │   ├── delta_client.py      # Delta REST API client with HMAC-SHA256 authentication
│   │   └── websocket_feed.py    # Delta WebSocket client with heartbeat & auto-reconnect
│   ├── backtest/
│   │   ├── __init__.py
│   │   ├── engine.py            # Historical backtesting engine with Delta candle fetcher
│   │   └── performance.py       # Metrics: Win Rate, Sharpe, Max Drawdown, Profit Factor
│   ├── paper/
│   │   ├── __init__.py
│   │   └── paper_trader.py      # Live paper trading engine on real-time Delta data
│   ├── storage/
│   │   ├── __init__.py
│   │   └── database.py          # SQLite database for candles, signals, trades & logs
│   ├── dashboard/
│   │   ├── __init__.py
│   │   ├── app.py               # Flask REST server & API endpoints
│   │   ├── templates/
│   │   │   └── index.html       # Terminal-style dark trading UI
│   │   └── static/
│   │       ├── css/style.css    # High-performance CSS styling
│   │       └── js/dashboard.js  # Live UI updater & backtest chart viewer
│   └── tests/
│       ├── test_indicators.py   # Parity & mathematical precision tests
│       ├── test_signals.py      # Reversal & EMA confirmation tests
│       ├── test_risk_manager.py # Sizing & circuit breaker tests
│       └── test_strategy.py     # Reversal execution sequence tests
├── run_backtest.py              # CLI backtesting runner
├── run_paper.py                 # CLI paper trading runner
├── run_dashboard.py             # CLI web dashboard runner (port 5000)
└── .env.example                 # Example credentials & settings
```

---

### 6. Quickstart Commands

#### 1. Run Unit Tests (100% Passing)
```bash
python -m pytest delta_trading_agent/tests
```

#### 2. Run Historical Backtest
Download real historical candles directly from Delta Exchange and evaluate performance:
```bash
# 14 days backtest with Method A
python run_backtest.py --days 14 --method METHOD_A --leverage 10

# 30 days backtest with Method C
python run_backtest.py --days 30 --method METHOD_C --leverage 5
```

#### 3. Launch Real-Time Trading Dashboard
```bash
python run_dashboard.py --port 5000
```
Open **`http://127.0.0.1:5000`** in your browser to view:
- Live Account & Margin overview (Capital, Equity, Margin used, Buffer)
- Market & Indicator overview (SuperTrend direction, EMA 9 slope, Live BTC price)
- Active Position Status (Side, Size, Entry, Liquidation, PnL)
- Interactive Strategy Controls (EMA Method Selector, Leverage, Emergency Close)
- Trade History & In-Dashboard Backtest Simulator
