import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from src.library_sync import (
    LibrarySyncError,
    LibrarySyncService,
    OwnedGame,
    SteamProvider,
    UbisoftProvider,
    normalize_game_name,
)


def make_library_settings(**overrides):
    settings = {
        "enabled": True,
        "list_mode": "blacklist",
        "blacklist": [],
        "whitelist": [],
        "steam": {
            "enabled": True,
            "api_key": "test-key",
            "steam_id": "76561198000000000",
        },
        "ubisoft": {
            "enabled": False,
            "remember_me_ticket": "",
        },
    }
    settings.update(overrides)
    return settings


class FakeSettings:
    """Minimal stand-in for src.config.settings.Settings."""

    def __init__(self, library_sync=None):
        self.library_sync = library_sync if library_sync is not None else make_library_settings()
        self.games_to_watch = []
        self.proxy = ""
        self.save_count = 0

    def save(self):
        self.save_count += 1


class TestNormalizeGameName(unittest.TestCase):
    def test_case_insensitive(self):
        self.assertEqual(normalize_game_name("Rust"), normalize_game_name("RUST"))

    def test_trademark_symbols_ignored(self):
        self.assertEqual(
            normalize_game_name("Tom Clancy's Rainbow Six® Siege"),
            normalize_game_name("Tom Clancy's Rainbow Six Siege"),
        )

    def test_punctuation_and_spacing_ignored(self):
        self.assertEqual(
            normalize_game_name("Counter-Strike:  Global Offensive"),
            normalize_game_name("counter strike global offensive"),
        )

    def test_different_games_stay_different(self):
        self.assertNotEqual(normalize_game_name("Dota 2"), normalize_game_name("Dota Underlords"))


class TestSteamProvider(unittest.TestCase):
    def test_parse_steam_id64(self):
        value, is_id64 = SteamProvider.parse_steam_id_input("76561198000000000")
        self.assertEqual(value, "76561198000000000")
        self.assertTrue(is_id64)

    def test_parse_vanity_name(self):
        value, is_id64 = SteamProvider.parse_steam_id_input("gaben")
        self.assertEqual(value, "gaben")
        self.assertFalse(is_id64)

    def test_parse_profile_url_with_id64(self):
        value, is_id64 = SteamProvider.parse_steam_id_input(
            "https://steamcommunity.com/profiles/76561198000000000/"
        )
        self.assertEqual(value, "76561198000000000")
        self.assertTrue(is_id64)

    def test_parse_profile_url_with_vanity(self):
        value, is_id64 = SteamProvider.parse_steam_id_input(
            "https://steamcommunity.com/id/gaben/"
        )
        self.assertEqual(value, "gaben")
        self.assertFalse(is_id64)

    def test_parse_owned_games(self):
        data = {
            "response": {
                "game_count": 2,
                "games": [
                    {"appid": 252490, "name": "Rust", "rtime_last_played": 1751000000},
                    {"appid": 730, "name": "Counter-Strike 2"},  # no last played data
                    {"appid": 999},  # no name: skipped
                ],
            }
        }
        games = SteamProvider.parse_owned_games(data)
        self.assertEqual(len(games), 2)
        self.assertEqual(
            games[0],
            OwnedGame(name="Rust", app_id="252490", provider="steam", last_played=1751000000),
        )
        self.assertEqual(games[1].last_played, 0)

    def test_parse_owned_games_private_profile(self):
        with self.assertRaises(LibrarySyncError):
            SteamProvider.parse_owned_games({"response": {}})

    def test_redacts_credentials_in_error_text(self):
        provider = SteamProvider(FakeSettings())
        masked = provider._redact(
            "GET https://api.steampowered.com/x?key=test-key&steamid=76561198000000000 failed"
        )
        self.assertNotIn("test-key", masked)
        self.assertNotIn("76561198000000000", masked)
        self.assertIn("***", masked)

    def test_is_configured(self):
        provider = SteamProvider(FakeSettings())
        self.assertTrue(provider.is_configured)
        self.assertTrue(provider.enabled)

        unconfigured = SteamProvider(
            FakeSettings(make_library_settings(steam={"enabled": True, "api_key": "", "steam_id": ""}))
        )
        self.assertFalse(unconfigured.is_configured)
        self.assertFalse(unconfigured.enabled)


