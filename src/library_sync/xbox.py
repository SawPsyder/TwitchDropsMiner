"""
Xbox / Microsoft Store game library provider.

Pulls a library together from up to five independent sources, each of which can
be turned on separately in the settings:

Account library (needs the Microsoft account connected, see xbox_auth.py):
- played titles: titlehub's title history, the only source that carries real
  last-played timestamps, so these drive the auto watch list's recency ordering
- entitlements: the Xbox inventory service, i.e. games actually bought on the
  account. It reports title ids rather than names, so the names are resolved
  through titlehub's batch endpoint.

Subscription catalogs (public data, no account or credentials needed at all):
- PC Game Pass, Console Game Pass and EA Play, each behind its own toggle.
  These come from the same catalog.gamepass.com + displaycatalog.mp.microsoft.com
  pair the Game Pass website uses. They carry no last-played information, so a
  catalog-only game sorts with the rest of the not-recently-played games (by
  campaign deadline) rather than jumping the queue.

A failing source never fails the whole sync: whatever the other sources returned
is still used, and the problem is logged. That matters most for the two account
sources, which talk to Xbox services that Microsoft changes without notice - a
dead endpoint degrades the library instead of emptying it.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from typing import TYPE_CHECKING, Any, ClassVar

import aiohttp

from src.library_sync.base import LibraryProvider, LibrarySyncError, OwnedGame, normalize_game_name
from src.library_sync.xbox_auth import XboxAuth, XboxLoginPending
from src.utils import chunk


if TYPE_CHECKING:
    from pathlib import Path

    from src.config.settings import Settings
    from src.library_sync.xbox_auth import DeviceCodePrompt


logger = logging.getLogger("TwitchDrops")

TITLEHUB_HISTORY_URL = (
    "https://titlehub.xboxlive.com/users/xuid({xuid})/titles/titlehistory/decoration/detail"
)
TITLEHUB_BATCH_URL = "https://titlehub.xboxlive.com/titles/batch/decoration/detail"
INVENTORY_URL = "https://inventory.xboxlive.com/users/me/inventory"
SIGL_URL = "https://catalog.gamepass.com/sigls/v2"
DISPLAYCATALOG_URL = "https://displaycatalog.mp.microsoft.com/v7.0/products"

# Subscription catalog ids, as used by xbox.com itself. Each maps to one
# settings toggle (see CATALOG_SETTING_KEYS).
CATALOG_PC_GAME_PASS = "fdd9e2a7-0fee-49f6-ad69-4354098401ff"
CATALOG_CONSOLE_GAME_PASS = "f6f1f99f-9b49-4ccd-b3bf-4d9767a77f5e"
CATALOG_EA_PLAY = "1d33fbb9-b895-4732-a8ca-a55c8b99fa2c"

# Store regions the subscription catalogs are actually served in, sorted by display
# name for the settings dropdown. Determined by probing every ISO 3166-1 alpha-2 code
# against the PC Game Pass catalog: these 86 return a full catalog (449-526 titles),
# every other code returns nothing at all - there is no partial middle ground, so an
# unsupported region would silently sync zero games.
XBOX_MARKETS: tuple[tuple[str, str], ...] = (
    ("AL", "Albania"),
    ("DZ", "Algeria"),
    ("AR", "Argentina"),
    ("AU", "Australia"),
    ("AT", "Austria"),
    ("BH", "Bahrain"),
    ("BE", "Belgium"),
    ("BO", "Bolivia"),
    ("BA", "Bosnia & Herzegovina"),
    ("BR", "Brazil"),
    ("BG", "Bulgaria"),
    ("CA", "Canada"),
    ("CL", "Chile"),
    ("CO", "Colombia"),
    ("CR", "Costa Rica"),
    ("HR", "Croatia"),
    ("CY", "Cyprus"),
    ("CZ", "Czechia"),
    ("DK", "Denmark"),
    ("EC", "Ecuador"),
    ("EG", "Egypt"),
    ("SV", "El Salvador"),
    ("EE", "Estonia"),
    ("FI", "Finland"),
    ("FR", "France"),
    ("GE", "Georgia"),
    ("DE", "Germany"),
    ("GR", "Greece"),
    ("GT", "Guatemala"),
    ("HN", "Honduras"),
    ("HK", "Hong Kong SAR China"),
    ("HU", "Hungary"),
    ("IS", "Iceland"),
    ("IN", "India"),
    ("ID", "Indonesia"),
    ("IE", "Ireland"),
    ("IL", "Israel"),
    ("IT", "Italy"),
    ("JP", "Japan"),
    ("KW", "Kuwait"),
    ("LV", "Latvia"),
    ("LY", "Libya"),
    ("LI", "Liechtenstein"),
    ("LT", "Lithuania"),
    ("LU", "Luxembourg"),
    ("MY", "Malaysia"),
    ("MT", "Malta"),
    ("MX", "Mexico"),
    ("MD", "Moldova"),
    ("ME", "Montenegro"),
    ("MA", "Morocco"),
    ("NL", "Netherlands"),
    ("NZ", "New Zealand"),
    ("NI", "Nicaragua"),
    ("MK", "North Macedonia"),
    ("NO", "Norway"),
    ("OM", "Oman"),
    ("PA", "Panama"),
    ("PY", "Paraguay"),
    ("PE", "Peru"),
    ("PH", "Philippines"),
    ("PL", "Poland"),
    ("PT", "Portugal"),
    ("QA", "Qatar"),
    ("RO", "Romania"),
    ("RU", "Russia"),
    ("SA", "Saudi Arabia"),
    ("RS", "Serbia"),
    ("SG", "Singapore"),
    ("SK", "Slovakia"),
    ("SI", "Slovenia"),
    ("ZA", "South Africa"),
    ("KR", "South Korea"),
    ("ES", "Spain"),
    ("SE", "Sweden"),
    ("CH", "Switzerland"),
    ("TW", "Taiwan"),
    ("TH", "Thailand"),
    ("TN", "Tunisia"),
    ("TR", "Türkiye"),
    ("UA", "Ukraine"),
    ("AE", "United Arab Emirates"),
    ("GB", "United Kingdom"),
    ("US", "United States"),
    ("UY", "Uruguay"),
    ("VN", "Vietnam"),
)
XBOX_MARKET_CODES = frozenset(code for code, _ in XBOX_MARKETS)
DEFAULT_MARKET = "US"

# displaycatalog rejects very long bigId lists; the store front-end batches by 20
_CATALOG_BATCH_SIZE = 20
# titlehub's batch endpoint is comfortable with larger batches
_TITLE_BATCH_SIZE = 100

# titlehub reports apps, demos and such alongside games
_GAME_TITLE_TYPES = frozenset({"game"})

# Microsoft Store product titles carry platform and edition qualifiers that no
# Twitch category ever has ("EA SPORTS FC 26 - PC", "A Plague Tale: Requiem -
# Windows", "9 Kings (Game Preview)", "Minecraft: Windows 10 Edition"). Left in
# place they'd stop those games from ever matching a campaign, so they're trimmed
# off the tail of the name. Deliberately an allowlist of known qualifiers rather
# than a generic "drop everything after the dash" rule, which would maul real
# subtitles like "Halo: The Master Chief Collection".
_TITLE_QUALIFIERS = (
    "pc",
    "pc edition",
    "windows",
    "windows 10",
    "windows 11",
    "windows edition",
    "windows 10 edition",
    "windows 11 edition",
    "console edition",
    "game preview",
    "xbox one edition",
    "xbox series x|s",
    "xbox series x|s edition",
)
# escaped because qualifiers contain regex metacharacters ("x|s"), longest first
_QUALIFIER_ALTERNATION = "|".join(
    re.escape(qualifier) for qualifier in sorted(_TITLE_QUALIFIERS, key=len, reverse=True)
)
_TITLE_SUFFIX_PATTERN = re.compile(
    # " - PC" / ": Windows 10 Edition" / " (Game Preview)" / " for Windows 10"
    rf"(?:\s*[-:–—]\s*(?:{_QUALIFIER_ALTERNATION})"
    rf"|\s*\(\s*(?:{_QUALIFIER_ALTERNATION})\s*\)"
    rf"|\s+for\s+windows(?:\s+1[01])?)\s*$",
    re.IGNORECASE,
)


class XboxProvider(LibraryProvider):
    """Fetches played, owned and subscription-included games from Xbox services."""

    name = "xbox"

    # settings key -> subscription catalog id, in the order they're merged
    CATALOG_SETTING_KEYS: ClassVar[dict[str, str]] = {
        "include_gamepass_pc": CATALOG_PC_GAME_PASS,
        "include_gamepass_console": CATALOG_CONSOLE_GAME_PASS,
        "include_ea_play": CATALOG_EA_PLAY,
    }

    def __init__(self, settings: Settings, auth_path: Path | None = None) -> None:
        super().__init__(settings)
        self._auth = XboxAuth() if auth_path is None else XboxAuth(auth_path)
        self._poll_task: asyncio.Task[None] | None = None

    @property
    def auth(self) -> XboxAuth:
        return self._auth

    @property
    def market(self) -> str:
        """
        Store region used for the subscription catalogs.

        Falls back to the default rather than passing an unsupported region
        through, which the catalog answers with an empty list instead of an
        error (see XBOX_MARKETS).
        """
        market = str(self.provider_settings.get("market") or "").strip().upper()
        return market if market in XBOX_MARKET_CODES else DEFAULT_MARKET

    @property
    def enabled_catalogs(self) -> dict[str, str]:
        """Subscription catalogs the user turned on, keyed by their settings key."""
        return {
            key: catalog_id
            for key, catalog_id in self.CATALOG_SETTING_KEYS.items()
            if bool(self.provider_settings.get(key, False))
        }

    @property
    def is_configured(self) -> bool:
        """
        Configured once there's anything to fetch.

        The subscription catalogs need no account, so enabling one of those is
        enough on its own - signing in is only required for the account library.
        """
        return self._auth.signed_in or bool(self.enabled_catalogs)

    def _sensitive_values(self) -> tuple[str, ...]:
        return self._auth.sensitive_values()

    # ------------------------------------------------------------------ sign-in

    def login_state(self) -> dict[str, Any]:
        """Sign-in state for the web GUI."""
        prompt = self._auth.pending_prompt
        return {
            "signed_in": self._auth.signed_in,
            "gamertag": self._auth.gamertag,
            "pending": prompt.as_dict() if prompt is not None else None,
        }

    def status_extra(self) -> dict[str, Any]:
        """Surface the sign-in state and which catalogs are on in the status payload."""
        return {"login": self.login_state(), "catalogs": sorted(self.enabled_catalogs)}

    def fetch_fingerprint(self) -> str:
        """
        Everything that changes which games come back.

        Switching store region or flipping a catalogue toggle has to refetch
        right away, otherwise the change looks like it did nothing until the
        12h cache expires. Signing in/out counts too - it adds or removes the
        two account sources.
        """
        catalogs = ",".join(sorted(self.enabled_catalogs))
        return f"market={self.market};catalogs={catalogs};account={int(self._auth.signed_in)}"

    async def start_login(self, session: aiohttp.ClientSession) -> DeviceCodePrompt:
        """
        Begin a device-code sign-in and poll for approval in the background.

        Returns:
            The prompt (code + URL) the user has to approve
        """
        proxy: str | None = self._settings.proxy or None
        prompt = await self._auth.start_device_code(session, proxy)
        if self._poll_task is not None and not self._poll_task.done():
            self._poll_task.cancel()
        self._poll_task = asyncio.create_task(self._poll_until_approved(prompt.interval))
        return prompt

    async def _poll_until_approved(self, interval: int) -> None:
        """Poll the device-code approval until it completes, expires or is cancelled."""
        proxy: str | None = self._settings.proxy or None
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30)
            ) as session:
                while self._auth.pending_prompt is not None:
                    await asyncio.sleep(max(interval, 1))
                    try:
                        if await self._auth.poll_device_code(session, proxy):
                            return
                    except XboxLoginPending:
                        continue
                    except LibrarySyncError as exc:
                        logger.warning("Xbox sign-in failed: %s", exc)
                        return
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - defensive, task must never crash
            logger.warning("Xbox sign-in polling stopped: %s", self._redact(str(exc)))

    def sign_out(self) -> None:
        """Disconnect the Microsoft account."""
        if self._poll_task is not None and not self._poll_task.done():
            self._poll_task.cancel()
        self._poll_task = None
        self._auth.sign_out()

    # ------------------------------------------------------------------ requests

    async def _xbl_request(
        self,
        session: aiohttp.ClientSession,
        method: str,
        url: str,
        proxy: str | None,
        *,
        contract_version: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Call an Xbox Live service with the account's XSTS authorization."""
        token = await self._auth.authorization(session, proxy)
        headers = {
            "Authorization": token.authorization,
            "x-xbl-contract-version": contract_version,
            "Accept": "application/json",
            "Accept-Language": "en-US",
        }
        try:
            async with session.request(
                method, url, headers=headers, json=payload, proxy=proxy
            ) as response:
                if response.status in (401, 403):
                    raise LibrarySyncError(
                        f"Xbox: the account's authorization was rejected ({response.status})"
                    )
                if response.status >= 400:
                    raise LibrarySyncError(f"Xbox: request failed ({response.status})")
                return await response.json(content_type=None)  # type: ignore[no-any-return]
        except LibrarySyncError:
            raise
        except Exception as exc:
            raise LibrarySyncError(f"Xbox: connection error: {self._redact(str(exc))}") from exc

    async def _public_get(
        self,
        session: aiohttp.ClientSession,
        url: str,
        params: dict[str, str],
        proxy: str | None,
    ) -> Any:
        """Call one of the public catalog endpoints (no authorization involved)."""
        try:
            async with session.get(url, params=params, proxy=proxy) as response:
                if response.status >= 400:
                    raise LibrarySyncError(f"Xbox: catalog request failed ({response.status})")
                return await response.json(content_type=None)
        except LibrarySyncError:
            raise
        except Exception as exc:
            raise LibrarySyncError(
                f"Xbox: catalog connection error: {self._redact(str(exc))}"
            ) from exc

    # ------------------------------------------------------------------- parsing

    @staticmethod
    def clean_product_title(name: str) -> str:
        """
        Strip Microsoft's platform/edition qualifiers off a product title.

        Applied to every Xbox source so the same game reported as "Game - PC" by
        the store and "Game" by Twitch merges into one entry. Qualifiers can
        stack ("Game - Windows (Game Preview)"), so this trims repeatedly, and
        never strips a name down to nothing.
        """
        cleaned = name.strip()
        for _ in range(3):
            trimmed = _TITLE_SUFFIX_PATTERN.sub("", cleaned).strip()
            if trimmed == cleaned or not trimmed:
                break
            cleaned = trimmed
        return cleaned or name.strip()

    @staticmethod
    def _parse_last_played(value: Any) -> int:
        """Convert a titlehub ISO timestamp into a unix timestamp (0 if absent)."""
        if not value:
            return 0
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return 0
        return int(parsed.timestamp())

    @classmethod
    def parse_title_history(cls, data: dict[str, Any]) -> list[OwnedGame]:
        """Parse a titlehub titlehistory/batch response into OwnedGame objects."""
        games: list[OwnedGame] = []
        for title in data.get("titles") or []:
            name = title.get("name")
            if not name:
                continue
            # skip apps, demos and other non-game entries
            if str(title.get("type") or "game").casefold() not in _GAME_TITLE_TYPES:
                continue
            history: dict[str, Any] = title.get("titleHistory") or {}
            games.append(
                OwnedGame(
                    name=cls.clean_product_title(str(name)),
                    app_id=str(title.get("titleId") or ""),
                    provider=XboxProvider.name,
                    last_played=cls._parse_last_played(history.get("lastTimePlayed")),
                )
            )
        return games

    @staticmethod
    def parse_inventory_title_ids(data: dict[str, Any]) -> list[str]:
        """Extract owned game title ids from an inventory response."""
        title_ids: list[str] = []
        for item in data.get("items") or []:
            if str(item.get("itemType") or "").casefold() != "game":
                continue
            if str(item.get("state") or "Enabled").casefold() not in ("enabled", "active"):
                continue
            title_id = item.get("titleId")
            if title_id:
                title_ids.append(str(title_id))
        return title_ids

    @staticmethod
    def parse_catalog_product_ids(data: Any) -> list[str]:
        """
        Extract product ids from a Game Pass sigl response.

        The first entry describes the collection itself, the rest are products.
        """
        if not isinstance(data, list):
            raise LibrarySyncError("Xbox: unexpected catalog response")
        return [str(entry["id"]) for entry in data if isinstance(entry, dict) and entry.get("id")]

    @classmethod
    def parse_catalog_products(cls, data: dict[str, Any]) -> list[str]:
        """Extract game titles from a displaycatalog products response."""
        names: list[str] = []
        for product in data.get("Products") or []:
            if str(product.get("ProductType") or "Game").casefold() != "game":
                continue
            localized: list[dict[str, Any]] = product.get("LocalizedProperties") or []
            if not localized:
                continue
            title = localized[0].get("ProductTitle")
            if title:
                names.append(cls.clean_product_title(str(title)))
        return names

    # -------------------------------------------------------------- the sources

    async def _fetch_played_titles(
        self, session: aiohttp.ClientSession, proxy: str | None
    ) -> list[OwnedGame]:
        """Games the account has actually launched, with last-played times."""
        token = await self._auth.authorization(session, proxy)
        if not token.xuid:
            raise LibrarySyncError("Xbox: the account has no XUID to read a library for")
        data = await self._xbl_request(
            session,
            "GET",
            TITLEHUB_HISTORY_URL.format(xuid=token.xuid),
            proxy,
            contract_version="2",
        )
        return self.parse_title_history(data)

    async def _fetch_entitlements(
        self, session: aiohttp.ClientSession, proxy: str | None
    ) -> list[OwnedGame]:
        """Games bought on the account, resolved from title ids to names."""
        inventory = await self._xbl_request(
            session, "GET", INVENTORY_URL, proxy, contract_version="2"
        )
        title_ids = self.parse_inventory_title_ids(inventory)
        if not title_ids:
            return []
        games: list[OwnedGame] = []
        for batch in chunk(title_ids, _TITLE_BATCH_SIZE):
            data = await self._xbl_request(
                session,
                "POST",
                TITLEHUB_BATCH_URL,
                proxy,
                contract_version="2",
                payload={"pfns": None, "titleIds": batch},
            )
            games.extend(self.parse_title_history(data))
        return games

    async def _fetch_catalog(
        self, session: aiohttp.ClientSession, proxy: str | None, catalog_id: str
    ) -> list[OwnedGame]:
        """Every game included in one subscription catalog."""
        sigl = await self._public_get(
            session,
            SIGL_URL,
            {"id": catalog_id, "language": "en-us", "market": self.market},
            proxy,
        )
        product_ids = self.parse_catalog_product_ids(sigl)
        games: list[OwnedGame] = []
        for batch in chunk(product_ids, _CATALOG_BATCH_SIZE):
            data = await self._public_get(
                session,
                DISPLAYCATALOG_URL,
                {
                    "bigIds": ",".join(batch),
                    "market": self.market,
                    "languages": "en-us",
                    # the catalog requires a correlation vector header/param
                    "MS-CV": "DGU1mcuYo0WMMp1U.1",
                },
                proxy,
            )
            for name in self.parse_catalog_products(data):
                games.append(
                    OwnedGame(
                        name=name,
                        app_id="",
                        provider=XboxProvider.name,
                        # catalogs carry no play history
                        last_played=0,
                    )
                )
        return games

    # ------------------------------------------------------------------ fetching

    @staticmethod
    def merge_games(sources: list[list[OwnedGame]]) -> list[OwnedGame]:
        """
        Merge the per-source results, de-duplicating by normalized name.

        The same game routinely shows up in several sources (played, bought and
        included in Game Pass); the entry with the most recent last-played time
        wins so recency ordering survives the merge.
        """
        merged: dict[str, OwnedGame] = {}
        for games in sources:
            for game in games:
                key = normalize_game_name(game.name)
                if not key:
                    continue
                existing = merged.get(key)
                if existing is None:
                    merged[key] = game
                elif game.last_played > existing.last_played:
                    # keep the better timestamp, and the app_id if we gained one
                    merged[key] = OwnedGame(
                        name=existing.name,
                        app_id=existing.app_id or game.app_id,
                        provider=existing.provider,
                        last_played=game.last_played,
                    )
                elif not existing.app_id and game.app_id:
                    merged[key] = OwnedGame(
                        name=existing.name,
                        app_id=game.app_id,
                        provider=existing.provider,
                        last_played=existing.last_played,
                    )
        return list(merged.values())

    async def fetch_owned_games(self, session: aiohttp.ClientSession) -> list[OwnedGame]:
        if not self.is_configured:
            raise LibrarySyncError(
                "Xbox: connect a Microsoft account or enable a subscription catalog"
            )

        proxy: str | None = self._settings.proxy or None
        sources: list[list[OwnedGame]] = []
        failures: list[str] = []

        if self._auth.signed_in:
            for label, fetch in (
                ("played titles", self._fetch_played_titles),
                ("entitlements", self._fetch_entitlements),
            ):
                try:
                    games = await fetch(session, proxy)
                except LibrarySyncError as exc:
                    logger.warning("Xbox library sync: %s unavailable: %s", label, exc)
                    failures.append(str(exc))
                    continue
                logger.info("Xbox library sync: %d %s", len(games), label)
                sources.append(games)

        for setting_key, catalog_id in self.enabled_catalogs.items():
            try:
                games = await self._fetch_catalog(session, proxy, catalog_id)
            except LibrarySyncError as exc:
                logger.warning("Xbox library sync: %s unavailable: %s", setting_key, exc)
                failures.append(str(exc))
                continue
            logger.info("Xbox library sync: %d games from %s", len(games), setting_key)
            sources.append(games)

        if not sources:
            # every configured source failed - report it so the UI shows the reason
            raise LibrarySyncError(failures[0] if failures else "Xbox: no library sources enabled")

        merged = self.merge_games(sources)
        logger.info("Xbox library sync: fetched %d games", len(merged))
        return merged
