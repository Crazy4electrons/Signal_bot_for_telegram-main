/* ==========================================================================
   Signal Execution Desk — desk client

   Reads five endpoints and renders them as a ledger. The risk ladder mirrors
   the checks in main.py: a sequence has martingale_levels + 1 legs, per-leg
   stakes multiply by martingale_multiplier, and a leg above max_trade_amount
   is refused before it is placed. If those rules change in main.py, change
   them here too.
   ========================================================================== */

"use strict";

const POLL_INTERVAL_MS = 5000;
const THEME_KEY = "signal-desk-theme";
const LAYOUT_KEY = "signal-desk-layout";
const RETRY_HINT = "The desk retries every 5 seconds. If this keeps up, check the bot's log.";
const MAX_LADDER_LEGS = 12;

const RISK_NUMBER_FIELDS = [
	"initial_amount",
	"martingale_levels",
	"martingale_multiplier",
	"timeframe",
	"drawback_threshold",
	"max_trade_amount",
	"max_sequence_exposure",
	"max_open_trades",
	"max_open_trades_per_asset",
	"max_open_trades_per_provider",
	"stale_sequence_seconds",
	"min_balance_reserve",
];
const INTEGER_FIELDS = [
	"martingale_levels",
	"timeframe",
	"drawback_threshold",
	"max_open_trades",
	"max_open_trades_per_asset",
	"max_open_trades_per_provider",
	"stale_sequence_seconds",
];

const money = new Intl.NumberFormat("en-US", {
	minimumFractionDigits: 2,
	maximumFractionDigits: 2,
});
const signedMoney = new Intl.NumberFormat("en-US", {
	minimumFractionDigits: 2,
	maximumFractionDigits: 2,
	signDisplay: "exceptZero",
});

const els = {
	balance: document.getElementById("balance-result"),
	pnl: document.getElementById("P_n_L_day-result"),
	lifespan: document.getElementById("lifespan-result"),
	readings: document.querySelector(".readings"),
	openTrades: document.getElementById("open-trades-result"),
	signals: document.getElementById("current-signals-result"),
	closedTrades: document.getElementById("closed-trades-result"),
	ladder: document.getElementById("risk-ladder"),
	limits: document.getElementById("risk-result"),
	limitsState: document.getElementById("risk-limits-state"),
	riskProfileMeta: document.getElementById("risk-profile-meta"),
	status: document.getElementById("connection-state"),
	statusLabel: document.getElementById("connection-state-label"),
	statusTime: document.getElementById("last-updated"),
	themeToggle: document.getElementById("theme-toggle"),
	layoutChoice: document.getElementById("layout-choice"),
	modal: document.getElementById("popup_bg"),
	form: document.getElementById("risk-form-element"),
	formError: document.getElementById("risk-form-error"),
	riskProfile: document.getElementById("risk-profile-input"),
	riskProfileHint: document.getElementById("risk-profile-hint"),
	riskGlobalTimezone: document.getElementById("risk-global-timezone-field"),
	openForm: document.getElementById("set-risk-button"),
	openBrokerRiskForm: document.getElementById("set-broker-risk-button"),
	cancelForm: document.getElementById("cancel-risk-button"),
	channels: document.getElementById("channels-result"),
	channelNote: document.getElementById("channels-problems"),
	addChannel: document.getElementById("add-channel-button"),
	channelModal: document.getElementById("channel-popup_bg"),
	channelForm: document.getElementById("channel-form-element"),
	channelFormError: document.getElementById("channel-form-error"),
	channelHeading: document.getElementById("channel-form-heading"),
	channelTimezone: document.getElementById("channel-timezone-input"),
	channelBroker: document.getElementById("channel-broker-input"),
	deleteChannel: document.getElementById("delete-channel-button"),
	cancelChannel: document.getElementById("cancel-channel-button"),
	brokers: document.getElementById("broker-workspaces"),
	brokerNotice: document.getElementById("broker-notice"),
	accountMeta: document.getElementById("account-meta"),
};

const HTML_ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const BROKER_NAMES = { pocket_option: "Pocket Option", quotex: "Quotex" };
const BROKER_IDS = ["pocket_option", "quotex"];

/* --- Small helpers ------------------------------------------------------ */

