import unittest
from unittest.mock import AsyncMock, MagicMock

from src.config.settings import Settings
from src.web.app import SettingsUpdate
from src.web.managers.settings import SettingsManager


class TestSettingsAPI(unittest.IsolatedAsyncioTestCase):
    def test_settings_update_model(self):
        # Verify model accepts new fields
        update_data = {
            "inventory_filters": {"show_upcoming": True},
            "mining_benefits": {"BADGE": True},
        }
        model = SettingsUpdate(**update_data)
        self.assertEqual(model.inventory_filters, update_data["inventory_filters"])
        self.assertEqual(model.mining_benefits, update_data["mining_benefits"])

    async def test_settings_manager_networking(self):
        # Mock dependencies
        mock_broadcaster = AsyncMock()
        mock_settings = MagicMock(spec=Settings)
        # Initialize mock attributes with default values for comparison
        mock_settings.inventory_filters = {}
        mock_settings.mining_benefits = {}
        mock_settings.games_to_watch = []

        mock_console = MagicMock()
        mock_callback = MagicMock()

        manager = SettingsManager(
            mock_broadcaster, mock_settings, mock_console, on_change=mock_callback
        )

        # 1. Update Inventory Filters (does NOT trigger callback per implementation)
        inv_filters = {"show_upcoming": False}
        manager.update_settings({"inventory_filters": inv_filters})
        mock_callback.assert_not_called()  # inventory_filters has should_trigger_update=False
        self.assertEqual(mock_settings.inventory_filters, inv_filters)
        mock_console.print.assert_called_with(
            "Setting changed: inventory_filters = {'show_upcoming': False}"
        )

        # 2. Update Mining Benefits (SHOULD trigger callback)
        benefits = {"BADGE": False}
        manager.update_settings({"mining_benefits": benefits})
        mock_callback.assert_called_once()
        self.assertEqual(mock_settings.mining_benefits, benefits)
        mock_console.print.assert_called_with("Setting changed: mining_benefits = {'BADGE': False}")
        mock_callback.reset_mock()

        # 3. Update Games to Watch (SHOULD trigger callback)
        games = ["Game 1"]
        manager.update_settings({"games_to_watch": games})
        mock_callback.assert_called_once()

    async def test_set_favorite_drop_toggle(self):
        mock_broadcaster = AsyncMock()
        mock_settings = MagicMock(spec=Settings)
        mock_settings.favorite_drops = []
        mock_console = MagicMock()
        mock_callback = MagicMock()

        manager = SettingsManager(
            mock_broadcaster, mock_settings, mock_console, on_change=mock_callback
        )

        manager.set_favorite_drop("camp1", "drop1", True)
        self.assertEqual(mock_settings.favorite_drops, ["camp1#drop1"])
        mock_callback.assert_called_once()
        mock_settings.save.assert_called_once()
        mock_callback.reset_mock()
        mock_settings.save.reset_mock()

        # Re-applying the same value is a no-op: no save, no broadcast, no restart trigger
        manager.set_favorite_drop("camp1", "drop1", True)
        mock_callback.assert_not_called()
        mock_settings.save.assert_not_called()

        manager.set_favorite_drop("camp1", "drop1", False)
        self.assertEqual(mock_settings.favorite_drops, [])
        mock_callback.assert_called_once()

    async def test_animations_setting_validation(self):
        mock_broadcaster = AsyncMock()
        mock_settings = MagicMock(spec=Settings)
        mock_settings.animations = "auto"
        mock_console = MagicMock()
        mock_callback = MagicMock()

        manager = SettingsManager(
            mock_broadcaster, mock_settings, mock_console, on_change=mock_callback
        )

        # Valid value is applied and does not require a mining-loop restart
        manager.update_settings({"animations": "off"})
        self.assertEqual(mock_settings.animations, "off")
        mock_callback.assert_not_called()

        # Invalid value is ignored, current setting is left untouched
        manager.update_settings({"animations": "bogus"})
        self.assertEqual(mock_settings.animations, "off")
        mock_console.print.assert_called_with("Ignoring unknown animations mode: 'bogus'")

    async def test_notifications_update_does_not_trigger_restart(self):
        mock_broadcaster = AsyncMock()
        mock_settings = MagicMock(spec=Settings)
        mock_settings.notifications = {
            "enabled": False,
            "cooldown_minutes": 15,
            "discord": {
                "enabled": False,
                "bot_token": "",
                "guild_id": "",
                "channel_id": "",
                "events": {
                    "drop_received": True,
                    "unlinked_tracked_game": True,
                    "auth_attention": True,
                    "mining_stalled": True,
                    "new_campaign": True,
                },
            },
        }
        mock_console = MagicMock()
        mock_callback = MagicMock()

        manager = SettingsManager(
            mock_broadcaster, mock_settings, mock_console, on_change=mock_callback
        )

        manager.update_settings(
            {
                "notifications": {
                    "enabled": True,
                    "cooldown_minutes": 30,
                    "discord": {
                        "enabled": True,
                        "bot_token": "secret-token",
                        "guild_id": "",
                        "channel_id": "",
                        "events": mock_settings.notifications["discord"]["events"],
                    },
                }
            }
        )
        # notifications never affect the mining loop
        mock_callback.assert_not_called()
        self.assertEqual(mock_settings.notifications["discord"]["bot_token"], "secret-token")
        # never log the raw bot token
        mock_console.print.assert_called_with(
            "Setting changed: notifications = "
            "{'enabled': True, 'cooldown_minutes': 30, "
            "'discord': {'enabled': True, 'guild_id': '', 'channel_id': '', "
            "'events': {'drop_received': True, 'unlinked_tracked_game': True, "
            "'auth_attention': True, 'mining_stalled': True, 'new_campaign': True}}}"
        )

    async def test_notifications_token_change_clears_guild_and_channel(self):
        mock_broadcaster = AsyncMock()
        mock_settings = MagicMock(spec=Settings)
        mock_settings.notifications = {
            "enabled": True,
            "cooldown_minutes": 15,
            "discord": {
                "enabled": True,
                "bot_token": "old-token",
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
        mock_console = MagicMock()
        manager = SettingsManager(mock_broadcaster, mock_settings, mock_console)

        manager.update_settings(
            {
                "notifications": {
                    "enabled": True,
                    "cooldown_minutes": 15,
                    "discord": {
                        "enabled": True,
                        "bot_token": "new-token",
                        "guild_id": "guild-1",
                        "channel_id": "channel-1",
                        "events": mock_settings.notifications["discord"]["events"],
                    },
                }
            }
        )
        self.assertEqual(mock_settings.notifications["discord"]["bot_token"], "new-token")
        self.assertEqual(mock_settings.notifications["discord"]["guild_id"], "")
        self.assertEqual(mock_settings.notifications["discord"]["channel_id"], "")

    async def test_dark_mode_setting_validation(self):
        mock_broadcaster = AsyncMock()
        mock_settings = MagicMock(spec=Settings)
        mock_settings.dark_mode = "auto"
        mock_console = MagicMock()
        mock_callback = MagicMock()

        manager = SettingsManager(
            mock_broadcaster, mock_settings, mock_console, on_change=mock_callback
        )

        # Valid value is applied and does not require a mining-loop restart
        manager.update_settings({"dark_mode": "on"})
        self.assertEqual(mock_settings.dark_mode, "on")
        mock_callback.assert_not_called()

        # Invalid value is ignored, current setting is left untouched
        manager.update_settings({"dark_mode": "bogus"})
        self.assertEqual(mock_settings.dark_mode, "on")
        mock_console.print.assert_called_with("Ignoring unknown dark_mode mode: 'bogus'")

    async def test_date_format_setting_validation(self):
        mock_broadcaster = AsyncMock()
        mock_settings = MagicMock(spec=Settings)
        mock_settings.date_format = "auto"
        mock_console = MagicMock()
        mock_callback = MagicMock()

        manager = SettingsManager(
            mock_broadcaster, mock_settings, mock_console, on_change=mock_callback
        )

        # Valid value is applied and does not require a mining-loop restart
        manager.update_settings({"date_format": "dmy_dot"})
        self.assertEqual(mock_settings.date_format, "dmy_dot")
        mock_callback.assert_not_called()

        # Invalid value is ignored, current setting is left untouched
        manager.update_settings({"date_format": "bogus"})
        self.assertEqual(mock_settings.date_format, "dmy_dot")
        mock_console.print.assert_called_with("Ignoring unknown date_format: 'bogus'")

    async def test_time_format_setting_validation(self):
        mock_broadcaster = AsyncMock()
        mock_settings = MagicMock(spec=Settings)
        mock_settings.time_format = "auto"
        mock_console = MagicMock()
        mock_callback = MagicMock()

        manager = SettingsManager(
            mock_broadcaster, mock_settings, mock_console, on_change=mock_callback
        )

        # Valid value is applied and does not require a mining-loop restart
        manager.update_settings({"time_format": "24h"})
        self.assertEqual(mock_settings.time_format, "24h")
        mock_callback.assert_not_called()

        # Invalid value is ignored, current setting is left untouched
        manager.update_settings({"time_format": "bogus"})
        self.assertEqual(mock_settings.time_format, "24h")
        mock_console.print.assert_called_with("Ignoring unknown time_format: 'bogus'")

    def test_settings_update_model_accepts_datetime_formats(self):
        model = SettingsUpdate(date_format="iso", time_format="12h")
        self.assertEqual(model.date_format, "iso")
        self.assertEqual(model.time_format, "12h")


if __name__ == "__main__":
    unittest.main()
