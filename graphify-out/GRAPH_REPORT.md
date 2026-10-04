# Graph Report - Signal_bot_for_telegram-main  (2026-10-03)

## Corpus Check
- Corpus is ~13,807 words - fits in a single context window. You may not need a graph.

## Summary
- 230 nodes · 389 edges · 16 communities (12 shown, 4 thin omitted)
- Extraction: 91% EXTRACTED · 9% INFERRED · 0% AMBIGUOUS · INFERRED: 36 edges (avg confidence: 0.84)
- Token cost: 12,000 input · 11,000 output

## Community Hubs (Navigation)
- Docs & Architecture
- Dashboard UI Script
- Scraper & Ingest Imports
- FastAPI Web App
- Webhook Middleware & Auth
- Telegram Signal Listener
- Martingale Risk Guards
- Signal Parsing
- Trade Execution
- Secret & Env Management
- Risk Management Models
- Account State Lifecycle
- Webhook Secret Rotation
- Package Entry
- Rich Logging Dependency
- UI Theme Toggle

## God Nodes (most connected - your core abstractions)
1. `updateOpenTrades()` - 13 edges
2. `FastAPI Application (main:app)` - 11 edges
3. `TelegramSignalListener` - 10 edges
4. `updateClosedTrades()` - 10 edges
5. `take_trade()` - 9 edges
6. `isNumber()` - 9 edges
7. `setRiskData()` - 9 edges
8. `refreshAll()` - 9 edges
9. `parse_signal` - 9 edges
10. `lifespan()` - 8 edges

## Surprising Connections (you probably didn't know these)
- `Three-Bar Amber Chart Mark Favicon` --conceptually_related_to--> `Pocket Option Signal Bot`  [INFERRED]
  ui/favicon.svg → README.md
- `Three-Bar Amber Chart Mark Favicon` --semantically_similar_to--> `Signal Desk Dashboard Page`  [INFERRED] [semantically similar]
  ui/favicon.svg → ui/index.html
- `lifespan()` --uses--> `TelegramSignalListener`  [INFERRED]
  main.py → telegram_listener.py
- `signal_provider Global Variable` --conceptually_related_to--> `parse_signal`  [INFERRED]
  Macrodroid/README.md → README.md
- `timezone Global Variable` --conceptually_related_to--> `parse_signal`  [INFERRED]
  Macrodroid/README.md → README.md

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Signal Ingestion Pipeline (Phone to Parser)** — macrodroid_macro_droid, macrodroid_http_request_action, macrodroid_tunnel_url, macrodroid_webhook_secret, readme_macrodroid_webhook_ingestion, readme_fastapi_app, readme_parse_signal [EXTRACTED 0.95]
- **Martingale Risk Enforcement Loop** — readme_martingale_strategy, readme_risk_controls, readme_manage_martingale, ui_index_risk_form_element, ui_index_risk_ladder [INFERRED 0.85]
- **Dashboard Live Readings Surface** — ui_index_signal_desk, ui_index_live_readings, ui_index_connection_state, readme_http_endpoints, readme_health_endpoint [INFERRED 0.85]

## Communities (16 total, 4 thin omitted)

### Community 0 - "Docs & Architecture"
Cohesion: 0.05
Nodes (48): MacroDroid Custom Widgets, HTTP Request Action, MacroDroid, MacroDroid.mdr Export File, pytz Inverted Timezone Offset Convention, MacroDroid Setup Guide, signal_provider Global Variable, timezone Global Variable (+40 more)

### Community 1 - "Dashboard UI Script"
Cohesion: 0.12
Nodes (42): asArray(), cell(), clearFailure(), clearFormError(), displayValue(), els, emptyState(), errorState() (+34 more)

### Community 2 - "Scraper & Ingest Imports"
Cohesion: 0.08
Nodes (32): argparse, asyncio, collections_abc, datetime, dotenv, json, os, pathlib (+24 more)

### Community 3 - "FastAPI Web App"
Cohesion: 0.11
Nodes (23): binaryoptionstoolsv2_pocketoption, contextlib, fastapi_middleware_cors, fastapi_responses, fastapi_staticfiles, get, JSONResponse, get_closed_trades() (+15 more)

