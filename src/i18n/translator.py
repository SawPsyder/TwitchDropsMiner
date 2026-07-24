from __future__ import annotations

import json
import logging
from typing import TypedDict, cast

from src.config import DEFAULT_LANG, LANG_PATH


class StatusMessages(TypedDict):
    terminated: str
    watching: str
    goes_online: str
    goes_offline: str
    claimed_drop: str
    no_channel: str
    no_campaign: str
    library_sync_added: str


class LoginStatus(TypedDict):
    logged_in: str
    logged_out: str
    logging_in: str
    required: str
    waiting_auth: str


class LoginMessages(TypedDict):
    error_code: str
    unexpected_content: str
    email_code_required: str
    twofa_code_required: str
    incorrect_login_pass: str
    incorrect_email_code: str
    incorrect_twofa_code: str
    status: LoginStatus


class ErrorMessages(TypedDict):
    captcha: str
    no_connection: str
    site_down: str


class GUIStatus(TypedDict):
    name: str
    idle: str
    ready: str
    exiting: str
    terminated: str
    cleanup: str
    gathering: str
    switching: str
    fetching_inventory: str
    fetching_campaigns: str
    adding_campaigns: str


class GUITabs(TypedDict):
    main: str
    inventory: str
    settings: str


class GUILoginForm(TypedDict):
    name: str
    labels: str
    request: str
    username: str
    password: str
    twofa_code: str
    button: str
    oauth_prompt: str
    oauth_activate: str
    oauth_confirm: str


class GUIWebsocket(TypedDict):
    name: str
    websocket: str
    initializing: str
    connected: str
    disconnected: str
    connecting: str
    disconnecting: str
    reconnecting: str


class GUIProgress(TypedDict):
    name: str
    drop: str
    game: str
    campaign: str
    remaining: str
    drop_progress: str
    campaign_progress: str
    no_drop: str
    return_to_auto: str
    manual_mode_info: str
    estimated_badge: str
    estimated_tooltip: str
    confirmation_pending: str


class GUIChannels(TypedDict):
    name: str
    online: str
    pending: str
    offline: str
    no_channels: str
    no_channels_for_games: str
    no_channels_for_games_sub: str
    channel_count: str
    channel_count_plural: str
    viewers: str


class GUIFooter(TypedDict):
    version: str
    loading: str
    update_available: str


class GUIBadgeItem(TypedDict):
    title: str


class GUIBadges(TypedDict):
    manual: GUIBadgeItem
    auto: GUIBadgeItem
    proxy: GUIBadgeItem


class GUIWantedSource(TypedDict):
    favorite: str
    manual: str
    auto: str
    idle: str


class GUIWantedUnlinkedAuto(TypedDict):
    name: str
    none: str
    link_button: str
    refresh_button: str


class GUIWanted(TypedDict):
    name: str
    none: str
    source: GUIWantedSource
    unlinked_auto: GUIWantedUnlinkedAuto


class GUIInvFilters(TypedDict):
    active: str
    not_linked: str
    upcoming: str
    expired: str
    favorite: str
    finished: str
    item: str
    badge: str
    emote: str
    other: str
    clear: str
    search_placeholder: str
    favorite_toggle: str


class GUIInvStatus(TypedDict):
    active: str
    expired: str
    upcoming: str
    finished: str
    claimed: str


class GUIInvViewMode(TypedDict):
    game: str
    category: str


class GUIInventory(TypedDict):
    no_campaigns: str
    no_matches: str
    status: GUIInvStatus
    starts: str
    ends: str
    claimed_drops: str
    campaign_count: str
    campaign_count_plural: str
    view_mode: GUIInvViewMode
    refresh_status: str
    filters: GUIInvFilters
    manual_progress: str


# shared shape for the "auto"/"on"/"off" tri-state toggles (dark mode, animations)
class GUISettingsAnimations(TypedDict):
    name: str
    auto: str
    on: str
    off: str


GUISettingsDarkMode = GUISettingsAnimations


class GUISettingsGeneral(TypedDict):
    name: str


class GUISettingsDateFormat(TypedDict):
    name: str
    auto: str


# functional syntax: "24h"/"12h" are not valid Python identifiers
GUISettingsTimeFormat = TypedDict(
    "GUISettingsTimeFormat",
    {"name": str, "auto": str, "24h": str, "12h": str},
)


