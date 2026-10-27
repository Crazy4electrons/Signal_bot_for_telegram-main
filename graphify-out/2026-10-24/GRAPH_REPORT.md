# Graph Report - Signal_bot_for_telegram-main  (2026-10-24)

## Corpus Check
- cluster-only mode — file stats not available

## Summary
- 1016 nodes · 2219 edges · 68 communities (38 shown, 30 thin omitted)
- Extraction: 94% EXTRACTED · 6% INFERRED · 0% AMBIGUOUS · INFERRED: 126 edges (avg confidence: 0.9)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `8ad3d26f`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- script.js
- AsyncWebSocketClient
- PocketOptionBroker
- login.py
- main.py
- fetch_channels.py
- telegram_listener.py
- client.py
- playwright_scraper.py
- Order
- test_broker_dashboard.py
- Any
- utils.py
- ConnectionKeepAlive
- AsyncQuotexClient
- RISK_MANAGEMENT
- manage_martingale
- Quotex
- test_quotex_auth_config.py
- __init__.py
- TelegramSignalListener
- .get_candles
- monitoring.py
- QuotexBrowserSession
- test_order_frame_matches_browser_time_and_option_type
- .connect_with_keep_alive
- test.py
- test_browser_auth_timeout_keeps_visible_profile_open
- OrderResult
- FastCandleStore
- ConnectionPool
- _listen_for_quotex_authorization
- get
- CircuitBreaker
- run_test
- ._start_regular_connection
- ConnectionError
- NoCacheUiMiddleware
- ._on_auth_error
- ._on_order_closed
- ._start_keep_alive_tasks
- HealthChecker
- TimestampFormatter
- Python Project Dependencies
- test_quotex_capture_always_initializes_persistent_browser_profile
- Crazy4electrons API-Quotex Fork
- test_reconnect_replaces_websocket_and_rebinds_forwarders
- .get_stats
- HTTP Request Action
- MacroDroid Setup Guide
- test_health_monitor_marks_connection_down_when_heartbeat_stops
- test_persistent_keepalive_sends_engine_ping
- test_reconnect_start_does_not_cancel_the_monitor_that_started_it
- pytz Inverted Timezone Offset Convention
- test_engine_pong_refreshes_keepalive_health_timestamp
- test_persistent_adapter_forwards_opened_and_closed_order_lists
- Independent Broker Workspaces
- Telegram Channel Broker Destination
- Effective Risk Limits
- Any
- BaseModel
- MacroDroid Custom Widgets
- signal_provider Global Variable
- Path
- Po_signal_bot_2
- Signal Execution Desk
- Three-Bar Amber Chart Mark Favicon

## God Nodes (most connected - your core abstractions)
1. `AsyncQuotexClient` - 126 edges
2. `AsyncWebSocketClient` - 58 edges
3. `ConnectionKeepAlive` - 53 edges
4. `Config` - 21 edges
5. `Order` - 21 edges
6. `get_ssid()` - 21 edges
7. `OrderDirection` - 20 edges
8. `QuotexBroker` - 19 edges
9. `ConnectionError` - 19 edges
10. `QuotexBrowserSession` - 17 edges

## Surprising Connections (you probably didn't know these)
- `QuotexBroker` --uses--> `AsyncQuotexClient`  [INFERRED]
  brokers/quotex.py → api_quotex/client.py
- `OrderProtocolTests` --uses--> `AsyncQuotexClient`  [INFERRED]
  tests/test_api_quotex_order_protocol.py → api_quotex/client.py
- `test_closed_order_list_derives_duration_and_uses_epoch_timestamps()` --calls--> `AsyncQuotexClient`  [EXTRACTED]
  tests/test_quotex_auth_config.py → api_quotex/client.py
- `test_opened_order_list_derives_duration_from_open_and_close_timestamps()` --calls--> `AsyncQuotexClient`  [EXTRACTED]
  tests/test_quotex_auth_config.py → api_quotex/client.py
- `test_order_frame_matches_browser_time_and_option_type()` --uses--> `AsyncQuotexClient`  [INFERRED]
  tests/test_quotex_auth_config.py → api_quotex/client.py

## Import Cycles
- None detected.

## Communities (68 total, 30 thin omitted)

### Community 0 - "script.js"
Cohesion: 0.07
Nodes (75): asArray(), BROKER_IDS, BROKER_NAMES, brokerName(), brokerPanel(), brokerQuery(), brokerStatuses, cell() (+67 more)

### Community 1 - "AsyncWebSocketClient"
Cohesion: 0.05
Nodes (23): AsyncWebSocketClient, Any, Asynchronous WebSocket client for Quotex API (Engine.IO/Socket.IO v3)., Queue outgoing Socket.IO frame and send in small batches to reduce syscalls.…, Flush queued frames and send a critical frame before returning., Send a message and wait until the WebSocket write has completed., Flush pending messages after a short delay to coalesce bursts., Compatibility: rate-limited wrapper that delegates to the batched send_message. (+15 more)

