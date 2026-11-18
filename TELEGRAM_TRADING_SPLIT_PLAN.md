# Telegram Trading Split — Agent Handoff

## Goal

Use the ingested repository as the starting point for one monorepo containing four independently configurable, testable, restartable, and deployable components:

1. **Telegram ingest:** one Telethon client reads configured channels and records each message once.
2. **Pocket Option consumer:** processes its own work items while preserving current Pocket Option behavior.
3. **MT5 orchestrator:** evaluates the same raw messages with an LLM, validates structured output, applies deterministic risk checks, and coordinates trading through the MT5 MCP interface.
4. **Shared dashboard:** shows ingestion health and the independent state of both consumers.

Do not begin with multiple repositories or a wholesale rewrite. Extract the existing behavior incrementally. Telethon is the intended single source; remove MacroDroid/phone forwarding from the active flow after confirming all required messages are accessible.

## Non-negotiable design rules

- **Independent outputs:** a Pocket Option outage must not block MT5, or vice versa.
- **Durable ingest:** persist the raw event before dispatch. Retain source metadata and original text even if evaluation fails.
- **Idempotency:** deduplicate by Telegram account/channel/message ID. Retries and restarts must not cause duplicate trade actions.
- **Explicit state:** track per-consumer status, attempts, timestamps, rejection reasons, errors, and resulting trade references.
- **LLM is not an execution authority:** it returns schema-constrained interpretation only. Deterministic validation and risk policy gate every action.
- **Safe by default:** MT5 live orders off by default; begin with dry-run/demo and manual approval where available. Do not enable live trading without owner approval.
- **Protect secrets:** never commit `.env`, Telethon sessions, broker credentials, MCP secrets, or account data. Provide placeholder-only example configuration.
- **Dashboard is observational:** trading and ingest continue if the dashboard is unavailable. Audit and restrict any operator controls.

## Suggested structure

Adapt to current conventions; do not restructure solely to match this example.

```text
apps/
  ingest/             # Telethon, event persistence, independent dispatch
  pocket-option/      # existing Pocket Option consumer/API
  mt5-orchestrator/   # LLM interpretation, validation, risk, trading loop
  dashboard/          # shared UI/API
packages/
  contracts/          # versioned event and signal schemas
```

Keep the existing dashboard where practical.

## Contracts and persistence

Define and validate versioned contracts before connecting the services:

- **Telegram event:** stable event ID; account/channel/message IDs; source and received timestamps; raw text; edit/reply metadata when available.
- **Consumer job/result:** event ID; consumer; status (`pending`, `processing`, `accepted`, `rejected`, `failed`, `executed`); attempts; timestamps; reason/error; resulting signal/trade reference.
- **Structured signal:** provider; instrument; direction; entry/expiry details and timezone when available; confidence/uncertainty; source event ID. Missing or uncertain fields must be explicit, never guessed.

Use a transactional outbox or equivalent durable queue so each consumer retries independently. Persist all state transitions for the dashboard. Define retention/redaction rules for raw messages and account-sensitive data.

## Work sequence and acceptance checks

### 1. Inventory and baseline

- Map the current Telegram listener, parsing, Pocket Option execution, persistence, dashboard, tests, and deployment.
- Identify required environment variables and state/session files; do not copy private credentials.
- Record current Pocket Option behavior and identify/add tests that protect it.
- Confirm required channels are accessible through Telethon.

**Accept when:** baseline behavior is documented, secrets are excluded, and Telethon can read the required channels.

### 2. Add contracts, event storage, and dispatch

- Validate and persist raw events before dispatch; deduplicate Telegram messages.
- Create independent consumer jobs, statuses, retry policies, and idempotency keys.

**Accept when:** replaying a message creates no duplicate jobs/actions, and an unavailable consumer does not block the other.

### 3. Extract Pocket Option

- Move existing signal handling/execution behind its own consumer boundary without changing its behavior.
- Give it independent configuration, health status, logs, and restart behavior.
- Keep dashboard operation intact during extraction.

**Accept when:** focused tests and dry-run events match expected existing decisions; ingest and dashboard work while the consumer is stopped.

### 4. Build MT5 orchestrator

- Consume raw events independently.
- Ask the configured LLM for schema-constrained output; persist normalized output and evaluation status.
- Explicitly reject/hold invalid, incomplete, ambiguous, stale, or below-policy-confidence signals. Never silently infer trade parameters.
- Apply deterministic allowlists, freshness checks, position/exposure limits, duplicate-action protection, and owner-approved risk rules before MCP calls.
- Put MCP behind a typed adapter with health checks, timeouts, explicit errors, and auditable requests/results.
- Implement lifecycle tracking: received → evaluated → validated/rejected → approved if required → order requested → confirmed/rejected → monitored/closed.
- Keep live execution disabled; test connectivity and behavior with dry-run/demo operations.

**Accept when:** valid, invalid, ambiguous, stale, duplicate, LLM failure, MCP failure, and restart/recovery cases are tested without unintended or duplicated orders.

### 5. Integrate dashboard

- Show service health; source event; separate consumer states; interpreted signals; rejections/errors; and trade lifecycle.
- Surface stale queues, retries, and disconnections. Never represent unknown state as success.
- Keep secrets out of responses and audit operator actions.

**Accept when:** one event visibly has independent outcomes for both consumers, and processing continues with the UI stopped.

### 6. Deploy and cut over

- Provide independent commands/configuration, health checks, logs, and restart policies.
- Run new ingest and Pocket Option in parallel/dry-run and compare results with the current app.
- Test MT5 with recorded events and demo/dry-run MCP operations, including restart, retry, duplicate, and disconnect recovery.
- Back up current state/configuration and retain rollback until checks pass.
- Remove obsolete MacroDroid assets/configuration only after confirming Telethon covers all required notifications.

**Accept when:** each service restarts independently, event and dashboard state recover, and MT5 live execution remains off absent explicit approval.

## Handoff guidance

- Make small, reviewable changes and preserve existing behavior unless this plan requires otherwise.
- Add focused tests with each extraction; run relevant tests, type checks, lint, and build.
- Document setup, environment variables, dry-run, deployment, recovery, and rollback.
- Record unresolved decisions rather than inventing trade-triggering defaults: LLM provider, confidence policy, MCP capabilities, risk limits, retention, and deployment host.
