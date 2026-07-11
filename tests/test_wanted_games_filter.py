import unittest
from unittest.mock import MagicMock

from src.models.campaign import DropsCampaign
from src.models.game import Game
from src.services.stream_selector import StreamSelector


class TestWantedGamesFilter(unittest.TestCase):
    def setUp(self):
        # Mock Settings
        self.settings = MagicMock()
        self.settings.games_to_watch = ["Game1", "Game2"]
        self.settings.mining_benefits = {
            "BADGE": True,
            "DIRECT_ENTITLEMENT": True,
        }  # both allowed by default

    def test_filter_wanted_campaigns(self):
        # Setup Campaigns

        # Campaign 1: Game1, Can Earn, Has Wanted Benefits -> Should be selected
        c1 = MagicMock(spec=DropsCampaign)
        c1.game = Game({"id": 1, "name": "Game1"})
        c1.can_earn_within.return_value = True
        c1.id = "123"
        c1.name = "Test Campaign"
        c1.campaign_url = "http://test.url"
        c1.linked = True
        c1.link_url = "http://link.url"
        d1 = MagicMock()
        d1.name = "Test Drop"
        d1.is_claimed = False
        d1.get_wanted_unclaimed_benefits.return_value = ["Benefit1"]
        c1.drops = [d1]
        c1.has_wanted_unclaimed_benefits.side_effect = (
            DropsCampaign.has_wanted_unclaimed_benefits.__get__(c1, DropsCampaign)
        )

        # Campaign 2: Game2, Can Earn, NO Wanted Benefits -> Should NOT be selected
        c2 = MagicMock(spec=DropsCampaign)
        c2.game = Game({"id": 2, "name": "Game2"})
        c2.can_earn_within.return_value = True
        d2 = MagicMock()
        d2.is_claimed = False
        d2.get_wanted_unclaimed_benefits.return_value = []
        c2.drops = [d2]
        c2.has_wanted_unclaimed_benefits.side_effect = (
            DropsCampaign.has_wanted_unclaimed_benefits.__get__(c2, DropsCampaign)
        )

        # Campaign 3: Game3 (Not in games_to_watch), Can Earn, Has Benefits -> Should NOT be selected
        c3 = MagicMock(spec=DropsCampaign)
        c3.game = Game({"id": 3, "name": "Game3"})
        c3.can_earn_within.return_value = True
        d3 = MagicMock()
        d3.is_claimed = False
        d3.get_wanted_unclaimed_benefits.return_value = ["Benefit3"]
        c3.drops = [d3]
        c3.has_wanted_unclaimed_benefits.side_effect = (
            DropsCampaign.has_wanted_unclaimed_benefits.__get__(c3, DropsCampaign)
        )

        # Campaign 4: Game1, Can Earn, Has Claimed Wanted Benefits -> Should NOT be selected
        c4 = MagicMock(spec=DropsCampaign)
        c4.game = Game({"id": 1, "name": "Game1"})
        c4.can_earn_within.return_value = True
        c4.id = "123"
        c4.name = "Test Campaign"
        c4.campaign_url = "http://test.url"
        d4 = MagicMock()
        d4.name = "Test Drop"
        d4.is_claimed = True
        d4.get_wanted_unclaimed_benefits.return_value = ["Benefit4"]
        c4.drops = [d4]
        c4.has_wanted_unclaimed_benefits.side_effect = (
            DropsCampaign.has_wanted_unclaimed_benefits.__get__(c4, DropsCampaign)
        )

        # Campaign 5: Game1, Can Not Earn, Has Wanted Benefits -> Should NOT be selected
        c5 = MagicMock(spec=DropsCampaign)
        c5.game = Game({"id": 1, "name": "Game1"})
        c5.can_earn_within.return_value = False
        c5.id = "123"
        c5.name = "Test Campaign"
        c5.campaign_url = "http://test.url"
        d5 = MagicMock()
        d5.name = "Test Drop"
        d5.is_claimed = False
        d5.get_wanted_unclaimed_benefits.return_value = ["Benefit5"]
        c5.drops = [d5]
        c5.has_wanted_unclaimed_benefits.side_effect = (
            DropsCampaign.has_wanted_unclaimed_benefits.__get__(c5, DropsCampaign)
        )

        inventory = [c1, c2, c3, c4, c5]
        stream_selector = StreamSelector()
        wanted_games = stream_selector.get_wanted_games(self.settings, inventory)

        self.assertEqual(len(wanted_games), 1)
        self.assertEqual(wanted_games[0].name, "Game1")


