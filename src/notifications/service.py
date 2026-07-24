"""
Notification service.

Decides *when* to fire a notification (per-provider enable + per-event-type
toggle, a global cooldown per (provider, event type), and first-run baseline
seeding for the two diff-based event types) and delegates delivery to each
enabled NotificationProvider. Provider failures are isolated here - a broken
notification integration must never affect mining.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from src.config import NOTIFICATIONS_STATE_PATH
from src.notifications.base import NotificationError, NotificationProvider
from src.notifications.discord import DiscordProvider
from src.utils import json_load, json_save


if TYPE_CHECKING:
    from src.config.settings import Settings
    from src.models.campaign import DropsCampaign


logger = logging.getLogger("TwitchDrops")

def _empty_state() -> dict[str, Any]:
    # a fresh dict per call - json_load's fallback only shallow-copies its
    # defaults argument, so a shared module-level dict would alias its nested
    # containers (last_sent/seen_unlinked/...) across every instance that
    # hasn't persisted a state file yet
    return {
        "last_sent": {},
        "seen_unlinked": [],
        "unlinked_seeded": False,
        "seen_campaigns": [],
        "campaigns_seeded": False,
    }


class NotificationService:
    """Fires outbound notifications for mining events across all providers."""

    def __init__(self, settings: Settings, state_path: Path = NOTIFICATIONS_STATE_PATH) -> None:
        self._settings = settings
        self._state_path = state_path
        self._providers: list[NotificationProvider] = [DiscordProvider(settings)]
        self._state: dict[str, Any] = json_load(state_path, _empty_state(), merge=False)
        self._last_errors: dict[str, str] = {}

    @property
    def notification_settings(self) -> dict[str, Any]:
        return cast("dict[str, Any]", self._settings.notifications)

    @property
    def enabled(self) -> bool:
        return bool(self.notification_settings.get("enabled", False))

    @property
    def cooldown(self) -> timedelta:
        minutes = int(self.notification_settings.get("cooldown_minutes", 15))
        return timedelta(minutes=max(0, minutes))

    def _save_state(self) -> None:
        json_save(self._state_path, self._state)

    @staticmethod
    def _cooldown_key(provider_name: str, event_type: str) -> str:
        return f"{provider_name}:{event_type}"

    def _in_cooldown(self, provider_name: str, event_type: str) -> bool:
        last_sent = cast("dict[str, Any]", self._state.setdefault("last_sent", {}))
        raw = last_sent.get(self._cooldown_key(provider_name, event_type))
        if not raw:
            return False
        try:
            sent_at = datetime.fromisoformat(raw)
        except ValueError:
            return False
        return datetime.now(UTC) - sent_at < self.cooldown

    def _mark_sent(self, provider_name: str, event_type: str) -> None:
        last_sent = cast("dict[str, Any]", self._state.setdefault("last_sent", {}))
        last_sent[self._cooldown_key(provider_name, event_type)] = (
            datetime.now(UTC).isoformat()
        )

    async def notify(self, event_type: str, title: str, description: str) -> None:
        """
        Deliver a notification to every enabled provider that has this event
        type turned on and isn't in cooldown for it.
        """
        if not self.enabled:
            return
        state_changed = False
        for provider in self._providers:
            if not provider.enabled or not provider.event_enabled(event_type):
                continue
            if self._in_cooldown(provider.name, event_type):
                continue
            try:
                await provider.send(event_type, title, description)
            except NotificationError as exc:
                logger.warning(
                    "Notification failed for %s/%s: %s", provider.name, event_type, exc
                )
                self._last_errors[provider.name] = str(exc)
                continue
            self._last_errors.pop(provider.name, None)
            self._mark_sent(provider.name, event_type)
            state_changed = True
        if state_changed:
            self._save_state()

    def get_provider(self, name: str) -> NotificationProvider | None:
        """Look up a registered provider by name (e.g. "discord")."""
        return next((provider for provider in self._providers if provider.name == name), None)

    async def send_test(self, provider_name: str = "discord") -> None:
        """
        Send a one-off test message through a provider, bypassing the
        enabled/event-type/cooldown gating - this is an explicit user action
        from the settings UI, not a mining event.
        """
        provider = self.get_provider(provider_name)
        if provider is None:
            raise NotificationError(f"Unknown notification provider: {provider_name}")
        await provider.send(
            "test",
            "Test notification",
            "This is a test notification from Twitch Drops Miner.",
        )

    # convenience wrappers ---------------------------------------------------

    async def notify_drop_received(self, game_name: str, benefits: Iterable[str]) -> None:
        benefit_text = ", ".join(benefits) or "a drop"
        await self.notify(
            "drop_received",
            "Drop received",
            f"Claimed **{benefit_text}** for *{game_name}*.",
        )

    async def notify_unlinked_tracked_game(self, game_name: str, campaign_name: str) -> None:
        await self.notify(
            "unlinked_tracked_game",
            "Unlinked tracked game",
            f'*{game_name}* has an active campaign ("{campaign_name}") but its Twitch'
            f" account isn't linked yet - link it to start earning.",
        )

    async def notify_auth_attention(self, reason: str) -> None:
        await self.notify("auth_attention", "Auth needs attention", reason)

    async def notify_mining_stalled(self, reason: str) -> None:
        await self.notify("mining_stalled", "Mining stalled", reason)

    async def notify_new_campaign(self, game_name: str, campaign_name: str) -> None:
        await self.notify(
            "new_campaign",
            "New campaign available",
            f'*{game_name}*: a new campaign ("{campaign_name}") just became available.',
        )

    # diff-against-previous-state helpers ------------------------------------

    async def track_unlinked_tracked_games(self, tree: list[dict[str, Any]]) -> None:
        """
        Diff the unlinked-auto-tracked tree (StreamSelector.
        get_unlinked_auto_tracked_tree) against what was last seen and notify
        about genuinely new (game, campaign) pairs. The first call ever seeds
        the baseline silently, so a restart doesn't re-report games that were
        already unlinked beforehand.
        """
        seen = set(cast("list[str]", self._state.get("seen_unlinked", [])))
        is_first_run = not self._state.get("unlinked_seeded", False)
        current: set[str] = set()
        new_entries: list[tuple[str, str]] = []
        for game_entry in tree:
            game_name = str(game_entry.get("game_name", ""))
            for campaign_entry in game_entry.get("campaigns", []):
                key = f"{game_name}::{campaign_entry.get('id', '')}"
                current.add(key)
                if key not in seen and not is_first_run:
                    new_entries.append((game_name, str(campaign_entry.get("name", ""))))
        self._state["seen_unlinked"] = sorted(current)
        self._state["unlinked_seeded"] = True
        self._save_state()
        for game_name, campaign_name in new_entries:
            await self.notify_unlinked_tracked_game(game_name, campaign_name)

    async def track_new_campaigns(
        self, campaigns: Iterable[DropsCampaign], games_to_watch: Iterable[str]
    ) -> None:
        """
        Diff active campaigns for watched games against what was last seen and
        notify about genuinely new ones. Seeds silently on first call, same as
        track_unlinked_tracked_games. The seen-set is updated every call
        regardless of whether notifications are enabled, so turning the
        setting on later doesn't dump a backlog of "new" campaigns.
        """
        watch_set = {name.casefold() for name in games_to_watch}
        seen = set(cast("list[str]", self._state.get("seen_campaigns", [])))
        is_first_run = not self._state.get("campaigns_seeded", False)
        current: set[str] = set()
        new_entries: list[tuple[str, str]] = []
        for campaign in campaigns:
            if campaign.game.name.casefold() not in watch_set:
                continue
            current.add(campaign.id)
            if campaign.id not in seen and not is_first_run:
                new_entries.append((campaign.game.name, campaign.name))
        self._state["seen_campaigns"] = sorted(current)
        self._state["campaigns_seeded"] = True
        self._save_state()
        for game_name, campaign_name in new_entries:
            await self.notify_new_campaign(game_name, campaign_name)

    def get_status(self) -> dict[str, Any]:
        """Current notification config/connection status for the web GUI."""
        providers: dict[str, Any] = {}
        for provider in self._providers:
            providers[provider.name] = {
                "enabled": bool(provider.provider_settings.get("enabled", False)),
                "configured": provider.is_configured,
                "last_error": self._last_errors.get(provider.name),
            }
        return {
            "enabled": self.enabled,
            "cooldown_minutes": int(self.notification_settings.get("cooldown_minutes", 15)),
            "providers": providers,
        }
