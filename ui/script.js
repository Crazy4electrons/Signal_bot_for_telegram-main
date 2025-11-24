let balanceElements = {
    balance_result: document.getElementById('balance-result'),
    P_n_L_day_result: document.getElementById('P_n_L_day-result'),
    lifespan_result: document.getElementById('lifespan-result')
};

let openTradesElements = document.getElementById('open-trades-result');
let currentSignalsElements = document.getElementById('current-signals-result');
let closedTradesElements = document.getElementById('closed-trades-result');

async function updateBalance() {
    try {
        let response = await fetch('/account_details');
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        let data = await response.json();
        balanceElements.balance_result.textContent = data.balance ?? '—';
        balanceElements.P_n_L_day_result.textContent = data.P_n_L_day ?? '—';
        balanceElements.lifespan_result.textContent = data.lifespan ?? '—';
    } catch (error) {
        console.error('Error fetching balance data:', error);
    }
}
async function updateOpenTrades() {
    try {
        let res = await fetch('/open_trades');
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        let data = await res.json();
        const trades = data.open_trades || [];
        if (!trades.length) {
            openTradesElements.innerHTML = '<div class="empty">No open trades</div>';
            return;
        }

        console.log('Open trades data:', trades);

        let html = `<table class="trades-table"><thead><tr><th>Direction</th><th>Asset</th><th>Amount</th><th>Open Price</th><th>Points</th><th>Profit</th><th>Total Returns</th><th>Opened time</th></tr></thead><tbody>`;
        html += trades.map(t => {
            // support both naming schemes (current_price or currentPrice)
            const priceArray = Array.isArray(t.current_price) ? t.current_price : (Array.isArray(t.currentPrice) ? t.currentPrice : []);
            const lastPriceObj = priceArray.length ? priceArray[priceArray.length - 1] : null;

            // robust last-close extraction with fallbacks
            const lastClose = lastPriceObj
                ? (typeof lastPriceObj.close !== 'undefined' && lastPriceObj.close !== null ? Number(lastPriceObj.close)
                    : (typeof lastPriceObj.c !== 'undefined' && lastPriceObj.c !== null ? Number(lastPriceObj.c)
                    : (typeof lastPriceObj.price !== 'undefined' && lastPriceObj.price !== null ? Number(lastPriceObj.price)
                    : (typeof lastPriceObj.open !== 'undefined' && lastPriceObj.open !== null ? Number(lastPriceObj.open) : NaN))))
                : (typeof t.current_price === 'number' ? Number(t.current_price) : (typeof t.currentPrice === 'number' ? Number(t.currentPrice) : NaN));

            const openPrice = t.openPrice !== undefined ? Number(t.openPrice) : (t.open_price !== undefined ? Number(t.open_price) : NaN);
            const amount = (t.amount !== undefined && t.amount !== null) ? t.amount : '—';
            const profit = (t.profit !== undefined && t.profit !== null) ? t.profit : '—';

            // compute points safely using the lastClose value
            let pointsHtml = '—';
            if (!isNaN(lastClose) && !isNaN(openPrice)) {
                const diff = lastClose - openPrice;
                const formatted = Math.abs(Math.round(diff * 100000) / 100000); // optional formatting
                if (t.direction === "BUY") {
                    pointsHtml = diff >= 0 ? `<span style="color:green;">${formatted}</span>` : `<span style="color:red;">${formatted}</span>`;
                } else if (t.direction === "SELL") {
                    pointsHtml = diff >= 0 ? `<span style="color:green;">${formatted}</span>` : `<span style="color:red;">${formatted}</span>`;
                } else {
                    pointsHtml = String(formatted);
                }
            }

            const totalReturns = (typeof amount === 'number' && typeof profit === 'number') ? (amount + profit) : (amount === '—' || profit === '—' ? '—' : `${amount}+${profit}`);

            return `
                <tr class="trade">
                    <td>${t.direction ?? ''}</td>
                    <td>${t.asset ?? '—'}</td>
                    <td>${amount}</td>
                    <td>${isNaN(openPrice) ? '—' : openPrice}</td>
                    <td>${pointsHtml}</td>
                    <td>${profit}</td>
                    <td>${totalReturns}</td>
                    <td>${t.openedTime ?? '—'}</td>
                </tr>
            `;
        }).join('');
        html += '</tbody></table>';

        openTradesElements.innerHTML = html;
    } catch (error) {
        console.error('Error fetching open trades:', error);
        openTradesElements.innerHTML = '<div class="error">Error loading open trades</div>';
    }
}
async function updateCurrentSignals() {
    try {
        let res = await fetch('/current_signals');
        if (res.status === 404) {
            currentSignalsElements.innerHTML = '<div class="info">No current-signals endpoint available on server.</div>';
            return;
        }
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        let data = await res.json();
        let items = [];
        if (Array.isArray(data.signals)) items = data.signals;
        else if (data.Signals && typeof data.Signals === 'object') items = Object.values(data.Signals);
        else if (Array.isArray(data)) items = data;
        if (!items.length) {
            currentSignalsElements.innerHTML = '<div class="empty">No active signals</div>';
            return;
        }
        currentSignalsElements.innerHTML = items.map(s => {
            const provider = s.signal_provider ?? s.provider ?? s.source ?? '—';
            const entry = s.entry_time ?? s.entryTime ?? s.entry ?? '';
            const dir = s.direction ?? '';
            return `<div class="signal"><strong>${provider}</strong> — ${dir} @ ${entry}</div>`;
        }).join('');
    } catch (error) {
        console.error('Error fetching current signals:', error);
        currentSignalsElements.innerHTML = '<div class="error">Error loading current signals</div>';
    }
}
async function updateClosedTrades() {
    try {
        let res = await fetch('/closed_trades');
        if (res.status === 404) {
            closedTradesElements.innerHTML = '<div class="info">No closed-trades endpoint available on server.</div>';
            return;
        }
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        let data = await res.json();
        const trades = data.closed_trades || data.trades || data || [];
        const list = Array.isArray(trades) ? trades : (typeof trades === 'object' ? Object.values(trades) : []);
        if (!list.length) {
            closedTradesElements.innerHTML = '<div class="empty">No closed trades</div>';
            return;
        }
        closedTradesElements.innerHTML = list.map(t => {
            const fmt = v => {
                if (v === null || v === undefined) return '';
                if (typeof v === 'number') return new Date(v * 1000).toLocaleString();
                let d = Date.parse(v);
                return isNaN(d) ? String(v) : new Date(d).toLocaleString();
            };
            return `
                <div class="trade">
                    <div><strong>${t.asset ?? '—'}</strong> ${t.direction ?? ''}</div>
                    <div>Amount: ${t.amount ?? '—'} | Profit: ${t.profit ?? '—'}</div>
                    <div>Opened: ${fmt(t.openedTime)} ${t.closedTime ? '| Closed: ' + fmt(t.closedTime) : ''}</div>
                </div>
            `;
        }).join('');
    } catch (error) {
        console.error('Error fetching closed trades:', error);
        closedTradesElements.innerHTML = '<div class="error">Error loading closed trades</div>';
    }
}
async function updateRiskData() {
    try {
        let res = await fetch('/get_risk_managment', { method: 'POST' });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        let data = await res.json();
        const msg = data.message ?? JSON.stringify(data);
        const el = document.getElementById('risk-result');
        if (el) el.textContent = msg;
    } catch (error) {
        console.error('Error fetching risk data:', error);
        const el = document.getElementById('risk-result');
        if (el) el.textContent = 'Error loading risk data';
    }
}

document.addEventListener('DOMContentLoaded', () => {
    updateBalance();
    updateOpenTrades();
    updateCurrentSignals();
    updateClosedTrades();
    updateRiskData();

    const UPDATE_INTERVAL_MS = 5000;
    setInterval(() => {
        updateBalance();
        updateOpenTrades();
        updateCurrentSignals();
        updateClosedTrades();
        updateRiskData();
    }, UPDATE_INTERVAL_MS);
});