def _make_campaign(game_id: int, game_name: str, linked: bool = True) -> MagicMock:
    """A campaign with a single earnable, wanted (unclaimed BADGE) drop."""
    campaign = MagicMock(spec=DropsCampaign)
    campaign.id = f"{game_name}_campaign"
    campaign.name = f"{game_name} Campaign"
    campaign.campaign_url = f"http://test.url/{game_name}"
    campaign.game = Game({"id": game_id, "name": game_name})
    campaign.can_earn_within.return_value = True
    campaign.linked = linked
    campaign.link_url = f"http://link.url/{game_name}"
    campaign.expired = False
    drop = MagicMock()
    drop.name = f"{game_name} Drop"
    drop.is_claimed = False
    drop.get_wanted_unclaimed_benefits.return_value = ["Benefit"]
    campaign.drops = [drop]
    return campaign


class TestIdleBehaviorFallback(unittest.TestCase):
    """
    Regression coverage for the idle_behavior.mine_all_when_idle fallback:
    it must trigger whenever the resulting wanted queue is empty, not merely
    when games_to_watch/auto_watch_games happen to be empty lists (games on
    those lists may simply have nothing earnable right now).
    """

    def setUp(self):
        self.settings = MagicMock()
        self.settings.mining_benefits = {"BADGE": True, "DIRECT_ENTITLEMENT": True}
        self.stream_selector = StreamSelector()

    def test_falls_back_to_all_campaigned_games_when_watch_list_is_unproductive(self):
        # games_to_watch names a game with no active campaign; idle fallback
        # should still surface the unrelated game that does have one.
        self.settings.games_to_watch = ["Game1"]
        self.settings.idle_behavior = {"mine_all_when_idle": True}
        inventory = [_make_campaign(2, "Game2")]

        wanted_games = self.stream_selector.get_wanted_games(self.settings, inventory, ["Game1"], [])

        self.assertEqual([g.name for g in wanted_games], ["Game2"])

    def test_does_not_fall_back_when_idle_behavior_disabled(self):
        self.settings.games_to_watch = ["Game1"]
        self.settings.idle_behavior = {"mine_all_when_idle": False}
        inventory = [_make_campaign(2, "Game2")]

        wanted_games = self.stream_selector.get_wanted_games(self.settings, inventory, ["Game1"], [])

        self.assertEqual(wanted_games, [])

    def test_source_tags_manual_auto_and_idle(self):
        self.settings.games_to_watch = ["Game1"]
        self.settings.idle_behavior = {"mine_all_when_idle": True}
        inventory = [_make_campaign(1, "Game1"), _make_campaign(2, "Game2")]

        tree = self.stream_selector.get_wanted_game_tree(
            self.settings, inventory, ["Game1"], ["Game2"]
        )

        sources = {entry["game_name"]: entry["source"] for entry in tree}
        self.assertEqual(sources, {"Game1": "manual", "Game2": "auto"})

    def test_idle_fallback_entries_are_tagged_idle(self):
        self.settings.games_to_watch = ["Game1"]
        self.settings.idle_behavior = {"mine_all_when_idle": True}
        # Game1 (manual) has no campaign, Game3 does -> idle fallback kicks in
        inventory = [_make_campaign(3, "Game3")]

        tree = self.stream_selector.get_wanted_game_tree(self.settings, inventory, ["Game1"], [])

        self.assertEqual(len(tree), 1)
        self.assertEqual(tree[0]["game_name"], "Game3")
        self.assertEqual(tree[0]["source"], "idle")

    def test_idle_preview_shown_behind_active_manual_games_in_tree(self):
        # Game1 (manual) is active/earnable, Game2 has no watch-list entry
        # but does have a campaign -> it should still show up in the display
        # tree, behind Game1, tagged as idle.
        self.settings.games_to_watch = ["Game1"]
        self.settings.idle_behavior = {"mine_all_when_idle": True}
        inventory = [_make_campaign(1, "Game1"), _make_campaign(2, "Game2")]

        tree = self.stream_selector.get_wanted_game_tree(self.settings, inventory, ["Game1"], [])

        self.assertEqual([entry["game_name"] for entry in tree], ["Game1", "Game2"])
        self.assertEqual(tree[0]["source"], "manual")
        self.assertEqual(tree[1]["source"], "idle")

    def test_idle_preview_not_included_in_actual_mining_priority(self):
        # Unlike the display tree, get_wanted_games (channel selection
        # priority) should NOT keep tracking idle-preview games while there's
        # still something active on the manual/auto watch list.
        self.settings.games_to_watch = ["Game1"]
        self.settings.idle_behavior = {"mine_all_when_idle": True}
        inventory = [_make_campaign(1, "Game1"), _make_campaign(2, "Game2")]

        wanted_games = self.stream_selector.get_wanted_games(self.settings, inventory, ["Game1"], [])

        self.assertEqual([g.name for g in wanted_games], ["Game1"])


