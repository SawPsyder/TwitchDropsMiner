"""
Tests for auto-claiming of badge/emote drops.

Badges and emotes are granted automatically by Twitch once the required watchtime is
registered - there's no drop instance to claim via GQL. TDM only needs to verify that
Twitch's registered watchtime meets the requirement, then mark the drop claimed locally.
"""

import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from src.models.campaign import DropsCampaign
from src.models.drop import TimedDrop
from src.services.claimed_drops import ClaimedDropsStore


def _benefit_edge(index: int, distribution_type: str) -> dict:
    return {
        "benefit": {
            "id": f"b{index}",
            "name": f"Benefit {index}",
            "distributionType": distribution_type,
            "imageAssetURL": "https://example.test/benefit.png",
        }
    }


def _mock_twitch(completed_drop_ids: set[str] | None = None) -> MagicMock:
    """A Twitch stub whose claimed-drops store reports the given IDs as completed."""
    completed = completed_drop_ids or set()
    twitch = MagicMock()
    twitch.claimed_drops.is_completed.side_effect = lambda drop_id: drop_id in completed
    # claim() awaits this - MagicMock children are not awaitable, so make it async
    twitch.notification_service.notify_drop_received = AsyncMock()
    return twitch


def _make_drop(
    benefit_types: list[str],
    *,
    required: int = 10,
    linked: bool = False,
    claim_id: str | None = None,
    ends_in_hours: int = 24,
    twitch: MagicMock | None = None,
) -> TimedDrop:
    now = datetime.now(timezone.utc)
    campaign_data = {
        "id": "campaign-1",
        "name": "Test Campaign",
        "game": {
            "id": "1",
            "name": "Test Game",
            "displayName": "Test Game",
            "boxArtURL": "https://example.test/game-{width}x{height}.jpg",
        },
        "self": {"isAccountConnected": linked},
        "accountLinkURL": "https://example.test/link",
        "startAt": (now - timedelta(hours=1)).isoformat(),
        "endAt": (now + timedelta(hours=ends_in_hours)).isoformat(),
        "status": "ACTIVE",
        "allow": {"channels": [], "isEnabled": True},
        "timeBasedDrops": [
            {
                "id": "drop-1",
                "name": "Test Drop",
                "benefitEdges": [
                    _benefit_edge(i, t) for i, t in enumerate(benefit_types)
                ],
                "startAt": (now - timedelta(hours=1)).isoformat(),
                "endAt": (now + timedelta(hours=ends_in_hours)).isoformat(),
                "preconditionDrops": [],
                "requiredMinutesWatched": required,
            }
        ],
    }
    campaign = DropsCampaign(twitch or _mock_twitch(), campaign_data, {})
    drop = campaign.timed_drops["drop-1"]
    drop.claim_id = claim_id
    return drop


class TestIsBadgeOrEmote(unittest.TestCase):
    def test_all_badge_or_emote(self):
        self.assertTrue(_make_drop(["BADGE"]).is_badge_or_emote)
        self.assertTrue(_make_drop(["EMOTE"]).is_badge_or_emote)
        self.assertTrue(_make_drop(["BADGE", "EMOTE"]).is_badge_or_emote)

    def test_direct_entitlement_is_not(self):
        self.assertFalse(_make_drop(["DIRECT_ENTITLEMENT"]).is_badge_or_emote)

    def test_mixed_benefits_are_not(self):
        # A badge bundled with a game key still needs the normal claim flow.
        self.assertFalse(_make_drop(["BADGE", "DIRECT_ENTITLEMENT"]).is_badge_or_emote)

    def test_no_benefits_is_not(self):
        self.assertFalse(_make_drop([]).is_badge_or_emote)


class TestBadgeEmoteCanClaim(unittest.TestCase):
    def test_claimable_once_watchtime_met_without_claim_id(self):
        drop = _make_drop(["BADGE"], required=10)
        drop.real_current_minutes = 10
        # No claim_id and unlinked - would be unclaimable for a regular drop.
        self.assertIsNone(drop.claim_id)
        self.assertTrue(drop.can_claim)

    def test_not_claimable_below_watchtime(self):
        drop = _make_drop(["BADGE"], required=10)
        drop.real_current_minutes = 9
        self.assertFalse(drop.can_claim)

    def test_local_estimate_does_not_count_towards_watchtime(self):
        # extra_current_minutes is our own estimate, not registered by Twitch, so it must
        # not make a badge/emote drop look claimable.
        drop = _make_drop(["BADGE"], required=10)
        drop.real_current_minutes = 8
        drop.extra_current_minutes = 5
        self.assertEqual(drop.current_minutes, 13)
        self.assertFalse(drop.can_claim)

    def test_not_claimable_when_already_claimed(self):
        drop = _make_drop(["BADGE"], required=10)
        drop.real_current_minutes = 10
        drop.is_claimed = True
        self.assertFalse(drop.can_claim)

    def test_direct_entitlement_still_requires_claim_id(self):
        drop = _make_drop(["DIRECT_ENTITLEMENT"], required=10)
        drop.real_current_minutes = 10
        # Reaching the watchtime is not enough for a real entitlement.
        self.assertFalse(drop.can_claim)
        drop.claim_id = f"{drop.id}-instance"
        self.assertTrue(drop.can_claim)


