"""Claim identity for Discord drop notifications."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

from src.notifications import NotificationError, NotificationService
from src.notifications.claims import (
    claim_keys,
    coerce_claim_history,
    is_synthetic_claim_id,
    prune_claim_history,
    repeating_benefit_names,
)
from src.notifications.render import render_digest
from tests.test_notifications import (
    FakeSettings,
    digest_notification_settings,
    make_notification_settings,
)


NOW = datetime(2026, 10, 4, 7, 14, tzinfo=UTC)
FIELD = "\x1f"


def _record(key: str, when: datetime, *, benefit: str = "Tee", campaign: str = "Day2") -> dict:
    return {
        "k": key,
        "at": when.isoformat(),
        "game": "PUBG",
        "game_id": "42",
        "campaign": campaign,
        "benefit": benefit,
    }


class ClaimKeyTests(unittest.TestCase):
    def test_synthetic_instance_id_is_not_part_of_the_key(self):
        self.assertTrue(is_synthetic_claim_id("user#camp#drop", "camp", "drop"))
        synthetic = claim_keys("camp", "drop", "user#camp#drop", ["b1"])
        missing = claim_keys("camp", "drop", None, ["b1"])
        self.assertEqual(synthetic, missing)
        self.assertEqual(synthetic, [f"camp{FIELD}drop{FIELD}b1{FIELD}"])

    def test_a_fresh_instance_id_changes_the_key(self):
        first = claim_keys("camp", "drop", "user#camp#drop", ["b1"])
        second = claim_keys("camp", "drop", "instance-9", ["b1"])
        self.assertNotEqual(first, second)
        self.assertTrue(second[0].endswith(f"{FIELD}instance-9"))

    def test_missing_campaign_or_drop_cannot_be_deduped(self):
        self.assertEqual(claim_keys("", "drop", "x", ["b"]), [])
        self.assertEqual(claim_keys("camp", None, "x", ["b"]), [])

    def test_one_key_per_benefit_and_one_when_there_are_none(self):
        self.assertEqual(len(claim_keys("c", "d", None, ["a", "b", "a"])), 2)
        self.assertEqual(claim_keys("c", "d", None, []), [f"c{FIELD}d{FIELD}{FIELD}"])

    def test_prune_drops_expired_rows_and_keeps_the_newest(self):
        old = _record("old", NOW - timedelta(days=8))
        middle = _record("middle", NOW - timedelta(days=2))
        newest = _record("newest", NOW - timedelta(hours=1))
        kept = prune_claim_history([old, newest, middle], NOW, ttl=timedelta(days=7), limit=2)
        self.assertEqual([entry["k"] for entry in kept], ["middle", "newest"])

    def test_coerce_drops_rows_without_a_key_or_a_stamp(self):
        raw = [
            {"k": "ok", "at": NOW.isoformat(), "game": "PUBG"},
            {"k": "", "at": NOW.isoformat()},
            {"at": NOW.isoformat()},
            {"k": "bad-stamp", "at": "yesterday"},
            "nope",
        ]
        clean = coerce_claim_history(raw)
        self.assertEqual([entry["k"] for entry in clean], ["ok"])

    def test_repeating_names_need_a_different_campaign(self):
        history = [
            _record("k", NOW, benefit="Tee", campaign="PAS2 Day2"),
            _record("k2", NOW, benefit="Spray", campaign="PAS2 Day2"),
        ]
        same = repeating_benefit_names(
            history, game="PUBG", game_id=42, campaign="PAS2 Day2", benefits=["Tee"]
        )
        other = repeating_benefit_names(
            history, game="PUBG", game_id="42", campaign="PAS2 Day3", benefits=["Tee", "New"]
        )
        self.assertEqual(same, [])
        self.assertEqual(other, ["Tee"])


class ClaimDedupeServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state_path = Path(self._tmp.name) / "notifications_state.json"

    def make_service(self, **overrides):
        settings = FakeSettings(digest_notification_settings(**overrides))
        service = NotificationService(settings, state_path=self.state_path)
        provider = service.get_provider("discord")
        assert provider is not None
        provider.send = AsyncMock()
        provider.send_digest = AsyncMock()
        return service, provider

    async def claim(self, service: NotificationService, **overrides) -> None:
        values = {
            "game_name": "PUBG",
            "benefits": ["Tee", "Spray"],
            "campaign": "PAS2 Day2",
            "drop_name": "Daily",
            "channel": "inventory",
            "game_id": 42,
            "campaign_id": "camp-day2",
            "drop_id": "drop-day2",
            "claim_id": "user#camp-day2#drop-day2",
            "benefit_ids": ["tee", "spray"],
        }
        values.update(overrides)
        benefits = values.pop("benefits")
        game_name = values.pop("game_name")
        await service.notify_drop_received(game_name, benefits, **values)

    async def test_the_same_instance_is_queued_once(self):
        service, _provider = self.make_service()
        await self.claim(service)
        await self.claim(service)
        self.assertEqual(len(service._state["digest_queue"]), 1)
        self.assertEqual(service._state["digest_seq"], 1)
        self.assertEqual(len(service._state["notified_claims"]), 2)

    async def test_duplicate_stays_suppressed_across_restart(self):
        service, _provider = self.make_service()
        await self.claim(service)
        await service.flush_pending_state()
        restarted, _provider = self.make_service()
        self.assertEqual(len(restarted._state["digest_queue"]), 1)
        await self.claim(restarted)
        self.assertEqual(len(restarted._state["digest_queue"]), 1)
        self.assertEqual(restarted._state["digest_seq"], 1)

    async def test_expired_history_does_not_suppress(self):
        service, _provider = self.make_service()
        await self.claim(service)
        for entry in service._state["notified_claims"]:
            entry["at"] = (datetime.now(UTC) - timedelta(days=8)).isoformat()
        await self.claim(service)
        self.assertEqual(len(service._state["digest_queue"]), 2)
        self.assertTrue(
            all(
                datetime.fromisoformat(entry["at"]) > datetime.now(UTC) - timedelta(days=1)
                for entry in service._state["notified_claims"]
            )
        )

    async def test_history_keeps_the_newest_keys(self):
        service, _provider = self.make_service()
        with patch("src.notifications.service.CLAIM_HISTORY_MAX", 2):
            await self.claim(service, benefits=["One"], benefit_ids=["b1"], drop_id="d1")
            await self.claim(service, benefits=["Two"], benefit_ids=["b2"], drop_id="d2")
            await self.claim(service, benefits=["Three"], benefit_ids=["b3"], drop_id="d3")
            self.assertEqual(
                [entry["benefit"] for entry in service._state["notified_claims"]],
                ["Two", "Three"],
            )
            await self.claim(service, benefits=["One"], benefit_ids=["b1"], drop_id="d1")
        games = [event["data"]["drop_id"] for event in service._state["digest_queue"]]
        self.assertEqual(games, ["d1", "d2", "d3", "d1"])

    async def test_a_new_campaign_or_instance_still_notifies(self):
        service, _provider = self.make_service()
        await self.claim(service)
        await self.claim(
            service,
            campaign="PAS2 Day3",
            campaign_id="camp-day3",
            drop_id="drop-day3",
            claim_id="user#camp-day3#drop-day3",
        )
        await self.claim(service, claim_id="fresh-instance")
        self.assertEqual(len(service._state["digest_queue"]), 3)

    async def test_a_new_benefit_on_the_same_drop_still_notifies(self):
        service, _provider = self.make_service()
        await self.claim(service, benefits=["Tee"], benefit_ids=["tee"])
        await self.claim(service, benefits=["Tee", "Spray"], benefit_ids=["tee", "spray"])
        self.assertEqual(len(service._state["digest_queue"]), 2)

    async def test_callers_without_ids_are_not_deduped(self):
        service, _provider = self.make_service()
        await service.notify_drop_received("PUBG", ["Tee"], campaign="Day2")
        await service.notify_drop_received("PUBG", ["Tee"], campaign="Day2")
        self.assertEqual(len(service._state["digest_queue"]), 2)
        self.assertEqual(service._state["notified_claims"], [])

    async def test_a_second_campaign_is_labelled_from_history(self):
        service, _provider = self.make_service()
        await self.claim(service, benefits=["Tee"], benefit_ids=["tee"])
        service._state["digest_queue"].clear()
        await self.claim(
            service,
            benefits=["Tee"],
            benefit_ids=["tee"],
            campaign="PAS2 Day3",
            campaign_id="camp-day3",
            drop_id="drop-day3",
            claim_id="user#camp-day3#drop-day3",
        )
        event = service._state["digest_queue"][-1]
        self.assertEqual(event["data"]["disambiguate"], ["Tee"])
        payload = render_digest(
            events=[event],
            window_start=NOW - timedelta(hours=6),
            window_end=NOW,
            next_at=NOW + timedelta(hours=6),
            interval_minutes=360,
            version="1.11.1",
        )
        self.assertEqual(payload["content"], "1 drop claimed")
        self.assertEqual(payload["embeds"][0]["description"], "✓ Tee · PAS2 Day3")

    async def test_event_arriving_during_send_is_kept_and_a_replay_is_not(self):
        service, provider = self.make_service()
        await self.claim(service, benefits=["Tee"], benefit_ids=["tee"], drop_id="first")
        started = asyncio.Event()
        release = asyncio.Event()

        async def slow(_message):
            started.set()
            await release.wait()

        provider.send_digest.side_effect = slow
        flushing = asyncio.create_task(service.flush_digest())
        await started.wait()
        await self.claim(service, benefits=["Tee"], benefit_ids=["tee"], drop_id="first")
        await self.claim(
            service,
            benefits=["Noggle"],
            benefit_ids=["noggle"],
            game_name="Warframe",
            campaign="PMCFQ",
            campaign_id="camp-wf",
            drop_id="second",
            claim_id="user#camp-wf#second",
            game_id=99,
        )
        release.set()
        self.assertTrue(await flushing)
        queued = [(event["data"]["game"], event["data"]["drop_id"]) for event in service._state["digest_queue"]]
        self.assertEqual(queued, [("Warframe", "second")])

    async def test_immediate_cooldown_does_not_record_the_swallowed_claim(self):
        settings = FakeSettings(make_notification_settings(cooldown_minutes=15))
        service = NotificationService(settings, state_path=self.state_path)
        provider = service.get_provider("discord")
        assert provider is not None
        provider.send = AsyncMock()
        await self.claim(service, benefits=["Tee"], benefit_ids=["tee"], drop_id="first")
        await self.claim(service, benefits=["Spray"], benefit_ids=["spray"], drop_id="second")
        self.assertEqual(provider.send.await_count, 1)
        self.assertEqual([entry["benefit"] for entry in service._state["notified_claims"]], ["Tee"])
        service._state["last_sent"] = {}
        await self.claim(service, benefits=["Spray"], benefit_ids=["spray"], drop_id="second")
        self.assertEqual(provider.send.await_count, 2)

    async def test_a_failed_immediate_send_can_be_retried(self):
        settings = FakeSettings(make_notification_settings())
        service = NotificationService(settings, state_path=self.state_path)
        provider = service.get_provider("discord")
        assert provider is not None
        provider.send = AsyncMock(side_effect=NotificationError("down"))
        await self.claim(service, benefits=["Tee"], benefit_ids=["tee"])
        self.assertEqual(service._state["notified_claims"], [])
        provider.send.side_effect = None
        await self.claim(service, benefits=["Tee"], benefit_ids=["tee"])
        self.assertEqual(provider.send.await_count, 2)
        self.assertEqual(len(service._state["notified_claims"]), 1)

    async def test_disabled_notifications_do_not_record_a_claim(self):
        service, _provider = self.make_service()
        service._settings.notifications["enabled"] = False
        await self.claim(service)
        self.assertEqual(service._state["digest_queue"], [])
        self.assertEqual(service._state["notified_claims"], [])
