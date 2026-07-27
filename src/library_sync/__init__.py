"""Game library synchronization (Steam, Ubisoft Connect, Xbox, more platforms later)."""

from __future__ import annotations

from src.library_sync.base import (
    LibraryProvider,
    LibrarySyncError,
    OwnedGame,
    normalize_game_name,
)
from src.library_sync.service import (
    LIST_MODE_BLACKLIST,
    LIST_MODE_WHITELIST,
    LIST_MODES,
    LibrarySyncService,
)
from src.library_sync.steam import SteamProvider
from src.library_sync.ubisoft import UbisoftProvider
from src.library_sync.xbox import (
    DEFAULT_MARKET,
    XBOX_MARKET_CODES,
    XBOX_MARKETS,
    XboxProvider,
)


__all__ = [
    "DEFAULT_MARKET",
    "XBOX_MARKETS",
    "XBOX_MARKET_CODES",
    "LIST_MODES",
    "LIST_MODE_BLACKLIST",
    "LIST_MODE_WHITELIST",
    "LibraryProvider",
    "LibrarySyncError",
    "LibrarySyncService",
    "OwnedGame",
    "SteamProvider",
    "UbisoftProvider",
    "XboxProvider",
    "normalize_game_name",
]
