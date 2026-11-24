// Real-Time Dashboard Client

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
                    <td>${(t.entry_fee + t.exit_fee + t.funding_fee).toFixed(2)}</td>
                    <td class="${pnlClass}">${t.net_pnl >= 0 ? '+' : ''}${t.net_pnl.toFixed(2)}</td>
                    <td class="${pnlClass}">${t.return_pct >= 0 ? '+' : ''}${t.return_pct.toFixed(2)}%</td>
                    <td><small>${t.exit_reason}</small></td>
                </tr>
            `;
        }).join("");
    }
}

// Controls
document.getElementById("configForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const ema_method = document.getElementById("selectEMAMethod").value;
    const leverage = document.getElementById("inputLeverage").value;
    const margin_allocation_pct = document.getElementById("inputMarginPct").value;

    const res = await fetch("/api/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ema_method, leverage, margin_allocation_pct })
    });
    if (res.ok) {
        alert("Parameters updated successfully!");
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

// Backtest Modal
const btModal = document.getElementById("backtestModal");
document.getElementById("btnOpenBacktestModal").addEventListener("click", () => {
    btModal.classList.add("active");
});
document.getElementById("btnCloseBacktestModal").addEventListener("click", () => {
    btModal.classList.remove("active");
});

document.getElementById("btnExecuteBacktest").addEventListener("click", async () => {
    const days = document.getElementById("btDays").value;
    const ema_method = document.getElementById("btMethod").value;
    
    document.getElementById("btLoadingSpinner").style.display = "block";
    document.getElementById("btResultsArea").style.display = "none";

    try {
        const res = await fetch("/api/backtest", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ days, ema_method })
        });
        const data = await res.json();
        document.getElementById("btLoadingSpinner").style.display = "none";

        if (data.success) {
            renderBacktestResults(data);
        } else {
            alert("Backtest failed: " + data.error);
        }
    } catch (e) {
        document.getElementById("btLoadingSpinner").style.display = "none";
        alert("Error running backtest: " + e);
    }
});

function renderBacktestResults(data) {
    const s = data.summary;
    const statsGrid = document.getElementById("btStatsGrid");
    statsGrid.innerHTML = `
        <div class="stat-item"><div class="stat-label">Net Return</div><div class="stat-value ${s.total_net_pnl>=0?'text-success':'text-danger'}">${s.total_return_pct>=0?'+':''}${s.total_return_pct}%</div></div>
        <div class="stat-item"><div class="stat-label">Net Profit</div><div class="stat-value">${s.total_net_pnl.toFixed(2)} USDT</div></div>
        <div class="stat-item"><div class="stat-label">Total Trades</div><div class="stat-value">${s.total_trades}</div></div>
        <div class="stat-item"><div class="stat-label">Win Rate</div><div class="stat-value">${s.win_rate_pct}%</div></div>
        <div class="stat-item"><div class="stat-label">Profit Factor</div><div class="stat-value">${s.profit_factor}</div></div>
        <div class="stat-item"><div class="stat-label">Max Drawdown</div><div class="stat-value text-danger">${s.max_drawdown_pct}%</div></div>
        <div class="stat-item"><div class="stat-label">Long Win Rate</div><div class="stat-value">${s.long_win_rate_pct}%</div></div>
        <div class="stat-item"><div class="stat-label">Short Win Rate</div><div class="stat-value">${s.short_win_rate_pct}%</div></div>
    `;

    document.getElementById("btResultsArea").style.display = "block";

    // Chart
    const ctx = document.getElementById("equityChartCanvas").getContext("2d");
    if (equityChartInstance) equityChartInstance.destroy();

    const labels = data.equity_sample.map(p => new Date(p.timestamp * 1000).toLocaleDateString());
    const values = data.equity_sample.map(p => p.total_equity);

    equityChartInstance = new Chart(ctx, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [{
                label: 'Simulated Equity (USDT)',
                data: values,
                borderColor: '#3b82f6',
                backgroundColor: 'rgba(59, 130, 246, 0.1)',
                borderWidth: 2,
                fill: true,
                tension: 0.1
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                x: { grid: { color: '#1f293d' } },
                y: { grid: { color: '#1f293d' } }
            }
        }
    });
}

// Initial fetch and start polling every 3 seconds
fetchState();
setInterval(fetchState, 3000);