function escapeHtml(value) {
	return String(value).replace(/[&<>"']/g, (character) => HTML_ESCAPES[character]);
}

/* For values going into innerHTML: escaped, with a dash for anything missing. */
function cell(value) {
	if (value === undefined || value === null || value === "" || value === "-") return "—";
	return escapeHtml(value);
}

/* For values going into textContent: no escaping, so entities stay literal. */
function displayValue(value) {
	if (value === undefined || value === null || value === "") return "—";
	return String(value);
}

function setTextContent(element, value) {
	const text = String(value);
	if (element.textContent !== text) element.textContent = text;
}

function isNumber(value) {
	return typeof value === "number" && Number.isFinite(value);
}

function toNumber(value) {
	const number = Number(value);
	return Number.isFinite(number) ? number : NaN;
}

function moneyOr(value) {
	return isNumber(value) ? money.format(value) : cell(value);
}

function asArray(value) {
	if (Array.isArray(value)) return value;
	if (value && typeof value === "object") return Object.values(value);
	return [];
}

function emptyState(headline, hint) {
	return `<div class="empty"><p>${headline}</p><p>${hint}</p></div>`;
}

function errorState(headline) {
	return `<div class="error"><p>${headline}</p><p>${RETRY_HINT}</p></div>`;
}

function renderMarkup(container, markup, key) {
	if (container.dataset.renderKey === key) return;
	container.innerHTML = markup;
	container.dataset.renderKey = key;
}

function renderRows(container, tableClass, headings, rows) {
	let table = container.querySelector(":scope > table");
	if (!table || !table.classList.contains(tableClass)) {
		container.innerHTML = `<table class="${tableClass}"><thead><tr>${headings}</tr></thead><tbody></tbody></table>`;
		container.dataset.renderKey = `table:${tableClass}`;
		table = container.querySelector(":scope > table");
	}

	const body = table.tBodies[0];
	const existing = new Map(
		Array.from(body.querySelectorAll(":scope > tr[data-row-key]"))
			.map((row) => [row.dataset.rowKey, row])
	);
	let cursor = body.firstElementChild;
	for (const item of rows) {
		let row = existing.get(item.key);
		if (row) existing.delete(item.key);
		if (!row) {
			const template = document.createElement("template");
			template.innerHTML = `<tr>${item.html}</tr>`;
			row = template.content.firstElementChild;
			row.dataset.rowKey = item.key;
			row.dataset.rendered = item.html;
		} else if (row.dataset.rendered !== item.html) {
			row.innerHTML = item.html;
			row.dataset.rendered = item.html;
		}
		if (row !== cursor) body.insertBefore(row, cursor);
		cursor = row.nextElementSibling;
	}
	for (const row of existing.values()) row.remove();
	while (cursor) {
		const next = cursor.nextElementSibling;
		cursor.remove();
		cursor = next;
	}
	delete container.dataset.renderKey;
	container.dataset.view = `table:${tableClass}`;
}

/*
 * A failed read keeps the last known table on screen, dimmed and labelled,
 * so open positions stay readable while the desk retries.
 */
function renderFailure(container, headline) {
	let error = container.querySelector(":scope > .error");
	if (!error) {
		const template = document.createElement("template");
		template.innerHTML = errorState(escapeHtml(headline));
		error = template.content.firstElementChild;
		container.prepend(error);
	} else {
		error.querySelector("p").textContent = headline;
	}
	container.dataset.stale = "true";
}

function clearFailure(container) {
	container.querySelector(":scope > .error")?.remove();
	delete container.dataset.stale;
}

/* --- Cells -------------------------------------------------------------- */

/* Signals are normalised to CALL/PUT, while the broker UI uses BUY/SELL. */
function formatDirection(value) {
	const direction = String(value ?? "").trim().toUpperCase();
	if (direction === "BUY" || direction === "CALL") return `<span class="chip chip--call">CALL</span>`;
	if (direction === "SELL" || direction === "PUT") return `<span class="chip chip--put">PUT</span>`;
	if (!direction || direction === "—" || direction === "-") return "—";
	return escapeHtml(direction);
}

function formatOutcome(value) {
	const outcome = String(value ?? "").trim().toUpperCase();
	if (outcome === "WON" || outcome === "WIN") return `<span class="chip chip--won">${escapeHtml(value)}</span>`;
	if (outcome === "LOSS" || outcome === "LOST") return `<span class="chip chip--loss">${escapeHtml(value)}</span>`;
	if (!outcome || outcome === "—") return "—";
	return escapeHtml(value);
}

/* Points move with the trade: positive while it is winning. */
function formatPoints(direction, openPrice, lastClose) {
	const normalised = String(direction ?? "").trim().toUpperCase();
	if (!isNumber(openPrice) || !isNumber(lastClose)) return { value: "—", className: "num" };
	const known = normalised === "BUY" || normalised === "CALL" || normalised === "SELL" || normalised === "PUT";
	const diff = normalised === "SELL" || normalised === "PUT" ? openPrice - lastClose : lastClose - openPrice;
	const magnitude = Math.abs(Math.round(diff * 100000));
	if (!known) return { value: String(magnitude), className: "num" };
	/* Flat is neither a gain nor a loss, so it stays uncoloured. */
	if (diff === 0) return { value: "0", className: "num" };
	return { value: String(magnitude), className: diff > 0 ? "num pos" : "num neg" };
}

/* The broker reports openTime as a unix timestamp; show it the way the other
   tables show times. Anything unrecognisable is printed as it arrived. */
function formatTimestamp(value) {
	if (!isNumber(value) || value < 1e8) return cell(value);
	const moment = new Date(value > 1e12 ? value : value * 1000);
	if (Number.isNaN(moment.getTime())) return cell(value);
	const pad = (number) => String(number).padStart(2, "0");
	return `${moment.getFullYear()}-${pad(moment.getMonth() + 1)}-${pad(moment.getDate())} ${pad(moment.getHours())}:${pad(moment.getMinutes())}:${pad(moment.getSeconds())}`;
}

/* --- Readers ------------------------------------------------------------ */

let brokerStatuses = [];
let brokerStructureKey = "";

function brokerName(brokerId) { return BROKER_NAMES[brokerId] || String(brokerId || "Broker"); }

function brokerQuery(path, brokerId) {
	return `${path}?broker_id=${encodeURIComponent(brokerId)}`;
}

function brokerPanel(brokerId) {
	return document.querySelector(`[data-broker-panel="${brokerId}"]`);
}

function renderBrokers() {
	if (!brokerStatuses.length || !els.brokers) {
		renderFailure(els.brokers, "Broker configuration could not be read.");
		return;
	}

	const structureKey = JSON.stringify(brokerStatuses.map(({ id, name, account_type, configured }) => ({
		id, name, account_type, configured,
	})));
	if (structureKey !== brokerStructureKey) {
		els.brokers.innerHTML = brokerStatuses.map((broker) => {
		const state = broker.connected ? "connected" : broker.configured ? "disconnected" : "missing";
		const stateLabel = broker.connected ? "Connected" : broker.configured ? "Session saved · not connected" : "Session needed";
		const captureCommand = `python playwright_scraper.py --broker ${broker.id} --account-type ${broker.account_type} --headed`;
		const sessionHelp = broker.configured ? "" : `
			<div class="broker-card__session">
				<span>Capture a session in a visible browser and complete verification there.</span>
				<code>${escapeHtml(captureCommand)}</code>
			</div>`;
		const connectLabel = broker.connected ? "Refresh connection" : "Connect";
		const connectDisabled = broker.configured ? "" : " disabled";
		return `
			<article class="broker-workspace" data-broker-panel="${escapeHtml(broker.id)}">
				<div class="broker-card__head">
					<div>
						<h2 class="broker-card__name">${escapeHtml(broker.name)}</h2>
						<p class="broker-card__account">${escapeHtml(broker.account_type)} account</p>
					</div>
					<span class="broker-card__state" data-state="${state}">${stateLabel}</span>
				</div>
				<div class="broker-card__actions">
					<button class="button" type="button" data-broker-action="connect" data-broker-id="${escapeHtml(broker.id)}"${connectDisabled}>${connectLabel}</button>
				</div>
				${sessionHelp}
				<section class="broker-account" aria-label="${escapeHtml(broker.name)} account details">
					<p class="region__meta">${escapeHtml(broker.account_type)} · ${escapeHtml(broker.name)} account</p>
					<dl class="readings">
						<div class="reading reading--primary" data-flash><dt class="reading__label">Balance</dt><dd class="reading__value" data-account="balance">&mdash;</dd></div>
						<div class="reading" data-flash><dt class="reading__label">Bot P/L today</dt><dd class="reading__value" data-account="pnl">&mdash;</dd></div>
						<div class="reading" data-flash><dt class="reading__label">Lifespan</dt><dd class="reading__value" data-account="lifespan">&mdash;</dd></div>
					</dl>
				</section>
				<div class="broker-activity">
					<section class="region"><h3 class="region__heading">Incoming signals</h3><div class="data" data-activity="signals">Loading&hellip;</div></section>
					<section class="region"><h3 class="region__heading">Open trades</h3><div class="data" data-activity="open">Loading&hellip;</div></section>
					<section class="region"><h3 class="region__heading">Closed trades</h3><p class="region__note">Newest settlement first.</p><div class="data" data-activity="closed">Loading&hellip;</div></section>
				</div>
			</article>`;
		}).join("");
		brokerStructureKey = structureKey;
	}

	clearFailure(els.brokers);
	for (const broker of brokerStatuses) {
		const panel = brokerPanel(broker.id);
		if (!panel) continue;
		const state = broker.connected ? "connected" : broker.configured ? "disconnected" : "missing";
		const stateLabel = broker.connected
			? "Connected"
			: broker.configured
				? "Session saved · not connected"
				: "Session needed";
		const stateElement = panel.querySelector(".broker-card__state");
		const button = panel.querySelector('[data-broker-action="connect"]');
		stateElement.textContent = stateLabel;
		stateElement.dataset.state = state;
		button.textContent = broker.connected ? "Refresh connection" : "Connect";
		button.disabled = !broker.configured || button.getAttribute("aria-busy") === "true";
	}
}

async function updateBrokerStatus() {
	const response = await fetch("/broker_status");
	if (!response.ok) throw new Error(`HTTP ${response.status}`);
	const data = await response.json();
	brokerStatuses = Array.isArray(data.brokers) ? data.brokers : [];
	renderBrokers();
}

async function connectBroker(brokerId, button) {
	button.disabled = true;
	button.setAttribute("aria-busy", "true");
	button.textContent = "Connecting…";
	try {
		const response = await fetch(`/brokers/${encodeURIComponent(brokerId)}/connect`, { method: "POST" });
		const body = await response.json();
		if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : `HTTP ${response.status}`);
		await updateBrokerStatus();
		await updateBrokerData(brokerId);
	} catch (error) {
		console.error(`Error connecting ${brokerId}:`, error);
		try {
			await updateBrokerStatus();
		} catch (statusError) {
			console.error("Error refreshing broker status:", statusError);
		}
		const panel = brokerPanel(brokerId);
		const status = panel && panel.querySelector(".broker-card__state");
		if (status) {
			status.textContent = error.message || `Could not connect to ${brokerName(brokerId)}.`;
			status.dataset.state = "missing";
		}
	} finally {
		button.disabled = !brokerStatuses.find((broker) => broker.id === brokerId)?.configured;
		button.removeAttribute("aria-busy");
	}
}

async function updateBalance(brokerId) {
	const panel = brokerPanel(brokerId);
	try {
		const response = await fetch(brokerQuery("/account_details", brokerId));
		if (!response.ok) throw new Error(`HTTP ${response.status}`);
		const data = await response.json();
		const balance = panel.querySelector('[data-account="balance"]');
		const pnl = panel.querySelector('[data-account="pnl"]');
		const lifespan = panel.querySelector('[data-account="lifespan"]');
		setTextContent(balance, isNumber(data.balance) ? money.format(data.balance) : displayValue(data.balance));
		setTextContent(pnl, isNumber(data.P_n_L_day) ? signedMoney.format(data.P_n_L_day) : displayValue(data.P_n_L_day));
		setTextContent(lifespan, displayValue(data.lifespan));
		if (isNumber(data.P_n_L_day) && data.P_n_L_day !== 0) {
			pnl.dataset.sign = data.P_n_L_day > 0 ? "pos" : "neg";
		} else {
			delete pnl.dataset.sign;
		}
		clearFailure(panel.querySelector(".readings"));
	} catch (error) {
		panel.querySelector(".readings").dataset.stale = "true";
		console.error(`Error fetching ${brokerId} account data:`, error);
		throw error;
	}
}

async function updateOpenTrades(brokerId) {
	const container = brokerPanel(brokerId).querySelector('[data-activity="open"]');
	try {
		const response = await fetch(brokerQuery("/open_trades", brokerId));
		if (!response.ok) throw new Error(`HTTP ${response.status}`);
		const data = await response.json();
		const trades = asArray(data.open_trades);
		clearFailure(container);

		if (!trades.length) {
			renderMarkup(container, emptyState(
				"No trades are open.",
				"The bot places each signal at its entry time."
			), "empty:open");
			return;
		}

		const rows = trades.map((trade, index) => {
			const openPrice = toNumber(trade.openPrice ?? trade.open_price);
			const lastClose = toNumber(trade.current_price ?? trade.currentPrice);
			const points = formatPoints(trade.direction, openPrice, lastClose);
			const totalReturns = isNumber(trade.amount) && isNumber(trade.profit)
				? money.format(trade.amount + trade.profit)
				: "—";

			return {
				key: String(trade.trade_id ?? `${trade.asset ?? "trade"}:${trade.openedTime ?? index}`),
				html: `
					<td>${formatDirection(trade.direction)}</td>
					<td>${cell(trade.asset)}</td>
					<td class="num">${moneyOr(trade.amount)}</td>
					<td class="num">${isNumber(openPrice) ? openPrice : "—"}</td>
					<td class="${points.className}">${points.value}</td>
					<td class="num">${moneyOr(trade.profit)}</td>
					<td class="num">${totalReturns}</td>
					<td class="mono">${formatTimestamp(trade.openedTime)}</td>
				`,
			};
		});

		renderRows(container, "trades-table", `
			<th>Direction</th>
			<th>Asset</th>
			<th class="num">Amount</th>
			<th class="num">Open price</th>
			<th class="num">Points</th>
			<th class="num">Profit</th>
			<th class="num">Total returns</th>
			<th>Opened</th>`, rows);
	} catch (error) {
		renderFailure(container, "Couldn't read the open trades.");
		console.error(`Error fetching ${brokerId} open trades:`, error);
		throw error;
	}
}

async function updateCurrentSignals(brokerId) {
	const container = brokerPanel(brokerId).querySelector('[data-activity="signals"]');
	try {
		const response = await fetch(brokerQuery("/current_signals", brokerId));
		if (!response.ok) throw new Error(`HTTP ${response.status}`);
		const data = await response.json();
		const signals = asArray(data.signals);

		clearFailure(container);

		if (!signals.length) {
			renderMarkup(container, emptyState(
				"No signals are waiting.",
				"Nothing to place until the next alert arrives."
			), "empty:signals");
			return;
		}

		const rows = signals.map((signal, index) => ({
			key: String(signal.signal_id ?? `${signal.signal_provider ?? "signal"}:${signal.asset ?? ""}:${signal.entry_time ?? index}`),
			html: `
				<td>${cell(signal.signal_provider)}</td>
				<td>${cell(signal.asset)}</td>
				<td>${formatDirection(signal.direction)}</td>
				<td class="mono">${cell(signal.entry_time)}</td>
				<td>${cell(signal.status || "pending")}</td>`,
		}));

		renderRows(container, "signals-table", `
			<th>Signal provider</th>
			<th>Asset</th>
			<th>Direction</th>
			<th>Entry time</th>
			<th>Status</th>`, rows);
	} catch (error) {
		renderFailure(container, "Couldn't read the incoming signals.");
		console.error(`Error fetching ${brokerId} signals:`, error);
		throw error;
	}
}

async function updateClosedTrades(brokerId) {
	const container = brokerPanel(brokerId).querySelector('[data-activity="closed"]');
	try {
		const response = await fetch(brokerQuery("/closed_trades", brokerId));
		if (response.status === 404) {
			clearFailure(container);
			renderMarkup(container, `<div class="info"><p>This bot build doesn't publish closed trades.</p><p>Every other reading on the desk is live.</p></div>`, "info:closed-unavailable");
			return;
		}
		if (!response.ok) throw new Error(`HTTP ${response.status}`);
		const data = await response.json();
		/* Newest settlement first; the endpoint returns the full retained history. */
		const tradeEntries = Array.isArray(data.closed_trades)
			? data.closed_trades.map((trade, index) => [String(trade.trade_id ?? index), trade])
			: Object.entries(data.closed_trades || {});
		const trades = tradeEntries.slice().reverse();

		clearFailure(container);

		if (!trades.length) {
			renderMarkup(container, emptyState(
				"No trades have settled yet.",
				"Each trade appears here the moment its result comes back."
			), "empty:closed");
			return;
		}

		const rows = trades.map(([tradeId, trade]) => {
			const details = trade.trade_details || {};
			return {
				key: String(tradeId),
				html: `
					<td>${cell(details.signal_provider)}</td>
					<td>${formatOutcome(details.result)}</td>
					<td>${cell(details.asset)}</td>
					<td>${formatDirection(details.direction)}</td>
					<td class="mono">${cell(details.entry_time)}</td>
					<td class="num">${moneyOr(details.amount)}</td>
					<td class="num">${cell(details.level)}</td>
				`,
			};
		});

		renderRows(container, "closed-trade-table", `
			<th>Signal provider</th>
			<th>Outcome</th>
			<th>Asset</th>
			<th>Direction</th>
			<th>Entry time</th>
			<th class="num">Amount</th>
			<th class="num">Level</th>`, rows);
	} catch (error) {
		renderFailure(container, "Couldn't read the closed trades.");
		console.error(`Error fetching ${brokerId} closed trades:`, error);
		throw error;
	}
}

async function updateRiskData() {
	try {
		const response = await fetch("/get_risk_management");
		if (!response.ok) throw new Error(`HTTP ${response.status}`);
		const payload = await response.json();
		const values = payload.risk_values || {};

		lastRiskValues = values;
		lastRiskSettings = {
			global: payload.global_risk_values || values,
			overrides: payload.broker_overrides || {},
			effective: payload.effective_risk_by_broker || {},
		};
		if (els.riskProfileMeta) {
			els.riskProfileMeta.textContent = "Global defaults apply to each broker unless overridden.";
		}
		els.limitsState.innerHTML = "";
		clearFailure(els.limits);

		for (const [key, value] of Object.entries(values)) {
			const target = document.getElementById(`${key}-risk-result`);
			if (!target) continue;
			target.textContent = typeof value === "boolean" ? (value ? "on" : "off") : displayValue(value);
		}

		renderLadder(values);
	} catch (error) {
		els.limits.dataset.stale = "true";
		els.limitsState.innerHTML = errorState("Couldn't read the risk limits.");
		console.error("Error fetching risk data:", error);
		throw error;
	}
}

/* --- The exposure ladder ------------------------------------------------ */

/*
 * One sequence can run martingale_levels recovery legs after its opening
 * stake, so the worst case is the sum of every leg. Legs above
 * max_trade_amount, or a total above max_sequence_exposure, are refused by
 * the bot before an order is placed - this shows the operator where that
 * happens before it does.
 */
function renderLadder(values) {
	const initial = toNumber(values.initial_amount);
	const multiplier = toNumber(values.martingale_multiplier);
	const levels = toNumber(values.martingale_levels);
	const maxTrade = toNumber(values.max_trade_amount);
	const ceiling = toNumber(values.max_sequence_exposure);
	const concurrent = toNumber(values.max_open_trades);
	const recoveryOn = values.martingale_enabled !== false;

	if (!isNumber(initial) || initial <= 0 || !isNumber(multiplier) || multiplier < 1) {
		els.ladder.innerHTML = `
			<p class="ladder__caption">Worst case for one sequence</p>
			<ol class="ladder__legs">
				<li class="ladder__leg ladder__leg--pending">The limits are not readable yet.</li>
			</ol>`;
		return;
	}

	const legCount = recoveryOn ? Math.max(1, Math.floor(levels) + 1) : 1;
	const stakes = [];
	for (let index = 0; index < legCount; index += 1) {
		stakes.push(initial * Math.pow(multiplier, index));
	}
	const total = stakes.reduce((sum, stake) => sum + stake, 0);
	const widest = Math.max(...stakes);
	const shown = stakes.slice(0, MAX_LADDER_LEGS);

	const legRows = shown.map((stake, index) => {
		const over = isNumber(maxTrade) && stake > maxTrade;
		const width = ((stake / widest) * 100).toFixed(2);
		return `
			<li class="ladder__leg">
				<span class="ladder__leg-index">${index + 1}</span>
				<span class="ladder__rail"><span class="ladder__fill${over ? " ladder__fill--over" : ""}" style="width:${width}%"></span></span>
				<span class="ladder__stake${over ? " ladder__stake--over" : ""}">${money.format(stake)}</span>
			</li>`;
	}).join("");

	const hiddenLegs = stakes.length - shown.length;
	const moreRow = hiddenLegs > 0
		? `<li class="ladder__leg ladder__leg--pending">${hiddenLegs} further legs, the last staking ${money.format(stakes[stakes.length - 1])}.</li>`
		: "";

	const totals = [
		["Recovery legs", recoveryOn ? String(legCount - 1) : "off", false],
		["Multiplier", `\u00d7${multiplier}`, false],
		["Per sequence", money.format(total), isNumber(ceiling) && total > ceiling],
	];
	if (isNumber(ceiling)) {
		totals.push(["Ceiling", money.format(ceiling), false]);
	}
	if (isNumber(concurrent) && concurrent > 1) {
		totals.push([`${concurrent} sequences at once`, money.format(total * concurrent), false]);
	}

	const totalRows = totals.map(([label, value, over]) =>
		`<div class="ladder__total${over ? " ladder__total--over" : ""}"><dt>${label}</dt><dd>${value}</dd></div>`
	).join("");

	const warnings = [];
	if (isNumber(ceiling) && total > ceiling) {
		warnings.push(`A full sequence risks ${money.format(total)}, above the ceiling of ${money.format(ceiling)}, so the bot refuses it before the first leg.`);
	}
	const overLegIndex = shown.findIndex((stake) => isNumber(maxTrade) && stake > maxTrade);
	if (overLegIndex !== -1) {
		warnings.push(`Leg ${overLegIndex + 1} stakes ${money.format(shown[overLegIndex])}, above the max trade amount of ${money.format(maxTrade)}, so that leg would be refused.`);
	}

	els.ladder.innerHTML = `
		<p class="ladder__caption">${recoveryOn ? "Worst case for one sequence" : "Recovery is off, so a loss ends the sequence"}</p>
		<ol class="ladder__legs">${legRows}${moreRow}</ol>
		<dl class="ladder__totals">${totalRows}</dl>
		${warnings.map((warning) => `<p class="ladder__warn">${warning}</p>`).join("")}`;
}

/* --- The risk sheet ----------------------------------------------------- */

let lastRiskValues = {};
let lastRiskSettings = { global: {}, overrides: {}, effective: {} };
let riskEditorProfile = "global";

function showFormError(message) {
	els.formError.textContent = message;
	els.formError.hidden = false;
}

function clearFormError() {
	els.formError.textContent = "";
	els.formError.hidden = true;
}

function setFieldValue(name, value) {
	const field = els.form.elements.namedItem(name);
	if (field) {
		field.value = value === undefined || value === null ? "" : String(value);
	}
}

/* The bot stores Etc/GMT-2 for a +2 offset; the field shows the +2. */
function toOffsetInput(value) {
	const match = /^Etc\/GMT([+-])(\d{1,2})$/.exec(String(value ?? ""));
	if (!match) return "";
	return (match[1] === "-" ? "+" : "-") + match[2];
}

function toEtcTimezone(value) {
	const match = /^([+-])(\d|1[0-4])$/.exec(String(value ?? "").trim());
	if (!match) return null;
	return `Etc/GMT${match[1] === "+" ? "-" : "+"}${match[2]}`;
}

function prefillRiskForm() {
	clearFormError();
	const globalProfile = riskEditorProfile === "global";
	const values = globalProfile
		? lastRiskSettings.global
		: lastRiskSettings.overrides[riskEditorProfile] || {};
	for (const field of RISK_NUMBER_FIELDS) {
		const value = field === "drawback_threshold" && values[field] !== undefined
			? Math.abs(toNumber(values[field]))
			: values[field];
		setFieldValue(field, value);
	}
	setFieldValue("local_timezone", toOffsetInput(lastRiskSettings.global.local_timezone));
	const martingale = els.form.elements.namedItem("martingale_enabled");
	martingale.value = values.martingale_enabled === undefined
		? globalProfile ? String(lastRiskSettings.global.martingale_enabled) : ""
		: String(values.martingale_enabled);
	els.riskGlobalTimezone.hidden = !globalProfile;
	els.form.elements.namedItem("local_timezone").required = globalProfile;
	for (const field of RISK_NUMBER_FIELDS) {
		els.form.elements.namedItem(field).required = globalProfile;
	}
	els.riskProfileHint.textContent = globalProfile
		? "Global defaults apply to both brokers unless a broker overrides a field."
		: "Leave a field blank to inherit its global value. Clear every field to remove this broker's overrides.";
}

let lastFocused = null;

function openRiskEditor(profile) {
	riskEditorProfile = profile;
	els.riskProfile.value = profile;
	showriskform();
}

function showriskform() {
	const opening = els.modal.classList.contains("hidden");

	if (opening) {
		lastFocused = document.activeElement;
		prefillRiskForm();
	}

	els.modal.classList.toggle("hidden", !opening);
	document.body.style.overflow = opening ? "hidden" : "";

	if (opening) {
		const firstField = els.form.querySelector("input");
		if (firstField) firstField.focus();
	} else if (lastFocused && typeof lastFocused.focus === "function") {
		lastFocused.focus();
	}
}

/*
 * Global saves send every editable risk field because the endpoint validates
 * a complete profile. Broker saves omit blank fields so they inherit global
 * values; the API validates and persists that sparse override.
 */
async function setRiskData(form) {
	const raw = Object.fromEntries(new FormData(form).entries());
	const globalProfile = riskEditorProfile === "global";
	const payload = {};
	for (const field of RISK_NUMBER_FIELDS) {
		if (raw[field] === "") {
			if (globalProfile) {
				showFormError("Every global risk value needs a number.");
				return false;
			}
			continue;
		}
		const value = toNumber(raw[field]);
		if (!isNumber(value)) {
			showFormError("Risk values must be valid numbers.");
			return false;
		}
		if (INTEGER_FIELDS.includes(field) && !Number.isInteger(value)) {
			showFormError("Trade duration, counts, levels, timeouts and the threshold must be whole numbers.");
			return false;
		}
		if (field === "drawback_threshold") {
			payload[field] = -Math.abs(value);
		} else {
			payload[field] = value;
		}
	}

	const martingale = raw.martingale_enabled;
	if (martingale !== "") {
		payload.martingale_enabled = martingale === "true";
	} else if (globalProfile) {
		showFormError("Choose whether recovery trades are enabled.");
		return false;
	}

	if (globalProfile) {
		const timezone = toEtcTimezone(raw.local_timezone);
		if (!timezone) {
			showFormError("Write the bot timezone as +2 or -3, in whole hours from 0 to 14.");
			return false;
		}
		payload.local_timezone = timezone;
	}

	const endpoint = globalProfile
		? "/set_risk_management"
		: `/risk_management/brokers/${encodeURIComponent(riskEditorProfile)}`;

	try {
		const response = await fetch(endpoint, {
			method: globalProfile ? "POST" : "PUT",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify(payload),
		});

		if (!response.ok) {
			let detail = `The bot rejected these risk settings (HTTP ${response.status}).`;
			try {
				const body = await response.json();
				if (typeof body.detail === "string") detail = body.detail;
			} catch (error) {
				/* Not JSON: keep the status message. */
			}
			showFormError(detail);
			return false;
		}

		clearFormError();
		form.reset();
		showriskform();

		try {
			await updateRiskData();
		} catch (error) {
			/* The rail shows its own error state. */
		}
		return true;
	} catch (error) {
		showFormError("Couldn't reach the bot, so these risk settings were not saved.");
		console.error("Error setting risk settings:", error);
		return false;
	}
}

async function handleRiskSubmit(event) {
	event.preventDefault();
	if (!els.form.reportValidity()) return;

	const submit = els.form.querySelector('button[type="submit"]');
	const originalLabel = submit?.textContent;
	if (submit) {
		submit.disabled = true;
		submit.setAttribute("aria-busy", "true");
		submit.textContent = "Saving…";
	}
	try {
		await setRiskData(els.form);
	} finally {
		if (submit) {
			submit.disabled = false;
			submit.removeAttribute("aria-busy");
			submit.textContent = originalLabel;
		}
	}
}

/* --- Signal channels ---------------------------------------------------- */

/*
 * Every change is saved whole: the bot replaces the channel file in one
 * atomic write and reloads the listener. There is no unsaved state to lose
 * when the five-second poll re-renders the table underneath an open editor.
 */
let lastChannels = [];
let channelStatus = [];
let listenerEnabled = false;
let timezoneChoicesFilled = false;
let editingIndex = null;

/* "Etc/GMT-2" is stored but "GMT+2" is what the channel actually printed. */
function timezoneLabel(value) {
	if (!value) return "not configured";
	const offset = toOffsetInput(value);
	return offset ? `GMT${offset}` : String(value);
}

function channelPayload(entry) {
	return {
		name: entry.name || null,
		channel: entry.channel,
		timezone: entry.timezone || null,
		provider: entry.provider || null,
		broker: entry.broker || null,
		enabled: entry.enabled !== false,
	};
}

function isEnabled(entry) {
	return entry.enabled !== false;
}

function liveStatusFor(channel) {
	return channelStatus.find((status) => String(status.channel) === String(channel)) || null;
}

function setChannelNote(text) {
	els.channelNote.textContent = text;
}

function renderChannels() {
	if (!lastChannels.length) {
		els.channels.innerHTML = emptyState(
			"No channels are configured.",
			"Add one to give the Telegram listener something to read."
		);
		setChannelNote(listenerEnabled ? "" : "The listener is switched off in .env, so nothing is read.");
		return;
	}

	const rows = lastChannels.map((entry, index) => {
		const status = entry.problem
			? `<span class="tag tag--bad">invalid</span>`
			: isEnabled(entry)
				? `<span class="tag tag--on">reading</span>`
				: `<span class="tag tag--off">paused</span>`;
		const live = liveStatusFor(entry.channel);
		const problem = entry.problem
			? `<span class="channel-problem">${escapeHtml(entry.problem)}</span>`
			: "";
		const route = entry.broker ? brokerName(entry.broker) : "not configured";

		return `
			<tr>
				<td>${status}</td>
				<td>${cell(entry.name)}</td>
				<td class="mono channel-cell">${cell(entry.channel)}${problem}</td>
				<td class="mono">${escapeHtml(timezoneLabel(entry.timezone))}</td>
				<td>${cell(entry.provider)}</td>
				<td>${escapeHtml(route)}</td>
				<td class="num">${live && live.last_message_id != null ? escapeHtml(String(live.last_message_id)) : "—"}</td>
				<td class="channel-actions">
					<button class="button button--quiet" type="button" data-action="edit" data-index="${index}">Edit</button>
					<button class="button button--quiet" type="button" data-action="toggle" data-index="${index}">${isEnabled(entry) ? "Pause" : "Resume"}</button>
				</td>
			</tr>`;
	}).join("");

	els.channels.innerHTML = `
		<table class="channels-table">
			<thead>
				<tr>
					<th>State</th>
					<th>Label</th>
					<th>Channel</th>
					<th>Timezone</th>
					<th>Provider</th>
					<th>Trade destination</th>
					<th class="num">Last id</th>
					<th></th>
				</tr>
			</thead>
			<tbody>${rows}</tbody>
		</table>`;

	const reading = lastChannels.filter((entry) => isEnabled(entry) && !entry.problem).length;
	const invalid = lastChannels.filter((entry) => entry.problem).length;
	const parts = [];
	if (!listenerEnabled) {
		parts.push("The listener is switched off in .env, so nothing is read yet.");
	} else {
		parts.push(`Reading ${reading} of ${lastChannels.length} channel${lastChannels.length === 1 ? "" : "s"}.`);
	}
	if (invalid) {
		parts.push(`${invalid} entr${invalid === 1 ? "y is" : "ies are"} invalid and skipped until corrected.`);
	}
	setChannelNote(parts.join(" "));
}

function fillTimezoneChoices(choices) {
	if (timezoneChoicesFilled || !els.channelTimezone) return;
	const fragment = document.createDocumentFragment();
	for (const choice of choices) {
		if (!choice || !choice.value) continue;
		const option = document.createElement("option");
		option.value = choice.value;
		option.textContent = choice.label || choice.value;
		fragment.append(option);
	}
	els.channelTimezone.append(fragment);
	timezoneChoicesFilled = true;
}

async function updateChannels() {
	try {
		const response = await fetch("/get_channels");
		if (!response.ok) throw new Error(`HTTP ${response.status}`);
		const data = await response.json();

		lastChannels = Array.isArray(data.channels) ? data.channels : [];
		channelStatus = Array.isArray(data.listener_channels) ? data.listener_channels : [];
		listenerEnabled = data.listener_enabled === true;
		fillTimezoneChoices(Array.isArray(data.timezone_choices) ? data.timezone_choices : []);

		clearFailure(els.channels);
		renderChannels();
	} catch (error) {
		renderFailure(els.channels, "Couldn't read the channel list.");
		console.error("Error fetching channels:", error);
		throw error;
	}
}

async function saveChannels(channels) {
	try {
		const response = await fetch("/set_channels", {
			method: "POST",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify({ channels }),
		});

		if (!response.ok) {
			let detail = `The bot rejected this list (HTTP ${response.status}).`;
			try {
				const body = await response.json();
				if (typeof body.detail === "string") detail = body.detail;
			} catch (error) {
				/* Not JSON: keep the status message. */
			}
			return { ok: false, detail };
		}

		const body = await response.json();
		return { ok: true, message: typeof body.message === "string" ? body.message : "Saved" };
	} catch (error) {
		console.error("Error saving channels:", error);
		return { ok: false, detail: "Couldn't reach the bot, so nothing was saved." };
	}
}

/* --- The channel sheet -------------------------------------------------- */

function showChannelError(message) {
	els.channelFormError.textContent = message;
	els.channelFormError.hidden = false;
}

function clearChannelError() {
	els.channelFormError.textContent = "";
	els.channelFormError.hidden = true;
}

function setChannelField(name, value) {
	const field = els.channelForm.elements.namedItem(name);
	if (field) field.value = value === undefined || value === null ? "" : String(value);
}

let lastChannelFocus = null;

function showchannelform() {
	const opening = els.channelModal.classList.contains("hidden");

	if (opening) {
		lastChannelFocus = document.activeElement;
	}

	els.channelModal.classList.toggle("hidden", !opening);
	document.body.style.overflow = opening ? "hidden" : "";

	if (opening) {
		const firstField = els.channelForm.querySelector("input");
		if (firstField) firstField.focus();
	} else if (lastChannelFocus && typeof lastChannelFocus.focus === "function") {
		lastChannelFocus.focus();
	}
}

function openChannelForm(index) {
	editingIndex = typeof index === "number" ? index : null;
	const entry = editingIndex === null ? null : lastChannels[editingIndex] || null;

	clearChannelError();
	els.channelForm.reset();
	setChannelField("channel", entry ? entry.channel : "");
	setChannelField("name", entry ? entry.name : "");
	setChannelField("provider", entry ? entry.provider : "");
	setChannelField("broker", entry ? entry.broker : "");
	setChannelField("timezone", entry && entry.timezone ? entry.timezone : "");
	els.channelForm.elements.namedItem("enabled").checked = entry ? isEnabled(entry) : true;

	els.channelHeading.textContent = entry ? "Edit channel" : "Add channel";
	els.deleteChannel.hidden = !entry;

	showchannelform();
}

function readChannelForm() {
	const raw = Object.fromEntries(new FormData(els.channelForm).entries());
	return {
		name: String(raw.name || "").trim() || null,
		channel: String(raw.channel || "").trim(),
		timezone: String(raw.timezone || "").trim() || null,
		provider: String(raw.provider || "").trim() || null,
		broker: String(raw.broker || "").trim() || null,
		enabled: els.channelForm.elements.namedItem("enabled").checked,
	};
}

function payloadList() {
	return lastChannels.map(channelPayload);
}

async function handleChannelSubmit(event) {
	event.preventDefault();
	if (!els.channelForm.reportValidity()) return;

	const entry = readChannelForm();
	const next = payloadList();
	if (editingIndex === null) {
		next.push(entry);
	} else {
		next[editingIndex] = entry;
	}

	const submit = els.channelForm.querySelector('button[type="submit"]');
	const originalLabel = submit?.textContent;
	if (submit) {
		submit.disabled = true;
		submit.setAttribute("aria-busy", "true");
		submit.textContent = "Saving…";
	}
	let result;
	try {
		result = await saveChannels(next);
	} finally {
		if (submit) {
			submit.disabled = false;
			submit.removeAttribute("aria-busy");
			submit.textContent = originalLabel;
		}
	}

	if (!result.ok) {
		showChannelError(result.detail);
		return;
	}

	clearChannelError();
	showchannelform();
	setChannelNote(result.message);
	try {
		await updateChannels();
	} catch (error) {
		/* The table shows its own error state. */
	}
}

async function toggleChannel(index) {
	const entry = lastChannels[index];
	if (!entry) return;

	const next = payloadList();
	next[index] = { ...next[index], enabled: !isEnabled(entry) };

	const result = await saveChannels(next);
	setChannelNote(result.ok ? result.message : result.detail);
	try {
		await updateChannels();
	} catch (error) {
		/* The table shows its own error state. */
	}
}

async function deleteChannel() {
	if (editingIndex === null) return;
	const entry = lastChannels[editingIndex];
	if (!entry) return;

	if (lastChannels.length === 1) {
		showChannelError("Keep at least one channel: the listener needs a source to read.");
		return;
	}
	if (!window.confirm(`Remove ${entry.name || entry.channel} from the listener?`)) return;

	const next = payloadList();
	next.splice(editingIndex, 1);

	const result = await saveChannels(next);
	if (!result.ok) {
		showChannelError(result.detail);
		return;
	}

	clearChannelError();
	showchannelform();
	setChannelNote(result.message);
	try {
		await updateChannels();
	} catch (error) {
		/* The table shows its own error state. */
	}
}

/* --- Connection state --------------------------------------------------- */

let lastSuccessAt = null;
let refreshing = false;
let flashReady = false;

function pulse() {
	if (els.status.dataset.state !== "live") return;
	els.status.classList.remove("is-tick");
	void els.status.offsetWidth;
	els.status.classList.add("is-tick");
	window.setTimeout(() => els.status.classList.remove("is-tick"), 800);
}

function setStatus(state) {
	if (els.status.dataset.state !== state) {
		els.status.dataset.state = state;
		/* "Updating" describes the readings themselves, so it cannot be
		   mistaken for the demo/live mode of the broker account. */
		els.statusLabel.textContent = state === "live" ? "Updating" : state === "stalled" ? "Not updating" : "Connecting";
	}
	if (state === "live") pulse();
}

function renderTimestamp() {
	if (lastSuccessAt === null) {
		els.statusTime.textContent = "no reading yet";
		return;
	}
	const seconds = Math.max(0, Math.round((Date.now() - lastSuccessAt) / 1000));
	const phrase = seconds < 2 ? "just now" : seconds < 60 ? `${seconds}s ago` : `${Math.round(seconds / 60)}m ago`;
	els.statusTime.textContent = els.status.dataset.state === "stalled" ? `last reading ${phrase}` : `updated ${phrase}`;
}

async function refreshAll() {
	if (refreshing || document.hidden) return;
	refreshing = true;

	let brokerStatusFailed = false;
	let results;
	if (document.body.dataset.page === "settings") {
		results = await Promise.allSettled([updateRiskData(), updateChannels()]);
	} else {
		try {
			await updateBrokerStatus();
		} catch (error) {
			brokerStatusFailed = true;
			renderFailure(els.brokers, "Couldn't read broker connection status.");
			console.error("Error fetching broker status:", error);
		}
		results = brokerStatusFailed
			? []
			: await Promise.allSettled(BROKER_IDS.map(updateBrokerData));
	}

	refreshing = false;

	if (!brokerStatusFailed && results.every((result) => result.status === "fulfilled")) {
		lastSuccessAt = Date.now();
		setStatus("live");
		if (!flashReady) {
			flashReady = true;
			observeValueChanges();
		}
	} else {
		setStatus("stalled");
	}

	renderTimestamp();
}

function updateBrokerData(brokerId) {
	const status = brokerStatuses.find((broker) => broker.id === brokerId);
	const panel = brokerPanel(brokerId);
	if (!status?.connected) {
		const state = status?.configured
			? "Connect this broker to read its account and activity."
			: "A broker session is required. Follow the capture command above, then connect.";
		for (const key of ["signals", "open", "closed"]) {
			const container = panel.querySelector(`[data-activity="${key}"]`);
			renderMarkup(container, emptyState(
				"Broker is not connected.",
				state
			), `disconnected:${state}`);
		}
		for (const key of ["balance", "pnl", "lifespan"]) {
			setTextContent(panel.querySelector(`[data-account="${key}"]`), "—");
		}
		return Promise.resolve();
	}
	return Promise.all([
		updateBalance(brokerId),
		updateCurrentSignals(brokerId),
		updateOpenTrades(brokerId),
		updateClosedTrades(brokerId),
	]);
}

/* Values that moved between polls say so, then settle. */
function observeValueChanges() {
	const hosts = document.querySelectorAll("[data-flash]");
	if (!hosts.length || typeof MutationObserver === "undefined") return;

	const observer = new MutationObserver((records) => {
		const touched = new Set();
		for (const record of records) {
			const node = record.target.nodeType === 1 ? record.target : record.target.parentElement;
			const host = node && node.closest ? node.closest("[data-flash]") : null;
			if (!host || touched.has(host)) continue;
			touched.add(host);
			host.classList.add("is-changed");
			window.setTimeout(() => host.classList.remove("is-changed"), 900);
		}
	});

	for (const host of hosts) {
		observer.observe(host, { childList: true, characterData: true, subtree: true });
	}
}

/* --- Theme -------------------------------------------------------------- */

function applyTheme(theme, persist) {
	const light = theme === "light";
	document.documentElement.dataset.theme = light ? "light" : "dark";
	if (els.themeToggle) {
		els.themeToggle.setAttribute("aria-pressed", String(light));
		els.themeToggle.setAttribute("aria-label", light ? "Switch to the dark theme" : "Switch to the light theme");
	}

	const themeColor = document.querySelector('meta[name="theme-color"]');
	if (themeColor) themeColor.setAttribute("content", light ? "#EFF1F2" : "#0E1418");

	if (persist) {
		try {
			localStorage.setItem(THEME_KEY, light ? "light" : "dark");
		} catch (error) {
			/* Private mode: the choice just won't outlive the tab. */
		}
	}
}

function applyLayout(layout, persist = true) {
	const selectedLayout = layout === "stacked" ? "stacked" : "adaptive";
	document.documentElement.dataset.layout = selectedLayout;
	document.body.dataset.layout = selectedLayout;
	if (els.layoutChoice) els.layoutChoice.value = selectedLayout;
	if (persist) {
		try {
			localStorage.setItem(LAYOUT_KEY, selectedLayout);
		} catch (error) {
			/* Private mode: the choice just won't outlive the tab. */
		}
	}
}

/* --- Start -------------------------------------------------------------- */

document.addEventListener("DOMContentLoaded", () => {
	applyTheme(document.documentElement.dataset.theme === "light" ? "light" : "dark", false);
	applyLayout(document.documentElement.dataset.layout, false);

	els.themeToggle?.addEventListener("click", () => {
		applyTheme(document.documentElement.dataset.theme === "light" ? "dark" : "light", true);
	});
	els.layoutChoice?.addEventListener("change", () => {
		applyLayout(els.layoutChoice.value);
	});

	const settingsPage = document.body.dataset.page === "settings";
	if (settingsPage) {
		els.openForm.addEventListener("click", () => openRiskEditor("global"));
		els.openBrokerRiskForm.addEventListener("click", () => openRiskEditor("quotex"));
		els.cancelForm.addEventListener("click", showriskform);
		els.form.addEventListener("submit", handleRiskSubmit);
		els.riskProfile.addEventListener("change", () => {
			riskEditorProfile = els.riskProfile.value;
			prefillRiskForm();
		});

		els.addChannel.addEventListener("click", () => openChannelForm(null));
		els.cancelChannel.addEventListener("click", showchannelform);
		els.deleteChannel.addEventListener("click", deleteChannel);
		els.channelForm.addEventListener("submit", handleChannelSubmit);

		els.channels.addEventListener("click", (event) => {
			const button = event.target.closest("button[data-action]");
			if (!button) return;
			const index = Number(button.dataset.index);
			if (!Number.isInteger(index)) return;
			if (button.dataset.action === "edit") openChannelForm(index);
			else if (button.dataset.action === "toggle") toggleChannel(index);
		});

		els.modal.addEventListener("click", (event) => {
			if (event.target === els.modal) showriskform();
		});

		els.channelModal.addEventListener("click", (event) => {
			if (event.target === els.channelModal) showchannelform();
		});
	} else {
		els.brokers.addEventListener("click", (event) => {
			const button = event.target.closest("button[data-broker-action]");
			if (!button) return;
			const brokerId = button.dataset.brokerId;
			if (button.dataset.brokerAction === "connect") {
				connectBroker(brokerId, button);
			}
		});
	}

	document.addEventListener("keydown", (event) => {
		if (!settingsPage) return;

		const activeModal = !els.channelModal.classList.contains("hidden")
			? els.channelModal
			: !els.modal.classList.contains("hidden")
				? els.modal
				: null;
		if (!activeModal) return;

		if (event.key === "Escape") {
			if (activeModal === els.channelModal) showchannelform();
			else showriskform();
			return;
		}
		if (event.key !== "Tab") return;

		const dialog = activeModal.querySelector('[role="dialog"]');
		const focusable = Array.from(dialog.querySelectorAll(
			'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
		)).filter((element) => !element.hidden && element.getClientRects().length > 0);
		if (!focusable.length) {
			event.preventDefault();
			return;
		}

		const first = focusable[0];
		const last = focusable[focusable.length - 1];
		if (event.shiftKey && (document.activeElement === first || !dialog.contains(document.activeElement))) {
			event.preventDefault();
			last.focus();
		} else if (!event.shiftKey && (document.activeElement === last || !dialog.contains(document.activeElement))) {
			event.preventDefault();
			first.focus();
		}
	});

	/* Refresh on return: polling pauses while the tab is in the background. */
	document.addEventListener("visibilitychange", () => {
		if (!document.hidden) refreshAll();
	});

	refreshAll();
	window.setInterval(refreshAll, POLL_INTERVAL_MS);
	window.setInterval(renderTimestamp, 1000);
});