class TestUbisoftProvider(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp_dir.cleanup)
        self.auth_path = Path(self._tmp_dir.name) / "ubisoft_auth.json"

    def make_provider(self, ticket="rm-ticket-from-browser", enabled=True):
        return UbisoftProvider(
            FakeSettings(
                make_library_settings(
                    ubisoft={"enabled": enabled, "remember_me_ticket": ticket}
                )
            ),
            auth_path=self.auth_path,
        )

    def test_is_configured(self):
        provider = self.make_provider()
        self.assertTrue(provider.is_configured)
        self.assertTrue(provider.enabled)

        unconfigured = self.make_provider(ticket="")
        self.assertFalse(unconfigured.is_configured)
        self.assertFalse(unconfigured.enabled)

    def test_redacts_tickets_in_error_text(self):
        provider = self.make_provider(ticket="rm-secret")
        provider._auth["ticket"] = "session-secret"
        masked = provider._redact("request with rm-secret and session-secret failed")
        self.assertNotIn("rm-secret", masked)
        self.assertNotIn("session-secret", masked)
        self.assertIn("***", masked)

    def test_clean_ticket_input(self):
        # localStorage values are often copied with surrounding JSON quotes
        self.assertEqual(UbisoftProvider.clean_ticket_input('  "abc-ticket"  '), "abc-ticket")
        # "null" is stored when "Remember me" was left unchecked at login
        self.assertEqual(UbisoftProvider.clean_ticket_input('"null"'), "")
        self.assertEqual(UbisoftProvider.clean_ticket_input("null"), "")

    def test_parse_login_response(self):
        ticket, session_id, rm_ticket = UbisoftProvider.parse_login_response(
            {"ticket": "abc", "sessionId": "session-1", "rememberMeTicket": "rm-2"}
        )
        self.assertEqual(ticket, "abc")
        self.assertEqual(session_id, "session-1")
        self.assertEqual(rm_ticket, "rm-2")

    def test_parse_login_response_without_rotation(self):
        ticket, session_id, rm_ticket = UbisoftProvider.parse_login_response(
            {"ticket": "abc", "sessionId": "session-1", "rememberMeTicket": None}
        )
        self.assertEqual(rm_ticket, "")

    def test_parse_login_response_unexpected_2fa(self):
        with self.assertRaisesRegex(LibrarySyncError, "2FA"):
            UbisoftProvider.parse_login_response(
                {"twoFactorAuthenticationTicket": "2fa-ticket"}
            )

    def test_parse_login_response_missing_ticket(self):
        with self.assertRaises(LibrarySyncError):
            UbisoftProvider.parse_login_response({"sessionId": "session-1"})

    def test_session_state_persists_across_instances(self):
        provider = self.make_provider()
        provider._auth.update(
            {
                "source_ticket": provider.remember_me_ticket,
                "remember_me_ticket": "rm-rotated",
                "ticket": "session-ticket",
                "session_id": "session-1",
                "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
            }
        )
        provider._save_auth()

        reloaded = self.make_provider()
        self.assertTrue(reloaded._session_valid())
        self.assertEqual(reloaded._auth["remember_me_ticket"], "rm-rotated")

    def test_session_invalid_when_user_pastes_new_token(self):
        provider = self.make_provider()
        provider._auth.update(
            {
                "source_ticket": "old-token",
                "remember_me_ticket": "rm-rotated",
                "ticket": "session-ticket",
                "session_id": "session-1",
                "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
            }
        )
        # the settings token differs from the chain's source - must re-login
        self.assertFalse(provider._session_valid())

    def test_session_invalid_when_expired(self):
        provider = self.make_provider()
        provider._auth.update(
            {
                "source_ticket": provider.remember_me_ticket,
                "ticket": "session-ticket",
                "expires_at": datetime.now(timezone.utc) - timedelta(minutes=1),
            }
        )
        self.assertFalse(provider._session_valid())

    def test_parse_owned_games(self):
        data = {
            "data": {
                "viewer": {
                    "id": "user-1",
                    "ownedGames": {
                        "totalCount": 3,
                        "nodes": [
                            {"id": "g1", "spaceId": "space-1", "name": "Anno 1800"},
                            {"id": "g2", "spaceId": None, "name": "Far Cry 6"},
                            {"id": "g3", "spaceId": "space-3"},  # no name: skipped
                        ],
                    },
                }
            }
        }
        games = UbisoftProvider.parse_owned_games(data)
        self.assertEqual(len(games), 2)
        self.assertEqual(
            games[0],
            OwnedGame(name="Anno 1800", app_id="space-1", provider="ubisoft", last_played=0),
        )
        # falls back to the game id when there's no space id
        self.assertEqual(games[1].app_id, "g2")

    def test_parse_owned_games_graphql_error(self):
        data = {"errors": [{"message": "Ticket is expired"}]}
        with self.assertRaisesRegex(LibrarySyncError, "Ticket is expired"):
            UbisoftProvider.parse_owned_games(data)

    def test_parse_owned_games_no_viewer(self):
        with self.assertRaises(LibrarySyncError):
            UbisoftProvider.parse_owned_games({"data": {"viewer": None}})

    def test_parse_owned_games_empty_library(self):
        data = {"data": {"viewer": {"id": "user-1", "ownedGames": {"nodes": []}}}}
        self.assertEqual(UbisoftProvider.parse_owned_games(data), [])


