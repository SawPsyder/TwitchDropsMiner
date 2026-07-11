"""
Base classes for game library providers.

A library provider connects to an external game platform (Steam, Epic, Ubisoft, ...)
and reports the list of games the user owns there. The LibrarySyncService uses that
list to automatically add games with active drop campaigns to the "Games to Watch"
list.
"""

from __future__ import annotations

import re
import unicodedata
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar, cast


if TYPE_CHECKING:
    import aiohttp

    from src.config.settings import Settings


class LibrarySyncError(Exception):
    """Raised when fetching a game library from a provider fails."""


@dataclass(frozen=True)
class OwnedGame:
    """A single game owned on an external platform."""

    name: str
    app_id: str
    provider: str
    # unix timestamp of the last play session, 0 if never played or unknown
    last_played: int = 0


# characters that commonly differ between platform catalogs and Twitch categories
_TRADEMARK_CHARS = re.compile(r"[™®©]")  # ™ ® ©
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize_game_name(name: str) -> str:
    """
    Normalize a game name for cross-platform comparison.

    Platform catalogs (Steam) and Twitch categories often differ in trademark
    symbols, punctuation, casing and unicode variants of the same title.
    """
    name = unicodedata.normalize("NFKD", name)
    name = _TRADEMARK_CHARS.sub("", name)
    name = name.casefold()
    name = _NON_ALNUM.sub(" ", name)
    return " ".join(name.split())


class LibraryProvider(ABC):
    """Base class for external game library providers."""

    # unique provider identifier, also the settings key (e.g. "steam")
    name: ClassVar[str]

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def provider_settings(self) -> dict[str, Any]:
        """This provider's section of the library_sync settings."""
        sync_settings = cast("dict[str, Any]", self._settings.library_sync)
        return sync_settings.get(self.name, {})

    @property
    def enabled(self) -> bool:
        """Whether this provider is enabled and configured well enough to sync."""
        return bool(self.provider_settings.get("enabled", False)) and self.is_configured

    @property
    @abstractmethod
    def is_configured(self) -> bool:
        """Whether the provider has all the configuration it needs to fetch."""
        raise NotImplementedError

    @abstractmethod
    async def fetch_owned_games(self, session: aiohttp.ClientSession) -> list[OwnedGame]:
        """
        Fetch the list of owned games from the platform.

        Raises:
            LibrarySyncError: If the library could not be fetched
                (bad credentials, private profile, network error, ...)
        """
        raise NotImplementedError
