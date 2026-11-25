// Real-Time Dashboard Client – Phase 2 Enhanced

let equityChartInstance = null;

async function fetchState() {
    try {
        const res = await fetch("/api/state");
        if (!res.ok) return;
        const data = await res.json();
        updateUI(data);
    } catch (e) {
        console.error("Failed to fetch state:", e);
    }
}

function updateUI(data) {
    const acc = data.account;
    const mkt = data.market;
    const pos = data.position;
    const sig = data.latest_signal;
    const trades = data.recent_trades || [];

    // 1. Account
    document.getElementById("valTotalCapital").textContent = acc.total_capital.toLocaleString() + " USDT";
    document.getElementById("valTotalEquity").textContent = acc.total_equity.toLocaleString() + " USDT";
    
    const unpnlEl = document.getElementById("valUnrealizedPnL");
    unpnlEl.textContent = (acc.unrealized_pnl >= 0 ? "+" : "") + acc.unrealized_pnl.toFixed(2) + " USDT";
    unpnlEl.className = "value " + (acc.unrealized_pnl > 0 ? "text-success" : acc.unrealized_pnl < 0 ? "text-danger" : "");

    document.getElementById("valUsedMargin").textContent = acc.used_margin.toLocaleString() + " USDT";
    document.getElementById("valAvailableMargin").textContent = acc.available_margin.toLocaleString() + " USDT";
    document.getElementById("valUnallocatedCash").textContent = acc.unallocated_cash.toLocaleString() + " USDT";

    const circuitBadge = document.getElementById("circuitStatusBadge");
    if (acc.circuit_breaker) {
        circuitBadge.textContent = "CIRCUIT BREAKER: " + acc.circuit_breaker_reason;
        circuitBadge.className = "circuit-status danger";
    } else {
        circuitBadge.textContent = "SYSTEM NORMAL";
        circuitBadge.className = "circuit-status";
    }

    // 2. Market
    document.getElementById("valLivePrice").textContent = mkt.live_price.toLocaleString(undefined, {minimumFractionDigits: 2}) + " USDT";
    
    const stBadge = document.getElementById("badgeSuperTrend");
    if (mkt.supertrend_trend === 1) {
        stBadge.textContent = "BULLISH (GREEN)";
        stBadge.className = "badge badge-green";
    } else if (mkt.supertrend_trend === -1) {
        stBadge.textContent = "BEARISH (RED)";
        stBadge.className = "badge badge-red";
    } else {
        stBadge.textContent = "INITIALIZING";
        stBadge.className = "badge badge-neutral";
    }
    document.getElementById("valSuperTrendLine").textContent = "Line: " + mkt.supertrend_val.toFixed(1);

    document.getElementById("valEMA").textContent = mkt.ema_val.toFixed(1);
    document.getElementById("valEMASlope").textContent = "Slope: " + (mkt.ema_slope >= 0 ? "+" : "") + mkt.ema_slope.toFixed(2);

    // Signal
    document.getElementById("signalType").textContent = "LATEST SIGNAL: " + sig.action + " (" + sig.type + ")";
    document.getElementById("signalReason").textContent = sig.reason;

    // 3. Position
    const posBadge = document.getElementById("badgePositionSide");
    posBadge.textContent = pos.side;
    if (pos.side === "LONG") {
        posBadge.className = "badge badge-green";
    } else if (pos.side === "SHORT") {
        posBadge.className = "badge badge-red";
    } else {
        posBadge.className = "badge badge-neutral";
    }

    document.getElementById("valPositionQty").textContent = `${pos.contracts} Contracts (${pos.btc_size.toFixed(3)} BTC)`;
    document.getElementById("valEntryPrice").textContent = pos.entry_price > 0 ? pos.entry_price.toFixed(2) + " USDT" : "--.--";
    document.getElementById("valPositionMargin").textContent = pos.margin_used.toFixed(2) + " USDT";
    document.getElementById("valLiqPrice").textContent = pos.liquidation_price > 0 ? pos.liquidation_price.toFixed(2) + " USDT" : "--.--";

    const curPnlEl = document.getElementById("valCurrentPnL");
    curPnlEl.textContent = (pos.unrealized_pnl >= 0 ? "+" : "") + pos.unrealized_pnl.toFixed(2) + " USDT";
    curPnlEl.className = "value " + (pos.unrealized_pnl > 0 ? "text-success" : pos.unrealized_pnl < 0 ? "text-danger" : "");

    // 4. Trade History Table
    const tbody = document.getElementById("tradeHistoryBody");
    if (trades.length > 0) {
        tbody.innerHTML = trades.slice().reverse().map(t => {
            const pnlClass = t.net_pnl > 0 ? "text-success" : t.net_pnl < 0 ? "text-danger" : "";
            const entryDate = new Date(t.entry_timestamp * 1000).toLocaleTimeString();
            const exitDate = new Date(t.exit_timestamp * 1000).toLocaleTimeString();
            const feeTotal = (t.entry_fee + t.exit_fee + t.funding_fee).toFixed(2);
            return `
                <tr>
                    <td><strong>${t.trade_id}</strong></td>
                    <td><span class="badge ${t.side === 'LONG' ? 'badge-green' : 'badge-red'}">${t.side}</span></td>
                    <td>${entryDate}</td>
                    <td>${exitDate}</td>
                    <td>${t.entry_price.toFixed(1)}</td>
                    <td>${t.exit_price.toFixed(1)}</td>
                    <td>${t.btc_size.toFixed(3)}</td>
                    <td>${t.margin_used.toFixed(2)}</td>
                    <td>${feeTotal}</td>
                    <td class="${pnlClass}">${t.net_pnl >= 0 ? '+' : ''}${t.net_pnl.toFixed(2)}</td>
                    <td class="${pnlClass}">${t.return_pct >= 0 ? '+' : ''}${t.return_pct.toFixed(2)}%</td>
                    <td><small>${t.exit_reason}</small></td>
                </tr>
            `;
        }).join("");
    }
}

