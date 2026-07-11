"""Campaign progress manager for tracking active drop mining progress."""

from __future__ import annotations

import asyncio
from time import monotonic
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from src.models import TimedDrop
    from src.web.managers.broadcaster import WebSocketBroadcaster


class CampaignProgressManager:
    """Manages active drop mining progress display and countdown timer.

    Tracks the currently mined drop and broadcasts real-time progress updates
    including remaining time and completion percentage to the web interface.
    """

    # Twitch normally credits a watched minute at least this often; if
    # `minute_almost_done` reports back that we've gone longer than this without
    # a progress update, the watch loop falls back to polling/estimating progress.
    STALE_UPDATE_SECONDS: float = 75.0

    def __init__(self, broadcaster: WebSocketBroadcaster):
        self._broadcaster = broadcaster
        self._current_drop: TimedDrop | None = None
        self._remaining_seconds: int = 0
        # Never received a progress update yet: treat as immediately stale, so the
        # watch loop's fallback can kick in right away instead of waiting on a
        # timer that has nothing to count down from.
        self._last_update_at: float = -self.STALE_UPDATE_SECONDS

    def update(self, drop: TimedDrop | None, remaining_seconds: int):
        """Update the current drop progress and remaining time.

        Args:
            drop: The drop currently being mined, or None if no active drop
            remaining_seconds: Estimated seconds remaining until the drop completes
        """
        self._current_drop = drop
        self._remaining_seconds = remaining_seconds
        self._last_update_at = monotonic()
        if drop:
            asyncio.create_task(
                self._broadcaster.emit(
                    "drop_progress",
                    {
                        "drop_id": drop.id,
                        "drop_name": drop.name,
                        "campaign_name": drop.campaign.name,
                        "campaign_id": drop.campaign.id,
                        "game_name": drop.campaign.game.name,
                        "current_minutes": drop.current_minutes,
                        "required_minutes": drop.required_minutes,
                        "progress": drop.progress,
                        "remaining_seconds": remaining_seconds,
                        "is_estimated": drop.extra_current_minutes > 0,
                    },
                )
            )

    def stop_timer(self):
        """Stop the progress timer and clear the current drop."""
        self._current_drop = None
        asyncio.create_task(self._broadcaster.emit("drop_progress_stop", {}))

    def minute_almost_done(self) -> bool:
        """Check if progress hasn't been updated recently and may need a nudge.

        `remaining_seconds` tracks the drop's total ETA for display purposes and
        can sit at many minutes even right after watching starts, so it can't be
        used to detect a stalled minute. Instead, this checks how long it's been
        since the last real progress update (websocket or GQL/bump fallback).

        Returns:
            True if no progress update has been recorded within STALE_UPDATE_SECONDS
        """
        return monotonic() - self._last_update_at >= self.STALE_UPDATE_SECONDS

    def get_current_drop(self) -> dict | None:
        """Get the current drop progress data for sending to newly connected clients.

        Returns:
            Dictionary with drop progress data, or None if no active drop
        """
        if self._current_drop is None:
            return None

        drop = self._current_drop
        return {
            "drop_id": drop.id,
            "drop_name": drop.name,
            "campaign_name": drop.campaign.name,
            "campaign_id": drop.campaign.id,
            "game_name": drop.campaign.game.name,
            "current_minutes": drop.current_minutes,
            "required_minutes": drop.required_minutes,
            "progress": drop.progress,
            "remaining_seconds": self._remaining_seconds,
            "is_estimated": drop.extra_current_minutes > 0,
        }