class TestUnlinkedAutoTrackedTree(unittest.TestCase):
    """
    Coverage for get_unlinked_auto_tracked_tree: the "games awaiting link"
    list surfaced separately from the main wanted queue. Includes both
    manually-watched and auto-tracked (Steam, Ubisoft, ...) games.
    """

    def setUp(self):
        self.settings = MagicMock()
        self.settings.games_to_watch = []
        self.settings.idle_behavior = {"mine_all_when_idle": False}
        self.settings.mining_benefits = {"BADGE": True, "DIRECT_ENTITLEMENT": True}
        self.stream_selector = StreamSelector()

    def test_empty_when_no_games(self):
        inventory = [_make_campaign(1, "Game1", linked=False)]

        tree = self.stream_selector.get_unlinked_auto_tracked_tree(
            self.settings, inventory, manual_games=[], auto_games=[]
        )

        self.assertEqual(tree, [])

    def test_unlinked_auto_game_is_surfaced(self):
        inventory = [_make_campaign(1, "Game1", linked=False)]

        tree = self.stream_selector.get_unlinked_auto_tracked_tree(
            self.settings, inventory, manual_games=[], auto_games=["Game1"]
        )

        self.assertEqual(len(tree), 1)
        self.assertEqual(tree[0]["game_name"], "Game1")
        self.assertEqual(tree[0]["source"], "auto")
        campaigns = tree[0]["campaigns"]
        self.assertEqual(len(campaigns), 1)
        self.assertFalse(campaigns[0]["linked"])
        self.assertEqual(campaigns[0]["link_url"], "http://link.url/Game1")

    def test_unlinked_manual_game_is_surfaced(self):
        inventory = [_make_campaign(1, "Game1", linked=False)]

        tree = self.stream_selector.get_unlinked_auto_tracked_tree(
            self.settings, inventory, manual_games=["Game1"], auto_games=[]
        )

        self.assertEqual(len(tree), 1)
        self.assertEqual(tree[0]["game_name"], "Game1")
        self.assertEqual(tree[0]["source"], "manual")

    def test_manual_games_listed_before_auto_games(self):
        inventory = [
            _make_campaign(1, "AutoGame", linked=False),
            _make_campaign(2, "ManualGame", linked=False),
        ]

        tree = self.stream_selector.get_unlinked_auto_tracked_tree(
            self.settings, inventory, manual_games=["ManualGame"], auto_games=["AutoGame"]
        )

        self.assertEqual([entry["game_name"] for entry in tree], ["ManualGame", "AutoGame"])
        self.assertEqual(tree[0]["source"], "manual")
        self.assertEqual(tree[1]["source"], "auto")

    def test_game_in_both_lists_is_not_duplicated(self):
        inventory = [_make_campaign(1, "Game1", linked=False)]

        tree = self.stream_selector.get_unlinked_auto_tracked_tree(
            self.settings, inventory, manual_games=["Game1"], auto_games=["Game1"]
        )

        self.assertEqual(len(tree), 1)
        self.assertEqual(tree[0]["source"], "manual")

    def test_linked_auto_game_is_excluded(self):
        inventory = [_make_campaign(1, "Game1", linked=True)]

        tree = self.stream_selector.get_unlinked_auto_tracked_tree(
            self.settings, inventory, manual_games=[], auto_games=["Game1"]
        )

        self.assertEqual(tree, [])

    def test_expired_unlinked_campaign_is_excluded(self):
        campaign = _make_campaign(1, "Game1", linked=False)
        campaign.expired = True

        tree = self.stream_selector.get_unlinked_auto_tracked_tree(
            self.settings, campaigns=[campaign], manual_games=[], auto_games=["Game1"]
        )

        self.assertEqual(tree, [])

    def test_unlinked_non_badge_campaign_still_surfaced(self):
        # Regression test: DropsCampaign.eligible (and can_earn_within, used
        # by the main wanted queue) is False for an unlinked campaign that
        # doesn't grant a badge/emote (e.g. an in-game item drop, like EVE
        # Online's). This list must bypass that check - that's the whole
        # point of a "link me" prompt.
        campaign = _make_campaign(1, "Game1", linked=False)
        campaign.can_earn_within.return_value = False

        tree = self.stream_selector.get_unlinked_auto_tracked_tree(
            self.settings, campaigns=[campaign], manual_games=[], auto_games=["Game1"]
        )

        self.assertEqual(len(tree), 1)
        self.assertEqual(tree[0]["game_name"], "Game1")
        self.assertFalse(tree[0]["campaigns"][0]["linked"])

    def test_only_unlinked_campaigns_kept_when_game_has_both(self):
        linked_campaign = _make_campaign(1, "Game1", linked=True)
        unlinked_campaign = _make_campaign(1, "Game1", linked=False)
        unlinked_campaign.id = "unlinked_campaign"
        unlinked_campaign.name = "Unlinked Campaign"
        inventory = [linked_campaign, unlinked_campaign]

        tree = self.stream_selector.get_unlinked_auto_tracked_tree(
            self.settings, inventory, manual_games=[], auto_games=["Game1"]
        )

        self.assertEqual(len(tree), 1)
        campaigns = tree[0]["campaigns"]
        self.assertEqual(len(campaigns), 1)
        self.assertEqual(campaigns[0]["name"], "Unlinked Campaign")


if __name__ == "__main__":
    unittest.main()
