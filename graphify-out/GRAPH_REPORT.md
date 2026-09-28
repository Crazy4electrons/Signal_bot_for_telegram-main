# Graph Report - Signal_bot_for_telegram-main  (2026-09-28)

## Corpus Check
- cluster-only mode — file stats not available

## Summary
- 117 nodes · 190 edges · 12 communities (11 shown, 1 thin omitted)
- Extraction: 97% EXTRACTED · 3% INFERRED · 0% AMBIGUOUS · INFERRED: 6 edges (avg confidence: 0.87)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `c2f8e31f`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- Community 0
- Community 1
- Community 2
- Community 3
- Community 4
- Community 5
- Community 6
- Community 7
- Community 8
- Community 9
- Community 10
- Community 11

## God Nodes (most connected - your core abstractions)
1. `TelegramSignalListener` - 10 edges
2. `take_trade()` - 9 edges
3. `lifespan()` - 8 edges
4. `trade_signal_webhook()` - 8 edges
5. `to_float()` - 7 edges
6. `QueueMiddleware` - 6 edges
7. `manage_martingale()` - 6 edges
8. `can_start_sequence()` - 5 edges
9. `send_test_signal()` - 5 edges
10. `ensure_webhook_secret()` - 5 edges

## Surprising Connections (you probably didn't know these)
- `lifespan()` --uses--> `TelegramSignalListener`  [INFERRED]
  main.py → telegram_listener.py
- `parse_signal()` --calls--> `parse_macrodroid_trade_data()`  [EXTRACTED]
  main.py → parse_data.py

## Import Cycles
- None detected.

## Communities (12 total, 1 thin omitted)

### Community 0 - "Community 0"
Cohesion: 0.24
Nodes (16): get, JSONResponse, get_account_details(), get_closed_trades(), get_current_signals(), get_open_trades(), get_risk_management(), health() (+8 more)

### Community 1 - "Community 1"
Cohesion: 0.15
Nodes (9): FastAPI, lifespan(), reset_P_n_L_day(), SignalCallback, Direct Telegram channel ingestion for the signal bot. The listener uses a…, Read new posts from one configured Telegram channel., Build a listener only when direct Telegram ingestion is enabled., Connect and process new posts until cancelled. (+1 more)

### Community 2 - "Community 2"
Cohesion: 0.18
Nodes (10): BaseHTTPMiddleware, NoCacheUiMiddleware, QueueMiddleware, Validate the shared secret when one is configured. Fails closed when a secret…, Keep dashboard assets fresh so UI fixes appear without a hard reload., Queue incoming HTTP requests and process them sequentially. - max_queue: 0…, trade_signal_webhook(), webhook_auth_ok() (+2 more)

### Community 3 - "Community 3"
Cohesion: 0.19
Nodes (11): balanceElements, closedTradesElements, currentSignalsElements, form, formatDirection(), openTradesElements, setRiskData(), updateClosedTrades() (+3 more)

### Community 4 - "Community 4"
Cohesion: 0.25
Nodes (11): Any, can_start_sequence(), can_start_trade(), manage_martingale(), martingale_exposure(), Return the maximum amount risked across the initial trade and recoveries., Coerce a broker response field to float. Pocket Option returns numeric fields…, risk_rejection() (+3 more)

### Community 5 - "Community 5"
Cohesion: 0.24
Nodes (7): Path, collect(), main(), Capture a Pocket Option session with a headed persistent Edge profile. The…, save_to_env(), main(), One-time interactive Telegram user-session login for the VPS.

### Community 6 - "Community 6"
Cohesion: 0.31
Nodes (8): get_asset_emojis(), get_direction_emoji(), get_next_5min_interval_time(), Simple mapping for common asset emojis., Simple mapping for direction emojis., Calculates the next 5-minute interval time (HH:MM) based on current local time.…, Prompts user for signal details, constructs notification, and sends it., send_test_signal()

### Community 7 - "Community 7"
Cohesion: 0.29
Nodes (6): BaseModel, ACCOUNT_DETAILS, active_sequences(), Currently open sequences, excluding one trade id when needed. A recovery leg…, SIGNAL_FIELDS, TRADE_FIELDS

### Community 8 - "Community 8"
Cohesion: 0.29
Nodes (7): ensure_webhook_secret(), env_file_path(), env_flag(), persist_env_value(), Location of the .env file to update (override with ENV_FILE)., Atomically set ``KEY=value`` in .env, preserving every other line., Generate and persist a secret once, if enabled and none is configured. This…

### Community 9 - "Community 9"
Cohesion: 0.29
Nodes (6): handle_telegram_signal(), parse_signal(), Route a new Telegram post through the same path as MacroDroid., SIGNAL, parse_macrodroid_trade_data(), Parse the common signal format from MacroDroid or Telegram. Telegram posts…

### Community 10 - "Community 10"
Cohesion: 0.50
Nodes (4): purge_stale_sequences(), Age after which a sequence is assumed dead and its slot reclaimed., Drop sequences that outlived their expected duration. Without this, a result…, sequence_max_age_seconds()

## Knowledge Gaps
- **6 isolated node(s):** `Po_signal_bot_2`, `balanceElements`, `closedTradesElements`, `currentSignalsElements`, `form` (+1 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 38 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **1 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `env_file_path()` connect `Community 8` to `Community 0`, `Community 5`?**
  _High betweenness centrality (0.116) - this node is a cross-community bridge._
- **Why does `TelegramSignalListener` connect `Community 1` to `Community 0`?**
  _High betweenness centrality (0.109) - this node is a cross-community bridge._
- **Why does `QueueMiddleware` connect `Community 2` to `Community 0`?**
  _High betweenness centrality (0.058) - this node is a cross-community bridge._
- **Are the 2 inferred relationships involving `lifespan()` (e.g. with `handle_telegram_signal()` and `TelegramSignalListener`) actually correct?**
  _`lifespan()` has 2 INFERRED edges - model-reasoned connections that need verification._
- **What connects `Po_signal_bot_2`, `balanceElements`, `closedTradesElements` to the rest of the system?**
  _6 weakly-connected nodes found - possible documentation gaps or missing edges._