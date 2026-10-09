import asyncio
import base64
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp

from src.config.constants import WATCH_INTERVAL
from src.exceptions import GQLException, RequestException
from src.models.channel import Channel, Stream
from src.services.watch_service import WatchService


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


def _mock_response(status: int, text: str = "") -> MagicMock:
    response = MagicMock()
    response.status = status
    response.text = AsyncMock(return_value=text)
    request_cm = MagicMock()
    request_cm.__aenter__ = AsyncMock(return_value=response)
    request_cm.__aexit__ = AsyncMock(return_value=False)
    return request_cm


STREAM_PLAYLIST_URL = "https://video-weaver.example/playlist.m3u8"
PLAYLIST = "\n".join(
    [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        "#EXT-X-TARGETDURATION:2",
        "#EXTINF:2.000,",
        "https://cdn.example/chunk1.ts",
        "#EXTINF:2.000,",
        "https://cdn.example/chunk2.ts",
        "#EXT-X-ENDLIST",
        "not-a-url",
        "https://cdn.example/chunk3.ts",
    ]
)
CHUNK_URLS = [
    "https://cdn.example/chunk1.ts",
    "https://cdn.example/chunk2.ts",
    "https://cdn.example/chunk3.ts",
]


def _channel_with_stream() -> tuple[MagicMock, Channel]:
    twitch = MagicMock()
    twitch.gui.channels = MagicMock()
    twitch._auth_state.user_id = 12345
    channel = Channel(twitch, id=67890, login="example_channel")
    channel._spade_url = "https://beacon.twitch.tv/track"
    channel._stream = _make_stream(channel)
    return twitch, channel


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

    async def test_send_watch_spade_posts_to_spade_url_and_returns_true_for_204(self):
        twitch = MagicMock()
        twitch.gui.channels = MagicMock()
        twitch._auth_state.user_id = 12345
        twitch.request = MagicMock(return_value=_mock_response(204))
        channel = Channel(twitch, id=67890, login="example_channel")
        channel._spade_url = "https://beacon.twitch.tv/track"
        channel._stream = _make_stream(channel)

        result = await channel._send_watch_spade()

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

    async def test_send_watch_spade_returns_false_when_request_fails(self):
        twitch = MagicMock()
        twitch.gui.channels = MagicMock()
        twitch._auth_state.user_id = 12345
        twitch.request = MagicMock(side_effect=RequestException())
        channel = Channel(twitch, id=67890, login="example_channel")
        channel._spade_url = "https://beacon.twitch.tv/track"
        channel._stream = _make_stream(channel)

        self.assertFalse(await channel._send_watch_spade())

    async def test_send_watch_spade_returns_false_for_non_204(self):
        twitch = MagicMock()
        twitch.gui.channels = MagicMock()
        twitch._auth_state.user_id = 12345
        twitch.request = MagicMock(return_value=_mock_response(503))
        channel = Channel(twitch, id=67890, login="example_channel")
        channel._spade_url = "https://beacon.twitch.tv/track"
        channel._stream = _make_stream(channel)

        self.assertFalse(await channel._send_watch_spade())


