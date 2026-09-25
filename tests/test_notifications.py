import asyncio
import json
import logging
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

from src.notifications import DiscordProvider, NotificationError, NotificationService
from src.notifications.render import message_char_count, render_digest
from src.notifications.schedule import next_digest_at
from src.utils import json_save
from src.web.managers.settings import SettingsManager


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


def digest_notification_settings(**overrides):
    settings = make_notification_settings(
        mode="digest",
        digest_interval_minutes=1440,
        digest_send_time="09:00",
        digest_send_weekday=0,
        digest_urgent_immediate=True,
        digest_send_empty=False,
        digest_sections={"progress": True, "errors": True},
    )
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
        provider.send_digest = AsyncMock()
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


def _event(event_type, stamp, **data):
    return {"type": event_type, "ts": stamp.isoformat(), "data": data}


def _render(**overrides):
    now = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
    kwargs = {
        "events": [],
        "window_start": now - timedelta(hours=6),
        "window_end": now,
        "next_at": now + timedelta(hours=6),
        "interval_minutes": 360,
        "version": "1.9.1",
        "progress": {"state": "idle", "campaigns": []},
    }
    kwargs.update(overrides)
    return render_digest(**kwargs)


class TestDigestQueue(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp_dir.cleanup)
        self.state_path = Path(self._tmp_dir.name) / "notifications_state.json"

    def make_service(self, **overrides):
        settings = FakeSettings(digest_notification_settings(**overrides))
        service = NotificationService(settings, state_path=self.state_path)
        provider = service.get_provider("discord")
        provider.send = AsyncMock()
        provider.send_digest = AsyncMock()
        return service, provider

    async def test_queue_cap_drops_oldest_and_does_not_send(self):
        service, provider = self.make_service()
        for index in range(505):
            await service.notify_drop_received(
                f"Game {index}", ["Badge"], campaign="Camp", drop_name="Drop", channel="chan"
            )
        queue = service._state["digest_queue"]
        self.assertEqual(len(queue), 500)
        self.assertEqual(service._state["digest_dropped"], 5)
        games = [event["data"]["game"] for event in queue]
        self.assertNotIn("Game 0", games)
        self.assertIn("Game 504", games)
        provider.send.assert_not_awaited()

    async def test_queue_survives_restart(self):
        service, _provider = self.make_service()
        await service.notify_drop_received("Game A", ["Badge"], campaign="Camp", channel="chan")
        reloaded = NotificationService(service._settings, state_path=self.state_path)
        self.assertEqual(len(reloaded._state["digest_queue"]), 1)
        self.assertEqual(reloaded._state["digest_queue"][0]["data"]["game"], "Game A")

    async def test_corrupt_state_is_backed_up_and_reset(self):
        self.state_path.write_text("{", encoding="utf8")
        service, _provider = self.make_service()
        self.assertEqual(service._state["digest_queue"], [])
        backup = self.state_path.with_name(self.state_path.name + ".corrupt")
        self.assertTrue(backup.exists())
        self.assertEqual(backup.read_text(encoding="utf8"), "{")

    async def test_failed_send_keeps_queue_until_success(self):
        service, provider = self.make_service()
        await service.notify_drop_received("Game A", ["Badge"], campaign="Camp", channel="chan")
        provider.send_digest.side_effect = NotificationError("500")
        sent = await service.flush_digest()
        self.assertFalse(sent)
        self.assertEqual(len(service._state["digest_queue"]), 1)

        provider.send_digest.side_effect = None
        service._state["last_digest"]["retry_at"] = "2000-01-01T00:00:00+00:00"
        sent = await service.flush_digest()
        self.assertTrue(sent)
        self.assertEqual(service._state["digest_queue"], [])

    async def test_rate_limit_sets_retry_and_keeps_queue(self):
        service, provider = self.make_service()
        await service.notify_drop_received("Game A", ["Badge"], campaign="Camp", channel="chan")
        provider.send_digest.side_effect = NotificationError("429", retry_after=30)
        before = datetime.now(UTC)
        sent = await service.flush_digest()
        self.assertFalse(sent)
        self.assertEqual(len(service._state["digest_queue"]), 1)
        status = service.get_status()
        last = status["last_digest"]
        self.assertFalse(last["ok"])
        self.assertEqual(last["error"], "Discord rate limit")
        retry_at = datetime.fromisoformat(last["retry_at"])
        self.assertGreater(retry_at, before + timedelta(seconds=20))
        self.assertLess(retry_at, before + timedelta(seconds=40))

    async def test_missed_digest_sends_on_scheduler_start(self):
        service, provider = self.make_service()
        await service.notify_drop_received("Game A", ["Badge"], campaign="Camp", channel="chan")
        service._state["digest_next_at"] = "2000-01-01T00:00:00+00:00"
        service.start()
        try:
            for _ in range(50):
                if provider.send_digest.await_count:
                    break
                await asyncio.sleep(0.02)
            provider.send_digest.assert_awaited()
            self.assertEqual(service._state["digest_queue"], [])
        finally:
            await service.stop()

    async def test_urgent_immediate_respects_cooldown_and_marks_alerted(self):
        service, provider = self.make_service(cooldown_minutes=15)
        await service.notify_mining_stalled("no channels")
        await service.notify_mining_stalled("no channels")
        self.assertEqual(provider.send.await_count, 1)
        queued = service._state["digest_queue"]
        self.assertEqual(len(queued), 2)
        self.assertTrue(queued[0]["data"]["alerted"])
        self.assertFalse(queued[1]["data"]["alerted"])

    async def test_new_campaigns_queue_without_immediate_send(self):
        service, provider = self.make_service()
        await service.track_new_campaigns([FakeCampaign("camp-1", "Campaign 1", "Game A")], ["Game A"])
        extra = [
            FakeCampaign("camp-1", "Campaign 1", "Game A"),
            FakeCampaign("camp-2", "Campaign 2", "Game A"),
            FakeCampaign("camp-3", "Campaign 3", "Game A"),
        ]
        await service.track_new_campaigns(extra, ["Game A"])
        provider.send.assert_not_awaited()
        kinds = [event["type"] for event in service._state["digest_queue"]]
        self.assertEqual(kinds, ["new_campaign", "new_campaign"])

    async def test_unlinked_games_queue_without_immediate_send(self):
        service, provider = self.make_service()
        await service.track_unlinked_tracked_games(
            [{"game_name": "Game A", "campaigns": [{"id": "camp-1", "name": "Campaign 1"}]}]
        )
        await service.track_unlinked_tracked_games(
            [
                {"game_name": "Game A", "campaigns": [{"id": "camp-1", "name": "Campaign 1"}]},
                {"game_name": "Game B", "campaigns": [{"id": "camp-2", "name": "Campaign 2"}]},
                {"game_name": "Game C", "campaigns": [{"id": "camp-3", "name": "Campaign 3"}]},
            ]
        )
        provider.send.assert_not_awaited()
        games = [event["data"]["game"] for event in service._state["digest_queue"]]
        self.assertEqual(games, ["Game B", "Game C"])

    async def test_errors_section_off_does_not_count_warnings(self):
        service, _provider = self.make_service(digest_sections={"progress": True, "errors": False})
        logging.getLogger("TwitchDrops").warning("ignored template %s", "x")
        self.assertEqual(service.queued_count(), 0)
        self.assertFalse(service.has_digest_content())

    async def test_preview_uses_saved_interval_and_keeps_the_queue(self):
        service, provider = self.make_service(digest_interval_minutes=360)
        await service.notify_drop_received("Game A", ["Badge"], campaign="Camp", channel="chan")
        before = list(service._state["digest_queue"])
        last_before = service._state.get("last_digest")
        next_before = service._state.get("digest_next_at")
        await service.send_preview()
        provider.send_digest.assert_awaited()
        embeds = provider.send_digest.await_args.args[0]
        self.assertTrue(embeds[0]["title"].startswith("Preview · "))
        self.assertIn("last 6 hours", embeds[0]["title"])
        self.assertEqual(service._state["digest_queue"], before)
        self.assertEqual(service._state.get("last_digest"), last_before)
        self.assertEqual(service._state.get("digest_next_at"), next_before)

    async def test_empty_window_skips_unless_send_empty(self):
        service, provider = self.make_service()
        sent = await service.flush_digest()
        self.assertFalse(sent)
        provider.send_digest.assert_not_awaited()
        self.assertTrue(service._state["last_digest"]["skipped"])
        self.assertIsNone(service.get_status()["last_digest"]["at"])

        service._settings.notifications["digest_send_empty"] = True
        sent = await service.flush_digest()
        self.assertTrue(sent)
        embeds = provider.send_digest.await_args.args[0]
        self.assertIn("Nothing new in this period.", embeds[0]["description"])
        progress = next(embed for embed in embeds if embed["title"].startswith("📈"))
        self.assertIn("Idle — nothing to mine right now", progress["description"])

    async def test_status_reports_digest_fields(self):
        service, _provider = self.make_service()
        await service.notify_drop_received("Game A", ["Badge"], campaign="Camp", channel="chan")
        status = service.get_status()
        self.assertEqual(status["mode"], "digest")
        self.assertEqual(status["queued_count"], 1)
        self.assertIsNotNone(status["next_digest_at"])
        self.assertTrue(status["timezone"])
        self.assertEqual(status["dropped_count"], 0)
        last = status["last_digest"]
        for key in ("at", "ok", "error", "retry_at"):
            self.assertIn(key, last)
        self.assertIsNone(last["at"])

    async def test_scheduler_starts_and_stops(self):
        service, _provider = self.make_service()
        service.start()
        task = service._digest_task
        self.assertIsNotNone(task)
        self.assertEqual(task.get_name(), "notification-digest")
        self.assertFalse(task.done())
        await service.stop()
        self.assertIsNone(service._digest_task)
        self.assertTrue(task.done())

    async def test_switching_to_immediate_flushes_one_digest(self):
        settings = FakeSettings(digest_notification_settings())
        settings.save = lambda: None
        service, provider = self.make_service()
        service._settings = settings
        await service.notify_drop_received("Game A", ["Badge"], campaign="Camp", channel="chan")
        manager = SettingsManager(MagicMock(), settings, MagicMock())
        manager.bind_notification_service(service)
        manager.update_settings(
            {"notifications": {**digest_notification_settings(), "mode": "immediate"}}
        )
        task = service._mode_switch_task
        self.assertIsNotNone(task)
        await task
        provider.send_digest.assert_awaited()
        self.assertEqual(service._state["digest_queue"], [])
        self.assertEqual(settings.notifications["mode"], "immediate")


