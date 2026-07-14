import unittest
from unittest.mock import MagicMock

from src.core.client import Twitch
from src.models.benefit import Benefit, BenefitType
from src.models.campaign import DropsCampaign
from src.models.drop import TimedDrop
from src.models.game import Game
from src.web.gui_manager import WebGUIManager


class TestWantedItems(unittest.TestCase):
    def setUp(self):
        # Mock Twitch Client
        self.twitch = MagicMock(spec=Twitch)
        self.twitch.settings = MagicMock()
        # idle_behavior preview isn't what these filtering tests exercise -
        # keep it off so extra games with campaigns don't show up unexpectedly
        self.twitch.settings.idle_behavior = {"mine_all_when_idle": False}
        self.twitch.settings.favorite_drops = []
        self.twitch.get_change_state_callable.return_value = lambda: None
        # the effective watch list combines user picks with library auto-detected games
        self.twitch.auto_watch_games = []
        self.twitch.get_effective_watch_list = lambda: (
            self.twitch.settings.games_to_watch + self.twitch.auto_watch_games
        )

        # Mock dependencies created in __init__
        # We can't easily mock internal creation of sub-managers without patching,
        # but for get_wanted_tree we only need self._twitch.settings and self._twitch.inventory

        # However, WebGUIManager __init__ calls self.twitch.state_change

        self.gui = WebGUIManager(self.twitch)
        # Suppress broadcaster
        self.gui._broadcaster = MagicMock()

    def test_get_wanted_tree(self):
        # Setup Settings
        self.twitch.settings.games_to_watch = ["Game1", "Game2"]
        self.twitch.settings.mining_benefits = {"BADGE": True, "DIRECT_ENTITLEMENT": False}

        # Setup Inventory

        # Campaign 1: Game1, Drop with BADGE (Wanted)
        c1 = MagicMock(spec=DropsCampaign)
        c1.id = "c1_id"
        c1.name = "Campaign1"
        c1.campaign_url = "http://url1"
        c1.game = Game({"id": 1, "name": "Game1", "boxArtURL": "http://img1"})
        c1.can_earn_within.return_value = True
        c1.linked = True
        c1.link_url = "http://link1"

        d1 = MagicMock(spec=TimedDrop)
        d1.name = "Drop1"
        d1.is_claimed = False
        d1.required_minutes = 30
        d1.get_wanted_unclaimed_benefits = TimedDrop.get_wanted_unclaimed_benefits.__get__(
            d1, TimedDrop
        )
        b1 = MagicMock(spec=Benefit)
        b1.name = "Badge1"
        b1.type = BenefitType.BADGE
        b1.image_url = "http://benefit1.img"
        b1.is_wanted = Benefit.is_wanted.__get__(b1, Benefit)
        d1.benefits = [b1]
        c1.drops = [d1]

        # Campaign 2: Game2, Drop with DIRECT_ENTITLEMENT (Unwanted)
        c2 = MagicMock(spec=DropsCampaign)
        c2.id = "c2_id"
        c2.name = "Campaign2"
        c2.campaign_url = "http://url2"
        c2.game = Game({"id": 2, "name": "Game2", "boxArtURL": "http://img2"})
        c2.can_earn_within.return_value = True
        c2.linked = True
        c2.link_url = "http://link2"

        d2 = MagicMock(spec=TimedDrop)
        d2.name = "Drop2"
        d2.is_claimed = False
        d2.required_minutes = 30
        d2.get_wanted_unclaimed_benefits = TimedDrop.get_wanted_unclaimed_benefits.__get__(
            d2, TimedDrop
        )
        b2 = MagicMock(spec=Benefit)
        b2.name = "Item1"
        b2.type = BenefitType.DIRECT_ENTITLEMENT
        b2.image_url = "http://benefit2.img"
        b2.is_wanted = Benefit.is_wanted.__get__(b2, Benefit)
        d2.benefits = [b2]
        c2.drops = [d2]

        # Campaign 3: Game3 (Not in watch list), Drop with BADGE (Wanted but wrong game)
        c3 = MagicMock(spec=DropsCampaign)
        c3.id = "c3_id"
        c3.name = "Campaign3"
        c3.campaign_url = "http://url3"
        c3.game = Game({"id": 3, "name": "Game3", "boxArtURL": "http://img3"})
        c3.can_earn_within.return_value = True
        c3.linked = True
        c3.link_url = "http://link3"

        d3 = MagicMock(spec=TimedDrop)
        d3.name = "Drop3"
        d3.is_claimed = False
        d3.required_minutes = 30
        d3.get_wanted_unclaimed_benefits = TimedDrop.get_wanted_unclaimed_benefits.__get__(
            d3, TimedDrop
        )
        b3 = MagicMock(spec=Benefit)
        b3.name = "Badge2"
        b3.type = BenefitType.BADGE
        b3.image_url = "http://benefit3.img"
        b3.is_wanted = Benefit.is_wanted.__get__(b3, Benefit)
        d3.benefits = [b3]
        c3.drops = [d3]

        # Campaign 4: Game1, Drop with BADGE, can't earn (Wanted)
        c4 = MagicMock(spec=DropsCampaign)
        c4.id = "c4_id"
        c4.name = "Campaign4"
        c4.campaign_url = "http://url4"
        c4.game = Game({"id": 1, "name": "Game1", "boxArtURL": "http://img1"})
        c4.can_earn_within.return_value = False
        c4.linked = True
        c4.link_url = "http://link4"

        d4 = MagicMock(spec=TimedDrop)
        d4.name = "Drop4"
        d4.is_claimed = False
        d4.required_minutes = 30
        d4.get_wanted_unclaimed_benefits = TimedDrop.get_wanted_unclaimed_benefits.__get__(
            d4, TimedDrop
        )
        b4 = MagicMock(spec=Benefit)
        b4.name = "Badge1"
        b4.type = BenefitType.BADGE
        b4.image_url = "http://benefit4.img"
        b4.is_wanted = Benefit.is_wanted.__get__(b4, Benefit)
        d4.benefits = [b4]
        c4.drops = [d4]

        self.twitch.inventory = [c1, c2, c3, c4]

        # Execute
        result = self.gui.get_wanted_game_tree()
        print(result)

        # Verify
        # Expected: Game1 only
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["game_name"], "Game1")
        self.assertEqual(result[0]["game_icon"], "http://img1")

        campaigns = result[0]["campaigns"]
        self.assertEqual(len(campaigns), 1)
        self.assertEqual(campaigns[0]["name"], "Campaign1")
        self.assertEqual(len(campaigns[0]["drops"]), 1)
        self.assertEqual(campaigns[0]["drops"][0]["name"], "Drop1")
        self.assertEqual(
            campaigns[0]["drops"][0]["benefits"],
            [{"name": "Badge1", "type": "BADGE", "image_url": "http://benefit1.img"}],
        )

    def test_get_wanted_tree_claimed_filtering(self):
        # Setup Settings
        self.twitch.settings.games_to_watch = ["Game1"]
        self.twitch.settings.mining_benefits = {"BADGE": True}

        # Setup Inventory
        # Drop is claimed -> Should be hidden
        c1 = MagicMock(spec=DropsCampaign)
        c1.id = "c1_id"
        c1.name = "Campaign1"
        c1.campaign_url = "http://url1"
        c1.game = Game({"id": 1, "name": "Game1", "boxArtURL": "http://img1"})
        c1.linked = True
        c1.link_url = "http://link1"

        d1 = MagicMock(spec=TimedDrop)
        d1.name = "Drop1"
        d1.is_claimed = True
        d1.required_minutes = 30
        b1 = MagicMock(spec=Benefit)
        b1.name = "Badge1"
        b1.type = BenefitType.BADGE
        d1.benefits = [b1]
        c1.drops = [d1]

        self.twitch.inventory = [c1]

        # Execute
        result = self.gui.get_wanted_game_tree()

        # Verify
        self.assertEqual(len(result), 0)


