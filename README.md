# DELTA EXCHANGE – BTCUSD AUTOMATED TRADING AGENT
## Modular Quantitative Trading System, Execution Engine & Risk-Hardened Architecture

Automated algorithmic trading application for **BTCUSD / BTCUSDT Perpetual Contracts on Delta Exchange** using **SuperTrend (10, 3)** and **EMA 9 (Method C)** with strict 70% margin allocation, 30% unallocated cash buffer, multi-timeframe analysis (5m, 15m, 1h), selectable market regime filters (ADX, ATR), post-only limit execution simulation, and dual-layer risk circuit breakers persisted to SQLite.

---

### 1. Strategy Overview

- **Asset**: BTCUSDT Linear Perpetual (`contract_value = 0.001 BTC`, `tick_size = 0.5 USDT`)
- **Timeframes Supported**: 5-Minute, 15-Minute, 1-Hour Candles (Completed bars only)
- **Primary Trend & Exit Driver**: SuperTrend (Length: 10, Multiplier: 3.0)
- **Confirmation Filter**: EMA 9 (Method C: Both Candle Close & Slope Confirmation)
- **Market Regime Filters (Phase 2)**:
  - **ADX Filter**: Minimum threshold (default: 20.0, 14-period)
  - **ATR Volatility Filter**: Minimum ATR% threshold (default: 0.15% of close, 14-period)
  - **Combined / Baseline**: Selectable independently (`NONE`, `ADX_ONLY`, `ATR_ONLY`, `BOTH`)
- **Execution Simulation Models (Phase 2 & 2.1)**:
  - **MARKET**: Taker order execution (0.05% taker fee + 0.02% slippage)
  - **POST_ONLY_LIMIT**: 0.01% depth offset, maker fill on touch (0.02% maker fee), partial fills (70–90%), timeout cancellation for entry orders, timeout taker liquidation for exit orders
- **Capital Allocation**: 70% Max Margin Allocation per trade (30% Cash Buffer)
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
│   ├── config/          # Central configuration & parameters (settings.py)
│   ├── indicators/      # SuperTrend (10,3), EMA 9, ADX (14), ATR (14) batch & stateful engines
│   ├── signals/         # Reversal, EMA confirmation & regime filter state machine
│   ├── risk/            # 70% margin, leverage, 5% daily loss (inc. floating), 3-loss breaker, SQLite persistence
│   ├── execution/       # Position lifecycle, reversal sequencing & maker/taker execution models
│   ├── exchange/        # Delta REST client (HMAC-SHA256) & WebSocket ticker/candle feed
│   ├── backtest/        # Bar-by-bar backtest engine & performance reconciliation analyzer
│   ├── paper/           # Simulated live execution engine on real-time data
│   ├── storage/         # SQLite persistent database (database.py)
│   ├── dashboard/       # Dark terminal web UI & Flask REST server
│   └── tests/           # Full unit test suite (28 passing tests)
├── data/                # Cached 30-day historical candle data (5m, 15m, 1h)
├── equity_curves/       # Exported CSV equity curves for all 24 configurations
├── run_backtest.py      # CLI backtesting runner
├── run_dashboard.py     # CLI web dashboard runner (port 5000)
├── run_paper.py         # CLI paper trading runner
├── run_full_audit.py    # Independent strategy & financial audit suite
├── run_phase2_comparison.py # Phase 2.1 24-config matrix & reconciliation analyzer
├── validate_ema_methods.py # EMA Methods A/B/C comparative validator
└── .env.example         # Example API credentials
```

---

### 3. Financial Accounting & Partition Reconciliation (Phase 2.1)

Phase 2.1 enforces strict mathematical consistency across the reporting pipeline:

1. **Exact P&L Accounting Identity**:
   $$\text{Gross P\&L} - \text{Entry Commission} - \text{Exit Commission} - \text{Funding Charges} = \text{Net P\&L}$$
   - Maker fee: 0.02% (applied only to confirmed limit maker fills)
   - Taker fee: 0.05% (applied to market orders and limit exit timeouts)
   - Slippage: 0.02% (reflected strictly in market fill prices, zero double counting)
   - Funding rate: 0.01% per 8-hour cycle

2. **Chronological Non-Overlapping Partitions**:
   - **In-Sample (IS)**: First 60% of timeline (Days 1–18)
   - **Validation (Val)**: Next 20% of timeline (Days 19–24)
   - **Out-of-Sample (OOS)**: Final 20% of timeline (Days 25–30)
   - Indicator warmup candles are preserved without allowing trades prior to partition start.
   - Positions open across partition boundaries remain active without artificial liquidations; realized P&L and fees are attributed to the partition in which the trade completes (`exit_timestamp`).

3. **Partition Equality Invariants (Verified across all 24 configurations)**:
   - $\text{Total Trades} = \text{IS Trades} + \text{Val Trades} + \text{OOS Trades}$
   - $\text{Total Gross P\&L} = \text{IS Gross P\&L} + \text{Val Gross P\&L} + \text{OOS Gross P\&L}$
   - $\text{Total Fees} = \text{IS Fees} + \text{Val Fees} + \text{OOS Fees}$
   - $\text{Total Net P\&L} = \text{IS Net P\&L} + \text{Val Net P\&L} + \text{OOS Net P\&L}$

---

### 4. Test Suite & Verification (28/28 Passing)

Run the full automated test suite:
```bash
python -m pytest delta_trading_agent/tests/ -v
```

```text
============================= test session starts =============================
platform win32 -- Python 3.13.14, pytest-9.2.0, pluggy-1.7.0
rootdir: C:\Users\acer\Downloads\Local_AI_Agent
collected 28 items