class TestDigestPersistence(unittest.TestCase):
    def test_json_save_is_atomic(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            json_save(path, {"a": 1})
            json_save(path, {"a": 2})
            self.assertEqual(json.loads(path.read_text(encoding="utf8"))["a"], 2)
            leftovers = [item.name for item in path.parent.iterdir() if item.name != path.name]
            self.assertEqual(leftovers, [])
            with self.assertRaises(TypeError):
                json_save(path, {"bad": object()})
            self.assertEqual(json.loads(path.read_text(encoding="utf8"))["a"], 2)


class TestDigestSchedule(unittest.TestCase):
    def test_daily_send_stays_on_local_hour_across_dst(self):
        zone = ZoneInfo("America/New_York")
        # 2026-03-07 14:00 UTC is 09:00 EST, the day before US clocks spring forward.
        after = datetime(2026, 3, 7, 14, 0, tzinfo=UTC)
        result = next_digest_at(after, 1440, "09:00", 0, tz=zone)
        self.assertEqual(result, datetime(2026, 3, 8, 13, 0, tzinfo=UTC))
        local = result.astimezone(zone)
        self.assertEqual(local.hour, 9)
        self.assertEqual(local.minute, 0)

    def test_send_time_later_today_stays_on_the_same_local_day(self):
        zone = ZoneInfo("Europe/Berlin")
        after = datetime(2026, 6, 1, 4, 0, tzinfo=UTC)  # 06:00 local
        result = next_digest_at(after, 1440, "09:00", 0, tz=zone)
        local = result.astimezone(zone)
        self.assertEqual(local.date(), after.astimezone(zone).date())
        self.assertEqual((local.hour, local.minute), (9, 0))

    def test_weekly_uses_weekday_monday(self):
        zone = ZoneInfo("Europe/Berlin")
        # Friday 2026-09-25 12:00 UTC is 14:00 CEST. Next Monday 09:00 CEST is 07:00 UTC.
        after = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
        result = next_digest_at(after, 10080, "09:00", 0, tz=zone)
        self.assertEqual(result, datetime(2026, 9, 28, 7, 0, tzinfo=UTC))
        self.assertEqual(result.astimezone(zone).weekday(), 0)

    def test_unanchored_interval_is_last_send_plus_interval(self):
        after = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
        result = next_digest_at(after, 180, "09:00", 3)
        self.assertEqual(result, after + timedelta(hours=3))


class TestDigestLogHandler(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp_dir.cleanup)
        self.state_path = Path(self._tmp_dir.name) / "notifications_state.json"

    async def test_warnings_group_by_template_and_skip_notifications_logger(self):
        settings = FakeSettings(digest_notification_settings())
        service = NotificationService(settings, state_path=self.state_path)
        miner = logging.getLogger("TwitchDrops")
        miner.warning("disk full on %s", "one")
        miner.warning("disk full on %s", "two")
        logging.getLogger("TwitchDrops.notifications").warning("digest failed %s", "secret")
        groups = service._state["digest_error_groups"]
        self.assertEqual(len(groups), 1)
        group = groups["disk full on %s"]
        self.assertEqual(group["count"], 2)
        self.assertEqual(group["latest"], "disk full on two")
        self.assertNotIn("digest failed %s", groups)


class TestDigestRenderer(unittest.TestCase):
    def test_forty_one_drops_trim_with_singular_more_line(self):
        now = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
        events = [
            _event(
                "drop_received",
                now - timedelta(minutes=50 - index),
                game="Game",
                campaign="Camp",
                drop=f"Drop {index}",
                benefits=["Badge"],
                channel="chan",
            )
            for index in range(41)
        ]
        embeds = _render(events=events, include_progress=False)
        text = "\n".join(embed["description"] for embed in embeds)
        self.assertIn("…and 1 more drop", text)
        self.assertLessEqual(message_char_count(embeds), 6000)
        self.assertLessEqual(len(embeds), 10)
        self.assertTrue(all(len(embed["description"]) <= 4096 for embed in embeds))

    def test_huge_queue_stays_one_message_inside_discord_limits(self):
        now = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
        long_name = "X" * 200
        events = []
        for index in range(200):
            events.append(
                _event(
                    "drop_received",
                    now - timedelta(minutes=index + 1),
                    game=long_name,
                    campaign=long_name,
                    drop=long_name,
                    benefits=[long_name],
                    channel=long_name,
                )
            )
        for index in range(40):
            events.append(
                _event(
                    "new_campaign",
                    now,
                    game=f"CampaignGame {index}",
                    campaign=long_name,
                    ends_at=(now + timedelta(days=index + 1)).isoformat(),
                )
            )
        for index in range(30):
            events.append(_event("unlinked_tracked_game", now, game=f"Unlinked {index}", campaign="C"))
        events.append(
            _event(
                "mining_stalled",
                now,
                reason="Y" * 500,
                alerted=True,
            )
        )
        groups = [
            {
                "level": "ERROR" if index < 5 else "WARNING",
                "count": 3,
                "latest": "Z" * 400,
                "last_ts": now.isoformat(),
            }
            for index in range(25)
        ]
        progress = {
            "state": "watching",
            "channel": long_name,
            "game": long_name,
            "campaigns": [
                {
                    "game": long_name,
                    "drop": long_name,
                    "percent": index,
                    "remaining_minutes": index * 10,
                    "mining_now": index == 0,
                }
                for index in range(20)
            ],
        }
        embeds = render_digest(
            events=events,
            error_groups=groups,
            window_start=now - timedelta(days=2),
            window_end=now,
            next_at=now + timedelta(days=1),
            interval_minutes=10080,
            dropped_count=80,
            progress=progress,
            version="1.9.1",
        )
        self.assertIsInstance(embeds, list)
        self.assertLessEqual(len(embeds), 10)
        self.assertLessEqual(message_char_count(embeds), 6000)
        self.assertTrue(all(len(embed.get("description") or "") <= 4096 for embed in embeds))
        self.assertLessEqual(len(embeds), 6)
        titles = [embed["title"] for embed in embeds]
        self.assertEqual(sum(1 for title in titles if title.startswith("🎁")), 1)

    def test_longest_section_is_trimmed_before_a_shorter_one(self):
        now = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
        events = [
            _event(
                "drop_received",
                now - timedelta(minutes=index + 1),
                game="Huge",
                campaign="Wall",
                drop="Reward " + ("Q" * 70),
                benefits=["Benefit " + ("B" * 70)],
                channel="channel-" + ("c" * 60),
            )
            for index in range(30)
        ]
        campaign_names = [f"ShortCamp {index}" for index in range(4)]
        for name in campaign_names:
            events.append(
                _event(
                    "new_campaign",
                    now,
                    game="Tiny",
                    campaign=name,
                    ends_at=(now + timedelta(days=3)).isoformat(),
                )
            )
        embeds = _render(events=events, include_progress=False)
        descriptions = {embed["title"]: embed["description"] for embed in embeds}
        drops = next(text for title, text in descriptions.items() if title.startswith("🎁"))
        campaigns = next(text for title, text in descriptions.items() if title.startswith("🆕"))
        self.assertIn("…and ", drops)
        for name in campaign_names:
            self.assertIn(name, campaigns)
        self.assertNotIn("…and ", campaigns)

    def test_warning_only_attention_is_last_and_amber(self):
        now = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
        embeds = _render(
            events=[_event("drop_received", now, game="G", campaign="C", benefits=["Badge"], channel="ch")],
            error_groups=[
                {"level": "WARNING", "count": 2, "latest": "slow disk", "last_ts": now.isoformat()}
            ],
            include_progress=False,
        )
        self.assertEqual(embeds[-1]["color"], 0xF1C40F)
        self.assertTrue(embeds[-1]["title"].startswith("⚠️"))
        self.assertNotEqual(embeds[1]["color"], 0xF1C40F)

    def test_urgent_attention_is_second_and_red(self):
        now = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
        embeds = _render(
            events=[
                _event("mining_stalled", now, reason="no channels", alerted=True),
                _event("drop_received", now, game="G", campaign="C", benefits=["Badge"], channel="ch"),
            ],
            include_progress=False,
        )
        self.assertEqual(embeds[0]["color"], 0x9146FF)
        self.assertEqual(embeds[1]["color"], 0xE74C3C)
        self.assertIn("alerted at the time", embeds[1]["description"])
        drops = next(embed for embed in embeds if embed["title"].startswith("🎁"))
        self.assertEqual(drops["color"], 0x2ECC71)

    def test_footer_and_timestamp_only_on_the_last_embed(self):
        now = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
        embeds = _render(
            events=[
                _event("drop_received", now, game="G", campaign="C", benefits=["Badge"], channel="ch"),
                _event("new_campaign", now, game="G", campaign="New", ends_at=(now + timedelta(days=1)).isoformat()),
            ],
        )
        self.assertGreater(len(embeds), 1)
        for embed in embeds[:-1]:
            self.assertNotIn("footer", embed)
            self.assertNotIn("timestamp", embed)
        self.assertIn("TwitchDropsMiner v1.9.1", embeds[-1]["footer"]["text"])
        self.assertIn("timestamp", embeds[-1])

    def test_drops_group_by_game_and_use_discord_timestamps(self):
        now = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
        earlier = now - timedelta(minutes=10)
        embeds = _render(
            events=[
                _event(
                    "drop_received",
                    earlier,
                    game="Alpha",
                    campaign="Spring",
                    drop="Other",
                    benefits=["Badge"],
                    channel="streamer",
                ),
                _event(
                    "drop_received",
                    now,
                    game="Alpha",
                    campaign="Spring",
                    benefits=["Badge"],
                    channel="inventory",
                ),
            ],
            include_progress=False,
            progress=None,
        )
        drops = next(embed for embed in embeds if embed["color"] == 0x2ECC71)
        self.assertIn("**Alpha** — Spring", drops["description"])
        self.assertIn("• Badge — Other · streamer · <t:", drops["description"])
        self.assertIn("• Badge · inventory · <t:", drops["description"])
        self.assertEqual(drops["description"].count("**Alpha**"), 1)


if __name__ == "__main__":
    unittest.main()