class GUISettingsAppearance(TypedDict):
    name: str
    dark_mode: GUISettingsDarkMode
    animations: GUISettingsAnimations
    date_format: GUISettingsDateFormat
    time_format: GUISettingsTimeFormat


class GUISettingsProxy(TypedDict):
    name: str
    help: str
    set: str
    verify: str


class GUISettingsIdleBehavior(TypedDict):
    name: str
    help: str


class GUISettingsLibrary(TypedDict):
    name: str
    help: str
    mode: str
    mode_blacklist_name: str
    mode_whitelist_name: str
    mode_blacklist_desc: str
    mode_whitelist_desc: str
    search_library: str
    no_owned_games: str
    no_library_match: str
    more_games: str
    not_configured: str
    steam: str
    steam_api_key: str
    steam_api_key_hint: str
    steam_id: str
    ubisoft: str
    ubisoft_ticket: str
    ubisoft_hint: str
    sync_now: str
    syncing: str
    sync_disabled: str
    auto_list_label: str
    auto_list_empty: str
    last_sync: str
    never_synced: str
    owned_games: str
    added_games: str
    no_new_games: str


class GUISettingsNotifications(TypedDict):
    name: str
    help: str
    cooldown_label: str
    discord: str
    discord_bot_token: str
    not_configured: str
    connected: str
    verifying: str
    verify_button: str
    invite_label: str
    server_label: str
    refresh_servers_button: str
    channel_label: str
    events_label: str
    event_drop_received: str
    event_unlinked_tracked_game: str
    event_auth_attention: str
    event_mining_stalled: str
    event_new_campaign: str
    test_button: str
    sending_test: str


class GUISettings(TypedDict):
    general: GUISettingsGeneral
    appearance: GUISettingsAppearance
    mining_benefits: str
    mining_benefits_help: str
    reload: str
    reload_campaigns: str
    games_to_watch: str
    games_help: str
    search_games: str
    add_game: str
    add_game_hint: str
    select_all: str
    deselect_all: str
    selected_games: str
    available_games: str
    no_games_selected: str
    no_games_match: str
    all_games_selected: str
    actions: str
    connection_quality: str
    minimum_refresh: str
    proxy: GUISettingsProxy
    idle_behavior: GUISettingsIdleBehavior
    library: GUISettingsLibrary
    notifications: GUISettingsNotifications


class GUIHeader(TypedDict):
    title: str
    language: str
    initializing: str
    auto_mode: str
    manual_mode: str
    connected: str
    disconnected: str


class GUILoadingOverlay(TypedDict):
    reload_headline: str
    reload_message: str


class GUIToasts(TypedDict):
    close: str
    link_failed_headline: str
    link_failed_message: str
    link_success_headline: str
    link_success_message: str
    drop_collected_headline: str
    drop_collected_message: str


class GUIMessages(TypedDict):
    output: str
    status: GUIStatus
    tabs: GUITabs
    login: GUILoginForm
    websocket: GUIWebsocket
    progress: GUIProgress
    channels: GUIChannels
    inventory: GUIInventory
    settings: GUISettings
    header: GUIHeader
    footer: GUIFooter
    badges: GUIBadges
    wanted: GUIWanted
    loading: GUILoadingOverlay
    toasts: GUIToasts


class Translation(TypedDict):
    language_name: str
    english_name: str
    status: StatusMessages
    login: LoginMessages
    error: ErrorMessages
    gui: GUIMessages


class Translator:
    def __init__(self) -> None:
        self.logger: logging.Logger = logging.getLogger("TwitchDropsMiner.i18n.Translator")
        self._langs: dict[str, Translation] = {}
        self.current_language: str
        self.t: Translation
        # load available languages from JSON files by reading language_name field
        for filepath in LANG_PATH.glob("*.json"):
            with filepath.open("r", encoding="utf-8") as json_file:
                try:
                    loaded_translation: Translation = json.load(json_file)
                    self._langs[loaded_translation["language_name"]] = loaded_translation
                except Exception as e:
                    # if we can't read the file, skip it
                    self.logger.warning(f"Failed to load language file {filepath}: {e}")
                    continue
        self._langs = dict(sorted(self._langs.items()))
        self.set_language(DEFAULT_LANG)

    def get_languages(self) -> list[str]:
        return list(self._langs.keys())

    def set_language(self, language: str):
        if language not in self._langs:
            raise ValueError(f"Unrecognized language {language}")

        self.current_language = language
        self.t = cast(Translation, self._langs.get(language))


_ = Translator()
