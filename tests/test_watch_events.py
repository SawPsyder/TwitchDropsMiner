import base64
import json
import unittest
from unittest.mock import AsyncMock, MagicMock

from src.exceptions import RequestException
from src.models.channel import Channel, Stream


def _decode_spade_events(payload: dict):
    return json.loads(base64.b64decode(payload["data"]).decode("utf8"))


def _make_stream(channel: Channel) -> Stream:
    return Stream(
        channel,
        id=24680,
        game={"id": "13579", "name": "Example Game"},
        viewers=100,
        title="Example Stream",
    )


def _mock_response(status: int) -> MagicMock:
    response = MagicMock()
    response.status = status
    request_cm = MagicMock()
    request_cm.__aenter__ = AsyncMock(return_value=response)
    request_cm.__aexit__ = AsyncMock(return_value=False)
    return request_cm


class TestSpadeWatchEvents(unittest.IsolatedAsyncioTestCase):
    def test_stream_spade_payload_contains_minute_watched_event(self):
        twitch = MagicMock()
        twitch._auth_state.user_id = 12345
        channel = MagicMock(spec=Channel)
        channel.id = 67890
        channel._login = "example_channel"
        channel._twitch = twitch
        stream = _make_stream(channel)

        events = _decode_spade_events(stream._spade_payload)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event"], "minute-watched")
        properties = events[0]["properties"]
        self.assertEqual(properties["broadcast_id"], "24680")
        self.assertEqual(properties["channel_id"], "67890")
        self.assertEqual(properties["channel"], "example_channel")
        self.assertEqual(properties["game"], "Example Game")
        self.assertEqual(properties["game_id"], "13579")
        self.assertEqual(properties["location"], "channel")
        self.assertEqual(properties["player"], "site")
        self.assertEqual(properties["minutes_logged"], 1)
        self.assertEqual(properties["user_id"], 12345)
        self.assertIsInstance(properties["user_id"], int)
        self.assertRegex(properties["client_time"], r"^\d{4}-\d{2}-\d{2}T.*Z$")

    async def test_send_watch_posts_to_spade_url_and_returns_true_for_204(self):
        twitch = MagicMock()
        twitch.gui.channels = MagicMock()
        twitch._auth_state.user_id = 12345
        twitch.request = MagicMock(return_value=_mock_response(204))
        channel = Channel(twitch, id=67890, login="example_channel")
        channel._spade_url = "https://beacon.twitch.tv/track"
        channel._stream = _make_stream(channel)

        result = await channel.send_watch()

        self.assertTrue(result)
        # Assert on what was ACTUALLY sent rather than recomputing _spade_payload:
        # that property regenerates client_time via isonow() on every access, so a
        # byte-for-byte compare races the millisecond boundary and flakes.
        self.assertEqual(twitch.request.call_count, 1)
        args, kwargs = twitch.request.call_args
        self.assertEqual(args, ("POST", "https://beacon.twitch.tv/track"))
        events = _decode_spade_events(kwargs["data"])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event"], "minute-watched")
        properties = events[0]["properties"]
        self.assertEqual(properties["broadcast_id"], "24680")
        self.assertEqual(properties["channel_id"], "67890")
        self.assertEqual(properties["channel"], "example_channel")
        self.assertEqual(properties["user_id"], 12345)
        self.assertRegex(properties["client_time"], r"^\d{4}-\d{2}-\d{2}T.*Z$")

    async def test_send_watch_returns_false_without_stream(self):
        twitch = MagicMock()
        twitch.gui.channels = MagicMock()
        channel = Channel(twitch, id=67890, login="example_channel")

        self.assertFalse(await channel.send_watch())

    async def test_send_watch_returns_false_when_request_fails(self):
        twitch = MagicMock()
        twitch.gui.channels = MagicMock()
        twitch._auth_state.user_id = 12345
        twitch.request = MagicMock(side_effect=RequestException())
        channel = Channel(twitch, id=67890, login="example_channel")
        channel._spade_url = "https://beacon.twitch.tv/track"
        channel._stream = _make_stream(channel)

        self.assertFalse(await channel.send_watch())

    async def test_send_watch_returns_false_for_non_204(self):
        twitch = MagicMock()
        twitch.gui.channels = MagicMock()
        twitch._auth_state.user_id = 12345
        twitch.request = MagicMock(return_value=_mock_response(503))
        channel = Channel(twitch, id=67890, login="example_channel")
        channel._spade_url = "https://beacon.twitch.tv/track"
        channel._stream = _make_stream(channel)

        self.assertFalse(await channel.send_watch())


if __name__ == "__main__":
    unittest.main()
