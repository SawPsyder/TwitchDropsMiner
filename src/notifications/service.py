"""
Notification service.

Decides *when* to fire a notification. Immediate mode delivers one message per
event, with a per-(provider, event type) cooldown that drops repeats. Digest
mode queues every enabled event and a scheduler posts one summary; the same
cooldown only throttles the urgent alerts that also go out immediately.
Provider failures are isolated here - a broken notification integration must
never affect mining.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import logging
import threading
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from src.config import NOTIFICATIONS_STATE_PATH
from src.notifications.base import NotificationError, NotificationProvider
from src.notifications.digest_style import ERROR_GROUP_CAP, QUEUE_CAP
from src.notifications.discord import (
    MAX_RETRY_AFTER_SECONDS,
    DiscordProvider,
    clamp_retry_seconds,
)
from src.notifications.events import NotificationEvent
from src.notifications.logging_handler import NOTIFICATIONS_LOGGER, register_service
from src.notifications.render import render_digest
from src.notifications.schedule import local_timezone, next_digest_at, timezone_name
from src.utils import json_save
from src.version import __version__


if TYPE_CHECKING:
    from src.config.settings import Settings
    from src.models.campaign import DropsCampaign


logger = logging.getLogger(NOTIFICATIONS_LOGGER)

URGENT_EVENTS = frozenset({"auth_attention", "mining_stalled"})
RETRY_BACKOFF = timedelta(minutes=5)
# coalesce a burst of queue/log writes into one disk save
STATE_SAVE_DELAY = 2.0
# shutdown must return inside Docker's 10s stop grace even if Discord hangs
STOP_TIMEOUT = 2.0


def _idle_progress() -> dict[str, Any]:
    return {
        "state": "idle",
        "channel": None,
        "game": None,
        "stalled_since": None,
        "campaigns": [],
    }


def _empty_state() -> dict[str, Any]:
    # a fresh dict per call - a shared module-level dict would alias its nested
    # containers across every instance that hasn't persisted a state file yet
    return {
        "last_sent": {},
        "seen_unlinked": [],
        "unlinked_seeded": False,
        "seen_campaigns": [],
        "campaigns_seeded": False,
        "digest_queue": [],
        "digest_window_start": None,
        "digest_next_at": None,
        "digest_dropped": 0,
        "digest_error_groups": {},
        "digest_error_overflow_types": 0,
        "digest_error_overflow_count": 0,
        "digest_flush_pending": False,
        "digest_seq": 0,
        "last_digest": None,
    }


def _load_state(path: Path) -> dict[str, Any]:
    """Load the state file. A corrupt file is renamed aside and replaced with empty state."""
    empty = _empty_state()
    if not path.exists():
        return empty
    try:
        import json

        with path.open(encoding="utf8") as handle:
            raw = json.load(handle)
        if not isinstance(raw, dict):
            raise ValueError("notifications state is not an object")
    except Exception:
        logger.warning(
            "Corrupt notifications state at %s; backing it up and starting fresh", path
        )
        backup = path.with_name(path.name + ".corrupt")
        try:
            path.replace(backup)
        except OSError:
            logger.warning("Could not back up corrupt notifications state %s", path)
        return empty
    for key, value in empty.items():
        raw.setdefault(key, value)
    if not isinstance(raw.get("digest_queue"), list):
        raw["digest_queue"] = []
    if not isinstance(raw.get("digest_error_groups"), dict):
        raw["digest_error_groups"] = {}
    if not isinstance(raw.get("last_sent"), dict):
        raw["last_sent"] = {}
    dropped_bad = False
    clean_queue: list[Any] = []
    for item in raw["digest_queue"]:
        if isinstance(item, dict) and isinstance(item.get("type"), str):
            clean_queue.append(item)
        else:
            dropped_bad = True
    raw["digest_queue"] = clean_queue
    clean_groups: dict[str, Any] = {}
    for key, group in raw["digest_error_groups"].items():
        if isinstance(key, str) and isinstance(group, dict):
            clean_groups[key] = group
        else:
            dropped_bad = True
    raw["digest_error_groups"] = clean_groups
    for key in (
        "digest_dropped",
        "digest_error_overflow_types",
        "digest_error_overflow_count",
    ):
        raw[key] = _coerce_count(raw.get(key))
    _ensure_queue_seqs(raw)
    _clamp_stored_retry(raw)
    if dropped_bad:
        logger.warning("Dropped invalid items from the notifications digest queue")
    return raw


def _ensure_queue_seqs(raw: dict[str, Any]) -> None:
    """Give every queued event a stable seq and keep the counter ahead of them."""
    counter = _coerce_count(raw.get("digest_seq"))
    for item in raw["digest_queue"]:
        seq = item.get("seq")
        if isinstance(seq, bool) or not isinstance(seq, int) or seq <= 0:
            counter += 1
            item["seq"] = counter
        elif seq > counter:
            counter = seq
    raw["digest_seq"] = counter


def _clamp_stored_retry(raw: dict[str, Any]) -> None:
    """A persisted retry_at more than an hour ahead is pulled back to that cap."""
    last = raw.get("last_digest")
    if not isinstance(last, dict):
        return
    retry_raw = last.get("retry_at")
    if not isinstance(retry_raw, str) or not retry_raw:
        return
    try:
        stamp = datetime.fromisoformat(retry_raw)
    except ValueError:
        return
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    cap = datetime.now(UTC) + timedelta(seconds=MAX_RETRY_AFTER_SECONDS)
    if stamp > cap:
        last["retry_at"] = cap.isoformat()


def _coerce_count(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return 0
    try:
        return max(0, int(value))
    except ValueError:
        return 0


def short_discord_error(exc: NotificationError) -> str:
    """A short phrase safe to show in the UI and inside a digest. Never the raw exception."""
    if exc.retry_after is not None or "429" in str(exc):
        return "Discord rate limit"
    text = str(exc)
    if "401" in text or "403" in text:
        return "Discord rejected the bot"
    if "404" in text:
        return "Discord channel not found"
    if "connection" in text.lower():
        return "Couldn't reach Discord"
    return "Discord request failed"


def _bound_loop(obj: object) -> asyncio.AbstractEventLoop | None:
    """Loop an asyncio primitive bound itself to, without touching it."""
    loop = getattr(obj, "_loop", None)
    if isinstance(loop, asyncio.AbstractEventLoop):
        return loop
    return None


class NotificationService:
    """Fires outbound notifications for mining events across all providers."""

    def __init__(self, settings: Settings, state_path: Path = NOTIFICATIONS_STATE_PATH) -> None:
        self._settings = settings
        self._state_path = state_path
        self._providers: list[NotificationProvider] = [DiscordProvider(settings)]
        self._state: dict[str, Any] = _load_state(state_path)
        self._last_errors: dict[str, str] = {}
        self._progress_provider: Any = None
        self._digest_task: asyncio.Task[None] | None = None
        self._mode_switch_task: asyncio.Task[bool] | None = None
        self._sending = False
        # one lock for the scheduler, the mode-switch flush, and the preview
        self._send_lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._state_lock = threading.RLock()
        self._dirty = False
        self._write_generation = 0
        self._write_lock = asyncio.Lock()
        self._write_now = asyncio.Event()
        self._writer_task: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        register_service(self)

    @property
    def notification_settings(self) -> dict[str, Any]:
        return cast("dict[str, Any]", self._settings.notifications)

    @property
    def enabled(self) -> bool:
        return bool(self.notification_settings.get("enabled", False))

    @property
    def mode(self) -> str:
        mode = self.notification_settings.get("mode", "immediate")
        return mode if mode in ("immediate", "digest") else "immediate"

    @property
    def cooldown(self) -> timedelta:
        minutes = self._cooldown_minutes()
        return timedelta(minutes=minutes)

    def _cooldown_minutes(self) -> int:
        try:
            minutes = int(self.notification_settings.get("cooldown_minutes", 15))
        except (TypeError, ValueError):
            minutes = 15
        return max(0, min(1440, minutes))

    def _interval_minutes(self) -> int:
        try:
            minutes = int(self.notification_settings.get("digest_interval_minutes", 1440))
        except (TypeError, ValueError):
            minutes = 1440
        return max(60, min(10080, minutes))

    def _sections(self) -> dict[str, bool]:
        raw = self.notification_settings.get("digest_sections") or {}
        if not isinstance(raw, dict):
            raw = {}
        return {
            "progress": bool(raw.get("progress", True)),
            "errors": bool(raw.get("errors", True)),
        }

    @property
    def urgent_immediate(self) -> bool:
        return bool(self.notification_settings.get("digest_urgent_immediate", True))

    @property
    def send_empty(self) -> bool:
        return bool(self.notification_settings.get("digest_send_empty", False))

    def set_progress_provider(self, provider: Any) -> None:
        """Callable returning the mining snapshot folded into a digest at send time."""
        self._progress_provider = provider

    def _mark_dirty(self) -> None:
        with self._state_lock:
            self._dirty = True
            self._write_generation += 1
        self._schedule_save()

    def _running_loop(self) -> asyncio.AbstractEventLoop | None:
        """The loop this service writes on.

        The first running loop wins. A closed loop is not replaced: a log
        record from a later loop must not attach a writer to primitives that
        are still bound to the dead one.
        """
        owned = self._loop
        if owned is not None:
            if owned.is_closed() or not owned.is_running():
                return None
            return owned
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return None
        if loop.is_running():
            self._loop = loop
            return loop
        return None

    def _schedule_save(self) -> None:
        """Coalesce a burst of edits onto the one tracked writer."""
        loop = self._running_loop()
        if loop is None:
            if self._loop is not None and (
                self._loop.is_closed() or not self._loop.is_running()
            ):
                # Drop the task reference so a finished service can be collected
                # instead of handling logs on whatever loop is current.
                self._writer_task = None
                return
            self._write_state_now()
            return

        def arm() -> None:
            self._rebind_writer(loop)
            task = self._writer_task
            if task is None or task.done():
                self._writer_task = loop.create_task(
                    self._debounced_write(), name="notification-state"
                )

        try:
            current = asyncio.get_running_loop()
        except RuntimeError:
            current = None
        if current is loop:
            arm()
        else:
            loop.call_soon_threadsafe(arm)

    def _rebind_writer(self, loop: asyncio.AbstractEventLoop) -> None:
        """Replace writer primitives left bound to a different loop."""
        if _bound_loop(self._write_now) not in (None, loop):
            self._write_now = asyncio.Event()
        if _bound_loop(self._write_lock) not in (None, loop):
            self._write_lock = asyncio.Lock()
        task = self._writer_task
        if task is None or task.done():
            return
        try:
            task_loop = task.get_loop()
        except RuntimeError:
            task_loop = None
        if task_loop is not loop:
            self._writer_task = None

    async def _debounced_write(self) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._write_now.wait(), timeout=STATE_SAVE_DELAY)
        self._write_now.clear()
        await self._write_latest()

    def _write_payload(self, payload: dict[str, Any]) -> bool:
        try:
            if not self._state_path.parent.exists():
                return False
            json_save(self._state_path, payload)
        except OSError:
            with self._state_lock:
                self._dirty = True
            logger.warning("Could not save notifications state to %s", self._state_path)
            return False
        return True

    def _write_state_now(self) -> None:
        with self._state_lock:
            if not self._dirty:
                return
            generation = self._write_generation
            payload = copy.deepcopy(self._state)
            self._dirty = False
        if self._write_payload(payload):
            return
        with self._state_lock:
            if self._write_generation == generation:
                self._dirty = True

    async def _write_latest(self) -> None:
        """Write the newest state. An older in-flight payload cannot finish last."""
        async with self._write_lock:
            while True:
                with self._state_lock:
                    generation = self._write_generation
                    if not self._dirty:
                        return
                    payload = copy.deepcopy(self._state)
                    self._dirty = False
                wrote = await asyncio.to_thread(self._write_payload, payload)
                with self._state_lock:
                    if not wrote:
                        return
                    if self._write_generation == generation and not self._dirty:
                        return
                    self._dirty = True

    async def flush_pending_state(self) -> None:
        """Write the latest state now. Used before a send and from stop()."""
        self._rebind_writer(asyncio.get_running_loop())
        task = self._writer_task
        if task is not None and not task.done() and task is not asyncio.current_task():
            self._write_now.set()
            await task
            return
        await self._write_latest()

    @staticmethod
    def _cooldown_key(provider_name: str, event_type: str) -> str:
        return f"{provider_name}:{event_type}"

    def _in_cooldown(self, provider_name: str, event_type: str) -> bool:
        last_sent = cast("dict[str, Any]", self._state.setdefault("last_sent", {}))
        raw = last_sent.get(self._cooldown_key(provider_name, event_type))
        if not raw:
            return False
        try:
            sent_at = datetime.fromisoformat(raw)
        except ValueError:
            return False
        if sent_at.tzinfo is None:
            sent_at = sent_at.replace(tzinfo=UTC)
        return datetime.now(UTC) - sent_at < self.cooldown

    def _mark_sent(self, provider_name: str, event_type: str) -> None:
        last_sent = cast("dict[str, Any]", self._state.setdefault("last_sent", {}))
        last_sent[self._cooldown_key(provider_name, event_type)] = (
            datetime.now(UTC).isoformat()
        )

    def _providers_for(self, event_type: str) -> list[NotificationProvider]:
        return [
            provider
            for provider in self._providers
            if provider.enabled and provider.event_enabled(event_type)
        ]

    def _discord_can_send(self) -> bool:
        provider = self.get_provider("discord")
        return (
            provider is not None
            and provider.enabled
            and provider.is_configured
            and self.enabled
        )

    async def notify(
        self,
        event_type: str,
        title: str,
        description: str,
        *,
        data: dict[str, Any] | None = None,
    ) -> None:
        """
        Deliver a notification to every enabled provider that has this event
        type turned on.

        Immediate mode applies the cooldown and drops repeats. Digest mode
        queues every event (no cooldown loss). Urgent events in digest mode
        are also sent immediately, and that immediate send still respects the
        cooldown.
        """
        if not self.enabled:
            return
        event = NotificationEvent(
            type=event_type,
            ts=datetime.now(UTC),
            data=dict(data) if data is not None else {"title": title, "description": description},
        )
        await self._dispatch(event, title, description)

    async def _dispatch(self, event: NotificationEvent, title: str, description: str) -> None:
        providers = self._providers_for(event.type)
        if not providers:
            return
        if self.mode == "digest":
            alerted = False
            if event.type in URGENT_EVENTS and self.urgent_immediate:
                alerted = await self._send_immediate(providers, event.type, title, description)
            event.data["alerted"] = alerted
            self._enqueue(event)
            return
        await self._send_immediate(providers, event.type, title, description)

    async def _send_immediate(
        self,
        providers: list[NotificationProvider],
        event_type: str,
        title: str,
        description: str,
    ) -> bool:
        """Send now, honouring cooldown. Returns True when at least one provider accepted it."""
        sent_any = False
        state_changed = False
        for provider in providers:
            if self._in_cooldown(provider.name, event_type):
                continue
            try:
                await provider.send(event_type, title, description)
            except NotificationError as exc:
                logger.warning(
                    "Notification failed for %s/%s: %s", provider.name, event_type, exc
                )
                self._last_errors[provider.name] = str(exc)
                continue
            self._last_errors.pop(provider.name, None)
            self._mark_sent(provider.name, event_type)
            sent_any = True
            state_changed = True
        if state_changed:
            self._mark_dirty()
        return sent_any

    def _allocate_seq(self) -> int:
        seq = _coerce_count(self._state.get("digest_seq")) + 1
        self._state["digest_seq"] = seq
        return seq

    def _enqueue(self, event: NotificationEvent) -> None:
        with self._state_lock:
            queue = cast("list[dict[str, Any]]", self._state.setdefault("digest_queue", []))
            item = event.to_dict()
            item["seq"] = self._allocate_seq()
            queue.append(item)
            dropped = 0
            while len(queue) > QUEUE_CAP:
                queue.pop(0)
                dropped += 1
            if dropped:
                self._state["digest_dropped"] = (
                    int(self._state.get("digest_dropped") or 0) + dropped
                )
            if not self._state.get("digest_window_start"):
                self._state["digest_window_start"] = datetime.now(UTC).isoformat()
            self._ensure_next_at()
        self._mark_dirty()

    def record_log_event(self, record: logging.LogRecord) -> None:
        """Group a WARNING+ log record into the open digest window."""
        if self.mode != "digest" or not self.enabled:
            return
        if not self._sections().get("errors", True):
            return
        template = record.msg if isinstance(record.msg, str) else str(record.msg)
        try:
            latest = record.getMessage()
        except Exception:
            latest = template
        groups = cast("dict[str, Any]", self._state.setdefault("digest_error_groups", {}))
        now = datetime.now(UTC).isoformat()
        level = "ERROR" if record.levelno >= logging.ERROR else "WARNING"
        existing = groups.get(template)
        if existing is None or not isinstance(existing, dict):
            if len(groups) >= ERROR_GROUP_CAP:
                self._state["digest_error_overflow_types"] = (
                    int(self._state.get("digest_error_overflow_types") or 0) + 1
                )
                self._state["digest_error_overflow_count"] = (
                    int(self._state.get("digest_error_overflow_count") or 0) + 1
                )
            else:
                groups[template] = {
                    "level": level,
                    "count": 1,
                    "latest": latest,
                    "last_ts": now,
                }
        else:
            existing["count"] = int(existing.get("count") or 0) + 1
            existing["latest"] = latest
            existing["last_ts"] = now
            if level == "ERROR":
                existing["level"] = "ERROR"
        if not self._state.get("digest_window_start"):
            self._state["digest_window_start"] = now
        self._mark_dirty()

    def _error_totals(self) -> tuple[int, int]:
        """(occurrences, overflow occurrences). Zero when the errors section is off."""
        if not self._sections().get("errors", True):
            return 0, 0
        groups = self._state.get("digest_error_groups") or {}
        occurrences = 0
        if isinstance(groups, dict):
            occurrences = sum(
                int(group.get("count") or 0)
                for group in groups.values()
                if isinstance(group, dict)
            )
        overflow = int(self._state.get("digest_error_overflow_count") or 0)
        return occurrences, overflow

    def queued_count(self) -> int:
        queue = self._state.get("digest_queue") or []
        occurrences, overflow = self._error_totals()
        return len(queue) + occurrences + overflow

    def has_digest_content(self) -> bool:
        """True when the window has anything other than a progress snapshot."""
        if self._state.get("digest_queue"):
            return True
        occurrences, overflow = self._error_totals()
        return occurrences + overflow > 0

    def _parse_stamp(self, value: object) -> datetime | None:
        if not isinstance(value, str) or not value:
            return None
        try:
            stamp = datetime.fromisoformat(value)
        except ValueError:
            return None
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=UTC)
        return stamp

    def _retry_at(self) -> datetime | None:
        last = self._state.get("last_digest")
        if not isinstance(last, dict):
            return None
        return self._parse_stamp(last.get("retry_at"))

    def _ensure_next_at(self) -> None:
        if self._parse_stamp(self._state.get("digest_next_at")) is not None:
            return
        self._state["digest_next_at"] = self._compute_next(datetime.now(UTC)).isoformat()

    def _compute_next(self, after: datetime) -> datetime:
        settings = self.notification_settings
        return next_digest_at(
            after,
            self._interval_minutes(),
            str(settings.get("digest_send_time") or "09:00"),
            int(settings.get("digest_send_weekday") or 0),
            tz=local_timezone(),
        )

    def seconds_until_next_digest(self) -> float:
        """How long the scheduler should sleep. 0 means a digest is due now."""
        now = datetime.now(UTC)
        pending = bool(self._state.get("digest_flush_pending"))
        if self.mode != "digest" and not pending:
            return 30.0
        if not self._discord_can_send():
            return 60.0
        retry_at = self._retry_at()
        if pending:
            if retry_at is not None and retry_at > now:
                return (retry_at - now).total_seconds()
            return 0.0
        self._ensure_next_at()
        next_at = self._parse_stamp(self._state.get("digest_next_at")) or now
        if retry_at is not None and retry_at > next_at:
            next_at = retry_at
        return max(0.0, (next_at - now).total_seconds())

    def _progress_snapshot(self) -> dict[str, Any]:
        if self._progress_provider is None:
            return _idle_progress()
        try:
            snapshot = self._progress_provider()
        except Exception:
            logger.exception("Failed to read the mining progress snapshot")
            return _idle_progress()
        if not isinstance(snapshot, dict):
            return _idle_progress()
        return snapshot

    def _render(
        self, *, preview: bool, final: bool = False, now: datetime | None = None
    ) -> list[dict[str, Any]]:
        now = now or datetime.now(UTC)
        window_start = self._parse_stamp(self._state.get("digest_window_start")) or now
        # A preview shows the send already on the clock. A digest going out now
        # announces the one after it. Leaving digest mode has no following send.
        stored_next = self._parse_stamp(self._state.get("digest_next_at"))
        if final:
            next_at = None
        elif preview and stored_next is not None and stored_next > now:
            next_at = stored_next
        else:
            next_at = self._compute_next(now)
        groups = self._state.get("digest_error_groups") or {}
        group_list = [dict(group) for group in groups.values()] if isinstance(groups, dict) else []
        sections = self._sections()
        return render_digest(
            events=list(self._state.get("digest_queue") or []),
            error_groups=group_list,
            error_overflow_types=int(self._state.get("digest_error_overflow_types") or 0),
            error_overflow_count=int(self._state.get("digest_error_overflow_count") or 0),
            window_start=window_start,
            window_end=now,
            next_at=next_at,
            interval_minutes=self._interval_minutes(),
            dropped_count=int(self._state.get("digest_dropped") or 0),
            progress=self._progress_snapshot(),
            include_progress=sections.get("progress", True),
            include_errors=sections.get("errors", True),
            version=__version__,
            preview=preview,
        )

    def _window_snapshot(self) -> dict[str, Any]:
        """The queue and counts a send is about to consume. Later arrivals are not in it."""
        with self._state_lock:
            queue = list(self._state.get("digest_queue") or [])
            groups = copy.deepcopy(self._state.get("digest_error_groups") or {})
            seqs = [
                item["seq"]
                for item in queue
                if isinstance(item, dict) and isinstance(item.get("seq"), int)
            ]
            return {
                "queue_seqs": seqs,
                "groups": groups if isinstance(groups, dict) else {},
                "dropped": _coerce_count(self._state.get("digest_dropped")),
                "overflow_types": _coerce_count(self._state.get("digest_error_overflow_types")),
                "overflow_count": _coerce_count(self._state.get("digest_error_overflow_count")),
            }

    def _release_snapshot(self, snapshot: dict[str, Any], render_time: datetime) -> None:
        """Drop only what this send rendered. Events that arrived during the POST stay."""
        with self._state_lock:
            sent = {
                seq
                for seq in snapshot["queue_seqs"]
                if isinstance(seq, int) and not isinstance(seq, bool)
            }
            queue = self._state.get("digest_queue") or []
            self._state["digest_queue"] = [
                item
                for item in queue
                if not (isinstance(item, dict) and item.get("seq") in sent)
            ]
            groups = self._state.get("digest_error_groups")
            if not isinstance(groups, dict):
                groups = {}
                self._state["digest_error_groups"] = groups
            for key, snap in snapshot["groups"].items():
                current = groups.get(key)
                if not isinstance(current, dict) or not isinstance(snap, dict):
                    continue
                remaining = _coerce_count(current.get("count")) - _coerce_count(snap.get("count"))
                if remaining <= 0:
                    groups.pop(key, None)
                else:
                    current["count"] = remaining
            self._state["digest_dropped"] = max(
                0, _coerce_count(self._state.get("digest_dropped")) - snapshot["dropped"]
            )
            self._state["digest_error_overflow_types"] = max(
                0,
                _coerce_count(self._state.get("digest_error_overflow_types"))
                - snapshot["overflow_types"],
            )
            self._state["digest_error_overflow_count"] = max(
                0,
                _coerce_count(self._state.get("digest_error_overflow_count"))
                - snapshot["overflow_count"],
            )
            self._state["digest_window_start"] = render_time.isoformat()
            self._state["digest_flush_pending"] = False
            self._state["digest_next_at"] = self._compute_next(render_time).isoformat()

    def reschedule(self) -> None:
        """
        Recompute the next send from the current window and wake the scheduler.

        Called when the mode, interval, send time or weekday is saved, so a new
        schedule applies without waiting out the previously computed slot.
        """
        now = datetime.now(UTC)
        anchor = self._parse_stamp(self._state.get("digest_window_start"))
        if anchor is None:
            last = self._state.get("last_digest")
            if isinstance(last, dict):
                anchor = self._parse_stamp(last.get("sent_at")) or self._parse_stamp(last.get("at"))
        if anchor is None or anchor > now:
            anchor = now
        nxt = self._compute_next(anchor)
        if nxt < now:
            nxt = now
        self._state["digest_next_at"] = nxt.isoformat()
        self._mark_dirty()
        self._wake.set()

    def _record_digest(
        self,
        *,
        ok: bool,
        error: str | None = None,
        retry_at: datetime | None = None,
        skipped: bool = False,
    ) -> None:
        previous = self._state.get("last_digest")
        sent_at = None
        if isinstance(previous, dict):
            sent_at = previous.get("sent_at")
        now = datetime.now(UTC).isoformat()
        if ok and not skipped:
            sent_at = now
        self._state["last_digest"] = {
            "at": now,
            "ok": ok,
            "error": error,
            "retry_at": retry_at.isoformat() if retry_at is not None else None,
            "skipped": skipped,
            "sent_at": sent_at,
        }

    def _clear_window(self) -> None:
        now = datetime.now(UTC)
        self._state["digest_queue"] = []
        self._state["digest_dropped"] = 0
        self._state["digest_error_groups"] = {}
        self._state["digest_error_overflow_types"] = 0
        self._state["digest_error_overflow_count"] = 0
        self._state["digest_window_start"] = now.isoformat()
        self._state["digest_flush_pending"] = False
        self._state["digest_next_at"] = self._compute_next(now).isoformat()

    async def flush_digest(self, *, final: bool = False) -> bool:
        """
        Send the queued digest. Only the snapshotted seqs are removed after a 2xx.

        `final` is the digest posted when the user leaves digest mode. It does
        not announce a next digest. Shutdown does not call this. An empty window
        is skipped unless digest_send_empty is on.
        """
        async with self._send_lock:
            return await self._flush_digest_locked(final=final)

    async def _flush_digest_locked(self, *, final: bool) -> bool:
        pending = bool(self._state.get("digest_flush_pending"))
        if self.mode != "digest" and not final and not pending:
            return False
        if not self._discord_can_send():
            return False
        retry_at = self._retry_at()
        if retry_at is not None and retry_at > datetime.now(UTC):
            return False
        # another sender may have drained the queue while this call waited
        if not self.has_digest_content() and not self.send_empty:
            # a flush that lost the race to one that just sent must not record a
            # skip over that success; the window it left behind is already in the future
            next_at = self._parse_stamp(self._state.get("digest_next_at"))
            if next_at is not None and next_at > datetime.now(UTC) + timedelta(seconds=1):
                return False
            self._record_digest(ok=True, skipped=True)
            self._clear_window()
            self._mark_dirty()
            await self.flush_pending_state()
            return False
        provider = self.get_provider("discord")
        if not isinstance(provider, DiscordProvider):
            return False
        await self.flush_pending_state()
        render_time = datetime.now(UTC)
        snapshot = self._window_snapshot()
        embeds = self._render(preview=False, final=final, now=render_time)
        self._sending = True
        try:
            await provider.send_digest(embeds)
        except NotificationError as exc:
            raw_delay = (
                exc.retry_after if exc.retry_after is not None else RETRY_BACKOFF.total_seconds()
            )
            delay = clamp_retry_seconds(raw_delay)
            if delay is None:
                delay = RETRY_BACKOFF.total_seconds()
            retry = datetime.now(UTC) + timedelta(seconds=delay)
            self._record_digest(ok=False, error=short_discord_error(exc), retry_at=retry)
            self._last_errors[provider.name] = short_discord_error(exc)
            logger.warning("Discord digest failed: %s", exc)
            self._mark_dirty()
            await self.flush_pending_state()
            return False
        else:
            self._last_errors.pop(provider.name, None)
            self._record_digest(ok=True)
            self._release_snapshot(snapshot, render_time)
            self._mark_dirty()
            await self.flush_pending_state()
            return True
        finally:
            self._sending = False

    async def send_preview(self) -> None:
        """
        Post the digest the current *saved* queue would produce, without clearing it.

        Raises:
            NotificationError: Discord is not configured or rejected the message.
        """
        async with self._send_lock:
            provider = self.get_provider("discord")
            if not isinstance(provider, DiscordProvider) or not provider.is_configured:
                raise NotificationError("Discord: bot token and channel must be configured")
            embeds = self._render(preview=True)
            await provider.send_digest(embeds)

    def schedule_mode_switch_flush(self) -> None:
        """Queue the final digest after a save that left digest mode with events waiting."""
        if not self.has_digest_content():
            return
        self._state["digest_flush_pending"] = True
        self._mark_dirty()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._mode_switch_task = loop.create_task(self.flush_digest(final=True))

    def start(self) -> None:
        """Start the digest scheduler. A next_at already in the past sends on the first pass."""
        if self._digest_task is not None and not self._digest_task.done():
            return
        self._digest_task = asyncio.create_task(self._digest_loop(), name="notification-digest")

    async def stop(self) -> None:
        """Cancel in-flight sends and persist the queue. Does not post a digest.

        A missed slot is sent when the scheduler starts again. Every await is
        bounded so a hung Discord call cannot hold shutdown past Docker's stop grace.
        """
        pending: list[asyncio.Task[Any]] = []
        task = self._digest_task
        self._digest_task = None
        if task is not None:
            task.cancel()
            pending.append(task)
        mode_task = self._mode_switch_task
        self._mode_switch_task = None
        if mode_task is not None and not mode_task.done():
            mode_task.cancel()
            pending.append(mode_task)
        if pending:
            await asyncio.wait(pending, timeout=STOP_TIMEOUT)
        self._rebind_writer(asyncio.get_running_loop())
        writer = self._writer_task
        if writer is not None and not writer.done():
            self._write_now.set()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(asyncio.shield(writer), timeout=STOP_TIMEOUT)
        else:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._write_latest(), timeout=STOP_TIMEOUT)

    async def _digest_loop(self) -> None:
        while True:
            try:
                self._wake.clear()
                delay = self.seconds_until_next_digest()
                if delay > 0:
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(self._wake.wait(), timeout=delay)
                # a settings save wakes the loop early; only send when it is due
                if (
                    self.seconds_until_next_digest() > 0
                    and not self._state.get("digest_flush_pending")
                ):
                    continue
                await self.flush_digest(final=bool(self._state.get("digest_flush_pending")))
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Discord digest scheduler failed")
                await asyncio.sleep(RETRY_BACKOFF.total_seconds())

    def get_provider(self, name: str) -> NotificationProvider | None:
        """Look up a registered provider by name (e.g. "discord")."""
        return next((provider for provider in self._providers if provider.name == name), None)

    async def send_test(self, provider_name: str = "discord") -> None:
        """
        Send a one-off test message through a provider, bypassing the
        enabled/event-type/cooldown gating - this is an explicit user action
        from the settings UI, not a mining event.
        """
        provider = self.get_provider(provider_name)
        if provider is None:
            raise NotificationError(f"Unknown notification provider: {provider_name}")
        await provider.send(
            "test",
            "Test notification",
            "This is a test notification from Twitch Drops Miner.",
        )

    # convenience wrappers ---------------------------------------------------

    async def notify_drop_received(
        self,
        game_name: str,
        benefits: Iterable[str],
        *,
        campaign: str = "",
        drop_name: str = "",
        channel: str = "",
    ) -> None:
        benefit_list = [str(benefit) for benefit in benefits]
        benefit_text = ", ".join(benefit_list) or "a drop"
        await self.notify(
            "drop_received",
            "Drop received",
            f"Claimed **{benefit_text}** for *{game_name}*.",
            data={
                "game": game_name,
                "campaign": campaign,
                "drop": drop_name,
                "benefits": benefit_list,
                "channel": channel or "inventory",
            },
        )

    async def notify_unlinked_tracked_game(self, game_name: str, campaign_name: str) -> None:
        await self.notify(
            "unlinked_tracked_game",
            "Unlinked tracked game",
            f'*{game_name}* has an active campaign ("{campaign_name}") but its Twitch'
            f" account isn't linked yet - link it to start earning.",
            data={"game": game_name, "campaign": campaign_name},
        )

    async def notify_auth_attention(self, reason: str) -> None:
        await self.notify(
            "auth_attention",
            "Auth needs attention",
            reason,
            data={"reason": reason},
        )

    async def notify_mining_stalled(self, reason: str) -> None:
        await self.notify(
            "mining_stalled",
            "Mining stalled",
            reason,
            data={"reason": reason},
        )

    async def notify_new_campaign(
        self,
        game_name: str,
        campaign_name: str,
        *,
        starts_at: datetime | None = None,
        ends_at: datetime | None = None,
    ) -> None:
        await self.notify(
            "new_campaign",
            "New campaign available",
            f'*{game_name}*: a new campaign ("{campaign_name}") just became available.',
            data={
                "game": game_name,
                "campaign": campaign_name,
                "starts_at": starts_at.isoformat() if starts_at is not None else None,
                "ends_at": ends_at.isoformat() if ends_at is not None else None,
            },
        )

    # diff-against-previous-state helpers ------------------------------------

    async def track_unlinked_tracked_games(self, tree: list[dict[str, Any]]) -> None:
        """
        Diff the unlinked-auto-tracked tree (StreamSelector.
        get_unlinked_auto_tracked_tree) against what was last seen and notify
        about genuinely new (game, campaign) pairs. The first call ever seeds
        the baseline silently, so a restart doesn't re-report games that were
        already unlinked beforehand.
        """
        seen = set(cast("list[str]", self._state.get("seen_unlinked", [])))
        is_first_run = not self._state.get("unlinked_seeded", False)
        current: set[str] = set()
        new_entries: list[tuple[str, str]] = []
        for game_entry in tree:
            game_name = str(game_entry.get("game_name", ""))
            for campaign_entry in game_entry.get("campaigns", []):
                key = f"{game_name}::{campaign_entry.get('id', '')}"
                current.add(key)
                if key not in seen and not is_first_run:
                    new_entries.append((game_name, str(campaign_entry.get("name", ""))))
        self._state["seen_unlinked"] = sorted(current)
        self._state["unlinked_seeded"] = True
        self._mark_dirty()
        for game_name, campaign_name in new_entries:
            await self.notify_unlinked_tracked_game(game_name, campaign_name)

    async def track_new_campaigns(
        self, campaigns: Iterable[DropsCampaign], games_to_watch: Iterable[str]
    ) -> None:
        """
        Diff active campaigns for watched games against what was last seen and
        notify about genuinely new ones. Seeds silently on first call, same as
        track_unlinked_tracked_games. The seen-set is updated every call
        regardless of whether notifications are enabled, so turning the
        setting on later doesn't dump a backlog of "new" campaigns.
        """
        watch_set = {name.casefold() for name in games_to_watch}
        seen = set(cast("list[str]", self._state.get("seen_campaigns", [])))
        is_first_run = not self._state.get("campaigns_seeded", False)
        current: set[str] = set()
        new_entries: list[tuple[str, str, datetime | None, datetime | None]] = []
        for campaign in campaigns:
            if campaign.game.name.casefold() not in watch_set:
                continue
            current.add(campaign.id)
            if campaign.id not in seen and not is_first_run:
                new_entries.append(
                    (
                        campaign.game.name,
                        campaign.name,
                        getattr(campaign, "starts_at", None),
                        getattr(campaign, "ends_at", None),
                    )
                )
        self._state["seen_campaigns"] = sorted(current)
        self._state["campaigns_seeded"] = True
        self._mark_dirty()
        for game_name, campaign_name, starts_at, ends_at in new_entries:
            await self.notify_new_campaign(
                game_name, campaign_name, starts_at=starts_at, ends_at=ends_at
            )

    def get_status(self) -> dict[str, Any]:
        """Current notification config/connection status for the web GUI."""
        providers: dict[str, Any] = {}
        for provider in self._providers:
            providers[provider.name] = {
                "enabled": bool(provider.provider_settings.get("enabled", False)),
                "configured": provider.is_configured,
                "last_error": self._last_errors.get(provider.name),
            }
        last = self._state.get("last_digest")
        if not isinstance(last, dict):
            last_digest: dict[str, Any] = {
                "at": None,
                "ok": None,
                "error": None,
                "retry_at": None,
            }
        elif last.get("ok") is False:
            last_digest = {
                "at": last.get("at"),
                "ok": False,
                "error": last.get("error"),
                "retry_at": last.get("retry_at"),
                "skipped": False,
            }
        elif last.get("sent_at"):
            last_digest = {
                "at": last.get("sent_at"),
                "ok": True,
                "error": None,
                "retry_at": None,
                "skipped": False,
            }
        else:
            # skipped empty window, and nothing has ever been delivered
            last_digest = {
                "at": None,
                "ok": None,
                "error": None,
                "retry_at": None,
                "skipped": bool(last.get("skipped")),
            }
        queue = self._state.get("digest_queue") or []
        return {
            "enabled": self.enabled,
            "cooldown_minutes": self._cooldown_minutes(),
            "mode": self.mode,
            "queued_count": self.queued_count(),
            "queue_full": len(queue) >= QUEUE_CAP,
            "next_digest_at": self._state.get("digest_next_at"),
            "timezone": timezone_name(),
            "last_digest": last_digest,
            "dropped_count": int(self._state.get("digest_dropped") or 0),
            "sending": self._sending,
            "providers": providers,
        }
