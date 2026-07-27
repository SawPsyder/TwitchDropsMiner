# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Guidelines

- **Testing**: always add unit tests for backend changes; frontend changes should have tests where practical.
- **DRY / OOP**: the codebase follows DRY principles; backend code should be OOP.
- **Refactoring**: you're authorized to refactor code to align with DRY/OOP, but ask for permission
  before any significant refactor.
- **User-facing text**: edit `lang/English.json` for any change to UI or console strings (see
  Translations below - it's the only translation file left). Frontend translation rendering must use
  safe DOM construction - never inject translated strings via non-clearing `innerHTML`; allowlist any
  intentional links and build them as DOM nodes.
- **Docs**: update `README.md` when a change affects setup, features, or scope described there.

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
  rotated tickets persist in `DATA_DIR/ubisoft_auth.json`; no last-played data) + `XboxProvider`
  (`xbox.py` + `xbox_auth.py`, see below), `LibrarySyncService`
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
copied from the browser after logging in at connect.ubisoft.com, which also covers 2FA accounts;
Xbox/Microsoft Store - see below),
matches them against campaign games by
normalized name, and filters through the blacklist/whitelist (`settings.library_sync.list_mode`).
The result is the runtime `Twitch.auto_watch_games` list, ordered by the platform's last-played
time (most recent first, never-played last alphabetically). The watch list is two-tier:
`Twitch.get_effective_watch_list()` = user's `games_to_watch` (persisted, user-ordered) followed by
`auto_watch_games` (runtime only, never written into settings) - consumed by `StreamSelector`, the
wanted-items tree, and channel priority. `StreamSelector` builds the actual wanted-items queue from
that two-tier list; if `settings.idle_behavior["mine_all_when_idle"]` is enabled, every other game
with an active campaign is appended at the lowest priority - fully integrated (channels fetched,
tracked, shown in the channels panel, and mined once the higher tiers have nothing watchable),
not just a display preview. Each queue entry is tagged with a `source` of
`"manual"`, `"auto"`, or `"idle"` (shown as a badge in the web GUI's Wanted Drop Queue). Provider
failures are logged but never break the mining loop. New platforms subclass `LibraryProvider` and
get registered in `LibrarySyncService._providers`. A provider with a connection model that doesn't
fit "paste a credential into the settings" can report it to the web GUI by overriding
`LibraryProvider.status_extra()`, which `get_status()` merges into that provider's entry.

#### Xbox / Microsoft Store (`xbox.py`, `xbox_auth.py`)

Five independent sources, each separately toggleable in `settings.library_sync.xbox`:

- **Account library** (needs a connected Microsoft account): `titlehub` title history (the only
  source with real last-played timestamps) plus purchase entitlements from `inventory.xboxlive.com`,
  whose title ids are resolved to names through titlehub's batch endpoint.
- **Subscription catalogs** (`include_gamepass_pc`, `include_gamepass_console`, `include_ea_play`):
  public `catalog.gamepass.com` sigls resolved to names via `displaycatalog.mp.microsoft.com`,
  batched 20 bigIds per request. These need **no credentials at all**, so the provider counts as
  configured with only a catalog enabled and no sign-in. Each catalog is hundreds of titles and
  carries no play history, so those games sort into the deadline-ordered tail like any other
  not-recently-played game (no separate tier or `source` badge).

`settings.library_sync.xbox.market` picks the store region for the catalogs. Only the 86 regions in
`XBOX_MARKETS` are valid - determined by probing every ISO 3166-1 alpha-2 code against the PC Game
Pass catalog, which either serves a full catalogue (449-526 titles) or nothing at all, so an
unsupported region would silently sync zero games. The web GUI renders it as a dropdown fed by
`GET /api/library/xbox/markets` (same pattern as `/api/languages`), and both `XboxProvider.market`
and the settings sanitizer fall back to `DEFAULT_MARKET` for anything outside the set.

Because switching region or a catalogue toggle changes the result set without changing the clock,
`LibraryProvider.fetch_fingerprint()` (overridden by `XboxProvider` with market + enabled catalogs +
signed-in state) is cached next to `last_sync`; `LibrarySyncService.sync()` refetches as soon as it
differs, so those settings take effect immediately instead of after the 12h cache expiry. A cache
file written before fingerprinting existed has no entry, so the first sync after upgrading refetches
once.

`XboxAuth` (`xbox_auth.py`) owns the auth chain: MSA **device code** (login.live.com, same UX as the
Twitch login) → Xbox user token (`user.auth.xboxlive.com`) → XSTS token (`xsts.auth.xboxlive.com`),
using the public Xbox client id with the `MBI_SSL` scope. Only the rotating refresh token and the
gamertag/XUID persist, in `DATA_DIR/xbox_auth.json`; XSTS tokens are cached in memory per relying
party. **No Xbox credential is ever stored in `settings.json`**, which is why `xbox` needs no entry in
`SettingsManager._LIBRARY_CREDENTIAL_KEYS`. Sign-in is driven from the settings page via
`POST /api/library/xbox/login` (returns the code + URL, then polls Microsoft in a background task)
and `POST /api/library/xbox/logout`; the frontend watches `/api/library/status` until the account
flips to signed in. A rejected refresh token signs the account out rather than retrying forever.
Any single source failing is logged and skipped - only an all-sources failure raises. A damaged
`xbox_auth.json` is ignored with a warning instead of throwing during startup.

Device-code specifics that cost real debugging time, so don't undo them: Microsoft's response has no
`verification_uri_complete`, but its code-entry page accepts the code as an `?otc=` query parameter
and pre-fills the field, so `build_complete_uri()` synthesizes that link - a hand-typed code is the
most likely failure (user codes are full of 5/S, 6/G, 0/O, 8/B look-alikes and a mistype is reported
as "that code is not correct", which reads like a bug in the app). `poll_device_code` must treat
`slow_down` as "keep polling, wider interval" per RFC 8628 rather than fatal, and terminal outcomes
are recorded in `XboxAuth.last_login_error` so `login_state()` can show them - the flow is otherwise
completely silent when it fails. `POST /api/library/xbox/logout` also calls
`LibrarySyncService.invalidate_provider("xbox")` so account-derived games stop counting immediately.

Microsoft product titles carry platform/edition qualifiers that no Twitch category has
("… - PC", "… - Windows", "… (Game Preview)", "Minecraft: Windows 10 Edition"), so
`XboxProvider.clean_product_title()` trims them off an allowlist of known qualifiers before
matching. Relatedly, `normalize_game_name()` strips ™/®/© *before* its NFKD pass, because NFKD
decomposes ™ into the letters "TM" - the Microsoft catalog is full of ™ and would otherwise never
match.

`StreamSelector.get_unlinked_auto_tracked_tree()` surfaces manually-watched and auto-tracked games
that have at least one campaign whose account isn't linked yet - manual games first, then
auto-tracked ones not already on the manual list (no duplicates), each tagged `"manual"`/`"auto"`.
It intentionally bypasses the eligible/can-earn-within gate above, since an unlinked campaign
without a badge/emote reward is never "eligible" and would otherwise never surface anywhere. The
web GUI shows this as a dedicated, unordered "Tracked Games Awaiting Link" panel (below the Wanted
Drop Queue) with a single Link/Refresh Status button per game card - pushed via the
`unlinked_auto_items_update` Socket.IO event and the `unlinked_auto_items` key in `initial_state`.

### Channel selection priority

1. User-selected channel (if clicked in UI)
2. ACL-listed channels over directory-discovered channels
3. Game priority order (from settings)
4. Viewer count, descending
5. Hard cap: 199 channels tracked simultaneously (websocket topic limit)

### Translations (`i18n/`)

Multi-language support was removed; `lang/English.json` is now the only translation file and the sole
source of truth (`Translation` TypedDict in `src/i18n/translator.py` still enforces its schema). The
`Translator` loader, `/api/languages` endpoint, and language dropdown in the web GUI still work
mechanically but only ever offer English. When changing user-facing strings, edit `lang/English.json`
directly - there are no other language files to keep in sync anymore.

### Docker / paths

`src/config/paths.py` detects Docker via `DOCKER_ENV` env var or `/.dockerenv`, switching between
`/app` + `/app/data` (Docker) and `<project_root>` + `<project_root>/data` (dev). All persisted user
state (cookies, settings, image cache, logs) lives under `DATA_DIR` - never assume a path relative to
the source tree for user data.

## Release Process

This repo (`SawPsyder/TwitchDropsMiner`) is a personal fork of `rangermix/TwitchDropsMiner` and is
released independently - it does **not** use upstream's release pipeline (Docker Hub, Gemini-authored
release notes, `release/<version>` branches, `PUBLISHER_TOKEN`/`DOCKERHUB_*`/`GEMINI_API_KEY` secrets).
None of those secrets exist on this fork, and there's no push access to upstream, so don't assume
upstream's workflows are runnable here. Do not resurrect `docker-release.yml`, `version-release.yml`,
or `generate_release_notes.sh` - they were deleted on purpose.

