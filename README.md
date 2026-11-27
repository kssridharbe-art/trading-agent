# DELTA EXCHANGE – BTCUSD AUTOMATED TRADING AGENT
## Modular Quantitative Trading System, Execution Engine & Risk-Hardened Architecture

Automated algorithmic trading application for **BTCUSD / BTCUSDT Perpetual Contracts on Delta Exchange** using **SuperTrend (10, 3)** and **EMA 9 (Method C)** with strict margin allocation, cash buffering, multi-timeframe analysis (5m, 15m, 1h), selectable market regime filters (ADX, ATR), post-only limit execution simulation, and dual-layer risk circuit breakers persisted to SQLite.

---

### 1. Strategy Overview

- **Asset**: BTCUSDT Linear Perpetual (`contract_value = 0.001 BTC`, `tick_size = 0.5 USDT`)
- **Timeframes Supported**: 5-Minute, 15-Minute, 1-Hour Candles (Completed bars only)
- **Primary Trend & Exit Driver**: SuperTrend (Length: 10, Multiplier: 3.0)
- **Confirmation Filter**: EMA 9 (Method C: Both Candle Close & Slope Confirmation)
- **Market Regime Filters**:
  - **ADX Filter**: Minimum threshold (default: 20.0, 14-period)
  - **ATR Volatility Filter**: Minimum ATR% threshold (default: 0.15% of close, 14-period)
  - **Combined / Baseline**: Selectable independently (`NONE`, `ADX_ONLY`, `ATR_ONLY`, `BOTH`)
- **Execution Simulation Models**:
  - **MARKET**: Taker order execution (0.05% taker fee + 0.02% slippage)
  - **POST_ONLY_LIMIT**: 0.01% depth offset, maker fill on touch (0.02% maker fee), partial fills (70–90%), timeout cancellation for entry orders, timeout taker liquidation for exit orders
- **Capital Allocation**: Configurable margin allocation per trade (10%, 20%, 30%, 40%, 70%) with unallocated cash buffer
- **Leverage**: 10x Isolated (Configurable 1x–100x)

#### Rules of Engagement
- **LONG Entry**: Completed candle confirms SuperTrend reversal from RED to GREEN + Close > EMA 9 + EMA 9 higher than previous candle ($Slope > 0$) + Regime Filter passed.
- **SHORT Entry**: Completed candle confirms SuperTrend reversal from GREEN to RED + Close < EMA 9 + EMA 9 lower than previous candle ($Slope < 0$) + Regime Filter passed.
- **Exit & Reversal**:
  - Close existing position first on opposite SuperTrend signal unconditionally (bypassing regime filters).
  - Verify exchange position is completely flat before placing entry order.
  - Check EMA 9 confirmation and regime filter before opening opposite position. If confirmation fails, remain flat in Cash.

---

### 2. Project Architecture

```
trading-agent/
├── delta_trading_agent/
│   ├── config/          # Central configuration & parameters (settings.py, presets)
│   ├── indicators/      # SuperTrend (10,3), EMA 9, ADX (14), ATR (14) batch & stateful engines
│   ├── signals/         # Reversal, EMA confirmation & regime filter state machine
│   ├── risk/            # Margin sizing, leverage, 5% daily loss (inc. floating), 3-loss breaker, SQLite persistence
│   ├── execution/       # Position lifecycle, reversal sequencing, MAE tracking, maker/taker execution models
│   ├── exchange/        # Delta REST client (HMAC-SHA256) & WebSocket ticker/candle feed
│   ├── backtest/        # Bar-by-bar backtest engine & performance reconciliation analyzer
│   ├── paper/           # Simulated live execution engine on real-time data
│   ├── storage/         # SQLite persistent database (database.py)
│   ├── dashboard/       # Dark terminal web UI & Flask REST server
│   └── tests/           # Full unit test suite (33 passing tests)
├── data/                # Cached historical candle data (5m, 15m, 1h 30d, 1h 365d)
├── equity_curves/       # Exported CSV equity curves for Phase 2.1 (24 configurations)
├── equity_curves_phase3/# Exported CSV equity & drawdown curves for Phase 3 (20 configurations)
├── run_backtest.py      # CLI backtesting runner
├── run_dashboard.py     # CLI web dashboard runner (port 5000)
├── run_paper.py         # CLI paper trading runner
├── run_full_audit.py    # Independent strategy & financial audit suite
├── run_phase2_comparison.py # Phase 2.1 24-config matrix & reconciliation analyzer
├── run_phase3_validation.py # Phase 3 12-month extended validation & risk optimization analyzer
├── validate_ema_methods.py # EMA Methods A/B/C comparative validator
└── .env.example         # Example API credentials
```

