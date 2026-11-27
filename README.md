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

### 4. Automated Test Suite (33/33 Passing)

Run the full automated test suite:
```bash
python -m pytest delta_trading_agent/tests/ -v
```

```text
============================= test session starts =============================
platform win32 -- Python 3.13.14, pytest-9.2.0, pluggy-1.7.0
rootdir: C:\Users\acer\Downloads\Local_AI_Agent
collected 33 items

delta_trading_agent/tests/test_indicators.py::TestIndicators::test_ema_batch_and_stateful_parity PASSED [  3%]
delta_trading_agent/tests/test_indicators.py::TestIndicators::test_stateful_vs_batch_supertrend_parity PASSED [  6%]
delta_trading_agent/tests/test_indicators.py::TestIndicators::test_supertrend_batch_calculation PASSED [  9%]
delta_trading_agent/tests/test_phase2.py::TestPhase2Indicators::test_adx_batch_and_stateful_parity PASSED [ 12%]
delta_trading_agent/tests/test_phase2.py::TestPhase2Indicators::test_atr_batch_and_stateful_parity PASSED [ 15%]
delta_trading_agent/tests/test_phase2.py::TestPhase2RiskManager::test_max_total_drawdown_protection PASSED [ 18%]
delta_trading_agent/tests/test_phase2.py::TestPhase2RiskManager::test_sqlite_persistence_and_restore PASSED [ 21%]
delta_trading_agent/tests/test_phase2.py::TestPhase2RiskManager::test_unrealized_floating_loss_trips_circuit_breaker PASSED [ 24%]
delta_trading_agent/tests/test_phase2.py::TestPhase2RegimeFilters::test_adx_filter_allows_entry_when_above_threshold PASSED [ 27%]
delta_trading_agent/tests/test_phase2.py::TestPhase2RegimeFilters::test_adx_filter_blocks_entry_when_below_threshold PASSED [ 30%]
delta_trading_agent/tests/test_phase2.py::TestPhase2RegimeFilters::test_atr_filter_blocks_entry_when_low_volatility PASSED [ 33%]
delta_trading_agent/tests/test_phase2.py::TestPhase2RegimeFilters::test_unconditional_exit_even_when_regime_fails PASSED [ 36%]
delta_trading_agent/tests/test_phase2.py::TestPhase2ExecutionModel::test_financial_pnl_accounting_identity PASSED [ 39%]
delta_trading_agent/tests/test_phase2.py::TestPhase2ExecutionModel::test_limit_entry_fill_on_touch PASSED [ 42%]
delta_trading_agent/tests/test_phase2.py::TestPhase2ExecutionModel::test_limit_entry_timeout_cancellation PASSED [ 45%]
delta_trading_agent/tests/test_phase2.py::TestPhase2ExecutionModel::test_limit_exit_timeout_liquidation PASSED [ 48%]
delta_trading_agent/tests/test_phase2.py::TestPhase2ExecutionModel::test_tick_size_rounding PASSED [ 51%]
delta_trading_agent/tests/test_phase3.py::TestPhase3Research::test_liquidation_distance_calculation PASSED [ 54%]
delta_trading_agent/tests/test_phase3.py::TestPhase3Research::test_margin_allocation_scaling PASSED [ 57%]
delta_trading_agent/tests/test_phase3.py::TestPhase3Research::test_maximum_adverse_excursion_long PASSED [ 60%]
delta_trading_agent/tests/test_phase3.py::TestPhase3Research::test_maximum_adverse_excursion_short PASSED [ 63%]
delta_trading_agent/tests/test_phase3.py::TestPhase3Research::test_research_configuration_preset PASSED [ 66%]
delta_trading_agent/tests/test_risk_manager.py::TestRiskManager::test_70_percent_margin_allocation_sizing PASSED [ 69%]
delta_trading_agent/tests/test_risk_manager.py::TestRiskManager::test_consecutive_losses_circuit_breaker PASSED [ 72%]
delta_trading_agent/tests/test_risk_manager.py::TestRiskManager::test_daily_loss_circuit_breaker PASSED [ 75%]
delta_trading_agent/tests/test_risk_manager.py::TestRiskManager::test_liquidation_price_estimation PASSED [ 78%]
delta_trading_agent/tests/test_signals.py::TestSignals::test_duplicate_candle_rejection PASSED [ 81%]
delta_trading_agent/tests/test_signals.py::TestSignals::test_long_entry_method_a PASSED [ 84%]
delta_trading_agent/tests/test_signals.py::TestSignals::test_reversal_without_ema_confirmation_exits_to_cash PASSED [ 87%]
delta_trading_agent/tests/test_signals.py::TestSignals::test_short_entry_and_reversal_rules PASSED [ 90%]
delta_trading_agent/tests/test_strategy.py::TestStrategyExecution::test_automatic_reversal_long_to_short PASSED [ 93%]
delta_trading_agent/tests/test_strategy.py::TestStrategyExecution::test_exit_to_cash_when_ema_rejects_reversal PASSED [ 96%]
delta_trading_agent/tests/test_strategy.py::TestStrategyExecution::test_open_long_execution PASSED [100%]

======================= 33 passed in 2.48s =======================
```

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

### 6. Risk Disclosures & Live Trading Safeguards

- **Live Trading Hard Stop**: `live_trading_authorized = False` is strictly hardcoded and enforced.
- **Capital Allocation Finding**: 70% capital allocation on 10x leverage creates 7.0x effective leverage on account equity, causing catastrophic drawdown during prolonged choppy regimes. Only conservative margin sizing (10% to 20%) allows the account to survive adverse market cycles without triggering total drawdown circuit breakers.
- **Operational Verdict**: While the strategy generated profitable net returns in Validation and Out-of-Sample periods (H2 2026), its performance in choppy ranging regimes (H1 2026) experienced substantial drawdowns. The strategy is NOT approved for live capital until extensive paper trading in live market conditions confirms regime filter resilience.
