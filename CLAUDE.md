# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> Note: `AGENTS.md` is this repo's canonical AI-agent instructions file. Its "Development Guidelines"
> section (DRY/OOP requirements, refusal permission for large refactors, i18n rules, docs-update rules)
> applies here too - read it if this file doesn't cover something.

## Project Overview

Twitch Drops Miner (TDM) automatically mines timed Twitch drops without downloading stream video/audio.
It simulates "watching" via periodic POSTs of minute-watched events to the Spade tracking endpoint
(beacon.twitch.tv preferred - spade.twitch.tv is ad-block DNS listed) and tracks progress over a
websocket connection. Python 3.12+, fully async (asyncio), with a FastAPI + Socket.IO web GUI (no
tkinter/desktop GUI - that was removed in favor of a browser-based interface).

## Development Commands

Dependency management uses `uv` (see `uv.lock`).

```bash
# Install dependencies
uv sync

# Run the app (web GUI at http://localhost:8080)
uv run main.py

# Verbose logging (stackable: -v, -vv, -vvv, -vvvv)
uv run main.py -vvv

# Debug specific subsystems
uv run main.py -vvv --debug-ws     # websocket traffic
uv run main.py -vvv --debug-gql    # GraphQL traffic
uv run main.py --dump              # create a data dump for debugging
```

Lint/type-check (matches `.github/workflows/validation.yml`):

```bash
ruff check src/
mypy src/
```

Tests:

```bash
python -m pytest tests/
python -m pytest tests/test_watch_events.py           # single file
python -m pytest tests/test_watch_events.py -k name    # single test
```

Docker:

```bash
docker-compose up -d   # http://localhost:8080, data persisted to ./data
```

## Architecture

### Package layout (`src/`)

- `models/` - domain objects: `Game`, `Channel`/`Stream`, `DropsCampaign`, `TimedDrop`/`BaseDrop`, `Benefit`
- `config/` - `constants.py` (State/WebsocketTopic enums, logging), `operations.py` (`GQL_OPERATIONS`),
  `paths.py` (Docker-aware path resolution), `settings.py` (JSON-persisted settings), `client_info.py`
  (spoofed Android Client-Id/User-Agent)
- `utils/` - pure helpers (string/json utils, async helpers, rate limiter, backoff)
- `i18n/` - `Translator` singleton (`from src.i18n import _`), typed against `lang/English.json` as schema
- `auth/` - `_AuthState`: OAuth device-code flow, cookie-jar persisted tokens
- `api/` - `HTTPClient`, `GQLClient`
- `websocket/` - `WebsocketPool` (sharded, ≤50 topics/socket, ≤199 channels), reconnect w/ backoff
- `services/` - business logic consumed by the client: `ChannelService`, `InventoryService`,
  `WatchService`, `MaintenanceService`, `MessageHandlerService`
- `library_sync/` - external game library sync: `LibraryProvider` ABC + `SteamProvider` (Steam Web
  API) + `UbisoftProvider` (unofficial ubiservices API + Uplay GraphQL; authenticates via a
  browser-copied `rememberMeTicket` - Ubisoft disabled password Basic-auth logins ~April 2026 -
  rotated tickets persist in `DATA_DIR/ubisoft_auth.json`; no last-played data), `LibrarySyncService`
  builds a runtime auto watch list of owned games with active campaigns (blacklist/whitelist modes,
  ordered by last played, ~12h cache in `DATA_DIR/library_cache.json`)
- `web/` - FastAPI app (`app.py`) + `WebGUIManager` (`gui_manager.py`) composing per-concern managers
  under `web/managers/` (status, console, channels, campaigns, inventory, login, settings, cache,
  broadcaster). Real-time push via Socket.IO through `WebSocketBroadcaster`.
- `core/client.py` - the `Twitch` class: central state machine that composes `_AuthState`, `HTTPClient`,
  `GQLClient`, `WebsocketPool` and delegates business logic to `services/`.

Frontend (`web/`) is a static single-page app: `index.html`, `static/app.js` (Socket.IO client, REST
calls, inventory filtering), `static/styles.css`. It is *not* a build-step framework app.

### State machine (`core/client.py`)

`IDLE → INVENTORY_FETCH → GAMES_UPDATE → CHANNELS_CLEANUP → CHANNELS_FETCH → CHANNEL_SWITCH`, looping
between `CHANNEL_SWITCH` and periodic `INVENTORY_FETCH` (hourly). `MaintenanceService` runs in the
background to trigger channel cleanup on drop start/end and periodic inventory reload (~60 min).

### GraphQL operations

Persisted operations live in `src/config/operations.py` as `GQL_OPERATIONS` (Inventory, Campaigns,
CampaignDetails, GameDirectory, GetStreamInfo, CurrentDrop, ClaimDrop, AvailableDrops,
NotificationsDelete). Raw/non-persisted payloads use `GQLQuery` directly instead (the
`sendSpadeEvents` watch-minute mutation is unused since ~July 2026 - Twitch stopped counting it;
watch events are POSTed to the Spade endpoint instead).

### Library sync (`library_sync/`)

`Twitch.sync_game_libraries()` runs during `GAMES_UPDATE` (and on `POST /api/library/sync`): it
fetches owned games from enabled providers (Steam - needs a Steam Web API key and
SteamID64/vanity name, game details set to public; Ubisoft Connect - needs a `rememberMeTicket`
copied from the browser after logging in at connect.ubisoft.com, which also covers 2FA accounts),
matches them against campaign games by
normalized name, and filters through the blacklist/whitelist (`settings.library_sync.list_mode`).
The result is the runtime `Twitch.auto_watch_games` list, ordered by the platform's last-played
time (most recent first, never-played last alphabetically). The watch list is two-tier:
`Twitch.get_effective_watch_list()` = user's `games_to_watch` (persisted, user-ordered) followed by
`auto_watch_games` (runtime only, never written into settings) - consumed by `StreamSelector`, the
wanted-items tree, and channel priority. Provider failures are logged but never break the mining
loop. New platforms subclass `LibraryProvider` and get registered in
`LibrarySyncService._providers`.

### Channel selection priority

1. User-selected channel (if clicked in UI)
2. ACL-listed channels over directory-discovered channels
3. Game priority order (from settings)
4. Viewer count, descending
5. Hard cap: 199 channels tracked simultaneously (websocket topic limit)

### Translations (`i18n/`)

`lang/English.json` is the single source of truth/fallback; 18 other language files must stay
structurally in sync with it (`Translation` TypedDict in `src/i18n/translator.py` enforces the schema).
Language switching is live - no restart required - and persisted to `settings.json`. When changing any
user-facing string, update `lang/English.json` and note translation-file drift; don't silently invent
strings in other language files.

### Docker / paths

`src/config/paths.py` detects Docker via `DOCKER_ENV` env var or `/.dockerenv`, switching between
`/app` + `/app/data` (Docker) and `<project_root>` + `<project_root>/data` (dev). All persisted user
state (cookies, settings, image cache, logs) lives under `DATA_DIR` - never assume a path relative to
the source tree for user data.

## Project scope

Supported: web GUI, Docker deployment, remote/headless access.
Explicitly out of scope: multi-account support, channel-points mining, mining unlinked campaigns,
desktop GUI (removed).
