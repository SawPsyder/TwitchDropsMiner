"""Settings manager for application configuration."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from src.i18n.translator import _
from src.library_sync import LIST_MODES
from src.models.game import Game
from src.utils import merge_json


logger = logging.getLogger("TwitchDrops")

# shared by the "animations" and "dark_mode" tri-state settings: "auto" follows the
# browser/OS preference (reduced-motion / prefers-color-scheme), "on"/"off" force it
# regardless of that preference (see web/static/app.js and styles.css)
AUTO_ON_OFF_MODES = ("auto", "on", "off")

# allowed values for the date/time appearance settings (see web/static/app.js
# DATE_FORMATS / TIME_FORMATS - "auto" defers to the browser/OS locale)
DATE_FORMATS = ("auto", "iso", "dmy_dot", "dmy_slash", "mdy_slash", "ymd_slash")
TIME_FORMATS = ("auto", "24h", "12h")


if TYPE_CHECKING:
    from src.config.settings import Settings
    from src.web.managers.broadcaster import WebSocketBroadcaster
    from src.web.managers.console import ConsoleOutputManager


class SettingsManager:
    """Manages application settings in the web interface.

    Provides access to and modification of user preferences including
    game priorities, proxy configuration, and UI preferences.
    """

    def __init__(
        self,
        broadcaster: WebSocketBroadcaster,
        settings: Settings,
        console: ConsoleOutputManager,
        on_change: Callable[[], None] | None = None,
    ):
        self._broadcaster = broadcaster
        self._settings = settings
        self._console = console
        self._on_change = on_change
        self._available_games: list[str] = []

    def get_settings(self) -> dict[str, Any]:
        """Get current settings for display.

        Returns:
            Dictionary containing all user-configurable settings
        """
        settings = vars(self._settings).copy()
        # included so late-connecting/refreshed clients can populate the manual
        # tracklist's available games list without waiting for the next
        # "games_available" broadcast (e.g. from a manual "Reload Campaigns")
        settings["games_available"] = self._available_games
        return settings

    def get_languages(self) -> dict[str, Any]:
        """Get available languages and current selection.

        Returns:
            Dictionary with available languages and current language
        """
        return {
            "available": _.get_languages(),
            "current": _.current_language,
        }

    def _log_change(self, message: str):
        """Log setting change to both console and system logger."""
        self._console.print(message)

    def update_settings(self, settings_data: dict[str, Any]):
        """Update settings from user input.

        Args:
            settings_data: Dictionary of settings to update
        """
        should_trigger_update = False
        should_trigger_update |= self.check_and_update_setting(
            "games_to_watch", settings_data.get("games_to_watch"), True
        )
        dark_mode = settings_data.get("dark_mode")
        if dark_mode is not None and dark_mode not in AUTO_ON_OFF_MODES:
            self._log_change(f"Ignoring unknown dark_mode mode: {dark_mode!r}")
            dark_mode = None
        should_trigger_update |= self.check_and_update_setting("dark_mode", dark_mode)
        animations = settings_data.get("animations")
        if animations is not None and animations not in AUTO_ON_OFF_MODES:
            self._log_change(f"Ignoring unknown animations mode: {animations!r}")
            animations = None
        should_trigger_update |= self.check_and_update_setting("animations", animations)
        date_format = settings_data.get("date_format")
        if date_format is not None and date_format not in DATE_FORMATS:
            self._log_change(f"Ignoring unknown date_format: {date_format!r}")
            date_format = None
        should_trigger_update |= self.check_and_update_setting("date_format", date_format)
        time_format = settings_data.get("time_format")
        if time_format is not None and time_format not in TIME_FORMATS:
            self._log_change(f"Ignoring unknown time_format: {time_format!r}")
            time_format = None
        should_trigger_update |= self.check_and_update_setting("time_format", time_format)
        if settings_data.get("idle_behavior") is not None:
            should_trigger_update |= self.check_and_update_setting(
                "idle_behavior",
                self._sanitize_idle_behavior(settings_data["idle_behavior"]),
                True,
            )
        # guard against empty/unknown values (e.g. saves sent before the
        # frontend language dropdown is populated) - never abort the update
        language = settings_data.get("language") or None
        if language is not None and language not in _.get_languages():
            self._log_change(f"Ignoring unknown language: {language!r}")
            language = None
        should_trigger_update |= self.check_and_update_setting(
            "language", language, False, self._set_language
        )
        should_trigger_update |= self.check_and_update_setting(
            "connection_quality", settings_data.get("connection_quality")
        )
        if "proxy" in settings_data:
            proxy_value = settings_data["proxy"]
            should_trigger_update |= self.check_and_update_setting(
                "proxy",
                str(proxy_value).strip() if proxy_value else "",
                True,
                lambda proxy: self._log_change("Proxy cleared") if proxy == "" else None,
            )
        should_trigger_update |= self.check_and_update_setting(
            "minimum_refresh_interval_minutes",
            settings_data.get("minimum_refresh_interval_minutes"),
        )
        should_trigger_update |= self.check_and_update_setting(
            "inventory_filters", settings_data.get("inventory_filters")
        )
        should_trigger_update |= self.check_and_update_setting(
            "mining_benefits", settings_data.get("mining_benefits"), True
        )
        if settings_data.get("library_sync") is not None:
            sanitized_library_sync = self._sanitize_library_sync(settings_data["library_sync"])
            current_library_sync: dict[str, Any] = getattr(self._settings, "library_sync", {})
            # credential-only edits (API keys, account credentials) are persisted but don't
            # need to restart the mining loop - automation changes do
            requires_update = self._strip_library_credentials(
                current_library_sync
            ) != self._strip_library_credentials(sanitized_library_sync)
            should_trigger_update |= self.check_and_update_setting(
                "library_sync",
                sanitized_library_sync,
                requires_update,
                # never log provider credentials (API keys, account tokens)
                log_value=self._strip_library_credentials(sanitized_library_sync),
            )
        if settings_data.get("notifications") is not None:
            sanitized_notifications = self._sanitize_notifications(settings_data["notifications"])
            current_notifications: dict[str, Any] = getattr(self._settings, "notifications", {})
            previous_token = str(current_notifications.get("discord", {}).get("bot_token", ""))
            new_token = str(sanitized_notifications.get("discord", {}).get("bot_token", ""))
            if previous_token and new_token != previous_token:
                # a changed bot token invalidates any previously verified guild/channel
                sanitized_notifications["discord"]["guild_id"] = ""
                sanitized_notifications["discord"]["channel_id"] = ""
            # notifications never affect the mining loop, so this never triggers a restart
            should_trigger_update |= self.check_and_update_setting(
                "notifications",
                sanitized_notifications,
                False,
                # never log the bot token
                log_value=self._strip_notification_credentials(sanitized_notifications),
            )

        self._settings.save()
        self._broadcaster.emit_soon("settings_updated", self.get_settings())

        if should_trigger_update and self._on_change:
            self._on_change()

    def check_and_update_setting(
        self,
        key: str,
        new_value: Any,
        should_trigger_update: bool = False,
        action: Callable[[Any], None] = lambda x: None,
        *,
        log_value: Any = None,
    ):
        """Apply a changed setting; log_value replaces new_value in the log line
        when the value contains data that must not end up in logs (credentials)."""
        if new_value is None or getattr(self._settings, key, None) == new_value:
            return False
        setattr(self._settings, key, new_value)
        self._log_change(f"Setting changed: {key} = {log_value if log_value is not None else new_value}")
        action(new_value)
        return should_trigger_update

    def _sanitize_idle_behavior(self, value: dict[str, Any]) -> dict[str, Any]:
        """Validate an incoming idle_behavior settings object against the current one."""
        sanitized: dict[str, Any] = dict(value)
        current: dict[str, Any] = dict(self._settings.idle_behavior)
        merge_json(sanitized, current)
        sanitized["mine_all_when_idle"] = bool(sanitized["mine_all_when_idle"])
        return sanitized

    def _sanitize_library_sync(self, value: dict[str, Any]) -> dict[str, Any]:
        """Validate an incoming library_sync settings object against the current one.

        Unknown keys are dropped, missing keys are filled in from the current
        settings, and invalid values are replaced with their current ones.
        """
        sanitized: dict[str, Any] = dict(value)
        current: dict[str, Any] = dict(self._settings.library_sync)
        merge_json(sanitized, current)
        if sanitized["list_mode"] not in LIST_MODES:
            sanitized["list_mode"] = current["list_mode"]
        for list_key in ("blacklist", "whitelist"):
            sanitized[list_key] = [
                str(name).strip() for name in sanitized[list_key] if str(name).strip()
            ]
        return sanitized

    # per-provider settings keys that hold credentials rather than automation config
    _LIBRARY_CREDENTIAL_KEYS: dict[str, tuple[str, ...]] = {
        "steam": ("api_key", "steam_id"),
        "ubisoft": ("remember_me_ticket",),
    }

    @classmethod
    def _strip_library_credentials(cls, value: dict[str, Any]) -> dict[str, Any]:
        """A copy of a library_sync settings object without provider credentials."""
        stripped = dict(value)
        for provider_key, credential_keys in cls._LIBRARY_CREDENTIAL_KEYS.items():
            provider = dict(stripped.get(provider_key, {}))
            for credential_key in credential_keys:
                provider.pop(credential_key, None)
            stripped[provider_key] = provider
        return stripped

    def _sanitize_notifications(self, value: dict[str, Any]) -> dict[str, Any]:
        """Validate an incoming notifications settings object against the current one.

        Unknown keys are dropped, missing keys are filled in from the current
        settings, and invalid values are replaced with their current ones.
        """
        sanitized: dict[str, Any] = dict(value)
        current: dict[str, Any] = dict(self._settings.notifications)
        merge_json(sanitized, current)
        try:
            sanitized["cooldown_minutes"] = max(0, int(sanitized["cooldown_minutes"]))
        except (TypeError, ValueError):
            sanitized["cooldown_minutes"] = current["cooldown_minutes"]
        return sanitized

    # per-provider settings keys that hold credentials rather than connection config
    _NOTIFICATION_CREDENTIAL_KEYS: dict[str, tuple[str, ...]] = {
        "discord": ("bot_token",),
    }

    @classmethod
    def _strip_notification_credentials(cls, value: dict[str, Any]) -> dict[str, Any]:
        """A copy of a notifications settings object without provider credentials."""
        stripped = dict(value)
        for provider_key, credential_keys in cls._NOTIFICATION_CREDENTIAL_KEYS.items():
            provider = dict(stripped.get(provider_key, {}))
            for credential_key in credential_keys:
                provider.pop(credential_key, None)
            stripped[provider_key] = provider
        return stripped

    def _set_language(self, language: str):
        _.set_language(language)
        # Notify clients that translations need to be reloaded
        self._broadcaster.emit_soon("language_changed", {"language": language})

    def set_favorite_drop(self, campaign_id: str, drop_id: str, favorite: bool) -> None:
        """Mark (or unmark) a single drop as favorite, prioritizing its game in the
        mining queue (see StreamSelector.SOURCE_FAVORITE) until that drop is claimed.

        Args:
            campaign_id: The drop's campaign id
            drop_id: The drop id, scoped to its campaign (not globally unique)
            favorite: Whether the drop should be marked favorite
        """
        key = f"{campaign_id}#{drop_id}"
        favorites = set(self._settings.favorite_drops)
        already_favorite = key in favorites
        if favorite == already_favorite:
            return
        if favorite:
            favorites.add(key)
        else:
            favorites.discard(key)
        self._settings.favorite_drops = sorted(favorites)
        self._log_change(f"Setting changed: favorite {'added' if favorite else 'removed'} for drop {key}")
        self._settings.save()
        self._broadcaster.emit_soon("settings_updated", self.get_settings())
        if self._on_change:
            self._on_change()

    def set_games(self, games: set[Game]):
        """Update the list of available games for settings panel.

        Args:
            games: Set of Game objects discovered from campaigns
        """
        # Store and broadcast available games for settings panel
        game_names = sorted([g.name for g in games])
        self._available_games = game_names
        self._broadcaster.emit_soon("games_available", {"games": game_names})
