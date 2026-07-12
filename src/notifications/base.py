"""
Base classes for outbound notification providers.

A notification provider delivers short messages about mining events (a drop was
claimed, an auto-tracked game needs linking, ...) to an external service (Discord,
more providers later). NotificationService decides *when* to notify; providers only
know *how* to deliver a given message.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, ClassVar, cast


if TYPE_CHECKING:
    from src.config.settings import Settings


class NotificationError(Exception):
    """Raised when delivering a notification through a provider fails."""


# keys of NotificationEventSettings (src/config/settings.py) - also used as the
# per-(provider, event) cooldown/seen-state key in NotificationService
EVENT_TYPES = (
    "drop_received",
    "unlinked_tracked_game",
    "auth_attention",
    "mining_stalled",
    "new_campaign",
)


class NotificationProvider(ABC):
    """Base class for outbound notification providers."""

    # unique provider identifier, also the settings key (e.g. "discord")
    name: ClassVar[str]

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def provider_settings(self) -> dict[str, Any]:
        """This provider's section of the notifications settings."""
        notification_settings = cast("dict[str, Any]", self._settings.notifications)
        return notification_settings.get(self.name, {})

    @property
    def enabled(self) -> bool:
        """Whether this provider is enabled and configured well enough to send."""
        return bool(self.provider_settings.get("enabled", False)) and self.is_configured

    @property
    @abstractmethod
    def is_configured(self) -> bool:
        """Whether the provider has all the configuration it needs to send."""
        raise NotImplementedError

    def event_enabled(self, event_type: str) -> bool:
        """Whether this provider should send notifications for the given event type."""
        events = cast("dict[str, Any]", self.provider_settings.get("events", {}))
        return bool(events.get(event_type, False))

    def _sensitive_values(self) -> tuple[str, ...]:
        """Credential values that must never appear in error messages/logs."""
        return ()

    def _redact(self, text: str) -> str:
        """
        Mask this provider's credentials in text destined for logs or the UI.

        Exception messages can embed request details (aiohttp includes the full
        URL, query string and all), so anything interpolating an exception into
        a NotificationError message must pass it through here first.
        """
        for secret in self._sensitive_values():
            if secret:
                text = text.replace(secret, "***")
        return text

    @abstractmethod
    async def send(self, event_type: str, title: str, description: str) -> None:
        """
        Deliver a single notification.

        Raises:
            NotificationError: If the notification could not be delivered
                (bad credentials, missing permissions, network error, ...)
        """
        raise NotImplementedError
