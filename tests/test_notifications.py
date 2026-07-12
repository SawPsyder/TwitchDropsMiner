import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from src.notifications import DiscordProvider, NotificationError, NotificationService


def make_notification_settings(**overrides):
    settings = {
        "enabled": True,
        "cooldown_minutes": 15,
        "discord": {
            "enabled": True,
            "bot_token": "test-bot-token",
            "guild_id": "guild-1",
            "channel_id": "channel-1",
            "events": {
                "drop_received": True,
                "unlinked_tracked_game": True,
                "auth_attention": True,
                "mining_stalled": True,
                "new_campaign": True,
            },
        },
    }
    settings.update(overrides)
    return settings


class FakeSettings:
    """Minimal stand-in for src.config.settings.Settings."""

    def __init__(self, notifications=None):
        self.notifications = notifications if notifications is not None else make_notification_settings()


class FakeGame:
    def __init__(self, name):
        self.name = name


class FakeCampaign:
    def __init__(self, id, name, game_name):
        self.id = id
        self.name = name
        self.game = FakeGame(game_name)


class TestDiscordProvider(unittest.TestCase):
    def test_is_configured(self):
        provider = DiscordProvider(FakeSettings())
        self.assertTrue(provider.is_configured)
        self.assertTrue(provider.enabled)

        unconfigured = DiscordProvider(
            FakeSettings(make_notification_settings(discord={"enabled": True, "bot_token": "", "channel_id": ""}))
        )
        self.assertFalse(unconfigured.is_configured)
        self.assertFalse(unconfigured.enabled)

    def test_redacts_bot_token_in_error_text(self):
        provider = DiscordProvider(FakeSettings())
        masked = provider._redact("request with Authorization: Bot test-bot-token failed")
        self.assertNotIn("test-bot-token", masked)
        self.assertIn("***", masked)

    def test_event_enabled(self):
        provider = DiscordProvider(
            FakeSettings(
                make_notification_settings(
                    discord={
                        "enabled": True,
                        "bot_token": "tok",
                        "guild_id": "g",
                        "channel_id": "c",
                        "events": {"drop_received": True, "mining_stalled": False},
                    }
                )
            )
        )
        self.assertTrue(provider.event_enabled("drop_received"))
        self.assertFalse(provider.event_enabled("mining_stalled"))
        self.assertFalse(provider.event_enabled("unknown_event"))


class TestNotificationService(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp_dir.cleanup)
        self.state_path = Path(self._tmp_dir.name) / "notifications_state.json"

    def make_service(self, settings=None):
        service = NotificationService(settings or FakeSettings(), state_path=self.state_path)
        provider = service.get_provider("discord")
        provider.send = AsyncMock()
        return service, provider

    async def test_notify_sends_to_enabled_provider(self):
        service, provider = self.make_service()
        await service.notify("drop_received", "Drop received", "Claimed a drop")
        provider.send.assert_awaited_once_with("drop_received", "Drop received", "Claimed a drop")

    async def test_notify_skips_disabled_provider(self):
        settings = FakeSettings(make_notification_settings(discord={"enabled": False, "bot_token": "", "channel_id": ""}))
        service, provider = self.make_service(settings)
        await service.notify("drop_received", "Drop received", "Claimed a drop")
        provider.send.assert_not_awaited()

    async def test_notify_skips_disabled_event_type(self):
        settings = FakeSettings(
            make_notification_settings(
                discord={
                    "enabled": True,
                    "bot_token": "tok",
                    "guild_id": "g",
                    "channel_id": "c",
                    "events": {"drop_received": False},
                }
            )
        )
        service, provider = self.make_service(settings)
        await service.notify("drop_received", "Drop received", "Claimed a drop")
        provider.send.assert_not_awaited()

    async def test_notify_skips_when_globally_disabled(self):
        service, provider = self.make_service(FakeSettings(make_notification_settings(enabled=False)))
        await service.notify("drop_received", "Drop received", "Claimed a drop")
        provider.send.assert_not_awaited()

    async def test_cooldown_suppresses_repeat_then_allows_after_expiry(self):
        service, provider = self.make_service(FakeSettings(make_notification_settings(cooldown_minutes=15)))
        await service.notify("mining_stalled", "Mining stalled", "no channels")
        await service.notify("mining_stalled", "Mining stalled", "no channels")
        self.assertEqual(provider.send.await_count, 1)

        # simulate the cooldown window having expired
        service._state["last_sent"]["discord:mining_stalled"] = "2000-01-01T00:00:00+00:00"
        await service.notify("mining_stalled", "Mining stalled", "no channels")
        self.assertEqual(provider.send.await_count, 2)

    async def test_provider_error_is_isolated(self):
        service, provider = self.make_service()
        provider.send.side_effect = NotificationError("bot token was rejected")
        # must not raise
        await service.notify("drop_received", "Drop received", "Claimed a drop")
        self.assertEqual(service.get_status()["providers"]["discord"]["last_error"], "bot token was rejected")

    async def test_send_test_bypasses_gating(self):
        settings = FakeSettings(make_notification_settings(enabled=False))
        service, provider = self.make_service(settings)
        await service.send_test("discord")
        provider.send.assert_awaited_once()

    async def test_track_unlinked_tracked_games_seeds_silently_then_reports_new(self):
        service, provider = self.make_service()
        tree = [{"game_name": "Game A", "campaigns": [{"id": "camp-1", "name": "Campaign 1"}]}]

        await service.track_unlinked_tracked_games(tree)
        provider.send.assert_not_awaited()

        tree_with_new = tree + [
            {"game_name": "Game B", "campaigns": [{"id": "camp-2", "name": "Campaign 2"}]}
        ]
        await service.track_unlinked_tracked_games(tree_with_new)
        provider.send.assert_awaited_once()
        self.assertIn("Game B", provider.send.await_args.args[2])

    async def test_track_new_campaigns_seeds_silently_then_reports_new(self):
        service, provider = self.make_service()
        campaigns = [FakeCampaign("camp-1", "Campaign 1", "Game A")]

        await service.track_new_campaigns(campaigns, ["Game A"])
        provider.send.assert_not_awaited()

        campaigns_with_new = campaigns + [FakeCampaign("camp-2", "Campaign 2", "Game A")]
        await service.track_new_campaigns(campaigns_with_new, ["Game A"])
        provider.send.assert_awaited_once()
        self.assertIn("Campaign 2", provider.send.await_args.args[2])

    async def test_track_new_campaigns_ignores_unwatched_games(self):
        service, provider = self.make_service()
        await service.track_new_campaigns([FakeCampaign("camp-1", "Campaign 1", "Game A")], ["Game A"])
        await service.track_new_campaigns(
            [
                FakeCampaign("camp-1", "Campaign 1", "Game A"),
                FakeCampaign("camp-2", "Campaign 2", "Game B"),
            ],
            ["Game A"],
        )
        provider.send.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