### Community 4 - "Webhook Middleware & Auth"
Cohesion: 0.19
Nodes (9): BaseHTTPMiddleware, NoCacheUiMiddleware, QueueMiddleware, Validate the shared secret when one is configured. Fails closed when a secret…, Keep dashboard assets fresh so UI fixes appear without a hard reload., Queue incoming HTTP requests and process them sequentially. - max_queue: 0…, trade_signal_webhook(), webhook_auth_ok() (+1 more)

### Community 5 - "Telegram Signal Listener"
Cohesion: 0.21
Nodes (6): SignalCallback, Read new posts from one configured Telegram channel., Build a listener only when direct Telegram ingestion is enabled., Connect and process new posts until cancelled., TelegramSignalListener, handle_message()

### Community 6 - "Martingale Risk Guards"
Cohesion: 0.20
Nodes (10): active_sequences(), can_start_sequence(), can_start_trade(), martingale_exposure(), purge_stale_sequences(), Return the maximum amount risked across the initial trade and recoveries., Currently open sequences, excluding one trade id when needed. A recovery leg…, Age after which a sequence is assumed dead and its slot reclaimed. (+2 more)

### Community 7 - "Signal Parsing"
Cohesion: 0.22
Nodes (8): logging, handle_telegram_signal(), parse_signal(), Route a new Telegram post through the same path as MacroDroid., SIGNAL, parse_macrodroid_trade_data(), Parse the common signal format from MacroDroid or Telegram. Telegram posts…, re

### Community 8 - "Trade Execution"
Cohesion: 0.36
Nodes (8): Any, get_account_details(), manage_martingale(), Coerce a broker response field to float. Pocket Option returns numeric fields…, risk_rejection(), take_trade(), to_float(), TRADE

### Community 9 - "Secret & Env Management"
Cohesion: 0.25
Nodes (8): ensure_webhook_secret(), env_file_path(), env_flag(), persist_env_value(), Location of the .env file to update (override with ENV_FILE)., Atomically set ``KEY=value`` in .env, preserving every other line., Generate and persist a secret once, if enabled and none is configured. This…, Path

### Community 10 - "Risk Management Models"
Cohesion: 0.29
Nodes (7): BaseModel, load_risk_management(), RISK_MANAGEMENT, set_risk_management(), SIGNAL_FIELDS, TRADE_FIELDS, post

### Community 11 - "Account State Lifecycle"
Cohesion: 0.40
Nodes (4): FastAPI, ACCOUNT_DETAILS, lifespan(), reset_P_n_L_day()

## Knowledge Gaps
- **27 isolated node(s):** `Po_signal_bot_2`, `NUMBER_FIELDS`, `INTEGER_FIELDS`, `money`, `signedMoney` (+22 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 80 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **4 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `TelegramSignalListener` connect `Telegram Signal Listener` to `Account State Lifecycle`, `Scraper & Ingest Imports`, `FastAPI Web App`?**
  _High betweenness centrality (0.047) - this node is a cross-community bridge._
- **Why does `QueueMiddleware` connect `Webhook Middleware & Auth` to `FastAPI Web App`?**
  _High betweenness centrality (0.022) - this node is a cross-community bridge._
- **Are the 4 inferred relationships involving `FastAPI Application (main:app)` (e.g. with `HTTP API Endpoints` and `fastapi`) actually correct?**
  _`FastAPI Application (main:app)` has 4 INFERRED edges - model-reasoned connections that need verification._
- **What connects `Po_signal_bot_2`, `NUMBER_FIELDS`, `INTEGER_FIELDS` to the rest of the system?**
  _27 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Docs & Architecture` be split into smaller, more focused modules?**
  _Cohesion score 0.05230496453900709 - nodes in this community are weakly interconnected._
- **Should `Dashboard UI Script` be split into smaller, more focused modules?**
  _Cohesion score 0.11522198731501057 - nodes in this community are weakly interconnected._
- **Should `Scraper & Ingest Imports` be split into smaller, more focused modules?**
  _Cohesion score 0.07807807807807808 - nodes in this community are weakly interconnected._