class TestUnlinkedAutoTrackedItems(unittest.TestCase):
    def setUp(self):
        self.twitch = MagicMock(spec=Twitch)
        self.twitch.settings = MagicMock()
        self.twitch.settings.idle_behavior = {"mine_all_when_idle": False}
        self.twitch.get_change_state_callable.return_value = lambda: None
        self.twitch.settings.games_to_watch = []
        self.twitch.settings.mining_benefits = {"BADGE": True, "DIRECT_ENTITLEMENT": True}
        self.twitch.settings.favorite_drops = []

        self.gui = WebGUIManager(self.twitch)
        self.gui._broadcaster = MagicMock()

    def _make_campaign(
        self, game_name: str, linked: bool, can_earn_within: bool = True
    ) -> MagicMock:
        campaign = MagicMock(spec=DropsCampaign)
        campaign.id = f"{game_name}_campaign"
        campaign.name = f"{game_name} Campaign"
        campaign.campaign_url = f"http://test.url/{game_name}"
        campaign.game = Game({"id": 1, "name": game_name, "boxArtURL": "http://img"})
        campaign.can_earn_within.return_value = can_earn_within
        campaign.linked = linked
        campaign.link_url = f"http://link.url/{game_name}"
        campaign.expired = False

        drop = MagicMock(spec=TimedDrop)
        drop.name = f"{game_name} Drop"
        drop.is_claimed = False
        drop.required_minutes = 30
        drop.get_wanted_unclaimed_benefits = TimedDrop.get_wanted_unclaimed_benefits.__get__(
            drop, TimedDrop
        )
        benefit = MagicMock(spec=Benefit)
        benefit.name = "Badge1"
        benefit.type = BenefitType.BADGE
        benefit.image_url = "http://benefit.img"
        benefit.is_wanted = Benefit.is_wanted.__get__(benefit, Benefit)
        drop.benefits = [benefit]
        campaign.drops = [drop]
        return campaign

    def test_unlinked_auto_tracked_game_is_surfaced(self):
        self.twitch.auto_watch_games = ["Game1"]
        self.twitch.inventory = [self._make_campaign("Game1", linked=False)]

        result = self.gui.get_unlinked_auto_tracked_items()

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["game_name"], "Game1")
        self.assertEqual(result[0]["source"], "auto")
        self.assertFalse(result[0]["campaigns"][0]["linked"])

    def test_unlinked_manual_game_is_surfaced(self):
        self.twitch.settings.games_to_watch = ["Game1"]
        self.twitch.auto_watch_games = []
        self.twitch.inventory = [self._make_campaign("Game1", linked=False)]

        result = self.gui.get_unlinked_auto_tracked_items()

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["game_name"], "Game1")
        self.assertEqual(result[0]["source"], "manual")

    def test_manual_and_auto_games_combined_manual_first_no_duplicates(self):
        self.twitch.settings.games_to_watch = ["ManualGame", "SharedGame"]
        self.twitch.auto_watch_games = ["SharedGame", "AutoGame"]
        self.twitch.inventory = [
            self._make_campaign("ManualGame", linked=False),
            self._make_campaign("SharedGame", linked=False),
            self._make_campaign("AutoGame", linked=False),
        ]

        result = self.gui.get_unlinked_auto_tracked_items()

        self.assertEqual(
            [entry["game_name"] for entry in result], ["ManualGame", "SharedGame", "AutoGame"]
        )
        self.assertEqual(
            [entry["source"] for entry in result], ["manual", "manual", "auto"]
        )

    def test_linked_games_are_excluded(self):
        self.twitch.auto_watch_games = ["Game1"]
        self.twitch.inventory = [self._make_campaign("Game1", linked=True)]

        result = self.gui.get_unlinked_auto_tracked_items()

        self.assertEqual(result, [])

    def test_unlinked_non_badge_campaign_still_surfaced(self):
        # Regression test: DropsCampaign.eligible (and therefore
        # can_earn_within, used by the main wanted queue) is False for an
        # unlinked campaign that doesn't grant a badge/emote (e.g. an
        # in-game item drop, like EVE Online's). This list must still
        # surface it - that's the whole point of a "link me" prompt.
        self.twitch.auto_watch_games = ["Game1"]
        self.twitch.inventory = [
            self._make_campaign("Game1", linked=False, can_earn_within=False)
        ]

        result = self.gui.get_unlinked_auto_tracked_items()

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["game_name"], "Game1")
        self.assertFalse(result[0]["campaigns"][0]["linked"])


if __name__ == "__main__":
    unittest.main()