class TestLibrarySyncService(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp_dir.cleanup)
        self.cache_path = Path(self._tmp_dir.name) / "library_cache.json"

    def make_service(self, settings: FakeSettings) -> LibrarySyncService:
        service = LibrarySyncService(settings, cache_path=self.cache_path)
        return service

    def seed_owned_games(self, service: LibrarySyncService, names: list[str] | dict[str, int]):
        """Seed the cache with owned games; a dict maps name -> last_played timestamp."""
        entries = (
            list(names.items()) if isinstance(names, dict) else [(name, 0) for name in names]
        )
        service._provider_cache("steam")["games"] = [
            {"name": name, "app_id": str(i), "last_played": last_played}
            for i, (name, last_played) in enumerate(entries)
        ]

    @staticmethod
    def campaigns(
        names: list[str], ends_at: dict[str, datetime] | datetime | None = None
    ) -> list[tuple[str, datetime]]:
        """
        Pair game names with a campaign end date for get_auto_watch_games.

        Defaults to a far-future end date (irrelevant to the comparison)
        unless a single shared datetime or a per-name mapping is given.
        """
        default_end = datetime(2100, 1, 1, tzinfo=timezone.utc)
        if ends_at is None:
            return [(name, default_end) for name in names]
        if isinstance(ends_at, dict):
            return [(name, ends_at.get(name, default_end)) for name in names]
        return [(name, ends_at) for name in names]

    def test_disabled_returns_nothing(self):
        settings = FakeSettings(make_library_settings(enabled=False))
        service = self.make_service(settings)
        self.seed_owned_games(service, ["Rust"])
        self.assertEqual(service.get_auto_watch_games(self.campaigns(["Rust"])), [])

    def test_blacklist_mode(self):
        settings = FakeSettings(make_library_settings(blacklist=["Rust"]))
        service = self.make_service(settings)
        self.seed_owned_games(service, ["Rust", "Dota 2"])
        # owns both, Rust blacklisted, "Apex Legends" not owned
        result = service.get_auto_watch_games(
            self.campaigns(["Rust", "Dota 2", "Apex Legends"])
        )
        self.assertEqual(result, ["Dota 2"])

    def test_whitelist_mode(self):
        settings = FakeSettings(
            make_library_settings(list_mode="whitelist", whitelist=["Dota 2"])
        )
        service = self.make_service(settings)
        self.seed_owned_games(service, ["Rust", "Dota 2"])
        result = service.get_auto_watch_games(self.campaigns(["Rust", "Dota 2"]))
        self.assertEqual(result, ["Dota 2"])

    def test_name_matching_is_normalized(self):
        settings = FakeSettings()
        service = self.make_service(settings)
        self.seed_owned_games(service, ["Tom Clancy's Rainbow Six® Siege"])
        result = service.get_auto_watch_games(
            self.campaigns(["Tom Clancy's Rainbow Six Siege"])
        )
        self.assertEqual(result, ["Tom Clancy's Rainbow Six Siege"])

    def test_auto_watch_sorted_by_last_played_within_six_months(self):
        settings = FakeSettings()
        service = self.make_service(settings)
        now = datetime.now(timezone.utc)
        self.seed_owned_games(
            service,
            {
                "Old Favorite": int((now - timedelta(days=150)).timestamp()),
                "Fresh Hit": int((now - timedelta(days=10)).timestamp()),
                "Middle Game": int((now - timedelta(days=60)).timestamp()),
            },
        )

        result = service.get_auto_watch_games(
            self.campaigns(["Middle Game", "Old Favorite", "Fresh Hit"])
        )

        # all played within the last 6 months: most recently played first
        self.assertEqual(result, ["Fresh Hit", "Middle Game", "Old Favorite"])

    def test_auto_watch_stale_games_ranked_below_recently_played(self):
        settings = FakeSettings()
        service = self.make_service(settings)
        now = datetime.now(timezone.utc)
        self.seed_owned_games(
            service,
            {
                # played 200 days ago: outside the 6 month window
                "Stale Game": int((now - timedelta(days=200)).timestamp()),
                "Recent Game": int((now - timedelta(days=5)).timestamp()),
                "Never Played": 0,
            },
        )

        result = service.get_auto_watch_games(
            self.campaigns(["Stale Game", "Recent Game", "Never Played"])
        )

        # recently-played tier always ranks above the stale/never-played tier,
        # even though "Stale Game" has a more recent last-played time than
        # "Never Played"
        self.assertEqual(result[0], "Recent Game")
        self.assertIn(result[1:], (["Never Played", "Stale Game"], ["Stale Game", "Never Played"]))

    def test_auto_watch_stale_games_sorted_by_campaign_end_date(self):
        settings = FakeSettings()
        service = self.make_service(settings)
        # none of these were played recently (or ever), so ranking falls back
        # to campaign urgency: soonest-ending campaign first, to minimize the
        # chance of missing drops
        self.seed_owned_games(service, {"Ending Later": 0, "Ending Soon": 0, "Ending Middle": 0})
        now = datetime.now(timezone.utc)

        result = service.get_auto_watch_games(
            self.campaigns(
                ["Ending Later", "Ending Soon", "Ending Middle"],
                ends_at={
                    "Ending Later": now + timedelta(days=30),
                    "Ending Soon": now + timedelta(days=1),
                    "Ending Middle": now + timedelta(days=10),
                },
            )
        )

        self.assertEqual(result, ["Ending Soon", "Ending Middle", "Ending Later"])

    def test_auto_watch_deduplicates_campaign_games(self):
        settings = FakeSettings()
        service = self.make_service(settings)
        self.seed_owned_games(service, {"Rust": 100})
        # multiple campaigns for the same game produce one entry
        self.assertEqual(
            service.get_auto_watch_games(self.campaigns(["Rust", "Rust", "RUST"])), ["Rust"]
        )

    def test_auto_watch_deduplicate_keeps_earliest_campaign_end_date(self):
        settings = FakeSettings()
        service = self.make_service(settings)
        self.seed_owned_games(service, {"Rust": 0, "Dota 2": 0})
        now = datetime.now(timezone.utc)

        # "Rust" has two active campaigns; the sooner-ending one should be
        # used for ranking against "Dota 2"
        result = service.get_auto_watch_games(
            [
                ("Rust", now + timedelta(days=20)),
                ("Dota 2", now + timedelta(days=10)),
                ("Rust", now + timedelta(days=2)),
            ]
        )

        self.assertEqual(result, ["Rust", "Dota 2"])

    def test_combine_watch_lists_user_tier_first(self):
        combined = LibrarySyncService.combine_watch_lists(
            ["User Pick 2", "User Pick 1", "Shared Game"],
            ["Recently Played", "shared game", "Older Game"],
        )
        # user order untouched, auto games appended, case-insensitive dedup
        self.assertEqual(
            combined,
            ["User Pick 2", "User Pick 1", "Shared Game", "Recently Played", "Older Game"],
        )

    def test_auto_watch_never_mutates_settings(self):
        settings = FakeSettings()
        settings.games_to_watch = ["Existing Game"]
        service = self.make_service(settings)
        self.seed_owned_games(service, {"Rust": 100})

        service.get_auto_watch_games(self.campaigns(["Rust"]))

        self.assertEqual(settings.games_to_watch, ["Existing Game"])
        self.assertEqual(settings.save_count, 0)

    def test_owned_games_summary_sorted_and_deduplicated(self):
        settings = FakeSettings()
        service = self.make_service(settings)
        self.seed_owned_games(service, {"zebra Game": 500, "Alpha Game": 100})
        # a second provider entry for the same game (different case) merges in
        service._provider_cache("steam")["games"].append(
            {"name": "ALPHA GAME", "app_id": "99", "last_played": 900}
        )

        summary = service.get_owned_games_summary()

        self.assertEqual([entry["name"] for entry in summary], ["Alpha Game", "zebra Game"])
        # merged entry keeps the most recent last-played time
        self.assertEqual(summary[0]["last_played"], 900)
        self.assertEqual(summary[0]["providers"], ["steam"])

    def test_invalid_list_mode_falls_back_to_blacklist(self):
        settings = FakeSettings(make_library_settings(list_mode="bogus"))
        service = self.make_service(settings)
        self.assertEqual(service.list_mode, "blacklist")


