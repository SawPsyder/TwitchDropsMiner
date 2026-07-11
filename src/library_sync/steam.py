"""
Steam game library provider.

Uses the official Steam Web API (https://steamcommunity.com/dev) to fetch the
list of games owned by a Steam account. Requires:
- a Steam Web API key (free, from https://steamcommunity.com/dev/apikey)
- the account's SteamID64 or custom profile (vanity) name
- "Game details" profile privacy set to Public
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

from src.library_sync.base import LibraryProvider, LibrarySyncError, OwnedGame


if TYPE_CHECKING:
    import aiohttp


logger = logging.getLogger("TwitchDrops")

STEAM_API_BASE = "https://api.steampowered.com"
OWNED_GAMES_URL = f"{STEAM_API_BASE}/IPlayerService/GetOwnedGames/v1/"
RESOLVE_VANITY_URL = f"{STEAM_API_BASE}/ISteamUser/ResolveVanityURL/v1/"

# SteamID64 values are 17-digit numbers starting with the universe/account prefix 7656
_STEAM_ID64_PATTERN = re.compile(r"^7656\d{13}$")
# accept full profile URLs and extract the interesting part
_PROFILE_URL_PATTERN = re.compile(
    r"steamcommunity\.com/(?P<kind>id|profiles)/(?P<value>[^/?#]+)", re.IGNORECASE
)


class SteamProvider(LibraryProvider):
    """Fetches owned games from the Steam Web API."""

    name = "steam"

    @property
    def api_key(self) -> str:
        return str(self.provider_settings.get("api_key", "")).strip()

    @property
    def steam_id(self) -> str:
        return str(self.provider_settings.get("steam_id", "")).strip()

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key) and bool(self.steam_id)

    @staticmethod
    def parse_steam_id_input(value: str) -> tuple[str, bool]:
        """
        Parse the user-provided Steam ID input.

        Accepts a SteamID64, a vanity (custom URL) name, or a full profile URL.

        Returns:
            Tuple of (extracted value, True if it's already a SteamID64)
        """
        value = value.strip().rstrip("/")
        url_match = _PROFILE_URL_PATTERN.search(value)
        if url_match is not None:
            extracted = url_match["value"]
            if url_match["kind"].lower() == "profiles":
                return extracted, bool(_STEAM_ID64_PATTERN.match(extracted))
            return extracted, False
        return value, bool(_STEAM_ID64_PATTERN.match(value))

    async def _resolve_steam_id(self, session: aiohttp.ClientSession, proxy: str | None) -> str:
        """Resolve the configured Steam ID input into a SteamID64."""
        value, is_id64 = self.parse_steam_id_input(self.steam_id)
        if is_id64:
            return value

        # treat the value as a vanity (custom profile) name and resolve it
        params = {"key": self.api_key, "vanityurl": value}
        data = await self._api_get(session, RESOLVE_VANITY_URL, params, proxy)
        response: dict[str, Any] = data.get("response", {})
        if response.get("success") != 1 or not response.get("steamid"):
            raise LibrarySyncError(f"Steam: could not resolve profile name '{value}'")
        return str(response["steamid"])

    async def _api_get(
        self,
        session: aiohttp.ClientSession,
        url: str,
        params: dict[str, str],
        proxy: str | None,
    ) -> dict[str, Any]:
        """Perform a Steam Web API GET request and return the parsed JSON body."""
        try:
            async with session.get(url, params=params, proxy=proxy) as response:
                if response.status in (401, 403):
                    raise LibrarySyncError("Steam: API key was rejected (401/403)")
                if response.status >= 400:
                    raise LibrarySyncError(f"Steam: API request failed ({response.status})")
                return await response.json()
        except LibrarySyncError:
            raise
        except Exception as exc:
            raise LibrarySyncError(f"Steam: connection error: {exc}") from exc

    @staticmethod
    def parse_owned_games(data: dict[str, Any]) -> list[OwnedGame]:
        """Parse a GetOwnedGames API response into OwnedGame objects."""
        response: dict[str, Any] = data.get("response") or {}
        if "games" not in response:
            # a well-formed but empty response means the profile's game details
            # are private, or the account owns no games
            raise LibrarySyncError(
                "Steam: no games returned - make sure the profile's game details are public"
            )
        games: list[OwnedGame] = []
        for game in response["games"]:
            name = game.get("name")
            if not name:
                continue
            games.append(
                OwnedGame(
                    name=str(name),
                    app_id=str(game["appid"]),
                    provider=SteamProvider.name,
                    last_played=int(game.get("rtime_last_played") or 0),
                )
            )
        return games

    async def fetch_owned_games(self, session: aiohttp.ClientSession) -> list[OwnedGame]:
        if not self.is_configured:
            raise LibrarySyncError("Steam: API key and Steam ID must be configured")

        proxy: str | None = self._settings.proxy or None
        steam_id64 = await self._resolve_steam_id(session, proxy)
        params = {
            "key": self.api_key,
            "steamid": steam_id64,
            "include_appinfo": "1",
            "include_played_free_games": "1",
            "format": "json",
        }
        data = await self._api_get(session, OWNED_GAMES_URL, params, proxy)
        games = self.parse_owned_games(data)
        logger.info("Steam library sync: fetched %d owned games", len(games))
        return games
