"""WebSocket broadcaster for real-time updates to web clients."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from socketio import AsyncServer


logger = logging.getLogger("TwitchDrops")


class WebSocketBroadcaster:
    """Manages broadcasting messages to all connected web clients via Socket.IO.

    This class acts as a central hub for sending real-time updates from the application
    to all connected browser clients through Socket.IO events.
    """

    def __init__(self):
        self._sio: AsyncServer | None = None  # Will be set by webapp
        # Strong references to in-flight emit tasks scheduled via emit_soon, so
        # they aren't garbage-collected mid-flight (see asyncio.create_task docs).
        self._pending: set[asyncio.Task[Any]] = set()

    def set_socketio(self, sio: AsyncServer):
        """Set the Socket.IO server instance for broadcasting."""
        self._sio = sio

    async def emit(self, event: str, data: Any):
        """Emit an event to all connected clients.

        Args:
            event: The event name to emit
            data: The data payload to send with the event
        """
        if self._sio:
            await self._sio.emit(event, data)

    def emit_soon(self, event: str, data: Any) -> None:
        """Schedule an emit from sync code as a tracked background task.

        Prefer this over a bare ``asyncio.create_task(broadcaster.emit(...))``:
        the raw idiom drops any exception raised inside ``emit`` (it only
        surfaces as a "Task exception was never retrieved" warning at GC) and
        lets the loop garbage-collect the task before it finishes. Here we keep
        a strong reference until completion and log any failure.
        """
        task = asyncio.create_task(self.emit(event, data))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)
        task.add_done_callback(self._log_task_exception)

    @staticmethod
    def _log_task_exception(task: asyncio.Task[Any]) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error("Failed to broadcast web GUI event", exc_info=exc)