class TestLibrarySyncServiceSync(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp_dir.cleanup)
        self.cache_path = Path(self._tmp_dir.name) / "library_cache.json"

    def make_service_with_mock_provider(self, settings: FakeSettings):
        service = LibrarySyncService(settings, cache_path=self.cache_path)
        provider = MagicMock()
        provider.name = "steam"
        provider.enabled = True
        provider.fetch_owned_games = AsyncMock(
            return_value=[
                OwnedGame(name="Rust", app_id="252490", provider="steam", last_played=1751000000)
            ]
        )
        service._providers = [provider]
        return service, provider

    async def test_sync_updates_cache_and_persists(self):
        settings = FakeSettings()
        service, provider = self.make_service_with_mock_provider(settings)

        results = await service.sync(force=True)

        self.assertTrue(results["steam"]["synced"])
        self.assertEqual(results["steam"]["game_count"], 1)
        self.assertEqual(len(service.owned_games), 1)
        self.assertTrue(self.cache_path.exists())

        # a fresh service reads back the persisted cache, including last played data
        reloaded = LibrarySyncService(settings, cache_path=self.cache_path)
        self.assertEqual(len(reloaded.owned_games), 1)
        self.assertEqual(reloaded.owned_games[0].name, "Rust")
        self.assertEqual(reloaded.owned_games[0].last_played, 1751000000)

    async def test_sync_skips_fresh_cache_unless_forced(self):
        settings = FakeSettings()
        service, provider = self.make_service_with_mock_provider(settings)
        service._provider_cache("steam")["last_sync"] = datetime.now(timezone.utc)
        service._provider_cache("steam")["games"] = []

        results = await service.sync()
        self.assertFalse(results["steam"]["synced"])
        provider.fetch_owned_games.assert_not_called()

        results = await service.sync(force=True)
        self.assertTrue(results["steam"]["synced"])
        provider.fetch_owned_games.assert_called_once()

    async def test_sync_refreshes_stale_cache(self):
        settings = FakeSettings()
        service, provider = self.make_service_with_mock_provider(settings)
        service._provider_cache("steam")["last_sync"] = (
            datetime.now(timezone.utc) - LibrarySyncService.SYNC_INTERVAL - timedelta(minutes=1)
        )

        results = await service.sync()
        self.assertTrue(results["steam"]["synced"])

    async def test_sync_provider_error_is_contained(self):
        settings = FakeSettings()
        service, provider = self.make_service_with_mock_provider(settings)
        provider.fetch_owned_games = AsyncMock(side_effect=LibrarySyncError("Steam: boom"))

        results = await service.sync(force=True)

        self.assertFalse(results["steam"]["synced"])
        self.assertEqual(results["steam"]["error"], "Steam: boom")
        provider.provider_settings = {"enabled": True}
        provider.is_configured = True
        status = service.get_status()
        self.assertEqual(status["providers"]["steam"]["last_error"], "Steam: boom")

    async def test_cache_survives_empty_providers_pruning(self):
        # a cache saved with no synced providers ({"providers": {}}) gets the
        # empty dict pruned by json_load on reload - must not crash (KeyError)
        settings = FakeSettings()
        service = LibrarySyncService(settings, cache_path=self.cache_path)
        service._save_cache()

        reloaded = LibrarySyncService(settings, cache_path=self.cache_path)
        self.assertNotIn("providers", reloaded._cache)  # pruned by json_load
        self.assertEqual(reloaded.owned_games, [])
        self.assertIn("steam", reloaded.get_status()["providers"])

        provider = MagicMock()
        provider.name = "steam"
        provider.enabled = True
        provider.fetch_owned_games = AsyncMock(return_value=[])
        reloaded._providers = [provider]
        results = await reloaded.sync(force=True)  # would raise KeyError before the fix
        self.assertIn("steam", results)

    async def test_sync_disabled_service(self):
        settings = FakeSettings(make_library_settings(enabled=False))
        service, provider = self.make_service_with_mock_provider(settings)
        self.assertEqual(await service.sync(force=True), {})
        provider.fetch_owned_games.assert_not_called()


