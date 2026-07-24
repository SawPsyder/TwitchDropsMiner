import asyncio
import unittest
from unittest.mock import MagicMock

from src.utils import parse_version
from src.web.managers.broadcaster import WebSocketBroadcaster
from src.web.managers.status import WebsocketStatusManager


class TestParseVersion(unittest.TestCase):
    def test_orders_numerically_not_lexically(self):
        # the whole point: string compare gets this backwards ("1" < "9")
        self.assertGreater(parse_version("10.0.0"), parse_version("9.0.0"))
        self.assertGreater(parse_version("1.7.10"), parse_version("1.7.9"))

    def test_strips_v_prefix(self):
        self.assertEqual(parse_version("v1.7.1"), parse_version("1.7.1"))

    def test_equal_versions(self):
        self.assertEqual(parse_version("1.7.1"), (1, 7, 1))

    def test_handles_prerelease_suffix(self):
        # pre-release/build suffixes are stripped, so a pre-release compares equal
        # to its base release. Acceptable for the update-check use-case (it just
        # won't flag "1.8.0" as newer than a running "1.8.0-rc1").
        self.assertEqual(parse_version("1.7.1-rc2"), (1, 7, 1))
        self.assertEqual(parse_version("1.8.0-rc1"), parse_version("1.8.0"))

    def test_empty_or_garbage(self):
        self.assertEqual(parse_version(""), ())
        self.assertEqual(parse_version("garbage"), ())


class TestBroadcasterEmitSoon(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    async def _drain(broadcaster):
        # await the scheduled task, then yield once more so its done-callbacks
        # (which discard it from _pending) get to run.
        while broadcaster._pending:
            await asyncio.gather(*broadcaster._pending, return_exceptions=True)
            await asyncio.sleep(0)

    async def test_emit_soon_schedules_and_awaits_emit(self):
        sio = MagicMock()

        async def fake_emit(event, data):
            fake_emit.calls.append((event, data))

        fake_emit.calls = []
        sio.emit = fake_emit

        broadcaster = WebSocketBroadcaster()
        broadcaster.set_socketio(sio)
        broadcaster.emit_soon("hello", {"n": 1})

        # task is tracked (strong ref held) so it isn't GC'd mid-flight
        self.assertEqual(len(broadcaster._pending), 1)
        await self._drain(broadcaster)
        self.assertEqual(fake_emit.calls, [("hello", {"n": 1})])
        self.assertEqual(len(broadcaster._pending), 0)  # cleaned up after done

    async def test_emit_soon_swallows_and_logs_exceptions(self):
        broadcaster = WebSocketBroadcaster()
        sio = MagicMock()

        async def boom(event, data):
            raise RuntimeError("emit failed")

        sio.emit = boom
        broadcaster.set_socketio(sio)

        # should not raise into the caller, and should not leave a dangling task
        broadcaster.emit_soon("evt", {})
        await self._drain(broadcaster)
        self.assertEqual(len(broadcaster._pending), 0)


class TestWebsocketStatusRemove(unittest.TestCase):
    def _manager(self):
        broadcaster = MagicMock(spec=WebSocketBroadcaster)
        return WebsocketStatusManager(broadcaster), broadcaster

    def test_remove_drops_shard_and_broadcasts(self):
        mgr, broadcaster = self._manager()
        mgr.update(0, status="Connected", topics=10)
        mgr.update(1, status="Connected", topics=5)
        broadcaster.emit_soon.reset_mock()

        mgr.remove(0)

        self.assertNotIn(0, mgr._websockets)
        event, payload = broadcaster.emit_soon.call_args.args
        self.assertEqual(event, "websocket_removed")
        self.assertEqual(payload["idx"], 0)
        self.assertEqual(payload["total_websockets"], 1)
        self.assertEqual(payload["total_topics"], 5)  # only shard 1's topics remain

    def test_remove_unknown_shard_is_noop(self):
        mgr, broadcaster = self._manager()
        mgr.update(0, status="Connected", topics=3)
        broadcaster.emit_soon.reset_mock()

        mgr.remove(99)  # never tracked

        broadcaster.emit_soon.assert_not_called()
        self.assertIn(0, mgr._websockets)


if __name__ == "__main__":
    unittest.main()