class TestBadgeEmoteClaim(unittest.TestCase):
    def test_claim_skips_gql(self):
        drop = _make_drop(["BADGE"], required=10)
        drop.real_current_minutes = 10
        # gql_request must never be called for a badge/emote claim.
        drop._twitch.gql_request = MagicMock(
            side_effect=AssertionError("gql_request should not be called for badge/emote")
        )
        result = asyncio.run(drop._claim())
        self.assertTrue(result)
        drop._twitch.gql_request.assert_not_called()

    def test_claim_fails_when_watchtime_not_met(self):
        drop = _make_drop(["BADGE"], required=10)
        drop.real_current_minutes = 5
        result = asyncio.run(drop._claim())
        self.assertFalse(result)

    def test_claim_returns_true_when_already_claimed(self):
        drop = _make_drop(["BADGE"], required=10)
        drop.is_claimed = True
        result = asyncio.run(drop._claim())
        self.assertTrue(result)


class TestClaimedDropsStore(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "claimed_drops.json"

    def tearDown(self):
        self._tmp.cleanup()

    def _future(self, hours: int = 24) -> datetime:
        return datetime.now(timezone.utc) + timedelta(hours=hours)

    def test_mark_and_check(self):
        store = ClaimedDropsStore(self.path)
        self.assertFalse(store.is_completed("drop-1"))
        store.mark_completed("drop-1", self._future())
        self.assertTrue(store.is_completed("drop-1"))

    def test_persists_across_instances(self):
        store = ClaimedDropsStore(self.path)
        store.mark_completed("drop-1", self._future())
        # a fresh instance (simulating a restart) must still know it's completed
        reloaded = ClaimedDropsStore(self.path)
        self.assertTrue(reloaded.is_completed("drop-1"))

    def test_prunes_long_ended_campaigns(self):
        store = ClaimedDropsStore(self.path)
        store.mark_completed("old", datetime.now(timezone.utc) - timedelta(days=30))
        store.mark_completed("recent", self._future())
        reloaded = ClaimedDropsStore(self.path)
        self.assertFalse(reloaded.is_completed("old"))
        self.assertTrue(reloaded.is_completed("recent"))

    def test_malformed_entries_are_ignored(self):
        self.path.write_text('{"good": "2999-01-01T00:00:00+00:00", "bad": "not-a-date"}')
        store = ClaimedDropsStore(self.path)
        self.assertTrue(store.is_completed("good"))
        self.assertFalse(store.is_completed("bad"))


class TestClaimedDropsPersistenceIntegration(unittest.TestCase):
    def test_completed_drop_is_marked_claimed_on_rebuild(self):
        # A previously completed badge drop must come back already claimed.
        twitch = _mock_twitch(completed_drop_ids={"drop-1"})
        drop = _make_drop(["BADGE"], required=10, twitch=twitch)
        self.assertTrue(drop.is_claimed)
        # claimed drops report full watchtime regardless of what the inventory said
        self.assertEqual(drop.real_current_minutes, drop.required_minutes)

    def test_non_badge_drop_ignores_store(self):
        # The store only governs badge/emote drops; a real entitlement must not be
        # marked claimed just because its ID appears in the store.
        twitch = _mock_twitch(completed_drop_ids={"drop-1"})
        drop = _make_drop(["DIRECT_ENTITLEMENT"], required=10, twitch=twitch)
        self.assertFalse(drop.is_claimed)

    def test_claim_records_completion_in_store(self):
        drop = _make_drop(["BADGE"], required=10)
        drop.real_current_minutes = 10
        asyncio.run(drop.claim())
        drop._twitch.claimed_drops.mark_completed.assert_called_once()
        recorded_id = drop._twitch.claimed_drops.mark_completed.call_args.args[0]
        self.assertEqual(recorded_id, drop.id)


if __name__ == "__main__":
    unittest.main()
