/* ==========================================================================
   Pocket Option Signal Bot — desk client

   Reads five endpoints and renders them as a ledger. The risk ladder mirrors
   the checks in main.py: a sequence has martingale_levels + 1 legs, per-leg
   stakes multiply by martingale_multiplier, and a leg above max_trade_amount
   is refused before it is placed. If those rules change in main.py, change
   them here too.
   ========================================================================== */

"use strict";

const POLL_INTERVAL_MS = 5000;
const THEME_KEY = "signal-desk-theme";
const RETRY_HINT = "The desk retries every 5 seconds. If this keeps up, check the bot's log.";
const MAX_LADDER_LEGS = 12;

const NUMBER_FIELDS = ["initial_amount", "martingale_levels", "martingale_multiplier", "timeframe", "drawback_threshold"];
const INTEGER_FIELDS = ["martingale_levels", "timeframe", "drawback_threshold"];

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
	status: document.getElementById("connection-state"),
	statusLabel: document.getElementById("connection-state-label"),
	statusTime: document.getElementById("last-updated"),
	themeToggle: document.getElementById("theme-toggle"),
	modal: document.getElementById("popup_bg"),
	form: document.getElementById("risk-form-element"),
	formError: document.getElementById("risk-form-error"),
	openForm: document.getElementById("set-risk-button"),
	cancelForm: document.getElementById("cancel-risk-button"),
};

const HTML_ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

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

/*
 * A failed read keeps the last known table on screen, dimmed and labelled,
 * so open positions stay readable while the desk retries.
 */
function renderFailure(container, headline) {
	const table = container.querySelector("table");
	container.innerHTML = table ? `${errorState(headline)}${table.outerHTML}` : errorState(headline);
	if (table) {
		container.dataset.stale = "true";
	} else {
		delete container.dataset.stale;
	}
}