This fork's flow instead publishes versioned images straight to GHCR under this fork's own owner:

1. Feature work happens on `develop`; releases are cut from `main`.
2. Before cutting a release, draft a `# Release Notes - v<version>` section at the top of
   `RELEASE_NOTES.md` (there's no Gemini key configured here, so this is written by hand/by Claude
   from the commit log since the last tag - match the tone/format of existing entries) and commit it.
3. Run `.github/scripts/release.sh <version> [source_branch]` (default `source_branch` is `develop`).
   It fast-forwards `main`, validates the version, requires the `RELEASE_NOTES.md` entry to already
   exist, bumps `src/version.py` + `pyproject.toml`, commits, pushes, and pushes a `v<version>` tag.
4. The pushed tag independently triggers two GitHub Actions (no chaining/waiting between them):
   - `ghcr-publish.yml` builds and pushes `ghcr.io/<owner>/twitchdropsminer:<version>` (plus
     `:<major.minor>`, `:<major>`, `:latest` for stable releases) - this is the image referenced in
     personal `docker-compose`/Portainer setups.
   - `github-release.yml` creates the GitHub Release, extracting the matching section from
     `RELEASE_NOTES.md` via `extract_release_notes.sh`.
5. `.github/scripts/revert_release.sh <version>` undoes a bad release (deletes the tag/GitHub release,
   reverts version files) - it has no release branch to clean up since none is created.

None of these scripts set a bot git identity - they're meant to be run locally/by an agent using
whatever git identity is already configured, not by CI as `github-actions[bot]`.

## Project scope

Supported: web GUI, Docker deployment, remote/headless access.
Explicitly out of scope: multi-account support, channel-points mining, mining unlinked campaigns,
desktop GUI (removed).
