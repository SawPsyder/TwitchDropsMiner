"""
Library sync service.

Aggregates game libraries from external platform providers (Steam, Ubisoft Connect),
caches them in DATA_DIR, and determines which games with active drop campaigns
should be added to the "Games to Watch" list automatically.

Automation control:
- blacklist mode: every owned game with a campaign is auto-added,
  except games on the blacklist
- whitelist mode: only owned games that are also on the whitelist are auto-added
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, cast

import aiohttp

from src.config import LIBRARY_CACHE_PATH
from src.library_sync.base import LibraryProvider, LibrarySyncError, OwnedGame, normalize_game_name
from src.library_sync.steam import SteamProvider
from src.library_sync.ubisoft import UbisoftProvider
from src.utils import json_load, json_save


if TYPE_CHECKING:
    from pathlib import Path

    from src.config.settings import Settings


logger = logging.getLogger("TwitchDrops")

LIST_MODE_BLACKLIST = "blacklist"
LIST_MODE_WHITELIST = "whitelist"
LIST_MODES = (LIST_MODE_BLACKLIST, LIST_MODE_WHITELIST)


class LibrarySyncService:
    """
    Syncs owned games from external platforms and applies them
    to the games_to_watch list.
    """

    # how often libraries are refreshed during the regular inventory cycle
    SYNC_INTERVAL = timedelta(hours=12)
    # network timeout for provider API calls
    REQUEST_TIMEOUT = 30

    def __init__(self, settings: Settings, cache_path: Path = LIBRARY_CACHE_PATH) -> None:
        self._settings = settings
        self._cache_path = cache_path
        self._providers: list[LibraryProvider] = [
            SteamProvider(settings),
            UbisoftProvider(settings),
        ]
        self._cache: dict[str, Any] = json_load(cache_path, {"providers": {}}, merge=False)
        self._last_errors: dict[str, str] = {}
        self._sync_lock = asyncio.Lock()

    @property
    def sync_settings(self) -> dict[str, Any]:
        return cast("dict[str, Any]", self._settings.library_sync)

    @property
    def enabled(self) -> bool:
        return bool(self.sync_settings.get("enabled", False))

    @property
    def list_mode(self) -> str:
        mode = self.sync_settings.get("list_mode", LIST_MODE_BLACKLIST)
        return mode if mode in LIST_MODES else LIST_MODE_BLACKLIST

    def _provider_cache(self, provider_name: str) -> dict[str, Any]:
        # NOTE: json_load prunes empty dicts from persisted files, so the
        # "providers" key disappears when the cache was saved with no synced
        # providers - always recreate the structure instead of indexing it
        providers: dict[str, Any] = self._cache.setdefault("providers", {})
        return providers.setdefault(provider_name, {})

    def _last_sync(self, provider_name: str) -> datetime | None:
        last_sync = self._provider_cache(provider_name).get("last_sync")
        return last_sync if isinstance(last_sync, datetime) else None

    @property
    def owned_games(self) -> list[OwnedGame]:
        """All owned games cached from enabled providers."""
        games: list[OwnedGame] = []
        for provider in self._providers:
            if not provider.enabled:
                continue
            for game in self._provider_cache(provider.name).get("games", []):
                games.append(
                    OwnedGame(
                        name=str(game["name"]),
                        app_id=str(game["app_id"]),
                        provider=provider.name,
                        last_played=int(game.get("last_played", 0)),
                    )
                )
        return games

    def _save_cache(self) -> None:
        json_save(self._cache_path, self._cache)

    async def sync(self, *, force: bool = False) -> dict[str, dict[str, Any]]:
        """
        Refresh the owned-games cache from all enabled providers.

        Providers synced recently (within SYNC_INTERVAL) are skipped unless
        force is True. Provider failures are reported per provider and never
        propagate - mining must not break because a platform API is down.

        Returns:
            Per-provider result: {"synced": bool, "game_count": int, "error": str | None}
        """
        results: dict[str, dict[str, Any]] = {}
        if not self.enabled:
            return results

        async with self._sync_lock:
            now = datetime.now(timezone.utc)
            timeout = aiohttp.ClientTimeout(total=self.REQUEST_TIMEOUT)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                for provider in self._providers:
                    if not provider.enabled:
                        continue
                    provider_cache = self._provider_cache(provider.name)
                    last_sync = self._last_sync(provider.name)
                    if not force and last_sync is not None and now - last_sync < self.SYNC_INTERVAL:
                        results[provider.name] = {
                            "synced": False,
                            "game_count": len(provider_cache.get("games", [])),
                            "error": None,
                        }
                        continue
                    try:
                        owned = await provider.fetch_owned_games(session)
                    except LibrarySyncError as exc:
                        logger.warning("Library sync failed for %s: %s", provider.name, exc)
                        self._last_errors[provider.name] = str(exc)
                        results[provider.name] = {
                            "synced": False,
                            "game_count": len(provider_cache.get("games", [])),
                            "error": str(exc),
                        }
                        continue
                    self._last_errors.pop(provider.name, None)
                    provider_cache["games"] = [
                        {"name": game.name, "app_id": game.app_id, "last_played": game.last_played}
                        for game in owned
                    ]
                    provider_cache["last_sync"] = now
                    results[provider.name] = {
                        "synced": True,
                        "game_count": len(owned),
                        "error": None,
                    }
            self._save_cache()
        return results

    def get_owned_games_summary(self) -> list[dict[str, Any]]:
        """
        Owned games across all enabled providers for display in the web GUI.

        Games owned on multiple platforms are merged into one entry (keeping
        the most recent last-played time). Sorted alphabetically.
        """
        merged: dict[str, dict[str, Any]] = {}
        for game in self.owned_games:
            normalized = normalize_game_name(game.name)
            entry = merged.get(normalized)
            if entry is None:
                merged[normalized] = {
                    "name": game.name,
                    "providers": [game.provider],
                    "last_played": game.last_played,
                }
            else:
                if game.provider not in entry["providers"]:
                    entry["providers"].append(game.provider)
                entry["last_played"] = max(entry["last_played"], game.last_played)
        return sorted(merged.values(), key=lambda entry: str(entry["name"]).casefold())

    def get_status(self) -> dict[str, Any]:
        """Current sync status for display in the web GUI."""
        providers: dict[str, Any] = {}
        for provider in self._providers:
            last_sync = self._last_sync(provider.name)
            providers[provider.name] = {
                "enabled": bool(provider.provider_settings.get("enabled", False)),
                "configured": provider.is_configured,
                "last_sync": last_sync.isoformat() if last_sync is not None else None,
                "game_count": len(self._provider_cache(provider.name).get("games", [])),
                "last_error": self._last_errors.get(provider.name),
            }
        return {
            "enabled": self.enabled,
            "list_mode": self.list_mode,
            "providers": providers,
        }

    # games played within this window are ranked by recency; everything else
    # is ranked by campaign deadline instead, so stale libraries don't bury
    # games at risk of missing their drops
    RECENTLY_PLAYED_WINDOW = timedelta(days=180)

    def get_auto_watch_games(
        self, campaign_games: Iterable[tuple[str, datetime]]
    ) -> list[str]:
        """
        Determine which campaign games should be watched automatically.

        A campaign game qualifies when it's owned on an enabled platform and
        passes the active blacklist/whitelist filter. Returned names are the
        Twitch game names (so they match campaigns exactly), ordered in two
        tiers:
        1. Games played within the last 6 months, most recently played first.
        2. Everything else (played longer ago, or never played), ordered by
           campaign end date, soonest first, to minimize the chance of
           missing drops before they expire.
        """
        if not self.enabled:
            return []

        # last played per normalized name; a game owned on several platforms
        # counts with its most recent play time
        last_played_map: dict[str, int] = {}
        for game in self.owned_games:
            normalized = normalize_game_name(game.name)
            last_played_map[normalized] = max(
                last_played_map.get(normalized, 0), game.last_played
            )
        if not last_played_map:
            return []

        if self.list_mode == LIST_MODE_WHITELIST:
            whitelist = {
                normalize_game_name(name) for name in self.sync_settings.get("whitelist", [])
            }
            list_filter = lambda name: name in whitelist  # noqa: E731
        else:
            blacklist = {
                normalize_game_name(name) for name in self.sync_settings.get("blacklist", [])
            }
            list_filter = lambda name: name not in blacklist  # noqa: E731

        # soonest campaign deadline per normalized name; a game can have
        # several active campaigns, so the most urgent one wins
        ends_at_map: dict[str, datetime] = {}
        auto_games: list[str] = []
        seen: set[str] = set()
        for game_name, ends_at in campaign_games:
            normalized = normalize_game_name(game_name)
            if normalized not in ends_at_map or ends_at < ends_at_map[normalized]:
                ends_at_map[normalized] = ends_at
            if normalized in seen:
                continue
            if normalized in last_played_map and list_filter(normalized):
                seen.add(normalized)
                auto_games.append(game_name)

        recent_cutoff = datetime.now(timezone.utc) - self.RECENTLY_PLAYED_WINDOW
        recent_cutoff_ts = recent_cutoff.timestamp()

        def sort_key(name: str) -> tuple[int, float, str]:
            normalized = normalize_game_name(name)
            last_played = last_played_map[normalized]
            if last_played >= recent_cutoff_ts:
                return (0, -last_played, name.casefold())
            return (1, ends_at_map[normalized].timestamp(), name.casefold())

        auto_games.sort(key=sort_key)
        return auto_games

    @staticmethod
    def combine_watch_lists(user_games: Iterable[str], auto_games: Iterable[str]) -> list[str]:
        """
        Compose the effective two-tier watch list.

        User-selected games always come first (their order is the user's
        priority), followed by auto-detected games that aren't already on the
        user's list (case-insensitive).
        """
        combined = list(user_games)
        seen = {name.casefold() for name in combined}
        for name in auto_games:
            key = name.casefold()
            if key not in seen:
                seen.add(key)
                combined.append(name)
        return combined