---

### 3. Financial Accounting & Partition Reconciliation Standard

The framework enforces strict mathematical consistency across the entire reporting pipeline:

1. **Exact P&L Accounting Identity**:
   $$\text{Gross P\&L} - \text{Entry Commission} - \text{Exit Commission} - \text{Funding Charges} = \text{Net P\&L}$$
   - Maker fee: 0.02% (applied only to confirmed limit maker fills)
   - Taker fee: 0.05% (applied to market orders and limit exit timeouts)
   - Slippage: 0.02% (reflected strictly in market fill prices, zero double counting)
   - Funding rate: 0.01% per 8-hour cycle

2. **Chronological Non-Overlapping Partitions**:
   - **In-Sample (IS)**: First 60% of timeline
   - **Validation (Val)**: Next 20% of timeline
   - **Out-of-Sample (OOS)**: Final 20% of timeline
   - Warmup candles are preserved without allowing trades prior to partition start.
   - Positions open across partition boundaries remain active without artificial liquidations; realized P&L and fees are attributed to the partition in which the trade completes (`exit_timestamp`).

3. **Partition Equality Invariants (Verified across all configurations)**:
   - $\text{Total Trades} = \text{IS Trades} + \text{Val Trades} + \text{OOS Trades}$
   - $\text{Total Gross P\&L} = \text{IS Gross P\&L} + \text{Val Gross P\&L} + \text{OOS Gross P\&L}$
   - $\text{Total Fees} = \text{IS Fees} + \text{Val Fees} + \text{OOS Fees}$
   - $\text{Total Net P\&L} = \text{IS Net P\&L} + \text{Val Net P\&L} + \text{OOS Net P\&L}$

---

### 4. Automated Test Suite (42/42 Passing)

Run the full automated test suite:
```bash
python -m unittest discover -s delta_trading_agent/tests -p "test_*.py" -v
```

All 42 unit and integration tests pass across:
- Stateful & batch indicator parity (SuperTrend, EMA 9, ADX 14, ATR 14, Choppiness Index 14).
- 4-Hour HTF Trend aggregation (zero lookahead bias, completed 4h block boundaries).
- Regime filters (ADX Rising, HTF Trend, Choppiness, ST Separation).
- Delta Exchange tiered MMR schedule & 0.5% liquidation fee penalty buffer.
- Active emergency position closure upon daily loss and portfolio drawdown circuit breaker breach.
- Financial P&L accounting identity ($\text{Gross} - \text{Fees} == \text{Net}$ to the penny).

---

### 5. Phase 3: Extended 12-Month Validation & Risk Optimization

```bash
python run_phase3_validation.py
```

#### Experimental Preset: `BTCUSDT_1H_ADX_ATR_RESEARCH`
- **Data Scope**: 12 full months (365 days / 8,760 1-hour candles: Nov 27, 2025 to Nov 27, 2026 UTC).
- **Partitions**:
  - In-Sample (60%): Days 1 to 219 (5,256 candles)
  - Validation (20%): Days 220 to 292 (1,752 candles)
  - Out-of-Sample (20%): Days 293 to 365 (1,752 candles)
- **Filters Evaluated**: `NONE`, `ADX_ONLY`, `ATR_ONLY`, `BOTH`
- **Margin Allocations Evaluated**: 10%, 20%, 30%, 40%, 70%