class TestPlaylistWatch(unittest.IsolatedAsyncioTestCase):
    def _route(self, playlist_status: int, playlist_body: str, head_statuses: list[int]):
        head_urls: list[str] = []
        methods: list[str] = []

        def request(method, url, **kwargs):
            methods.append(method)
            if method == "GET":
                self.assertEqual(url, STREAM_PLAYLIST_URL)
                self.assertEqual(kwargs.get("headers"), {"Connection": "close"})
                return _mock_response(playlist_status, playlist_body)
            if method == "HEAD":
                head_urls.append(url)
                return _mock_response(head_statuses[len(head_urls) - 1])
            raise AssertionError(f"unexpected {method} {url}")

        return request, head_urls, methods

    async def test_send_watch_heads_every_http_chunk_in_order(self):
        twitch, channel = _channel_with_stream()
        channel._stream._stream_url = STREAM_PLAYLIST_URL
        request, head_urls, methods = self._route(200, PLAYLIST, [200, 200, 200])
        twitch.request = MagicMock(side_effect=request)

        self.assertTrue(await channel.send_watch())
        self.assertEqual(head_urls, CHUNK_URLS)
        self.assertEqual(methods, ["GET", "HEAD", "HEAD", "HEAD"])
        self.assertNotIn("POST", methods)

    async def test_send_watch_returns_false_on_first_non_200_head(self):
        twitch, channel = _channel_with_stream()
        channel._stream._stream_url = STREAM_PLAYLIST_URL
        request, head_urls, methods = self._route(200, PLAYLIST, [200, 404, 200])
        twitch.request = MagicMock(side_effect=request)

        self.assertFalse(await channel.send_watch())
        self.assertEqual(head_urls, CHUNK_URLS[:2])
        self.assertNotIn("POST", methods)

    async def test_send_watch_returns_false_when_stream_url_is_missing(self):
        twitch, channel = _channel_with_stream()
        channel._stream.get_stream_url = AsyncMock(return_value=None)
        twitch.request = MagicMock()

        self.assertFalse(await channel.send_watch())
        twitch.request.assert_not_called()

    async def test_send_watch_returns_false_when_playlist_url_is_expired(self):
        twitch, channel = _channel_with_stream()
        channel._stream._stream_url = STREAM_PLAYLIST_URL
        request, head_urls, methods = self._route(404, "", [])
        twitch.request = MagicMock(side_effect=request)

        self.assertFalse(await channel.send_watch())
        self.assertEqual(methods, ["GET"])
        self.assertEqual(head_urls, [])

    async def test_send_watch_returns_false_and_logs_playlist_error(self):
        cases = (
            '{"error": "expired"}',
            '[{"error": "expired"}]',
        )
        for body in cases:
            with self.subTest(body=body):
                twitch, channel = _channel_with_stream()
                channel._stream._stream_url = STREAM_PLAYLIST_URL
                request, head_urls, _methods = self._route(200, body, [])
                twitch.request = MagicMock(side_effect=request)

                with self.assertLogs("TwitchDrops", level="ERROR") as logs:
                    result = await channel.send_watch()

                self.assertFalse(result)
                self.assertEqual(head_urls, [])
                self.assertIn('Send watch error: "expired"', "\n".join(logs.output))

    async def test_send_watch_returns_false_when_stream_url_raises_gql(self):
        _twitch, channel = _channel_with_stream()
        assert channel._stream is not None
        channel._stream.get_stream_url = AsyncMock(side_effect=GQLException("playback token failed"))

        with self.assertLogs("TwitchDrops", level="WARNING") as logs:
            result = await channel.send_watch()

        self.assertFalse(result)
        self.assertIn("Stream URL fetch failed", "\n".join(logs.output))

    async def test_send_watch_returns_false_when_playback_token_is_null(self):
        twitch, channel = _channel_with_stream()
        assert channel._stream is not None
        self.assertIsNone(channel._stream._stream_url)
        twitch.gql_request = AsyncMock(return_value={"data": {"streamPlaybackAccessToken": None}})

        with self.assertLogs("TwitchDrops", level="WARNING") as logs:
            result = await channel.send_watch()

        self.assertFalse(result)
        logged = "\n".join(logs.output)
        self.assertIn("Stream URL fetch failed", logged)
        self.assertIn("NoneType", logged)
        twitch.request.assert_not_called()

    async def test_failed_playlist_get_drops_cached_stream_url(self):
        cases = (
            ("status", 403, None),
            ("error", None, aiohttp.ClientConnectionError()),
        )
        for label, status, error in cases:
            with self.subTest(label=label):
                twitch, channel = _channel_with_stream()
                assert channel._stream is not None
                channel._stream._stream_url = STREAM_PLAYLIST_URL
                if error is not None:
                    twitch.request = MagicMock(side_effect=error)
                else:
                    request, head_urls, _methods = self._route(status or 0, "", [])
                    twitch.request = MagicMock(side_effect=request)

                self.assertFalse(await channel.send_watch())
                self.assertIsNone(channel._stream._stream_url)
                if error is None:
                    self.assertEqual(head_urls, [])


class TestWatchLoopTiming(unittest.IsolatedAsyncioTestCase):
    def test_watch_interval_is_20_seconds(self):
        self.assertEqual(WATCH_INTERVAL.total_seconds(), 20)

    async def test_watch_loop_sleeps_fifteen_minus_send_duration(self):
        for send_duration in (0.0, 4.0, 15.0, 22.5):
            with self.subTest(send_duration=send_duration):
                await self._assert_progress_sleep(send_duration)

    async def _assert_progress_sleep(self, send_duration: float) -> None:
        clock = {"now": 1_000.0}

        def fake_time() -> float:
            return clock["now"]

        channel = MagicMock()
        channel.online = True
        channel.name = "example_channel"

        async def send_watch() -> bool:
            clock["now"] += send_duration
            return True

        channel.send_watch = send_watch

        twitch = MagicMock()

        async def get_channel():
            return channel

        twitch.watching_channel.get = get_channel
        service = WatchService(twitch)
        sleeps: list[float] = []

        async def fake_sleep(delay: float) -> None:
            sleeps.append(delay)
            raise asyncio.CancelledError

        with (
            patch("src.services.watch_service.time", fake_time),
            patch("src.services.watch_service.asyncio.sleep", fake_sleep),
            self.assertRaises(asyncio.CancelledError),
        ):
            await service.watch_loop()

        self.assertEqual(sleeps, [15 - min(send_duration, 15)])


if __name__ == "__main__":
    unittest.main()