function clearFailure(container) {
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

async function updateBalance() {
	try {
		const response = await fetch("/account_details");
		if (!response.ok) throw new Error(`HTTP ${response.status}`);
		const data = await response.json();

		els.balance.textContent = isNumber(data.balance) ? money.format(data.balance) : displayValue(data.balance);
		els.pnl.textContent = isNumber(data.P_n_L_day) ? signedMoney.format(data.P_n_L_day) : displayValue(data.P_n_L_day);
		els.lifespan.textContent = displayValue(data.lifespan);

		if (isNumber(data.P_n_L_day) && data.P_n_L_day !== 0) {
			els.pnl.dataset.sign = data.P_n_L_day > 0 ? "pos" : "neg";
		} else {
			delete els.pnl.dataset.sign;
		}

		clearFailure(els.readings);
	} catch (error) {
		els.readings.dataset.stale = "true";
		console.error("Error fetching balance data:", error);
		throw error;
	}
}

async function updateOpenTrades() {
	try {
		const response = await fetch("/open_trades");
		if (!response.ok) throw new Error(`HTTP ${response.status}`);
		const data = await response.json();
		const trades = asArray(data.open_trades);

		clearFailure(els.openTrades);

		if (!trades.length) {
			els.openTrades.innerHTML = emptyState(
				"No trades are open.",
				"The bot places each signal at its entry time."
			);
			return;
		}

		const rows = trades.map((trade) => {
			const openPrice = toNumber(trade.openPrice ?? trade.open_price);
			const lastClose = toNumber(trade.current_price ?? trade.currentPrice);
			const points = formatPoints(trade.direction, openPrice, lastClose);
			const totalReturns = isNumber(trade.amount) && isNumber(trade.profit)
				? money.format(trade.amount + trade.profit)
				: "—";

			return `
				<tr>
					<td>${formatDirection(trade.direction)}</td>
					<td>${cell(trade.asset)}</td>
					<td class="num">${moneyOr(trade.amount)}</td>
					<td class="num">${isNumber(openPrice) ? openPrice : "—"}</td>
					<td class="${points.className}">${points.value}</td>
					<td class="num">${moneyOr(trade.profit)}</td>
					<td class="num">${totalReturns}</td>
					<td class="mono">${formatTimestamp(trade.openedTime)}</td>
				</tr>`;
		}).join("");

		els.openTrades.innerHTML = `
			<table class="trades-table">
				<thead>
					<tr>
						<th>Direction</th>
						<th>Asset</th>
						<th class="num">Amount</th>
						<th class="num">Open price</th>
						<th class="num">Points</th>
						<th class="num">Profit</th>
						<th class="num">Total returns</th>
						<th>Opened</th>
					</tr>
				</thead>
				<tbody>${rows}</tbody>
			</table>`;
	} catch (error) {
		renderFailure(els.openTrades, "Couldn't read the open trades.");
		console.error("Error fetching open trades:", error);
		throw error;
	}
}

async function updateCurrentSignals() {
	try {
		const response = await fetch("/current_signals");
		if (!response.ok) throw new Error(`HTTP ${response.status}`);
		const data = await response.json();
		const signals = asArray(data.signals);

		clearFailure(els.signals);

		if (!signals.length) {
			els.signals.innerHTML = emptyState(
				"No signals are waiting.",
				"Nothing to place until the next alert arrives."
			);
			return;
		}

		const rows = signals.map((signal) => `
			<tr>
				<td>${cell(signal.signal_provider)}</td>
				<td>${cell(signal.asset)}</td>
				<td>${formatDirection(signal.direction)}</td>
				<td class="mono">${cell(signal.entry_time)}</td>
			</tr>`).join("");

		els.signals.innerHTML = `
			<table class="signals-table">
				<thead>
					<tr>
						<th>Signal provider</th>
						<th>Asset</th>
						<th>Direction</th>
						<th>Entry time</th>
					</tr>
				</thead>
				<tbody>${rows}</tbody>
			</table>`;
	} catch (error) {
		renderFailure(els.signals, "Couldn't read the incoming signals.");
		console.error("Error fetching current signals:", error);
		throw error;
	}
}

async function updateClosedTrades() {
	try {
		const response = await fetch("/closed_trades");
		if (response.status === 404) {
			clearFailure(els.closedTrades);
			els.closedTrades.innerHTML = `<div class="info"><p>This bot build doesn't publish closed trades.</p><p>Every other reading on the desk is live.</p></div>`;
			return;
		}
		if (!response.ok) throw new Error(`HTTP ${response.status}`);
		const data = await response.json();
		/* Newest settlement first: the endpoint keeps the last ten in order. */
		const trades = asArray(data.closed_trades).slice().reverse();

		clearFailure(els.closedTrades);

		if (!trades.length) {
			els.closedTrades.innerHTML = emptyState(
				"No trades have settled yet.",
				"Each trade appears here the moment its result comes back."
			);
			return;
		}

		const rows = trades.map((trade) => {
			const details = trade.trade_details || {};
			return `
				<tr>
					<td>${cell(details.signal_provider)}</td>
					<td>${formatOutcome(details.result)}</td>
					<td>${cell(details.asset)}</td>
					<td>${formatDirection(details.direction)}</td>
					<td class="mono">${cell(details.entry_time)}</td>
					<td class="num">${moneyOr(details.amount)}</td>
					<td class="num">${cell(details.level)}</td>
				</tr>`;
		}).join("");

		els.closedTrades.innerHTML = `
			<table class="closed-trade-table">
				<thead>
					<tr>
						<th>Signal provider</th>
						<th>Outcome</th>
						<th>Asset</th>
						<th>Direction</th>
						<th>Entry time</th>
						<th class="num">Amount</th>
						<th class="num">Level</th>
					</tr>
				</thead>
				<tbody>${rows}</tbody>
			</table>`;
	} catch (error) {
		renderFailure(els.closedTrades, "Couldn't read the closed trades.");
		console.error("Error fetching closed trades:", error);
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
	if (field && value !== undefined && value !== null && value !== "") {
		field.value = String(value);
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
	const values = lastRiskValues || {};
	setFieldValue("initial_amount", values.initial_amount);
	setFieldValue("martingale_levels", values.martingale_levels);
	setFieldValue("martingale_multiplier", values.martingale_multiplier);
	setFieldValue("timeframe", values.timeframe);
	setFieldValue("local_timezone", toOffsetInput(values.local_timezone));
	const drawback = toNumber(values.drawback_threshold);
	setFieldValue("drawback_threshold", isNumber(drawback) ? Math.abs(drawback) : "");
}

let lastFocused = null;

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
 * Only the six fields on the sheet are edited here; everything else the bot
 * holds (concurrency caps, balance reserve, recovery on/off) is sent back
 * unchanged. POSTing a partial body would let Pydantic fill those with
 * defaults and silently widen the limits.
 */
async function setRiskData(form) {
	const raw = Object.fromEntries(new FormData(form).entries());

	const timezone = toEtcTimezone(raw.local_timezone);
	if (!timezone) {
		showFormError("Write the timezone as +2 or -3, in whole hours from 0 to 14.");
		return false;
	}

	const numbers = {};
	for (const field of NUMBER_FIELDS) {
		const value = toNumber(raw[field]);
		if (!isNumber(value)) {
			showFormError("Every field on this sheet needs a number.");
			return false;
		}
		if (INTEGER_FIELDS.includes(field) && !Number.isInteger(value)) {
			showFormError("Levels, trade duration and the threshold must be whole numbers.");
			return false;
		}
		numbers[field] = value;
	}

	if (numbers.initial_amount <= 0 || numbers.timeframe <= 0 || numbers.drawback_threshold <= 0) {
		showFormError("Amounts and duration must be greater than zero.");
		return false;
	}
	if (numbers.martingale_levels < 0) {
		showFormError("Martingale levels cannot be negative.");
		return false;
	}
	if (numbers.martingale_multiplier < 1) {
		showFormError("The multiplier has to be 1 or more, or a recovery leg would be smaller than the loss it covers.");
		return false;
	}

	const payload = {
		...lastRiskValues,
		initial_amount: numbers.initial_amount,
		martingale_levels: numbers.martingale_levels,
		martingale_multiplier: numbers.martingale_multiplier,
		timeframe: numbers.timeframe,
		local_timezone: timezone,
		drawback_threshold: -Math.abs(numbers.drawback_threshold),
	};

	try {
		const response = await fetch("/set_risk_management", {
			method: "POST",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify(payload),
		});

		if (!response.ok) {
			let detail = `The bot rejected these limits (HTTP ${response.status}).`;
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
		showFormError("Couldn't reach the bot, so nothing was saved.");
		console.error("Error setting risk data:", error);
		return false;
	}
}

async function handleRiskSubmit(event) {
	event.preventDefault();
	if (!els.form.reportValidity()) return;

	const submit = els.form.querySelector('button[type="submit"]');
	if (submit) submit.disabled = true;
	try {
		await setRiskData(els.form);
	} finally {
		if (submit) submit.disabled = false;
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

/*
 * Sequential by design: the broker reads can outlast the poll interval, and
 * the app queues requests, so a second kick-off would only pile them up.
 */
async function refreshAll() {
	if (refreshing || document.hidden) return;
	refreshing = true;

	const results = await Promise.allSettled([
		updateBalance(),
		updateOpenTrades(),
		updateCurrentSignals(),
		updateClosedTrades(),
		updateRiskData(),
	]);

	refreshing = false;

	if (results.every((result) => result.status === "fulfilled")) {
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
	els.themeToggle.setAttribute("aria-pressed", String(light));
	els.themeToggle.setAttribute("aria-label", light ? "Switch to the dark theme" : "Switch to the light theme");

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

/* --- Start -------------------------------------------------------------- */

document.addEventListener("DOMContentLoaded", () => {
	applyTheme(document.documentElement.dataset.theme === "light" ? "light" : "dark", false);

	els.themeToggle.addEventListener("click", () => {
		applyTheme(document.documentElement.dataset.theme === "light" ? "dark" : "light", true);
	});

	els.openForm.addEventListener("click", showriskform);
	els.cancelForm.addEventListener("click", showriskform);
	els.form.addEventListener("submit", handleRiskSubmit);

	els.modal.addEventListener("click", (event) => {
		if (event.target === els.modal) showriskform();
	});

	document.addEventListener("keydown", (event) => {
		if (event.key === "Escape" && !els.modal.classList.contains("hidden")) {
			showriskform();
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
