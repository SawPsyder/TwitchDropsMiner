import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from src.library_sync import (
    DEFAULT_MARKET,
    XBOX_MARKET_CODES,
    XBOX_MARKETS,
    LibrarySyncError,
    OwnedGame,
    XboxProvider,
)
from src.library_sync.xbox import (
    CATALOG_CONSOLE_GAME_PASS,
    CATALOG_EA_PLAY,
    CATALOG_PC_GAME_PASS,
)
from src.library_sync.xbox_auth import XboxAuth, XboxLoginPending

from tests.test_library_sync import FakeSettings, make_library_settings


def make_xbox_settings(**xbox_overrides):
    """Library settings with the Xbox provider enabled and everything else off."""
    xbox = {
        "enabled": True,
        "include_gamepass_pc": False,
        "include_gamepass_console": False,
        "include_ea_play": False,
        "market": "US",
    }
    xbox.update(xbox_overrides)
    return make_library_settings(
        steam={"enabled": False, "api_key": "", "steam_id": ""},
        xbox=xbox,
    )


def make_provider(tmp_dir, **xbox_overrides):
    settings = FakeSettings(make_xbox_settings(**xbox_overrides))
    return XboxProvider(settings, Path(tmp_dir) / "xbox_auth.json")


class TestCleanProductTitle(unittest.TestCase):
    def test_strips_platform_and_edition_qualifiers(self):
        cases = {
            "EA SPORTS FC 26 - PC": "EA SPORTS FC 26",
            "A Plague Tale: Requiem - Windows": "A Plague Tale: Requiem",
            "9 Kings (Game Preview)": "9 Kings",
            "Minecraft: Windows 10 Edition": "Minecraft",
            "Forza Horizon 5 for Windows 10": "Forza Horizon 5",
            "Gears 5 - Xbox Series X|S": "Gears 5",
            # qualifiers can stack
            "Sea of Thieves - Windows (Game Preview)": "Sea of Thieves",
        }
        for raw, expected in cases.items():
            self.assertEqual(XboxProvider.clean_product_title(raw), expected, raw)

    def test_leaves_real_titles_alone(self):
        # subtitles, editions and titles that merely contain a qualifier word
        for name in (
            "Halo: The Master Chief Collection",
            "Age of Empires II: Definitive Edition",
            "ARK: Survival Ascended",
            "PC Building Simulator",
            "Tom Clancy's Rainbow Six Siege",
        ):
            self.assertEqual(XboxProvider.clean_product_title(name), name)

    def test_never_empties_a_name(self):
        # a product literally called "PC" must survive rather than become ""
        self.assertEqual(XboxProvider.clean_product_title("PC"), "PC")
        self.assertEqual(XboxProvider.clean_product_title("Windows"), "Windows")


class TestMarketList(unittest.TestCase):
    def test_codes_are_unique_two_letter_uppercase(self):
        codes = [code for code, _ in XBOX_MARKETS]
        self.assertEqual(len(codes), len(set(codes)))
        for code in codes:
            self.assertRegex(code, r"^[A-Z]{2}$")

    def test_every_entry_has_a_display_name(self):
        for code, name in XBOX_MARKETS:
            self.assertTrue(name.strip(), code)

    def test_sorted_by_display_name_for_the_dropdown(self):
        names = [name for _, name in XBOX_MARKETS]
        self.assertEqual(names, sorted(names))

    def test_default_market_is_selectable(self):
        self.assertIn(DEFAULT_MARKET, XBOX_MARKET_CODES)

    def test_code_set_matches_the_list(self):
        self.assertEqual(XBOX_MARKET_CODES, {code for code, _ in XBOX_MARKETS})


