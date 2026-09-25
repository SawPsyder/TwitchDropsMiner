"""
Capture WARNING+ records from the miner into the digest.

Attached to the "TwitchDrops" logger. Records emitted by the notifications
package itself are ignored so a failed Discord send cannot queue another
warning about itself and grow without bound.
"""

from __future__ import annotations

import logging
import threading
import weakref
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from src.notifications.service import NotificationService


NOTIFICATIONS_LOGGER = "TwitchDrops.notifications"
_INSTALL_LOCK = threading.Lock()
_handler: DigestLogHandler | None = None
_emitting = threading.local()


class DigestLogHandler(logging.Handler):
    """Fan a log record out to every live NotificationService."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self._services: weakref.WeakSet[NotificationService] = weakref.WeakSet()

    def add_service(self, service: NotificationService) -> None:
        self._services.add(service)

    def emit(self, record: logging.LogRecord) -> None:
        if getattr(_emitting, "on", False):
            return
        name = record.name
        if name == NOTIFICATIONS_LOGGER or name.startswith(f"{NOTIFICATIONS_LOGGER}."):
            return
        _emitting.on = True
        try:
            for service in list(self._services):
                try:
                    service.record_log_event(record)
                except Exception:
                    # a digest bookkeeping failure must never break the logger
                    continue
        finally:
            _emitting.on = False


def register_service(service: NotificationService) -> None:
    """Attach `service` to the process-wide handler, installing it once."""
    global _handler
    with _INSTALL_LOCK:
        if _handler is None:
            _handler = DigestLogHandler()
            logging.getLogger("TwitchDrops").addHandler(_handler)
        _handler.add_service(service)