class TestSettingsManagerLibrarySync(unittest.IsolatedAsyncioTestCase):
    def make_manager(self, library_sync=None):
        from src.config.settings import Settings
        from src.web.managers.settings import SettingsManager

        mock_broadcaster = AsyncMock()
        mock_settings = MagicMock(spec=Settings)
        mock_settings.library_sync = (
            library_sync if library_sync is not None else make_library_settings(enabled=False)
        )
        mock_settings.language = "English"
        mock_console = MagicMock()
        mock_callback = MagicMock()

        manager = SettingsManager(
            mock_broadcaster, mock_settings, mock_console, on_change=mock_callback
        )
        return manager, mock_settings, mock_callback

    async def test_update_settings_sanitizes_library_sync(self):
        manager, mock_settings, mock_callback = self.make_manager()

        manager.update_settings(
            {
                "library_sync": {
                    "enabled": True,
                    "list_mode": "bogus-mode",  # invalid: replaced with current value
                    "blacklist": ["  Rust  ", ""],  # stripped, empty entries dropped
                    "unknown_key": "dropped",
                    "steam": {"enabled": True, "api_key": "key", "steam_id": "gaben"},
                }
            }
        )

        mock_callback.assert_called_once()
        updated = mock_settings.library_sync
        self.assertTrue(updated["enabled"])
        self.assertEqual(updated["list_mode"], "blacklist")
        self.assertEqual(updated["blacklist"], ["Rust"])
        self.assertNotIn("unknown_key", updated)
        # missing keys are filled in from the current settings
        self.assertEqual(updated["whitelist"], [])
        self.assertEqual(updated["steam"]["api_key"], "key")

    async def test_credential_only_change_saves_without_trigger(self):
        manager, mock_settings, mock_callback = self.make_manager(make_library_settings())

        new_value = make_library_settings(
            steam={"enabled": True, "api_key": "new-key", "steam_id": "new-id"}
        )
        manager.update_settings({"library_sync": new_value})

        # persisted, but no mining loop restart for a credential edit
        mock_callback.assert_not_called()
        self.assertEqual(mock_settings.library_sync["steam"]["api_key"], "new-key")

        # an automation change (mode) does trigger the update
        manager.update_settings({"library_sync": make_library_settings(list_mode="whitelist")})
        mock_callback.assert_called_once()

    async def test_ubisoft_credential_only_change_saves_without_trigger(self):
        manager, mock_settings, mock_callback = self.make_manager(make_library_settings())

        new_value = make_library_settings(
            ubisoft={"enabled": False, "remember_me_ticket": "rm-ticket"}
        )
        manager.update_settings({"library_sync": new_value})

        # persisted, but no mining loop restart for a credential edit
        mock_callback.assert_not_called()
        self.assertEqual(mock_settings.library_sync["ubisoft"]["remember_me_ticket"], "rm-ticket")

        # toggling the provider on is an automation change and does trigger
        manager.update_settings(
            {
                "library_sync": make_library_settings(
                    ubisoft={"enabled": True, "remember_me_ticket": "rm-ticket"}
                )
            }
        )
        mock_callback.assert_called_once()

    async def test_setting_change_log_excludes_credentials(self):
        manager, mock_settings, mock_callback = self.make_manager()

        manager.update_settings(
            {
                "library_sync": make_library_settings(
                    steam={"enabled": True, "api_key": "secret-key", "steam_id": "secret-id"},
                    ubisoft={"enabled": True, "remember_me_ticket": "secret-ticket"},
                )
            }
        )

        logged = " ".join(str(call) for call in manager._console.print.call_args_list)
        self.assertIn("library_sync", logged)  # the change itself is still logged
        for secret in ("secret-key", "secret-id", "secret-ticket"):
            self.assertNotIn(secret, logged)

    async def test_empty_or_unknown_language_is_ignored(self):
        manager, mock_settings, mock_callback = self.make_manager()

        # neither raises nor changes the language setting
        manager.update_settings({"language": ""})
        self.assertEqual(mock_settings.language, "English")

        manager.update_settings({"language": "Klingon"})
        self.assertEqual(mock_settings.language, "English")


if __name__ == "__main__":
    unittest.main()