class TestTitleHistoryParsing(unittest.TestCase):
    def test_parses_names_ids_and_last_played(self):
        played_at = datetime(2026, 5, 1, 12, 30, tzinfo=UTC)
        games = XboxProvider.parse_title_history(
            {
                "titles": [
                    {
                        "titleId": "1234",
                        "name": "Sea of Thieves - Windows",
                        "type": "Game",
                        "titleHistory": {"lastTimePlayed": "2026-05-01T12:30:00.000Z"},
                    }
                ]
            }
        )
        self.assertEqual(len(games), 1)
        self.assertEqual(games[0].name, "Sea of Thieves")
        self.assertEqual(games[0].app_id, "1234")
        self.assertEqual(games[0].provider, "xbox")
        self.assertEqual(games[0].last_played, int(played_at.timestamp()))

    def test_skips_non_games_and_nameless_entries(self):
        games = XboxProvider.parse_title_history(
            {
                "titles": [
                    {"titleId": "1", "name": "Netflix", "type": "App"},
                    {"titleId": "2", "name": "", "type": "Game"},
                    {"titleId": "3", "name": "Halo Infinite", "type": "Game"},
                ]
            }
        )
        self.assertEqual([game.name for game in games], ["Halo Infinite"])

    def test_missing_or_bad_timestamp_is_zero(self):
        games = XboxProvider.parse_title_history(
            {
                "titles": [
                    {"titleId": "1", "name": "A", "type": "Game", "titleHistory": {}},
                    {
                        "titleId": "2",
                        "name": "B",
                        "type": "Game",
                        "titleHistory": {"lastTimePlayed": "not-a-date"},
                    },
                ]
            }
        )
        self.assertEqual([game.last_played for game in games], [0, 0])

    def test_empty_response_yields_nothing(self):
        self.assertEqual(XboxProvider.parse_title_history({}), [])


class TestInventoryParsing(unittest.TestCase):
    def test_keeps_only_enabled_games(self):
        title_ids = XboxProvider.parse_inventory_title_ids(
            {
                "items": [
                    {"itemType": "Game", "titleId": "1", "state": "Enabled"},
                    {"itemType": "Game", "titleId": "2", "state": "Suspended"},
                    {"itemType": "GameConsumable", "titleId": "3", "state": "Enabled"},
                    {"itemType": "Game", "state": "Enabled"},  # no title id
                ]
            }
        )
        self.assertEqual(title_ids, ["1"])

    def test_missing_state_counts_as_enabled(self):
        title_ids = XboxProvider.parse_inventory_title_ids(
            {"items": [{"itemType": "Game", "titleId": "42"}]}
        )
        self.assertEqual(title_ids, ["42"])


class TestCatalogParsing(unittest.TestCase):
    def test_skips_the_collection_header_entry(self):
        product_ids = XboxProvider.parse_catalog_product_ids(
            [
                {"siglId": "abc", "title": "All PC Games"},  # header, carries no id
                {"id": "9NPDN9R45JX4"},
                {"id": "9P8LR42PTRGJ"},
            ]
        )
        self.assertEqual(product_ids, ["9NPDN9R45JX4", "9P8LR42PTRGJ"])

    def test_rejects_unexpected_payloads(self):
        with self.assertRaises(LibrarySyncError):
            XboxProvider.parse_catalog_product_ids({"unexpected": "shape"})

    def test_parses_and_cleans_product_titles(self):
        names = XboxProvider.parse_catalog_products(
            {
                "Products": [
                    {
                        "ProductType": "Game",
                        "LocalizedProperties": [{"ProductTitle": "Forza Horizon 5 - PC"}],
                    },
                    {
                        "ProductType": "Durable",  # DLC/add-on, not a game
                        "LocalizedProperties": [{"ProductTitle": "Car Pack"}],
                    },
                    {"ProductType": "Game", "LocalizedProperties": []},  # unusable
                ]
            }
        )
        self.assertEqual(names, ["Forza Horizon 5"])


