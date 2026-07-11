"""Game library synchronization (Steam, more platforms later)."""

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


__all__ = [
    "LIST_MODES",
    "LIST_MODE_BLACKLIST",
    "LIST_MODE_WHITELIST",
    "LibraryProvider",
    "LibrarySyncError",
    "LibrarySyncService",
    "OwnedGame",
    "SteamProvider",
    "normalize_game_name",
]
