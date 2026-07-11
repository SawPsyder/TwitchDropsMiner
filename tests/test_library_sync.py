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

    def test_is_configured(self):
        provider = SteamProvider(FakeSettings())
        self.assertTrue(provider.is_configured)
        self.assertTrue(provider.enabled)

        unconfigured = SteamProvider(
            FakeSettings(make_library_settings(steam={"enabled": True, "api_key": "", "steam_id": ""}))
        )
        self.assertFalse(unconfigured.is_configured)
        self.assertFalse(unconfigured.enabled)


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

    def test_disabled_returns_nothing(self):
        settings = FakeSettings(make_library_settings(enabled=False))
        service = self.make_service(settings)
        self.seed_owned_games(service, ["Rust"])
        self.assertEqual(service.get_auto_watch_games(["Rust"]), [])

    def test_blacklist_mode(self):
        settings = FakeSettings(make_library_settings(blacklist=["Rust"]))
        service = self.make_service(settings)
        self.seed_owned_games(service, ["Rust", "Dota 2"])
        # owns both, Rust blacklisted, "Apex Legends" not owned
        result = service.get_auto_watch_games(["Rust", "Dota 2", "Apex Legends"])
        self.assertEqual(result, ["Dota 2"])

    def test_whitelist_mode(self):
        settings = FakeSettings(
            make_library_settings(list_mode="whitelist", whitelist=["Dota 2"])
        )
        service = self.make_service(settings)
        self.seed_owned_games(service, ["Rust", "Dota 2"])
        result = service.get_auto_watch_games(["Rust", "Dota 2"])
        self.assertEqual(result, ["Dota 2"])

    def test_name_matching_is_normalized(self):
        settings = FakeSettings()
        service = self.make_service(settings)
        self.seed_owned_games(service, ["Tom Clancy's Rainbow Six® Siege"])
        result = service.get_auto_watch_games(["Tom Clancy's Rainbow Six Siege"])
        self.assertEqual(result, ["Tom Clancy's Rainbow Six Siege"])

    def test_auto_watch_sorted_by_last_played(self):
        settings = FakeSettings()
        service = self.make_service(settings)
        self.seed_owned_games(
            service,
            {
                "Old Favorite": 1_600_000_000,
                "Never Played B": 0,
                "Fresh Hit": 1_750_000_000,
                "Never Played A": 0,
                "Middle Game": 1_700_000_000,
            },
        )

        result = service.get_auto_watch_games(
            ["Never Played B", "Middle Game", "Old Favorite", "Fresh Hit", "Never Played A"]
        )

        # recently played first, never-played last in alphabetical order
        self.assertEqual(
            result,
            ["Fresh Hit", "Middle Game", "Old Favorite", "Never Played A", "Never Played B"],
        )

    def test_auto_watch_deduplicates_campaign_games(self):
        settings = FakeSettings()
        service = self.make_service(settings)
        self.seed_owned_games(service, {"Rust": 100})
        # multiple campaigns for the same game produce one entry
        self.assertEqual(service.get_auto_watch_games(["Rust", "Rust", "RUST"]), ["Rust"])

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

        service.get_auto_watch_games(["Rust"])

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

    async def test_empty_or_unknown_language_is_ignored(self):
        manager, mock_settings, mock_callback = self.make_manager()

        # neither raises nor changes the language setting
        manager.update_settings({"language": ""})
        self.assertEqual(mock_settings.language, "English")

        manager.update_settings({"language": "Klingon"})
        self.assertEqual(mock_settings.language, "English")


if __name__ == "__main__":
    unittest.main()