### Community 2 - "PocketOptionBroker"
Cohesion: 0.05
Nodes (25): binaryoptionstoolsv2_pocketoption, Broker, NormalizedSignal, Any, Broker-agnostic normalized signal input., Minimal broker interface to support existing martingale flow., TradePlacement, PocketOptionBroker (+17 more)

### Community 3 - "login.py"
Cohesion: 0.06
Nodes (30): Config, ConnectionConfig, LoggingConfig, Any, Configuration file for the async Quotex API, TradingConfig, _cookies_string_to_dict(), _extract_csrf_from_signin() (+22 more)

### Community 4 - "main.py"
Cohesion: 0.06
Nodes (48): contextlib, FastAPI, fastapi_middleware_cors, fastapi_responses, fastapi_staticfiles, logging, broker_account_type(), broker_is_connected() (+40 more)

### Community 5 - "fetch_channels.py"
Cohesion: 0.07
Nodes (46): ArgumentParser, append_channels(), build_parser(), collect(), display_name(), format_table(), kind_of(), main() (+38 more)

### Community 6 - "telegram_listener.py"
Cohesion: 0.08
Nodes (37): collections_abc, JSONResponse, get_channels(), The channel file as it stands, disabled and invalid entries included. Invalid…, Replace the channel list, then reload the listener. The whole list is validated…, The Etc/GMT offsets the signal parser can handle, in human order., set_channels(), timezone_choices() (+29 more)

### Community 7 - "client.py"
Cohesion: 0.12
Nodes (31): Professional Async Quotex API Client – Unified & Cleaned., Connection Keep-Alive Manager for Quotex API, Any, Constants and configuration for the Quotex API., update_assets_from_api(), InvalidParameterError, ConnectionInfo, ConnectionStatus (+23 more)

### Community 8 - "playwright_scraper.py"
Cohesion: 0.13
Nodes (28): _browser_cookies(), _build_quotex_session(), _capture_pocket_frame(), watch_socket(), inspect(), _capture_quotex_browser(), capture_session(), _cloud_login() (+20 more)

### Community 9 - "Order"
Cohesion: 0.12
Nodes (10): OrderError, Order, OrderDirection, str, OrderProtocolTests, test_binary_opened_order_list_populates_client_active_orders(), test_binary_order_open_response_correlates_browser_request_id(), test_opened_order_list_emits_pending_created_once() (+2 more)

### Community 10 - "test_broker_dashboard.py"
Cohesion: 0.11
Nodes (17): get_broker_status(), Return independent broker readiness without exposing secrets., route_signal_to_broker(), SIGNAL_FIELDS, response_body(), test_account_read_failure_is_not_returned_as_a_success(), test_account_read_keeps_each_broker_balance_independent(), test_broker_status_detects_disconnected_quotex_socket() (+9 more)

### Community 12 - "utils.py"
Cohesion: 0.09
Nodes (17): analyze_candles(), calculate_order_expiration(), calculate_support_resistance(), calculate_volatility(), candles_to_dataframe(), determine_trend(), format_session_id(), performance_monitor() (+9 more)

### Community 14 - "AsyncQuotexClient"
Cohesion: 0.13
Nodes (3): AsyncQuotexClient, Return OrderResult by requestId (preferred) or broker_order_id (fallback).…, Professional async Quotex client with modern Python practices

### Community 15 - "RISK_MANAGEMENT"
Cohesion: 0.11
Nodes (23): active_sequences(), BROKER_RISK_OVERRIDE, can_start_sequence(), can_start_trade(), load_risk_management(), martingale_exposure(), persist_risk_settings(), purge_stale_sequences() (+15 more)

### Community 16 - "manage_martingale"
Cohesion: 0.17
Nodes (23): broker_lifetime_pnl(), broker_pnl(), connect_broker(), effective_risk_management(), ensure_broker_connected(), get_account_details(), get_or_create_broker(), get_or_create_pocket_broker() (+15 more)

### Community 17 - "Quotex"
Cohesion: 0.12
Nodes (22): Channel-Specific Broker Assignment, Direct Telegram Listener, Engine.IO Heartbeats and Socket Reconnects, FastAPI App, Global and Per-Broker Risk Profiles, Independent Broker Initialization, MacroDroid Webhook, Manual Broker Session Capture (+14 more)