class TestMergeGames(unittest.TestCase):
    def test_dedupes_by_normalized_name_keeping_best_last_played(self):
        merged = XboxProvider.merge_games(
            [
                [OwnedGame("Sea of Thieves", "1", "xbox", 100)],
                # same game from another source, played more recently
                [OwnedGame("SEA OF THIEVES™", "", "xbox", 500)],
            ]
        )
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].name, "Sea of Thieves")
        self.assertEqual(merged[0].app_id, "1")
        self.assertEqual(merged[0].last_played, 500)

    def test_backfills_app_id_from_a_later_source(self):
        merged = XboxProvider.merge_games(
            [
                [OwnedGame("Halo Infinite", "", "xbox", 0)],
                [OwnedGame("Halo Infinite", "999", "xbox", 0)],
            ]
        )
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].app_id, "999")

    def test_keeps_distinct_games(self):
        merged = XboxProvider.merge_games(
            [[OwnedGame("A", "1", "xbox", 0)], [OwnedGame("B", "2", "xbox", 0)]]
        )
        self.assertEqual(sorted(game.name for game in merged), ["A", "B"])


class TestXboxProviderConfiguration(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_not_configured_without_account_or_catalogs(self):
        provider = make_provider(self.tmp_dir)
        self.assertFalse(provider.is_configured)
        self.assertFalse(provider.enabled)

    def test_each_catalog_toggle_is_independent(self):
        expected = {
            "include_gamepass_pc": CATALOG_PC_GAME_PASS,
            "include_gamepass_console": CATALOG_CONSOLE_GAME_PASS,
            "include_ea_play": CATALOG_EA_PLAY,
        }
        for key, catalog_id in expected.items():
            provider = make_provider(self.tmp_dir, **{key: True})
            self.assertEqual(provider.enabled_catalogs, {key: catalog_id})
            # a catalog alone is enough to sync - no sign-in needed
            self.assertTrue(provider.is_configured)
            self.assertTrue(provider.enabled)

    def test_all_catalogs_together(self):
        provider = make_provider(
            self.tmp_dir,
            include_gamepass_pc=True,
            include_gamepass_console=True,
            include_ea_play=True,
        )
        self.assertEqual(len(provider.enabled_catalogs), 3)

    def test_signed_in_account_alone_is_configured(self):
        provider = make_provider(self.tmp_dir)
        provider.auth._auth["refresh_token"] = "stored-token"
        self.assertTrue(provider.is_configured)

    def test_market_defaults_and_normalizes(self):
        self.assertEqual(make_provider(self.tmp_dir).market, "US")
        self.assertEqual(make_provider(self.tmp_dir, market="de").market, "DE")
        self.assertEqual(make_provider(self.tmp_dir, market="").market, "US")

    def test_unsupported_market_falls_back_to_the_default(self):
        # the catalog answers an unsupported region with an empty list rather than
        # an error, so it must never reach the request
        for bogus in ("ZZ", "XX", "Germany", "de-DE", "CN"):
            self.assertEqual(make_provider(self.tmp_dir, market=bogus).market, DEFAULT_MARKET)

    def test_provider_toggle_off_disables_despite_catalogs(self):
        provider = make_provider(self.tmp_dir, enabled=False, include_ea_play=True)
        self.assertTrue(provider.is_configured)
        self.assertFalse(provider.enabled)

    def test_status_extra_reports_login_and_catalogs_without_secrets(self):
        provider = make_provider(self.tmp_dir, include_ea_play=True)
        provider.auth._auth["refresh_token"] = "super-secret"
        provider.auth._auth["gamertag"] = "Tester"
        extra = provider.status_extra()
        self.assertEqual(extra["catalogs"], ["include_ea_play"])
        self.assertTrue(extra["login"]["signed_in"])
        self.assertEqual(extra["login"]["gamertag"], "Tester")
        self.assertNotIn("super-secret", str(extra))


class TestFetchFingerprint(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_region_change_changes_the_fingerprint(self):
        us = make_provider(self.tmp_dir, include_ea_play=True, market="US")
        de = make_provider(self.tmp_dir, include_ea_play=True, market="DE")
        self.assertNotEqual(us.fetch_fingerprint(), de.fetch_fingerprint())

    def test_catalog_toggle_changes_the_fingerprint(self):
        one = make_provider(self.tmp_dir, include_ea_play=True)
        two = make_provider(self.tmp_dir, include_ea_play=True, include_gamepass_pc=True)
        self.assertNotEqual(one.fetch_fingerprint(), two.fetch_fingerprint())

    def test_signing_in_changes_the_fingerprint(self):
        provider = make_provider(self.tmp_dir, include_ea_play=True)
        before = provider.fetch_fingerprint()
        provider.auth._auth["refresh_token"] = "token"
        self.assertNotEqual(before, provider.fetch_fingerprint())

    def test_identical_settings_are_stable(self):
        a = make_provider(self.tmp_dir, include_ea_play=True, market="DE")
        b = make_provider(self.tmp_dir, include_ea_play=True, market="DE")
        self.assertEqual(a.fetch_fingerprint(), b.fetch_fingerprint())

    def test_fingerprint_carries_no_credentials(self):
        provider = make_provider(self.tmp_dir, include_ea_play=True)
        provider.auth._auth["refresh_token"] = "super-secret-token"
        self.assertNotIn("super-secret-token", provider.fetch_fingerprint())


class TestFingerprintInvalidatesCache(unittest.IsolatedAsyncioTestCase):
    async def test_changed_fingerprint_refetches_inside_the_interval(self):
        from datetime import datetime as dt

        from src.library_sync import LibrarySyncService

        with tempfile.TemporaryDirectory() as tmp:
            settings = FakeSettings(make_xbox_settings(include_ea_play=True))
            service = LibrarySyncService(settings, Path(tmp) / "cache.json")

            provider = MagicMock()
            provider.name = "fake"
            provider.enabled = True
            provider.provider_settings = {"enabled": True}
            provider.is_configured = True
            provider.status_extra.return_value = {}
            provider.fetch_fingerprint.return_value = "market=US"
            provider.fetch_owned_games = AsyncMock(
                return_value=[OwnedGame("Game A", "1", "fake", 0)]
            )
            service._providers = [provider]

            first = await service.sync()
            self.assertTrue(first["fake"]["synced"])
            self.assertEqual(provider.fetch_owned_games.await_count, 1)

            # same fingerprint, still fresh -> skipped
            second = await service.sync()
            self.assertFalse(second["fake"]["synced"])
            self.assertEqual(provider.fetch_owned_games.await_count, 1)

            # region switched -> refetched despite the fresh cache
            provider.fetch_fingerprint.return_value = "market=DE"
            third = await service.sync()
            self.assertTrue(third["fake"]["synced"])
            self.assertEqual(provider.fetch_owned_games.await_count, 2)
            self.assertIsInstance(service._last_sync("fake"), dt)


class TestXboxProviderFetch(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    async def test_unconfigured_fetch_raises(self):
        provider = make_provider(self.tmp_dir)
        with self.assertRaises(LibrarySyncError):
            await provider.fetch_owned_games(MagicMock())

    async def test_catalog_only_sync_needs_no_account(self):
        provider = make_provider(self.tmp_dir, include_ea_play=True)

        async def fake_catalog(session, proxy, catalog_id):
            self.assertEqual(catalog_id, CATALOG_EA_PLAY)
            return [OwnedGame("Battlefield 6", "", "xbox", 0)]

        provider._fetch_catalog = fake_catalog
        games = await provider.fetch_owned_games(MagicMock())
        self.assertEqual([game.name for game in games], ["Battlefield 6"])

    async def test_account_and_catalog_sources_are_merged(self):
        provider = make_provider(self.tmp_dir, include_gamepass_pc=True)
        provider.auth._auth["refresh_token"] = "token"

        async def fake_played(session, proxy):
            return [OwnedGame("Sea of Thieves", "1", "xbox", 500)]

        async def fake_entitlements(session, proxy):
            return [OwnedGame("Halo Infinite", "2", "xbox", 0)]

        async def fake_catalog(session, proxy, catalog_id):
            # also included in Game Pass - must not double up
            return [
                OwnedGame("Sea of Thieves", "", "xbox", 0),
                OwnedGame("Forza Horizon 5", "", "xbox", 0),
            ]

        provider._fetch_played_titles = fake_played
        provider._fetch_entitlements = fake_entitlements
        provider._fetch_catalog = fake_catalog

        games = await provider.fetch_owned_games(MagicMock())
        by_name = {game.name: game for game in games}
        self.assertEqual(
            sorted(by_name), ["Forza Horizon 5", "Halo Infinite", "Sea of Thieves"]
        )
        # the played entry's timestamp survives the merge with the catalog copy
        self.assertEqual(by_name["Sea of Thieves"].last_played, 500)

    async def test_one_failing_source_does_not_lose_the_others(self):
        provider = make_provider(self.tmp_dir, include_ea_play=True)
        provider.auth._auth["refresh_token"] = "token"

        async def failing(session, proxy):
            raise LibrarySyncError("Xbox: inventory service is gone")

        async def fake_played(session, proxy):
            return [OwnedGame("Halo Infinite", "2", "xbox", 10)]

        async def fake_catalog(session, proxy, catalog_id):
            return [OwnedGame("Battlefield 6", "", "xbox", 0)]

        provider._fetch_played_titles = fake_played
        provider._fetch_entitlements = failing
        provider._fetch_catalog = fake_catalog

        games = await provider.fetch_owned_games(MagicMock())
        self.assertEqual(
            sorted(game.name for game in games), ["Battlefield 6", "Halo Infinite"]
        )

    async def test_all_sources_failing_surfaces_the_error(self):
        provider = make_provider(self.tmp_dir, include_ea_play=True)

        async def failing_catalog(session, proxy, catalog_id):
            raise LibrarySyncError("Xbox: catalog request failed (503)")

        provider._fetch_catalog = failing_catalog
        with self.assertRaises(LibrarySyncError) as ctx:
            await provider.fetch_owned_games(MagicMock())
        self.assertIn("503", str(ctx.exception))


class FakeAuthTransport:
    """Records auth calls and replays queued (status, body) responses."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    async def __call__(self, session, url, payload, proxy):
        self.calls.append((url, payload))
        if not self._responses:
            raise AssertionError(f"unexpected request to {url}")
        return self._responses.pop(0)


class TestXboxAuth(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.auth_path = Path(self._tmp.name) / "xbox_auth.json"

    def tearDown(self):
        self._tmp.cleanup()

    def make_auth(self, responses):
        auth = XboxAuth(self.auth_path)
        transport = FakeAuthTransport(responses)
        auth._post_form = transport
        auth._post_json = transport
        return auth, transport

    async def test_device_code_start_and_approval(self):
        auth, transport = self.make_auth(
            [
                (
                    200,
                    {
                        "user_code": "ABCD1234",
                        "device_code": "device-code-value",
                        "verification_uri": "https://www.microsoft.com/link",
                        "expires_in": 900,
                        "interval": 5,
                    },
                ),
                (400, {"error": "authorization_pending"}),
                (200, {"refresh_token": "the-refresh-token", "access_token": "at"}),
            ]
        )

        prompt = await auth.start_device_code(MagicMock())
        self.assertEqual(prompt.user_code, "ABCD1234")
        self.assertIsNotNone(auth.pending_prompt)
        self.assertFalse(auth.signed_in)
        # the device code itself must not reach the web GUI payload
        self.assertNotIn("device-code-value", str(prompt.as_dict()))

        with self.assertRaises(XboxLoginPending):
            await auth.poll_device_code(MagicMock())

        self.assertTrue(await auth.poll_device_code(MagicMock()))
        self.assertTrue(auth.signed_in)
        self.assertIsNone(auth.pending_prompt)
        # persisted, so a restart stays signed in
        self.assertTrue(XboxAuth(self.auth_path).signed_in)

    async def test_declined_and_expired_sign_ins_clear_the_prompt(self):
        for error in ("authorization_declined", "expired_token"):
            auth, _ = self.make_auth(
                [
                    (
                        200,
                        {
                            "user_code": "CODE",
                            "device_code": "dc",
                            "verification_uri": "https://www.microsoft.com/link",
                            "expires_in": 900,
                            "interval": 5,
                        },
                    ),
                    (400, {"error": error}),
                ]
            )
            await auth.start_device_code(MagicMock())
            with self.assertRaises(LibrarySyncError):
                await auth.poll_device_code(MagicMock())
            self.assertIsNone(auth.pending_prompt)
            self.assertFalse(auth.signed_in)

    async def test_failed_device_code_start_raises(self):
        auth, _ = self.make_auth([(400, {"error": "invalid_client"})])
        with self.assertRaises(LibrarySyncError):
            await auth.start_device_code(MagicMock())

    async def test_polling_without_a_pending_prompt_raises(self):
        auth, _ = self.make_auth([])
        with self.assertRaises(LibrarySyncError):
            await auth.poll_device_code(MagicMock())

    async def test_expired_prompt_is_forgotten(self):
        auth, _ = self.make_auth(
            [
                (
                    200,
                    {
                        "user_code": "CODE",
                        "device_code": "dc",
                        "verification_uri": "https://www.microsoft.com/link",
                        # already expired by the time it's read back
                        "expires_in": -1,
                        "interval": 5,
                    },
                )
            ]
        )
        await auth.start_device_code(MagicMock())
        self.assertIsNone(auth.pending_prompt)

    async def test_full_token_chain_and_claims(self):
        auth, transport = self.make_auth(
            [
                # refresh_token grant
                (200, {"access_token": "msa-access", "refresh_token": "rotated-token"}),
                # user.auth.xboxlive.com
                (200, {"Token": "user-token"}),
                # xsts.auth.xboxlive.com
                (
                    200,
                    {
                        "Token": "xsts-token",
                        "NotAfter": "2099-01-01T00:00:00.0000000Z",
                        "DisplayClaims": {
                            "xui": [{"uhs": "user-hash", "xid": "2533274800000000", "gtg": "Tester"}]
                        },
                    },
                ),
            ]
        )
        auth._auth["refresh_token"] = "stored-token"

        token = await auth.authorization(MagicMock())
        self.assertEqual(token.authorization, "XBL3.0 x=user-hash;xsts-token")
        self.assertEqual(token.xuid, "2533274800000000")
        self.assertEqual(token.gamertag, "Tester")
        self.assertFalse(token.expired)
        # the rotated refresh token replaced the stored one
        self.assertEqual(auth.refresh_token, "rotated-token")
        self.assertEqual(auth.gamertag, "Tester")

        # a second call is served from the cache instead of re-running the chain
        call_count = len(transport.calls)
        again = await auth.authorization(MagicMock())
        self.assertEqual(again.authorization, token.authorization)
        self.assertEqual(len(transport.calls), call_count)

    async def test_rejected_refresh_token_signs_out(self):
        auth, _ = self.make_auth([(400, {"error": "invalid_grant"})])
        auth._auth["refresh_token"] = "stale-token"
        with self.assertRaises(LibrarySyncError):
            await auth.authorization(MagicMock())
        # unrecoverable, so the account is disconnected rather than retried forever
        self.assertFalse(auth.signed_in)

    async def test_authorization_without_sign_in_raises(self):
        auth, _ = self.make_auth([])
        with self.assertRaises(LibrarySyncError):
            await auth.authorization(MagicMock())

    async def test_xsts_error_codes_are_explained(self):
        auth, _ = self.make_auth(
            [
                (200, {"access_token": "msa-access"}),
                (200, {"Token": "user-token"}),
                (401, {"XErr": 2148916233}),
            ]
        )
        auth._auth["refresh_token"] = "stored-token"
        with self.assertRaises(LibrarySyncError) as ctx:
            await auth.authorization(MagicMock())
        self.assertIn("no Xbox profile", str(ctx.exception))

    def test_parse_xsts_response_requires_token_and_hash(self):
        with self.assertRaises(LibrarySyncError):
            XboxAuth.parse_xsts_response({"DisplayClaims": {"xui": [{"uhs": "h"}]}})
        with self.assertRaises(LibrarySyncError):
            XboxAuth.parse_xsts_response({"Token": "t", "DisplayClaims": {"xui": [{}]}})

    def test_parse_xsts_response_falls_back_to_a_default_expiry(self):
        token = XboxAuth.parse_xsts_response(
            {"Token": "t", "NotAfter": "nonsense", "DisplayClaims": {"xui": [{"uhs": "h"}]}}
        )
        self.assertGreater(token.expires_at, datetime.now(UTC) + timedelta(hours=15))

    def test_sign_out_clears_persisted_state(self):
        auth = XboxAuth(self.auth_path)
        auth._auth["refresh_token"] = "token"
        auth._auth["gamertag"] = "Tester"
        auth._save()
        self.assertTrue(XboxAuth(self.auth_path).signed_in)

        auth.sign_out()
        self.assertFalse(auth.signed_in)
        self.assertEqual(auth.gamertag, "")
        self.assertFalse(XboxAuth(self.auth_path).signed_in)

    def test_sensitive_values_cover_the_refresh_token(self):
        auth = XboxAuth(self.auth_path)
        auth._auth["refresh_token"] = "secret-refresh"
        self.assertIn("secret-refresh", auth.sensitive_values())


class TestXboxSettingsSanitization(unittest.IsolatedAsyncioTestCase):
    def make_manager(self, library_sync):
        from src.config.settings import Settings
        from src.web.managers.broadcaster import WebSocketBroadcaster
        from src.web.managers.settings import SettingsManager

        mock_broadcaster = MagicMock(spec=WebSocketBroadcaster)
        mock_settings = MagicMock(spec=Settings)
        mock_settings.library_sync = library_sync
        mock_settings.language = "English"
        return (
            SettingsManager(mock_broadcaster, mock_settings, MagicMock(), on_change=MagicMock()),
            mock_settings,
        )

    async def test_market_is_uppercased_and_validated(self):
        manager, mock_settings = self.make_manager(make_xbox_settings(market="US"))
        manager.update_settings({"library_sync": make_xbox_settings(market="de")})
        self.assertEqual(mock_settings.library_sync["xbox"]["market"], "DE")

        # not a supported region: the current value is kept
        for bogus in ("Germany", "ZZ", "CN", ""):
            manager.update_settings({"library_sync": make_xbox_settings(market=bogus)})
            self.assertEqual(mock_settings.library_sync["xbox"]["market"], "DE", bogus)

    async def test_unsupported_current_market_falls_back_to_default(self):
        # a hand-edited settings file could hold anything
        manager, mock_settings = self.make_manager(make_xbox_settings(market="ZZ"))
        manager.update_settings({"library_sync": make_xbox_settings(market="XX")})
        self.assertEqual(mock_settings.library_sync["xbox"]["market"], DEFAULT_MARKET)

    async def test_catalog_toggles_are_independent_booleans(self):
        manager, mock_settings = self.make_manager(make_xbox_settings())
        manager.update_settings(
            {"library_sync": make_xbox_settings(include_gamepass_pc=True, include_ea_play=True)}
        )
        xbox = mock_settings.library_sync["xbox"]
        self.assertIs(xbox["include_gamepass_pc"], True)
        self.assertIs(xbox["include_gamepass_console"], False)
        self.assertIs(xbox["include_ea_play"], True)

    async def test_wrongly_typed_toggle_keeps_the_current_value(self):
        # merge_json enforces the stored type across every setting, so a client
        # sending 1/"" instead of true/false gets the current value rather than a
        # surprise coercion
        manager, mock_settings = self.make_manager(
            make_xbox_settings(include_gamepass_pc=True, include_ea_play=False)
        )
        manager.update_settings(
            {"library_sync": make_xbox_settings(include_gamepass_pc=0, include_ea_play="yes")}
        )
        xbox = mock_settings.library_sync["xbox"]
        self.assertIs(xbox["include_gamepass_pc"], True)
        self.assertIs(xbox["include_ea_play"], False)

    async def test_missing_xbox_block_is_filled_in(self):
        # settings written before this provider existed carry no xbox block
        legacy = make_library_settings()
        legacy.pop("xbox")
        manager, mock_settings = self.make_manager(legacy)
        manager.update_settings({"library_sync": legacy})
        self.assertEqual(mock_settings.library_sync["xbox"]["market"], "US")
        self.assertFalse(mock_settings.library_sync["xbox"]["include_ea_play"])
