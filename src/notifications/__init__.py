"""Outbound notifications (Discord, more providers later)."""

from __future__ import annotations

from src.notifications.base import (
    EVENT_TYPES,
    NotificationError,
    NotificationProvider,
)
from src.notifications.discord import DiscordProvider
from src.notifications.service import NotificationService


__all__ = [
    "EVENT_TYPES",
    "DiscordProvider",
    "NotificationError",
    "NotificationProvider",
    "NotificationService",
]