#### Phase 3 Deliverables:
- **`phase3_comparison_report.csv`**: Comprehensive 20-configuration performance, risk, and exposure matrix.
- **`phase3_partition_reconciliation.csv`**: Exact mathematical proof of trade count, gross P&L, fees, and net P&L partition reconciliation.
- **`phase3_trade_by_trade_audit.csv`**: Itemized trade-by-trade log (1,450 trades) including Maximum Adverse Excursion (MAE in USDT and %), Liquidation Price, Liquidation Distance %, Notional Exposure, and Margin Used.
- **`phase3_monthly_performance.csv`**: Month-by-month trade counts, win rates, gross P&L, fees, net P&L, returns, profit factors, and drawdowns.
- **`phase3_regime_robustness.csv`**: Performance across Strong Uptrends, Strong Downtrends, Sideways Markets, High Volatility, and Low Volatility.
- **`equity_curves_phase3/`**: Directory containing 20 CSV files with bar-by-bar portfolio equity and drawdown curves.

---

### 6. Phase 4: Market Regime Detection & Strategy Robustness Research

```bash
python run_phase4_research.py
```

Phase 4 evaluates whether independent market regime filters can restore profitability and control drawdown across 12-Month (8,760 candles) and 24-Month (17,520 candles: Nov 27, 2024 to Nov 27, 2026) historical horizons.

#### Independent Regime Filters Evaluated:
1. **Baseline (`NONE`)**: Unconstrained SuperTrend (10,3) + EMA 9 Method C.
2. **Filter A (`BOTH`)**: Baseline ADX > 20 + ATR% > 0.15%.
3. **Filter B (`ADX_RISING`)**: Requires ADX > 20 and rising over completed candles ($ADX_t > ADX_{t-1}$).
4. **Filter C (`HTF_TREND`)**: 4-Hour higher-timeframe trend filter (4h EMA 50 > EMA 200 for Long, < EMA 200 for Short). Strictly zero look-ahead bias (aggregates 4h blocks at UTC 03, 07, 11, 15, 19, 23).
5. **Filter D (`CHOP_FILTER`)**: Choppiness Index (Dreiss formula, $n=14$, threshold 61.8). Blocks entries when market is classified as sideways.
6. **Filter E (`ST_SEPARATION`)**: SuperTrend separation filter ($|Close - ST| / ATR \ge 0.50$).

#### Risk Hardening & Circuit Breakers:
- **Root Cause of Daily Losses > 5% in Phase 3**: Passive circuit breakers blocked future entries but left open positions unmanaged, allowing adverse floating drift to expand.
- **Active Emergency Closure (`close_on_circuit_breaker=True`)**: Immediately liquidates open positions at market upon tripping the 5% daily loss limit or portfolio drawdown limit.
- **Delta Exchange Liquidation Model**: Tier 1 MMR (0.5%) + 0.5% liquidation fee penalty buffer + intrabar adverse excursion audits. 0 liquidations occurred due to 10% isolated margin sizing.

#### Phase 4 Deliverables:
- **`phase4_comparison_report.csv`**: 14-configuration performance, risk, and partition metrics.
- **`phase4_partition_reconciliation.csv`**: 56-row audit verifying exact partition and financial reconciliation.
- **`phase4_trade_by_trade_audit.csv`**: Complete audit of 1,597 executed trades with MAE, fees, and liquidation distances.
- **`phase4_monthly_performance.csv`**: 254-month breakdown across all configurations and regimes.
- **`phase4_regime_robustness.csv`**: 70-row performance matrix across 5 distinct market regimes.
- **`equity_curves_phase4/`**: 14 full bar-by-bar portfolio equity and drawdown curves.

---

### 7. Risk Disclosures & Scientific Verdict

- **Live Trading Hard Stop**: `live_trading_authorized = False` is strictly hardcoded and enforced.
- **Empirical Scientific Finding**:
  - Over a 12-month horizon, Filter C (HTF Trend) showed positive Out-of-Sample returns (+$1,298.15), but over the full 24-month horizon, it suffered a catastrophic -49.90% net loss and tripped the 50% drawdown safety breaker.
  - Filter B (ADX Rising) successfully mitigated whipsaws (cutting 12m drawdown from 50.3% to 28.9%), but still generated negative net returns (-19.85% in 12m, -32.80% in 24m).
  - Across all 14 configurations and both 12-month and 24-month horizons, **no filter produced a positive net expectancy or profitable profit factor (all PF < 1.0)**.
- **Final Quantitative Verdict**: The strategy is **NOT VIABLE FOR LIVE CAPITAL**. Live trading is **STRICTLY REJECTED AND UNAUTHORIZED**.