// Configuration Form Submission (Phase 2 Enhanced)
document.getElementById("configForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const payload = {
        resolution: document.getElementById("selectTimeframe").value,
        regime_filter: document.getElementById("selectRegimeFilter").value,
        execution_type: document.getElementById("selectExecutionType").value,
        ema_method: document.getElementById("selectEMAMethod").value,
        leverage: parseFloat(document.getElementById("inputLeverage").value),
        margin_allocation_pct: parseFloat(document.getElementById("inputMarginPct").value),
        adx_threshold: parseFloat(document.getElementById("inputADXThreshold").value),
        atr_min_pct: parseFloat(document.getElementById("inputATRThreshold").value)
    };

    try {
        const res = await fetch("/api/config", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });
        if (res.ok) {
            alert("Phase 2 Parameters applied successfully!");
            fetchState();
        } else {
            alert("Error updating parameters");
        }
    } catch (err) {
        alert("Network error: " + err);
    }
});

// Emergency Close
document.getElementById("emergencyCloseBtn").addEventListener("click", async () => {
    if (!confirm("Are you sure you want to trigger an EMERGENCY CLOSE of all active positions?")) return;
    const res = await fetch("/api/close_position", { method: "POST" });
    const data = await res.json();
    alert(data.message);
    fetchState();
});

// Backtest Modal Controls
const btModal = document.getElementById("backtestModal");
document.getElementById("btnOpenBacktestModal").addEventListener("click", () => {
    btModal.classList.add("active");
});
document.getElementById("btnCloseBacktestModal").addEventListener("click", () => {
    btModal.classList.remove("active");
});

// Matrix Modal Controls
const matrixModal = document.getElementById("matrixModal");
document.getElementById("btnOpenMatrixModal").addEventListener("click", async () => {
    matrixModal.classList.add("active");
    await loadComparisonMatrix();
});
document.getElementById("btnCloseMatrixModal").addEventListener("click", () => {
    matrixModal.classList.remove("active");
});