### Community 18 - "test_quotex_auth_config.py"
Cohesion: 0.12
Nodes (19): io, _extract_csrf_form(), _has_quotex_browser_auth(), _quotex_authorization_ssid(), _quotex_socket_event(), Find a sign-in form and its CSRF token, tolerating broker markup changes., ssl, test_closed_order_list_derives_duration_and_uses_epoch_timestamps() (+11 more)

### Community 19 - "__init__.py"
Cohesion: 0.19
Nodes (14): AssetError, AuthenticationError, Base64DecodeError, InsufficientFundsError, QuotexError, Custom exceptions for the Quotex API, TimeoutError, WebSocketError (+6 more)

### Community 20 - "TelegramSignalListener"
Cohesion: 0.13
Nodes (9): SignalCallback, Read new posts from one or more configured Telegram channels., First configured channel; kept for backwards compatibility., Build a listener only when direct Telegram ingestion is enabled., Telethon wants a numeric id for -100... channels, a username otherwise., Per-channel state, surfaced by the /health endpoint., Connect and process new posts until cancelled., TelegramSignalListener (+1 more)

### Community 21 - ".get_candles"
Cohesion: 0.14
Nodes (9): DataFrame, datetime, Periodically refresh instruments list, but only when the last snapshot is…, Idempotent request of chart notifications (server pushes live/history deltas).…, 1) Serve immediately from FastCandleStore if enough data is already cached. 2)…, Consolidates concurrent requests per (asset,timeframe) and resolves all waiters…, Fast path: use the latest instruments snapshot (kept in _assets_data) when…, Pocket-Option style speed: answer from instruments snapshot first (source of… (+1 more)

### Community 22 - "monitoring.py"
Cohesion: 0.20
Nodes (10): default_alert_handler(), ErrorCategory, ErrorEvent, ErrorMonitor, ErrorSeverity, PerformanceMetrics, Any, Enum (+2 more)

### Community 23 - "QuotexBrowserSession"
Cohesion: 0.19
Nodes (6): _is_quotex_trade_page(), QuotexBrowserSession, Keep a persistent Quotex browser profile available for fresh auth frames., test_browser_refresh_navigates_back_to_trade_page_and_captures_auth(), run_test(), test_quotex_trade_page_guard_rejects_wrong_account_route()

### Community 24 - "test_order_frame_matches_browser_time_and_option_type"
Cohesion: 0.14
Nodes (7): parametrize, test_authentication_debug_logs_do_not_expose_session(), test_instruments_header_does_not_mark_connection_authenticated(), test_order_frame_matches_browser_time_and_option_type(), test_order_send_failure_is_not_silently_ignored(), fake_send_message(), fake_send_message_immediate()

### Community 25 - ".connect_with_keep_alive"
Cohesion: 0.21
Nodes (4): Watchdog: if connection drops, attempt a clean reconnect and resubscribe to…, Regions, Validate an SSID frame by attempting a lightweight WebSocket connect. Uses…, validate_ssid()

### Community 26 - "test.py"
Cohesion: 0.24
Nodes (11): argparse, dotenv, requests, default_entry_time(), fetch_json(), format_signal(), main(), Send one explicitly confirmed test signal to a configured DEMO broker. (+3 more)

### Community 27 - "test_browser_auth_timeout_keeps_visible_profile_open"
Cohesion: 0.18
Nodes (5): Path, _quotex_profile_path(), test_browser_auth_timeout_keeps_visible_profile_open(), run_test(), test_quotex_profile_path_is_stable_when_launched_from_another_directory()

### Community 29 - "FastCandleStore"
Cohesion: 0.24
Nodes (4): FastCandleStore, Handles both history/list/v2 & chart_notification/get payloads. - Resolves the…, Robust parser for [ts, open, low, high, close, vol?] lists (order seen in…, Lock-free ring buffer per (asset, period) for near-instant candle reads.

### Community 30 - "ConnectionPool"
Cohesion: 0.18
Nodes (4): ConnectionPool, MessageBatcher, Batch outgoing messages to reduce WebSocket send calls under high load., Manages multiple WebSocket connections and tracks their performance statistics.

### Community 31 - "_listen_for_quotex_authorization"
Cohesion: 0.20
Nodes (7): Future, _listen_for_quotex_authorization(), watch_socket(), on_frame_received(), on_frame_sent(), test_browser_authorization_listener_ignores_previous_socket_generation(), run_test()

### Community 32 - "get"
Cohesion: 0.25
Nodes (11): get, get_closed_trades(), get_current_signals(), get_open_trades(), get_risk_management(), normalise_broker_id(), Return the canonical ID for a supported broker., root_index() (+3 more)

### Community 36 - "ConnectionError"
Cohesion: 0.25
Nodes (3): ConnectionError, test_persistent_connection_stops_region_retries_after_auth_rejection(), reject_authentication()

### Community 37 - "NoCacheUiMiddleware"
Cohesion: 0.29
Nodes (6): BaseHTTPMiddleware, NoCacheUiMiddleware, Validate the shared secret when one is configured. Fails closed when a secret…, Keep dashboard assets fresh so UI fixes appear without a hard reload., webhook_auth_ok(), Request

### Community 39 - "._on_order_closed"
Cohesion: 0.33
Nodes (3): Parse timestamp which could be a number (Unix timestamp) or a string in 'YYYY-…, Handle a single closed order 'deal' payload coming from: - s_orders/close ->…, Handle 'orders/closed/list' bulk payload. The server may push this list;…

### Community 42 - "TimestampFormatter"
Cohesion: 0.33
Nodes (4): Formatter, LogRecord, configure_terminal_logging(), TimestampFormatter

### Community 43 - "Python Project Dependencies"
Cohesion: 0.40
Nodes (5): binaryoptionstoolsv2, FastAPI, Playwright, Python Project Dependencies, Telethon

### Community 45 - "Crazy4electrons API-Quotex Fork"
Cohesion: 0.50
Nodes (4): Browser-Authorization Refresh Integration, Engine.IO Heartbeat Behavior, Crazy4electrons API-Quotex Fork, Verified TLS Configuration

### Community 48 - "HTTP Request Action"
Cohesion: 0.67
Nodes (3): HTTP Request Action, tunnel_url Global Variable, webhook_secret Global Variable

### Community 49 - "MacroDroid Setup Guide"
Cohesion: 0.67
Nodes (3): MacroDroid, MacroDroid.mdr Export File, MacroDroid Setup Guide

## Knowledge Gaps
- **38 isolated node(s):** `PerformanceMetrics`, `BROKER_IDS`, `BROKER_NAMES`, `brokerStatuses`, `channelStatus` (+33 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 353 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **30 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `AsyncQuotexClient` connect `AsyncQuotexClient` to `AsyncWebSocketClient`, `._start_regular_connection`, `login.py`, `ConnectionError`, `._on_auth_error`, `client.py`, `._on_order_closed`, `Order`, `playwright_scraper.py`, `Any`, `ConnectionKeepAlive`, `test_quotex_auth_config.py`, `__init__.py`, `.get_candles`, `monitoring.py`, `test_order_frame_matches_browser_time_and_option_type`, `OrderResult`, `FastCandleStore`?**
  _High betweenness centrality (0.174) - this node is a cross-community bridge._
- **Why does `AsyncWebSocketClient` connect `AsyncWebSocketClient` to `login.py`, `ConnectionError`, `client.py`, `ConnectionKeepAlive`, `AsyncQuotexClient`, `test_quotex_auth_config.py`, `__init__.py`, `monitoring.py`, `test_order_frame_matches_browser_time_and_option_type`, `.connect_with_keep_alive`, `ConnectionPool`?**
  _High betweenness centrality (0.077) - this node is a cross-community bridge._
- **Why does `ConnectionKeepAlive` connect `ConnectionKeepAlive` to `AsyncWebSocketClient`, `run_test`, `ConnectionError`, `._on_auth_error`, `client.py`, `._start_keep_alive_tasks`, `AsyncQuotexClient`, `.get_stats`, `test_reconnect_replaces_websocket_and_rebinds_forwarders`, `test_quotex_auth_config.py`, `test_health_monitor_marks_connection_down_when_heartbeat_stops`, `test_persistent_keepalive_sends_engine_ping`, `test_persistent_adapter_forwards_opened_and_closed_order_lists`, `monitoring.py`, `test_engine_pong_refreshes_keepalive_health_timestamp`, `test_reconnect_start_does_not_cancel_the_monitor_that_started_it`, `.connect_with_keep_alive`?**
  _High betweenness centrality (0.064) - this node is a cross-community bridge._
- **Are the 20 inferred relationships involving `AsyncQuotexClient` (e.g. with `Config` and `ConnectionKeepAlive`) actually correct?**
  _`AsyncQuotexClient` has 20 INFERRED edges - model-reasoned connections that need verification._
- **Are the 9 inferred relationships involving `AsyncWebSocketClient` (e.g. with `AsyncQuotexClient` and `ConnectionKeepAlive`) actually correct?**
  _`AsyncWebSocketClient` has 9 INFERRED edges - model-reasoned connections that need verification._
- **Are the 4 inferred relationships involving `ConnectionKeepAlive` (e.g. with `AsyncQuotexClient` and `ErrorCategory`) actually correct?**
  _`ConnectionKeepAlive` has 4 INFERRED edges - model-reasoned connections that need verification._
- **What connects `PerformanceMetrics`, `BROKER_IDS`, `BROKER_NAMES` to the rest of the system?**
  _38 weakly-connected nodes found - possible documentation gaps or missing edges._