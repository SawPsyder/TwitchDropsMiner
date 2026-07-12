import asyncio
import unittest
from unittest.mock import MagicMock

from fastapi import HTTPException

from src.web import app as app_module
from src.web.app import FavoriteToggleRequest, toggle_favorite


class TestToggleFavorite(unittest.TestCase):
    """
    Coverage for POST /api/favorites/toggle - specifically that only drops
    earned through watch time (required_minutes > 0) can be marked favorite,
    since favoriting anything else wouldn't affect mining priority at all.
    """

    def setUp(self):
        self.mock_gui = MagicMock()
        self.mock_twitch = MagicMock()
        app_module.gui_manager = self.mock_gui
        app_module.twitch_client = self.mock_twitch

    def tearDown(self):
        app_module.gui_manager = None
        app_module.twitch_client = None

    def _make_campaign_with_drop(self, campaign_id: str, drop_id: str, required_minutes: int):
        drop = MagicMock()
        drop.required_minutes = required_minutes
        campaign = MagicMock()
        campaign.id = campaign_id
        campaign.timed_drops = {drop_id: drop}
        return campaign

    def test_favoriting_a_watch_time_drop_succeeds(self):
        campaign = self._make_campaign_with_drop("c1", "d1", required_minutes=60)
        self.mock_twitch.inventory = [campaign]

        request = FavoriteToggleRequest(campaign_id="c1", drop_id="d1", favorite=True)
        result = asyncio.run(toggle_favorite(request))

        self.assertTrue(result["success"])
        self.mock_gui.settings.set_favorite_drop.assert_called_once_with("c1", "d1", True)

    def test_favoriting_a_non_watch_time_drop_is_rejected(self):
        campaign = self._make_campaign_with_drop("c1", "d1", required_minutes=0)
        self.mock_twitch.inventory = [campaign]

        request = FavoriteToggleRequest(campaign_id="c1", drop_id="d1", favorite=True)
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(toggle_favorite(request))

        self.assertEqual(ctx.exception.status_code, 400)
        self.mock_gui.settings.set_favorite_drop.assert_not_called()

    def test_favoriting_unknown_drop_is_rejected(self):
        self.mock_twitch.inventory = []

        request = FavoriteToggleRequest(campaign_id="missing", drop_id="d1", favorite=True)
        with self.assertRaises(HTTPException):
            asyncio.run(toggle_favorite(request))

        self.mock_gui.settings.set_favorite_drop.assert_not_called()

    def test_unfavoriting_does_not_require_a_watch_time_lookup(self):
        # Un-favoriting should always be allowed (idempotent cleanup), even if
        # the drop can no longer be found (e.g. inventory was refreshed/cleared).
        self.mock_twitch.inventory = []

        request = FavoriteToggleRequest(campaign_id="c1", drop_id="d1", favorite=False)
        result = asyncio.run(toggle_favorite(request))

        self.assertTrue(result["success"])
        self.mock_gui.settings.set_favorite_drop.assert_called_once_with("c1", "d1", False)


if __name__ == "__main__":
    unittest.main()