delta_trading_agent/tests/test_indicators.py::TestIndicators::test_ema_batch_and_stateful_parity PASSED [  3%]
delta_trading_agent/tests/test_indicators.py::TestIndicators::test_stateful_vs_batch_supertrend_parity PASSED [  7%]
delta_trading_agent/tests/test_indicators.py::TestIndicators::test_supertrend_batch_calculation PASSED [ 10%]
delta_trading_agent/tests/test_phase2.py::TestPhase2Indicators::test_adx_batch_and_stateful_parity PASSED [ 14%]
delta_trading_agent/tests/test_phase2.py::TestPhase2Indicators::test_atr_batch_and_stateful_parity PASSED [ 17%]
delta_trading_agent/tests/test_phase2.py::TestPhase2RiskManager::test_max_total_drawdown_protection PASSED [ 21%]
delta_trading_agent/tests/test_phase2.py::TestPhase2RiskManager::test_sqlite_persistence_and_restore PASSED [ 25%]
delta_trading_agent/tests/test_phase2.py::TestPhase2RiskManager::test_unrealized_floating_loss_trips_circuit_breaker PASSED [ 28%]
delta_trading_agent/tests/test_phase2.py::TestPhase2RegimeFilters::test_adx_filter_allows_entry_when_above_threshold PASSED [ 32%]
delta_trading_agent/tests/test_phase2.py::TestPhase2RegimeFilters::test_adx_filter_blocks_entry_when_below_threshold PASSED [ 35%]
delta_trading_agent/tests/test_phase2.py::TestPhase2RegimeFilters::test_atr_filter_blocks_entry_when_low_volatility PASSED [ 39%]
delta_trading_agent/tests/test_phase2.py::TestPhase2RegimeFilters::test_unconditional_exit_even_when_regime_fails PASSED [ 42%]
delta_trading_agent/tests/test_phase2.py::TestPhase2ExecutionModel::test_financial_pnl_accounting_identity PASSED [ 46%]
delta_trading_agent/tests/test_phase2.py::TestPhase2ExecutionModel::test_limit_entry_fill_on_touch PASSED [ 50%]
delta_trading_agent/tests/test_phase2.py::TestPhase2ExecutionModel::test_limit_entry_timeout_cancellation PASSED [ 53%]
delta_trading_agent/tests/test_phase2.py::TestPhase2ExecutionModel::test_limit_exit_timeout_liquidation PASSED [ 57%]
delta_trading_agent/tests/test_phase2.py::TestPhase2ExecutionModel::test_tick_size_rounding PASSED [ 60%]
delta_trading_agent/tests/test_risk_manager.py::TestRiskManager::test_70_percent_margin_allocation_sizing PASSED [ 64%]
delta_trading_agent/tests/test_risk_manager.py::TestRiskManager::test_consecutive_losses_circuit_breaker PASSED [ 67%]
delta_trading_agent/tests/test_risk_manager.py::TestRiskManager::test_daily_loss_circuit_breaker PASSED [ 71%]
delta_trading_agent/tests/test_risk_manager.py::TestRiskManager::test_liquidation_price_estimation PASSED [ 75%]
delta_trading_agent/tests/test_signals.py::TestSignals::test_duplicate_candle_rejection PASSED [ 78%]
delta_trading_agent/tests/test_signals.py::TestSignals::test_long_entry_method_a PASSED [ 82%]
delta_trading_agent/tests/test_signals.py::TestSignals::test_reversal_without_ema_confirmation_exits_to_cash PASSED [ 85%]
delta_trading_agent/tests/test_signals.py::TestSignals::test_short_entry_and_reversal_rules PASSED [ 89%]
delta_trading_agent/tests/test_strategy.py::TestStrategyExecution::test_automatic_reversal_long_to_short PASSED [ 92%]
delta_trading_agent/tests/test_strategy.py::TestStrategyExecution::test_exit_to_cash_when_ema_rejects_reversal PASSED [ 96%]
delta_trading_agent/tests/test_strategy.py::TestStrategyExecution::test_open_long_execution PASSED [100%]

======================= 28 passed in 2.24s =======================
```

---

### 5. Running the Phase 2.1 Comparison & Audit Pipeline

```bash
python run_phase2_comparison.py
```

Generated audit deliverables:
- **`phase2_comparison_report.csv`**: Full 24-configuration comparative matrix (Metrics, Long/Short split, IS/Val/OOS split).
- **`phase2_partition_reconciliation.csv`**: Mathematical reconciliation proof demonstrating zero trade count difference and exact P&L accounting identity across all 24 configurations.
- **`phase2_trade_by_trade_audit.csv`**: Itemized trade-by-trade log with entry/exit timestamps, prices, contracts, margin, gross P&L, fees, and net P&L.
- **`phase2_signal_reconciliation.csv`**: Log of all signals evaluated, indicating executions, filters, and circuit breaker blocks.
- **`equity_curves/`**: Directory containing 24 CSV files with candle-by-candle portfolio equity curves.

---

### 6. Risk Disclosures & Live Trading Safeguards

- **Live Trading Hard Stop**: `live_trading_authorized = False` is hardcoded. Live execution cannot be enabled without explicit API key binding and conscious authorization.
- **Transaction Drag & Leverage Warning**: 10x leverage with 70% capital allocation amplifies drawdown during whipsawing market regimes. On 5m and 15m timeframes, cumulative exchange fees and bid-ask slippage create heavy drag. Only higher timeframes (1h) with regime filtering exhibited positive gross and net returns.