// Load Pre-computed Matrix Table
async function loadComparisonMatrix() {
    const tbody = document.getElementById("matrixTableBody");
    tbody.innerHTML = `<tr><td colspan="12" style="text-align:center;">Loading 24-config comparative matrix...</td></tr>`;

    try {
        const res = await fetch("/api/phase2/comparison_results");
        const data = await res.json();
        if (!data.available || !data.rows.length) {
            tbody.innerHTML = `<tr><td colspan="12" class="empty-state">No comparison matrix available yet. Run 'run_phase2_comparison.py' to generate.</td></tr>`;
            return;
        }

        tbody.innerHTML = data.rows.map(r => {
            const pnlClass = r.net_pnl > 0 ? "text-success" : r.net_pnl < 0 ? "text-danger" : "";
            const oosClass = r.out_of_sample_net_pnl > 0 ? "text-success" : r.out_of_sample_net_pnl < 0 ? "text-danger" : "";
            return `
                <tr>
                    <td><strong>${r.timeframe}</strong></td>
                    <td><span class="badge ${r.regime_filter === 'NONE' ? 'badge-neutral' : 'badge-green'}">${r.regime_filter}</span></td>
                    <td><span class="badge ${r.execution_type === 'MARKET' ? 'badge-neutral' : 'badge-green'}">${r.execution_type}</span></td>
                    <td>${r.total_trades}</td>
                    <td>${Number(r.win_rate_pct).toFixed(1)}%</td>
                    <td>${Number(r.gross_pnl).toFixed(2)}</td>
                    <td>${Number(r.total_fees_and_funding).toFixed(2)}</td>
                    <td class="${pnlClass}"><strong>${Number(r.net_pnl) >= 0 ? '+' : ''}${Number(r.net_pnl).toFixed(2)}</strong></td>
                    <td class="${pnlClass}">${Number(r.net_return_pct) >= 0 ? '+' : ''}${Number(r.net_return_pct).toFixed(1)}%</td>
                    <td>${Number(r.profit_factor).toFixed(2)}</td>
                    <td>${Number(r.max_drawdown_pct).toFixed(1)}%</td>
                    <td class="${oosClass}">${Number(r.out_of_sample_net_pnl) >= 0 ? '+' : ''}${Number(r.out_of_sample_net_pnl).toFixed(2)}</td>
                </tr>
            `;
        }).join("");
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="12" class="empty-state">Failed to load matrix: ${e.message}</td></tr>`;
    }
}

// Execute Interactive Backtest with Baseline Comparison
document.getElementById("btnExecuteBacktest").addEventListener("click", async () => {
    const days = document.getElementById("btDays").value;
    const timeframe = document.getElementById("btTimeframe").value;
    const regime_filter = document.getElementById("btRegimeFilter").value;
    const execution_type = document.getElementById("btExecutionType").value;
    const ema_method = document.getElementById("btMethod").value;

    const spinner = document.getElementById("btLoadingSpinner");
    const resultsArea = document.getElementById("btResultsArea");
    spinner.style.display = "block";
    resultsArea.style.display = "none";

    try {
        const res = await fetch("/api/backtest", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                days,
                timeframe,
                regime_filter,
                execution_type,
                ema_method,
                compare_baseline: true
            })
        });

        const data = await res.json();
        spinner.style.display = "none";

        if (!data.success) {
            alert("Backtest failed: " + data.error);
            return;
        }

        resultsArea.style.display = "block";
        renderBacktestResults(data, { timeframe, regime_filter, execution_type });
    } catch (e) {
        spinner.style.display = "none";
        alert("Backtest request error: " + e.message);
    }
});

function renderBacktestResults(data, config) {
    const s = data.summary;
    const b = data.baseline ? data.baseline.summary : null;

    // Comparison Header
    const compHeader = document.getElementById("btComparisonHeader");
    if (b) {
        const netDiff = s.total_net_pnl - b.total_net_pnl;
        const diffColor = netDiff >= 0 ? "#00ff88" : "#ff4444";
        compHeader.innerHTML = `
            <div style="background: rgba(255,255,255,0.05); padding: 12px; border-radius: 6px; border-left: 4px solid #00e5ff;">
                <strong>Configuration:</strong> TF=${config.timeframe} | Filter=${config.regime_filter} | Exec=${config.execution_type}
                <span style="margin-left: 20px; color: ${diffColor}; font-weight: bold;">
                    Delta vs Baseline: ${netDiff >= 0 ? '+' : ''}${netDiff.toFixed(2)} USDT
                </span>
            </div>
        `;
    } else {
        compHeader.innerHTML = `
            <div style="background: rgba(255,255,255,0.05); padding: 12px; border-radius: 6px; border-left: 4px solid #00ff88;">
                <strong>Baseline Strategy (5m, No Filter, Market Execution)</strong>
            </div>
        `;
    }

    // Stats Grid
    const pnlClass = s.total_net_pnl >= 0 ? "text-success" : "text-danger";
    document.getElementById("btStatsGrid").innerHTML = `
        <div class="stat-card">
            <span class="label">Net Realized P&L</span>
            <span class="value ${pnlClass}">${s.total_net_pnl >= 0 ? '+' : ''}${s.total_net_pnl.toFixed(2)} USDT</span>
            <small>${s.total_return_pct >= 0 ? '+' : ''}${s.total_return_pct.toFixed(2)}% Return</small>
        </div>
        <div class="stat-card">
            <span class="label">Win Rate & Trades</span>
            <span class="value">${s.win_rate_pct.toFixed(1)}%</span>
            <small>${s.winning_trades}W / ${s.losing_trades}L (${s.total_trades} total)</small>
        </div>
        <div class="stat-card">
            <span class="label">Profit Factor</span>
            <span class="value">${s.profit_factor.toFixed(2)}</span>
            <small>Gross Profit: ${s.gross_profit.toFixed(2)}</small>
        </div>
        <div class="stat-card">
            <span class="label">Total Fees & Funding</span>
            <span class="value">${s.total_fees_paid.toFixed(2)} USDT</span>
            <small>Taker/Maker + 8h Funding</small>
        </div>
        <div class="stat-card">
            <span class="label">Max Equity Drawdown</span>
            <span class="value danger-val">${s.max_drawdown_pct.toFixed(1)}%</span>
            <small>${s.max_drawdown_amount.toFixed(2)} USDT Peak Drop</small>
        </div>
        <div class="stat-card">
            <span class="label">Long / Short Breakdown</span>
            <span class="value">${s.long_trades}L / ${s.short_trades}S</span>
            <small>L: ${s.long_win_rate_pct.toFixed(0)}% | S: ${s.short_win_rate_pct.toFixed(0)}%</small>
        </div>
    `;

    // Multi-Curve Comparison Chart
    const ctx = document.getElementById("equityChartCanvas").getContext("2d");
    if (equityChartInstance) {
        equityChartInstance.destroy();
    }

    const curLabels = data.equity_sample.map((pt, idx) => `Bar ${idx}`);
    const curEquity = data.equity_sample.map(pt => pt.total_equity);

    const datasets = [{
        label: `Current (${config.timeframe} - ${config.regime_filter} - ${config.execution_type})`,
        data: curEquity,
        borderColor: "#00e5ff",
        backgroundColor: "rgba(0, 229, 255, 0.08)",
        borderWidth: 2,
        tension: 0.1,
        fill: true
    }];

    if (data.baseline && data.baseline.equity_sample) {
        const baseEquity = data.baseline.equity_sample.map(pt => pt.total_equity);
        datasets.push({
            label: "Baseline (5m - No Filter - Market)",
            data: baseEquity,
            borderColor: "#ff9100",
            backgroundColor: "transparent",
            borderWidth: 2,
            borderDash: [5, 5],
            tension: 0.1,
            fill: false
        });
    }

    equityChartInstance = new Chart(ctx, {
        type: "line",
        data: {
            labels: curLabels,
            datasets: datasets
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    position: "top",
                    labels: { color: "#ccc", font: { family: "JetBrains Mono" } }
                },
                tooltip: {
                    callbacks: {
                        label: (ctx) => `${ctx.dataset.label}: ${ctx.parsed.y.toFixed(2)} USDT`
                    }
                }
            },
            scales: {
                x: { display: false },
                y: {
                    ticks: { color: "#888", font: { family: "JetBrains Mono" } },
                    grid: { color: "rgba(255, 255, 255, 0.05)" }
                }
            }
        }
    });
}

// Initial poll and recurring loop
fetchState();
setInterval(fetchState, 5000);
