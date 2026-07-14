"""
Persistent record of badge/emote drops verified as completed.

Twitch auto-grants badges and emotes once the required watchtime is registered, but it
does not reliably report them as claimed in the inventory response (especially for
unlinked-but-eligible campaigns). Every ``fetch_inventory`` rebuilds all drop objects
from scratch, so without a local record the drop comes back as unclaimed and gets
re-mined from zero on every reload/restart - only to be recognised as already earned,
over and over.

This store persists the IDs of drops we've confirmed complete (keyed to their campaign's
end time, so finished campaigns can be pruned safely) and is consulted when drops are
rebuilt, keeping them marked as claimed across reloads and restarts.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import cast

from src.config import CLAIMED_DROPS_PATH
from src.utils import json_load, json_save


logger = logging.getLogger("TwitchDrops")

# How long after a campaign ends we keep its completed-drop record. Once a campaign is
# over the drop can no longer be earned, so the entry only exists to keep the file from
# growing without bound - a small grace period guards against clock skew / late reloads.
_RETENTION = timedelta(days=7)


class ClaimedDropsStore:
    """Persists IDs of badge/emote drops verified as completed."""

    def __init__(self, path: Path = CLAIMED_DROPS_PATH) -> None:
        self._path = path
        # drop_id -> campaign end timestamp (used only for pruning stale entries)
        self._expiry: dict[str, datetime] = {}
        raw = cast("dict[str, str]", json_load(path, {}, merge=False))
        for drop_id, value in raw.items():
            try:
                self._expiry[drop_id] = datetime.fromisoformat(value)
            except (TypeError, ValueError):
                # ignore malformed entries rather than letting a corrupt file break mining
                logger.warning("Ignoring malformed claimed-drop entry: %s=%r", drop_id, value)
        # prune on load, persisting only if anything actually changed
        if self._prune():
            self._save()

    def is_completed(self, drop_id: str) -> bool:
        """Whether this drop was previously verified as completed."""
        return drop_id in self._expiry

    def mark_completed(self, drop_id: str, expires_at: datetime) -> None:
        """Record a drop as completed, keyed to its campaign end time, and persist."""
        if self._expiry.get(drop_id) == expires_at:
            # already recorded with the same expiry - avoid a redundant disk write
            return
        self._expiry[drop_id] = expires_at
        self._prune()
        self._save()

    def _prune(self) -> bool:
        """Drop entries for campaigns that ended long ago. Returns True if any were removed."""
        cutoff = datetime.now(timezone.utc) - _RETENTION
        stale = [drop_id for drop_id, expiry in self._expiry.items() if expiry < cutoff]
        for drop_id in stale:
            del self._expiry[drop_id]
        return bool(stale)

    def _save(self) -> None:
        json_save(
            self._path,
            {drop_id: expiry.isoformat() for drop_id, expiry in self._expiry.items()},
        )
