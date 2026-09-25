"""
When the next Discord digest should go out.

Daily (1440) and weekly (10080) digests are anchored to a local wall-clock time
so a daylight-saving change doesn't drift the send. Every other interval is
counted from the previous send.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


ANCHORED_DAILY = 1440
ANCHORED_WEEKLY = 10080


def local_timezone() -> tzinfo:
    """The container/host zone. `TZ` wins so tests and Docker can pin it."""
    name = os.environ.get("TZ")
    if name:
        try:
            return ZoneInfo(name)
        except ZoneInfoNotFoundError:
            pass
    current = datetime.now().astimezone().tzinfo
    return current or UTC


def timezone_name() -> str:
    """A display name such as "Europe/Berlin", or a short fallback."""
    zone = local_timezone()
    key = getattr(zone, "key", None)
    if isinstance(key, str) and key:
        return key
    env_name = os.environ.get("TZ")
    if env_name:
        return env_name
    named = datetime.now().astimezone().tzname()
    return named or "UTC"


def parse_send_time(value: object) -> tuple[int, int]:
    """Return (hour, minute) for an "HH:MM" string, or 09:00 when it isn't one."""
    if not isinstance(value, str) or ":" not in value:
        return 9, 0
    hour_text, minute_text = value.strip().split(":", 1)
    if not (hour_text.isdigit() and minute_text.isdigit()):
        return 9, 0
    hour, minute = int(hour_text), int(minute_text)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return 9, 0
    return hour, minute


def _wall_instants(zone: tzinfo, day: datetime, hour: int, minute: int) -> list[datetime]:
    """
    The one UTC instant for `hour:minute` on `day`.

    A repeated hour (the autumn fallback) fires once, on the first occurrence.
    The later one is the same local slot, not a second digest. A skipped hour
    (the spring-forward gap) returns the post-transition instant that clock
    maps onto, so the send still happens that morning.
    """
    for fold in (0, 1):
        candidate = datetime(day.year, day.month, day.day, hour, minute, fold=fold, tzinfo=zone)
        normalized = datetime.fromtimestamp(candidate.timestamp(), zone)
        if (
            normalized.date() == day.date()
            and normalized.hour == hour
            and normalized.minute == minute
            and normalized.fold == fold
        ):
            # First existing fold wins. On a repeated hour that is fold 0,
            # so the later occurrence is not a second send.
            return [candidate.astimezone(UTC)]
    # the wall clock does not exist; use the instant the zone maps it onto
    shifted = datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone)
    return [datetime.fromtimestamp(shifted.timestamp(), UTC)]


def next_digest_at(
    after: datetime,
    interval_minutes: int,
    send_time: str,
    weekday: int,
    tz: tzinfo | None = None,
) -> datetime:
    """
    The first send instant strictly after `after`.

    `weekday` is 0 (Monday) through 6 (Sunday) and is only consulted for the
    weekly interval. The result is timezone-aware UTC.
    """
    if after.tzinfo is None:
        after = after.replace(tzinfo=UTC)
    interval = int(interval_minutes)
    if interval not in (ANCHORED_DAILY, ANCHORED_WEEKLY):
        return after + timedelta(minutes=max(1, interval))

    zone = tz or local_timezone()
    local_after = after.astimezone(zone)
    hour, minute = parse_send_time(send_time)
    day = local_after
    if interval == ANCHORED_WEEKLY:
        # weekday() is Monday=0, the same numbering as digest_send_weekday
        day += timedelta(days=(int(weekday) % 7 - day.weekday()) % 7)
    step = 7 if interval == ANCHORED_WEEKLY else 1
    for _ in range(8):
        for instant in _wall_instants(zone, day, hour, minute):
            if instant > after:
                return instant
        day += timedelta(days=step)
    return after + timedelta(days=step)
