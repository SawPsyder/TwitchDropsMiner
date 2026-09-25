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


class XboxLibrarySettings(TypedDict):
    enabled: bool
    # the Microsoft account itself is connected via a device-code sign-in and lives
    # in DATA_DIR/xbox_auth.json - no Xbox credential is ever stored in the settings
    include_gamepass_pc: bool
    include_gamepass_console: bool
    include_ea_play: bool
    # store region for the subscription catalogs (they differ slightly per market)
    market: str


class LibrarySyncSettings(TypedDict):
    enabled: bool
    list_mode: str  # "blacklist" | "whitelist"
    blacklist: list[str]
    whitelist: list[str]
    steam: SteamLibrarySettings
    ubisoft: UbisoftLibrarySettings
    xbox: XboxLibrarySettings


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


class DigestSectionSettings(TypedDict):
    # the only digest sections without an event toggle behind them; drops, campaigns
    # and unlinked games follow the per-event toggles in both delivery modes
    progress: bool
    errors: bool


class NotificationSettings(TypedDict):
    enabled: bool
    # minimum minutes between two notifications of the same (provider, event type).
    # immediate mode skips repeats inside the window; digest mode still uses it to
    # throttle the urgent immediate alerts (auth attention and mining stalled)
    cooldown_minutes: int
    # "immediate" sends one message per event; "digest" collects them into a summary
    mode: str
    # how often a digest goes out, in minutes (60..10080). 1440 and 10080 are anchored
    # to digest_send_time (and digest_send_weekday for the weekly interval)
    digest_interval_minutes: int
    # "HH:MM" in the container's local time zone; used only for daily and weekly digests
    digest_send_time: str
    # 0 = Monday .. 6 = Sunday; used only when digest_interval_minutes is 10080
    digest_send_weekday: int
    # auth_attention and mining_stalled also go out immediately (and stay in the digest)
    digest_urgent_immediate: bool
    # when false, a window with no queued events is skipped instead of posting a digest
    digest_send_empty: bool
    digest_sections: DigestSectionSettings
    discord: DiscordNotificationSettings


# bounds enforced on load (a stored value is clamped) and on submit (rejected)
NOTIFICATION_MODES = ("immediate", "digest")
COOLDOWN_MINUTES_MAX = 1440
DIGEST_INTERVAL_MIN_MINUTES = 60
DIGEST_INTERVAL_MAX_MINUTES = 10080
# intervals that fire at digest_send_time instead of "last send + interval"
DIGEST_ANCHORED_INTERVALS = (1440, 10080)


class IdleBehaviorSettings(TypedDict):
    # when the manual and automated tracklists are both empty/exhausted, mine
    # drops for every actively-campaigned game instead of sitting idle
    mine_all_when_idle: bool


class InventoryFilters(TypedDict):
    # free-text inventory search (game / campaign / drop / benefit names); replaced the
    # former game multi-select, whose list value merge_json coerces back to the default
    search_text: str
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
        "search_text": "",
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
        "xbox": {
            "enabled": False,
            "include_gamepass_pc": False,
            "include_gamepass_console": False,
            "include_ea_play": False,
            "market": "US",
        },
    },
    "minimum_refresh_interval_minutes": 30,
    "notifications": {
        "enabled": False,
        "cooldown_minutes": 15,
        "mode": "immediate",
        "digest_interval_minutes": 1440,
        "digest_send_time": "09:00",
        "digest_send_weekday": 0,
        "digest_urgent_immediate": True,
        "digest_send_empty": False,
        "digest_sections": {
            "progress": True,
            "errors": True,
        },
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


def _clamp_stored_digest_interval(notifications: object) -> bool:
    """Pull a stored digest interval into 60..10080 minutes.

    Returns True when the value changed and should be written back. A value
    the user submits is still rejected; this only repairs a file that already
    holds an out-of-range interval, so a later save of the loaded object is valid.
    """
    if not isinstance(notifications, dict) or "digest_interval_minutes" not in notifications:
        return False
    raw = notifications["digest_interval_minutes"]
    if isinstance(raw, bool) or not isinstance(raw, int):
        return False
    clamped = min(DIGEST_INTERVAL_MAX_MINUTES, max(DIGEST_INTERVAL_MIN_MINUTES, raw))
    if clamped == raw:
        return False
    notifications["digest_interval_minutes"] = clamped
    return True


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
        if _clamp_stored_digest_interval(self.notifications):
            self.save()

    def save(self) -> None:
        json_save(SETTINGS_PATH, vars(self), sort=True)
