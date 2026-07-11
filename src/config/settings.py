from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict

from yarl import URL

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
    show_finished: bool
    show_not_linked: bool
    show_upcoming: bool


default_settings = {
    "animations": "auto",  # "auto" | "on" | "off" - UI motion/animation preference
    "connection_quality": 1,
    "dark_mode": False,
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
        "show_finished": False,
        "show_not_linked": True,
        "show_upcoming": True,
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
    dark_mode: bool
    games_to_watch: list[str]
    idle_behavior: IdleBehaviorSettings
    language: str
    inventory_filters: InventoryFilters
    library_sync: LibrarySyncSettings
    minimum_refresh_interval_minutes: int
    mining_benefits: dict[str, bool]
    proxy: str

    def __init__(self):
        self.load()

    def load(self):
        # TODO: remvoe customized serde in the future
        settings = json_load(SETTINGS_PATH, default_settings, merge=True)
        for key, value in settings.items():
            if value is URL:
                setattr(self, key, str(value))
            else:
                setattr(self, key, value)

    def save(self) -> None:
        json_save(SETTINGS_PATH, vars(self), sort=True)
