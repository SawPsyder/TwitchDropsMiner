"""
Ubisoft Connect game library provider.

Ubisoft has no official public Web API, and password-based (Basic auth) logins
to the ubiservices API were disabled by Ubisoft around April 2026 - they now get
rejected (401/429) even with correct credentials. This provider therefore
authenticates the way the official Ubisoft Connect web client stays logged in:
with the long-lived "remember me" ticket, which the user copies from their
browser's localStorage (key "PRODrememberMe") after logging in at
connect.ubisoft.com - the settings card in the web GUI walks them through it.
Since the actual login happens in the browser, accounts with two-factor
authentication work fine - the 2FA challenge is completed there.

Ubisoft rotates the remember-me ticket when it's used, so the latest ticket
(and the short-lived session derived from it) is persisted to
DATA_DIR/ubisoft_auth.json. The user-pasted token in the settings only
bootstraps that chain; pasting a new token restarts it.

Limitation: the API does not expose last-played times, so Ubisoft-only games
sort at the alphabetical tail of the auto watch list.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from src.config import UBISOFT_AUTH_PATH
from src.library_sync.base import LibraryProvider, LibrarySyncError, OwnedGame
from src.utils import json_load, json_save


if TYPE_CHECKING:
    from pathlib import Path

    import aiohttp

    from src.config.settings import Settings


logger = logging.getLogger("TwitchDrops")

SESSIONS_URL = "https://public-ubiservices.ubi.com/v3/profiles/sessions"
OWNED_GAMES_URL = "https://public-ubiservices.ubi.com/v1/profiles/me/uplay/graphql"
CONNECT_ORIGIN = "https://connect.ubisoft.com"

# App id of the Ubisoft Connect web client - required by all ubiservices calls
UBI_APP_ID = "f35adcb5-1911-440c-b1c9-48fdc1701c68"
UBI_GENOME_ID = "5b36b900-65d8-47f3-93c8-86bdaa48ab50"
# the login page users copy their remember-me ticket from (linked in the web GUI)
LOGIN_PAGE_URL = (
    f"{CONNECT_ORIGIN}/login?appId={UBI_APP_ID}&genomeId={UBI_GENOME_ID}"
    "&lang=en-US&nextUrl=https:%2F%2Fconnect.ubisoft.com%2Fready"
)
# ubiservices rejects requests with unknown/blank user agents
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    " (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# session tickets last ~3 hours; refresh a bit earlier to avoid using a stale one
TICKET_LIFETIME = timedelta(hours=2)

OWNED_GAMES_QUERY = """
query AllGames {
  viewer {
    id
    ownedGames: games(filterBy: {isOwned: true}) {
      totalCount
      nodes {
        id
        spaceId
        name
      }
    }
  }
}
""".strip()

_EMPTY_AUTH_STATE: dict[str, Any] = {
    # the settings token this session chain was bootstrapped from
    "source_ticket": "",
    # the latest (rotated) remember-me ticket
    "remember_me_ticket": "",
    # short-lived session derived from the remember-me ticket
    "ticket": "",
    "session_id": "",
    "expires_at": None,
}


class UbisoftProvider(LibraryProvider):
    """Fetches owned games from the Ubisoft Connect (ubiservices) API."""

    name = "ubisoft"

    def __init__(self, settings: Settings, auth_path: Path = UBISOFT_AUTH_PATH) -> None:
        super().__init__(settings)
        self._auth_path = auth_path
        self._auth: dict[str, Any] = json_load(auth_path, _EMPTY_AUTH_STATE, merge=False)

    @property
    def remember_me_ticket(self) -> str:
        """The user-pasted remember-me ticket from the settings."""
        return self.clean_ticket_input(
            str(self.provider_settings.get("remember_me_ticket", ""))
        )

    @property
    def is_configured(self) -> bool:
        return bool(self.remember_me_ticket)

    def _sensitive_values(self) -> tuple[str, ...]:
        return (
            self.remember_me_ticket,
            str(self._auth.get("remember_me_ticket") or ""),
            str(self._auth.get("ticket") or ""),
            str(self._auth.get("session_id") or ""),
        )

    @staticmethod
    def clean_ticket_input(value: str) -> str:
        """
        Normalize a pasted remember-me ticket.

        localStorage values are often copied with surrounding JSON quotes,
        and "null" is what the login page stores when "Remember me" was
        left unchecked.
        """
        value = value.strip().strip('"').strip()
        if value.lower() == "null":
            return ""
        return value

    def _base_headers(self) -> dict[str, str]:
        return {
            "Ubi-AppId": UBI_APP_ID,
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json",
            "Origin": CONNECT_ORIGIN,
            "Referer": CONNECT_ORIGIN,
        }

    def _save_auth(self) -> None:
        json_save(self._auth_path, self._auth)

    def _session_valid(self) -> bool:
        expires_at = self._auth.get("expires_at")
        return (
            bool(self._auth.get("ticket"))
            and self._auth.get("source_ticket") == self.remember_me_ticket
            and isinstance(expires_at, datetime)
            and datetime.now(timezone.utc) < expires_at
        )

    def _invalidate_session(self) -> None:
        """Drop the short-lived session but keep the remember-me ticket chain."""
        self._auth["ticket"] = ""
        self._auth["session_id"] = ""
        self._auth["expires_at"] = None

    @staticmethod
    def parse_login_response(data: dict[str, Any]) -> tuple[str, str, str]:
        """
        Extract (ticket, session id, new remember-me ticket) from a sessions
        API response. The remember-me ticket is empty when Ubisoft didn't
        rotate it.

        Raises:
            LibrarySyncError: If no session ticket was issued
        """
        if data.get("twoFactorAuthenticationTicket") and not data.get("ticket"):
            # shouldn't happen with remember-me logins - the 2FA challenge was
            # already completed in the browser - but surface it clearly anyway
            raise LibrarySyncError(
                "Ubisoft: login unexpectedly asked for a 2FA code -"
                " log in again in your browser and paste a fresh token"
            )
        ticket = data.get("ticket")
        if not ticket:
            raise LibrarySyncError("Ubisoft: login did not return a session ticket")
        return (
            str(ticket),
            str(data.get("sessionId", "")),
            str(data.get("rememberMeTicket") or ""),
        )

    async def _login(self, session: aiohttp.ClientSession, proxy: str | None) -> None:
        """Create a new ubiservices session from the remember-me ticket."""
        source_ticket = self.remember_me_ticket
        if self._auth.get("source_ticket") != source_ticket:
            # the user pasted a new token - restart the rotation chain from it
            self._auth = dict(_EMPTY_AUTH_STATE)
            self._auth["source_ticket"] = source_ticket
        rm_ticket = self._auth.get("remember_me_ticket") or source_ticket

        headers = self._base_headers()
        headers["Authorization"] = f"rm_v1 t={rm_ticket}"
        try:
            async with session.post(
                SESSIONS_URL, headers=headers, json={"rememberMe": True}, proxy=proxy
            ) as response:
                if response.status in (401, 403):
                    raise LibrarySyncError(
                        "Ubisoft: login token was rejected - log in again in your"
                        " browser and paste a fresh token (401/403)"
                    )
                if response.status == 429:
                    raise LibrarySyncError("Ubisoft: login rate limit reached - try again later")
                if response.status >= 400:
                    raise LibrarySyncError(f"Ubisoft: login request failed ({response.status})")
                data: dict[str, Any] = await response.json()
        except LibrarySyncError:
            raise
        except Exception as exc:
            raise LibrarySyncError(
                f"Ubisoft: connection error: {self._redact(str(exc))}"
            ) from exc

        ticket, session_id, new_rm_ticket = self.parse_login_response(data)
        self._auth["ticket"] = ticket
        self._auth["session_id"] = session_id
        self._auth["expires_at"] = datetime.now(timezone.utc) + TICKET_LIFETIME
        if new_rm_ticket:
            self._auth["remember_me_ticket"] = new_rm_ticket
        self._save_auth()

    async def _ensure_session(self, session: aiohttp.ClientSession, proxy: str | None) -> None:
        """Login if there's no reusable session for the current token."""
        if self._session_valid():
            return
        self._invalidate_session()
        await self._login(session, proxy)

    @staticmethod
    def parse_owned_games(data: dict[str, Any]) -> list[OwnedGame]:
        """Parse an owned-games GraphQL response into OwnedGame objects."""
        if data.get("errors"):
            message = str(data["errors"][0].get("message", "unknown error"))
            raise LibrarySyncError(f"Ubisoft: games query failed: {message}")
        viewer: dict[str, Any] | None = (data.get("data") or {}).get("viewer")
        if viewer is None:
            raise LibrarySyncError("Ubisoft: games query returned no account data")
        nodes: list[dict[str, Any]] = (viewer.get("ownedGames") or {}).get("nodes") or []
        games: list[OwnedGame] = []
        for node in nodes:
            name = node.get("name")
            if not name:
                continue
            games.append(
                OwnedGame(
                    name=str(name),
                    app_id=str(node.get("spaceId") or node.get("id") or ""),
                    provider=UbisoftProvider.name,
                    # ubiservices doesn't expose last-played times
                    last_played=0,
                )
            )
        return games

    async def _fetch_games(
        self, session: aiohttp.ClientSession, proxy: str | None
    ) -> dict[str, Any]:
        headers = self._base_headers()
        headers["Authorization"] = f"Ubi_v1 t={self._auth['ticket']}"
        if self._auth.get("session_id"):
            headers["Ubi-SessionId"] = str(self._auth["session_id"])
        payload = {"operationName": "AllGames", "variables": {}, "query": OWNED_GAMES_QUERY}
        try:
            async with session.post(
                OWNED_GAMES_URL, headers=headers, json=payload, proxy=proxy
            ) as response:
                if response.status in (401, 403):
                    # session ticket expired server-side
                    self._invalidate_session()
                    raise LibrarySyncError("Ubisoft: session expired (401/403)")
                if response.status >= 400:
                    raise LibrarySyncError(f"Ubisoft: games request failed ({response.status})")
                return await response.json()  # type: ignore[no-any-return]
        except LibrarySyncError:
            raise
        except Exception as exc:
            raise LibrarySyncError(
                f"Ubisoft: connection error: {self._redact(str(exc))}"
            ) from exc

    async def fetch_owned_games(self, session: aiohttp.ClientSession) -> list[OwnedGame]:
        if not self.is_configured:
            raise LibrarySyncError("Ubisoft: login token must be configured")

        proxy: str | None = self._settings.proxy or None
        await self._ensure_session(session, proxy)
        try:
            data = await self._fetch_games(session, proxy)
        except LibrarySyncError:
            if self._auth.get("ticket"):
                raise
            # the reused session was rejected - retry once with a fresh login
            await self._ensure_session(session, proxy)
            data = await self._fetch_games(session, proxy)
        games = self.parse_owned_games(data)
        logger.info("Ubisoft library sync: fetched %d owned games", len(games))
        return games
