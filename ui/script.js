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
        // const controller = new AbortController();
        // const signal = controller.signal;
        // const timeoutId = setTimeout(() => controller.abort(), 5000);
        console.log('Fetching open trades...');
        let res = await fetch('/open_trades'/*,{method:"GET",signal}*/);
        if (!res.ok){ /*;throw new Error*/(`HTTP ${res.status}`) ;};
        let data = await res.json();
        // clearTimeout(timeoutId);
        const trades = data.open_trades || [];
        if (!trades.length) {
            openTradesElements.innerHTML = '<div class="empty">No open trades</div>';
            return;
        }

        console.log('Open trades data:', trades);

        let html = `<table class="trades-table table"><thead><tr><th>Direction</th><th>Asset</th><th>Amount</th><th>Open Price</th><th>Points</th><th>Profit</th><th>Total Returns</th><th>Opened time</th></tr></thead><tbody>`;
        html += trades.map(t => {
            // support both naming schemes (current_price or currentPrice)
            const priceArray = Array.isArray(t.current_price) ? t.current_price : (Array.isArray(t.currentPrice) ? t.currentPrice : []);
            const lastPriceObj = priceArray.length ? priceArray[priceArray.length - 1] : null;
            // console.log('Last price object for trade:', lastPriceObj);
            // robust last-close extraction with fallbacks
            let lastClose = lastPriceObj
                ? (typeof lastPriceObj.close !== 'undefined' && lastPriceObj.close !== null ? Number(lastPriceObj.close)
                    : (typeof lastPriceObj.c !== 'undefined' && lastPriceObj.c !== null ? Number(lastPriceObj.c)
                    : (typeof lastPriceObj.price !== 'undefined' && lastPriceObj.price !== null ? Number(lastPriceObj.price)
                    : (typeof lastPriceObj.open !== 'undefined' && lastPriceObj.open !== null ? Number(lastPriceObj.open) : NaN))))
                : (typeof t.current_price === 'number' ? Number(t.current_price) : (typeof t.currentPrice === 'number' ? Number(t.currentPrice) : NaN));
            // console.log(`Extracted last close price: ${lastClose}`);
            let openPrice = t.openPrice !== undefined ? Number(t.openPrice) : (t.open_price !== undefined ? Number(t.open_price) : NaN);
            const amount = (t.amount !== undefined && t.amount !== null) ? t.amount : '—';
            const profit = (t.profit !== undefined && t.profit !== null) ? t.profit : '—';
            // console.log(`Trade details - Open Price: ${openPrice}, Amount: ${amount}, Profit: ${profit}`);
            // compute points safely using the lastClose value
            let pointsHtml = '—';
            console.log(`Calculating points for trade. Last Close: ${lastClose}, Open Price: ${openPrice}, Direction: ${t.direction}`);
            if (!isNaN(lastClose) && !isNaN(openPrice)) {
                lastClose = lastClose;
                openPrice = openPrice;
                if (t.direction === "BUY") {
                    const diff = lastClose - openPrice;
                    const formatted =Math.abs(Math.round(diff*1000)); // optional formatting
                    pointsHtml = diff >= 0 ? `<span style="color:green;">${formatted}</span>` : `<span style="color:red;">${formatted}</span>`;
                } else if (t.direction === "SELL") {
                    const diff = openPrice-lastClose ;
                    const formatted = Math.abs(Math.round(diff/1000)); // optional formatting
                    pointsHtml = diff >= 0 ? `<span style="color:green;">${formatted}</span>` : `<span style="color:red;">${formatted}</span>`;
                } else {
                    const diff = lastClose - openPrice;
                    const formatted  =Math.abs(Math.round(diff/1000)); // optional formatting
                    pointsHtml = String(formatted);
                }
            }
            // console.log(`Calculated points HTML: ${pointsHtml}`);
            const totalReturns = (typeof amount === 'number' && typeof profit === 'number') ? (amount + profit) : (amount === '—' || profit === '—' ? '—' : `${amount}+${profit}`);

            console.log('Processed trade:', t);
            return `
                <tr>
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
        // console.log('Final HTML for open trades table:', html);
        html += '</tbody></table>';
        openTradesElements.innerHTML = html;
        console.log('Open trades HTML updated.');
    } catch (error) {
        console.error('Error fetching open trades:', error);
        openTradesElements.innerHTML = '<div class="error">Error loading open trades</div>';
    }
}
async function updateCurrentSignals() {
    try {
        let res = await fetch('/current_signals');
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        let data = await res.json();
        const signals = data.signals || {};
        if (!signals.length) {
            currentSignalsElements.innerHTML = '<div class="empty">No current signals</div>';
            return;
        }
        console.log('Current signals data:', signals);
        let html = `<table class="signals-table table"><thead><tr><th>Signal provider</th><th>Asset</th><th>Direction</th><th>Entry time</th></tr></thead><tbody>`;
        html += signals.map(signal=> {
            const signal_provider = (signal.signal_provider !== undefined && signal.signal_provider !== null) ? signal.signal_provider : '—';
            const asset = (signal.asset !== undefined && signal.asset !== null) ? signal.asset : '—';
            const entry_time = (signal.entry_time !== undefined && signal.entry_time !== null) ? signal.entry_time : '—';
            let direction = '—';
            if (signal.direction !== undefined && signal.direction !== null) {
                if (signal.direction.toUpperCase() === 'BUY') {
                    direction = '<span style="color:green;">BUY</span>';
                } else if (signal.direction.toUpperCase() === 'SELL') {
                    direction = '<span style="color:red;">SELL</span>';
                }
            }
            return `
                <tr>
                    <td>${signal_provider}</td>
                    <td>${asset}</td>
                    <td>${direction}</td>
                    <td>${entry_time}</td>
                </tr>
            `;

        }).join('');
        html += '</tbody></table>';

        currentSignalsElements.innerHTML = html;
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
        console.log('Closed trades data:', data);
        const trades = data.closed_trades || [];
        if (!Object.keys(trades).length) {
            closedTradesElements.innerHTML = '<div class="empty">No closed trades</div>';
            return;
        }
        let html = `<table class="closed_trade-table table"><thead><tr><th>Signal provider</th><th>Outcome</th><th>Asset</th><th>Direction</th><th>Entry time</th><th>Amount</th><th>Level</th><th>Open time</th></tr></thead><tbody>`;
        html += Object.keys(trades).map(key => {

            const t = trades[key];
            let direction = '—';
            if (t.trade_details.direction !== undefined && t.trade_details.direction !== null) {
                if (t.trade_details.direction.toUpperCase() === 'BUY') {
                    direction = '<span style="color:green;">BUY</span>';
                } else if (t.trade_details.direction.toUpperCase() === 'SELL') {
                    direction = '<span style="color:red;">SELL</span>';
                }
            }
            let Outcome = '—';
            if (t.trade_details.result !== undefined && t.trade_details.result !== null) {
                if (t.trade_details.result.toUpperCase() === 'WON') {
                    Outcome = '<span style="color:green;">WON</span>';
                } else if (t.trade_details.result.toUpperCase() === 'LOSS') {
                    Outcome = '<span style="color:red;">LOSS</span>';
                }else{
                    Outcome = t.trade_details.result;
                }
            }
            // console.log(t.trade_details);
            return `
                <tr>
                <td>${t.trade_details.signal_provider ?? '—'}</td>
                <td>${Outcome}</td>
                    <td>${t.trade_details.asset ?? '—'}</td>
                    <td>${direction}</td>
                    <td>${t.trade_details.entry_time ?? '—'}</td>
                    <td>${t.trade_details.amount ?? '—'}</td>
                    <td>${t.trade_details.level ?? '—'}</td>
                    <td>${t.trade_details.openedTime ?? '—'}</td>
                </tr>
            `;

        }).join('');
        html += '</tbody></table>'
        closedTradesElements.innerHTML = html;
    } catch (error) {
        console.error('Error fetching closed trades:', error);
        closedTradesElements.innerHTML = '<div class="error">Error loading closed trades</div>';
    }
}
async function updateRiskData() {
    try {
        let res = await fetch('/get_risk_management');
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