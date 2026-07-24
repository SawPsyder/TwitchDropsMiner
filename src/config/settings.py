from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict

from src.config import DEFAULT_LANG, SETTINGS_PATH
from src.utils import json_load, json_save


class SteamLibrarySettings(TypedDict):
    enabled: bool
    api_key: str
    steam_id: str


class UbisoftLibrarySettings(TypedDict):
    enabled: bool
    # long-lived "remember me" ticket copied from the browser after logging in
    # at connect.ubisoft.com (password logins were disabled by Ubisoft ~04/2026)
    remember_me_ticket: str


class LibrarySyncSettings(TypedDict):
    enabled: bool
    list_mode: str  # "blacklist" | "whitelist"
    blacklist: list[str]
    whitelist: list[str]
    steam: SteamLibrarySettings
    ubisoft: UbisoftLibrarySettings


class NotificationEventSettings(TypedDict):
    drop_received: bool
    unlinked_tracked_game: bool
    auth_attention: bool
    mining_stalled: bool
    new_campaign: bool


class DiscordNotificationSettings(TypedDict):
    enabled: bool
    # bot token from a Discord Application the user owns (Developer Portal), used to
    # list guilds/channels and post messages - never a shared/TDM-owned bot identity
    bot_token: str
    guild_id: str
    channel_id: str
    events: NotificationEventSettings


class NotificationSettings(TypedDict):
    enabled: bool
    # minimum minutes between two notifications of the same (provider, event type),
    # to avoid spam from a flapping condition (e.g. repeatedly stalled mining)
    cooldown_minutes: int
    discord: DiscordNotificationSettings


class IdleBehaviorSettings(TypedDict):
    # when the manual and automated tracklists are both empty/exhausted, mine
    # drops for every actively-campaigned game instead of sitting idle
    mine_all_when_idle: bool


class InventoryFilters(TypedDict):
    game_name_search: list[str]
    show_active: bool
    show_benefit_badge: bool
    show_benefit_emote: bool
    show_benefit_item: bool
    show_benefit_other: bool
    show_expired: bool
    show_favorites: bool
    show_finished: bool
    show_not_linked: bool


default_settings = {
    "animations": "auto",  # "auto" | "on" | "off" - UI motion/animation preference
    "connection_quality": 1,
    "dark_mode": "auto",  # "auto" | "on" | "off" - UI light/dark theme preference
    # display appearance of dates/times in the web GUI (see DATE_FORMATS / TIME_FORMATS
    # in src/web/managers/settings.py); "auto" defers to the browser/OS locale
    "date_format": "auto",  # "auto" | "iso" | "dmy_dot" | "dmy_slash" | "mdy_slash" | "ymd_slash"
    "time_format": "auto",  # "auto" | "24h" | "12h"
    "favorite_drops": [],  # "{campaign_id}#{drop_id}" keys, see StreamSelector.SOURCE_FAVORITE
    "games_to_watch": [],
    "idle_behavior": {
        "mine_all_when_idle": True,
    },
    "language": DEFAULT_LANG,
    "inventory_filters": {
        "game_name_search": [],
        "show_active": False,
        "show_benefit_badge": True,
        "show_benefit_emote": True,
        "show_benefit_item": True,
        "show_benefit_other": True,
        "show_expired": False,
        "show_favorites": False,
        "show_finished": False,
        "show_not_linked": True,
    },
    "library_sync": {
        "enabled": False,
        "list_mode": "blacklist",
        "blacklist": [],
        "whitelist": [],
        "steam": {
            "enabled": False,
            "api_key": "",
            "steam_id": "",
        },
        "ubisoft": {
            "enabled": False,
            "remember_me_ticket": "",
        },
    },
    "minimum_refresh_interval_minutes": 30,
    "notifications": {
        "enabled": False,
        "cooldown_minutes": 15,
        "discord": {
            "enabled": False,
            "bot_token": "",
            "guild_id": "",
            "channel_id": "",
            "events": {
                "drop_received": True,
                "unlinked_tracked_game": True,
                "auth_attention": True,
                "mining_stalled": True,
                "new_campaign": True,
            },
        },
    },
    "mining_benefits": {
        "BADGE": True,
        "DIRECT_ENTITLEMENT": True,
        "EMOTE": True,
        "UNKNOWN": True,
    },
    "proxy": "",
}


@dataclass
class Settings:
    animations: str
    connection_quality: int
    dark_mode: str
    date_format: str
    time_format: str
    favorite_drops: list[str]
    games_to_watch: list[str]
    idle_behavior: IdleBehaviorSettings
    language: str
    inventory_filters: InventoryFilters
    library_sync: LibrarySyncSettings
    minimum_refresh_interval_minutes: int
    notifications: NotificationSettings
    mining_benefits: dict[str, bool]
    proxy: str

    def __init__(self):
        self.load()

    def load(self):
        settings = json_load(SETTINGS_PATH, default_settings, merge=True)
        for key, value in settings.items():
            setattr(self, key, value)

    def save(self) -> None:
        json_save(SETTINGS_PATH, vars(self), sort=True)
