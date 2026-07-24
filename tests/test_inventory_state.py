"""
Unit tests for the inventory/maintenance state-ownership API on Twitch.

These methods let the services drive an inventory refresh without poking the
client's private attributes. They only touch simple containers, so we exercise
them as unbound methods against a minimal stand-in ``self`` rather than building
a full Twitch instance (which would spin up websocket/service objects).
"""

import unittest
from collections import deque
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.config import State
from src.core.client import Twitch


def _fake_client():
    return SimpleNamespace(
        _drops={},
        inventory=[],
        _campaigns={},
        _mnt_triggers=deque(),
        _state=State.IDLE,
        _manual_target_game=None,
        gui=MagicMock(),
    )


def _fake_campaign(campaign_id, drop_ids):
    drops = [SimpleNamespace(id=d) for d in drop_ids]
    return SimpleNamespace(id=campaign_id, drops=drops)


class TestInventoryStateOwnership(unittest.TestCase):
    def test_reset_inventory_state_clears_everything(self):
        c = _fake_client()
        c._drops = {"d1": object()}
        c.inventory = [object()]
        c._mnt_triggers = deque([datetime.now(UTC)])

        Twitch.reset_inventory_state(c)

        self.assertEqual(c._drops, {})
        self.assertEqual(c.inventory, [])
        self.assertEqual(len(c._mnt_triggers), 0)
        c.gui.inv.clear.assert_called_once()

    def test_register_campaign_tracks_drops_inventory_and_campaign(self):
        c = _fake_client()
        campaign = _fake_campaign("camp1", ["d1", "d2"])

        Twitch.register_campaign(c, campaign)

        self.assertEqual(set(c._drops), {"d1", "d2"})
        self.assertIn(campaign, c.inventory)
        self.assertIs(c._campaigns["camp1"], campaign)

    def test_get_drop_returns_tracked_or_none(self):
        c = _fake_client()
        sentinel = object()
        c._drops = {"d1": sentinel}
        self.assertIs(Twitch.get_drop(c, "d1"), sentinel)
        self.assertIsNone(Twitch.get_drop(c, "missing"))

    def test_is_exiting_only_true_for_exit_state(self):
        c = _fake_client()
        c._state = State.CHANNEL_SWITCH
        self.assertFalse(Twitch.is_exiting(c))
        c._state = State.EXIT
        self.assertTrue(Twitch.is_exiting(c))

    def test_manual_target_game_property_reads_field(self):
        c = _fake_client()
        game = object()
        c._manual_target_game = game
        self.assertIs(Twitch.manual_target_game.fget(c), game)


class TestMaintenanceTriggers(unittest.TestCase):
    def test_set_maintenance_triggers_sorts_and_trims_past(self):
        c = _fake_client()
        now = datetime.now(UTC)
        past = now - timedelta(minutes=5)
        soon = now + timedelta(minutes=5)
        later = now + timedelta(minutes=10)

        # deliberately unsorted, with one already-past trigger
        Twitch.set_maintenance_triggers(c, [later, past, soon])

        # past trigger dropped, remainder sorted ascending
        self.assertEqual(list(c._mnt_triggers), [soon, later])

    def test_next_maintenance_trigger_pops_earliest_before_bound(self):
        c = _fake_client()
        now = datetime.now(UTC)
        t1 = now + timedelta(minutes=5)
        t2 = now + timedelta(minutes=15)
        c._mnt_triggers = deque([t1, t2])
        bound = now + timedelta(minutes=30)

        result = Twitch.next_maintenance_trigger(c, bound)

        # earliest trigger before the bound is returned and consumed
        self.assertEqual(result, t1)
        self.assertEqual(list(c._mnt_triggers), [t2])

    def test_next_maintenance_trigger_returns_bound_when_none_due(self):
        c = _fake_client()
        now = datetime.now(UTC)
        far = now + timedelta(hours=2)
        c._mnt_triggers = deque([far])
        bound = now + timedelta(minutes=30)

        result = Twitch.next_maintenance_trigger(c, bound)

        # nothing is due before the bound, so the bound itself is returned untouched
        self.assertEqual(result, bound)
        self.assertEqual(list(c._mnt_triggers), [far])


if __name__ == "__main__":
    unittest.main()
