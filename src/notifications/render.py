"""
Pure renderer: queued events plus a progress snapshot become one Discord message.

The result is always a single list of embeds. Trimming keeps it inside Discord's
limits (6000 characters total, 4096 per description, 10 embeds) and inside the
tighter targets from the UX spec (5800 total, 4000 per description, 6 embeds).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from src.notifications.digest_style import (
    ATTENTION_URGENT_COLOR,
    ATTENTION_WARNING_COLOR,
    CAMPAIGN_LINE_CAP,
    CAMPAIGNS_COLOR,
    DESCRIPTION_HARD_MAX,
    DROP_LINE_CAP,
    DROPS_COLOR,
    FOOTER_PREFIX,
    HEADER_COLOR,
    INVENTORY_SOURCE,
    LOG_CHAR_CAP,
    LOG_PATH_HINT,
    MAX_DESCRIPTION_CHARS,
    MAX_EMBEDS,
    MAX_TITLE_CHARS,
    MAX_TOTAL_CHARS,
    NAME_CHAR_CAP,
    PROGRESS_COLOR,
    PROGRESS_LINE_CAP,
    TOTAL_CHAR_TARGET,
    UNLINKED_COLOR,
    UNLINKED_EXPLANATION,
    UNLINKED_LINE_CAP,
    WARNING_GROUP_CAP,
)


_MARKDOWN = re.compile(r"([\\*_~|>#`])")

PERIOD_LABELS = {
    60: "last hour",
    180: "last 3 hours",
    360: "last 6 hours",
    720: "last 12 hours",
    1440: "last 24 hours",
    10080: "last 7 days",
}


def escape_discord(text: object, limit: int = NAME_CHAR_CAP) -> str:
    """Collapse whitespace, cut to `limit` characters, then escape Discord markdown."""
    cleaned = " ".join(str(text).split())
    if len(cleaned) > limit:
        cleaned = cleaned[: limit - 1] + "…"
    return _MARKDOWN.sub(r"\\\1", cleaned)


def discord_tag(stamp: datetime, style: str) -> str:
    """A Discord timestamp tag. Readers see it in their own local time."""
    aware = stamp if stamp.tzinfo is not None else stamp.replace(tzinfo=UTC)
    return f"<t:{int(aware.timestamp())}:{style}>"


def format_remaining(minutes: int) -> str:
    """`<1 min`, `38 min`, `2 h 05 min` or `3 d 4 h`."""
    remaining = max(0, int(minutes))
    if remaining < 1:
        return "<1 min"
    if remaining < 60:
        return f"{remaining} min"
    if remaining < 24 * 60:
        hours, mins = divmod(remaining, 60)
        return f"{hours} h {mins:02d} min"
    days, leftover = divmod(remaining, 24 * 60)
    return f"{days} d {leftover // 60} h"


def period_label(interval_minutes: int) -> str:
    """The header's "last …" phrase for a digest interval."""
    if interval_minutes in PERIOD_LABELS:
        return PERIOD_LABELS[interval_minutes]
    if interval_minutes < 48 * 60:
        hours = max(1, round(interval_minutes / 60))
        unit = "hour" if hours == 1 else "hours"
        return f"last {hours} {unit}"
    days = max(1, round(interval_minutes / 1440))
    unit = "day" if days == 1 else "days"
    return f"last {days} {unit}"


def message_char_count(embeds: list[dict[str, Any]]) -> int:
    """Characters Discord counts toward the 6000-character message budget."""
    total = 0
    for embed in embeds:
        total += len(embed.get("title") or "")
        total += len(embed.get("description") or "")
        footer = embed.get("footer") or {}
        total += len(footer.get("text") or "")
    return total


@dataclass
class _Group:
    header: str
    lines: list[str]


@dataclass
class _Section:
    kind: str
    title: str
    color: int
    fixed: list[str] = field(default_factory=list)
    items: list[Any] = field(default_factory=list)
    omitted: int = 0
    more_singular: str = "item"
    more_plural: str = "items"
    more_extra: str = ""
    forced: str | None = None

    def can_trim(self) -> bool:
        if self.kind == "header" or self.forced is not None:
            return False
        if self.kind == "drops":
            return any(isinstance(item, _Group) and item.lines for item in self.items)
        return bool(self.items)

    def trim(self) -> bool:
        if not self.can_trim():
            return False
        if self.kind == "drops":
            group = self.items[-1]
            if not isinstance(group, _Group) or not group.lines:
                self.items.pop()
                return self.trim() or True
            group.lines.pop()
            self.omitted += 1
            # a group header never stays behind once its last drop line is gone
            if not group.lines:
                self.items.pop()
            return True
        self.items.pop()
        self.omitted += 1
        return True

    def description(self) -> str:
        if self.forced is not None:
            return self.forced
        lines = list(self.fixed)
        if self.kind == "drops":
            for index, item in enumerate(self.items):
                if not isinstance(item, _Group):
                    continue
                if index > 0:
                    lines.append("")
                lines.append(item.header)
                lines.extend(item.lines)
        else:
            rendered = [item for item in self.items if isinstance(item, str)]
            if self.kind == "attention" and self.fixed and (rendered or self.omitted):
                lines.append("")
            lines.extend(rendered)
        if self.omitted:
            noun = self.more_singular if self.omitted == 1 else self.more_plural
            extra = f" {self.more_extra}" if self.more_extra else ""
            lines.append(f"…and {self.omitted} more {noun}{extra}")
        return "\n".join(lines).strip()


def _parse_stamp(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if not isinstance(value, str) or not value:
        return None
    try:
        stamp = datetime.fromisoformat(value)
    except ValueError:
        return None
    return stamp if stamp.tzinfo is not None else stamp.replace(tzinfo=UTC)


def _event_stamp(event: dict[str, Any]) -> datetime:
    return _parse_stamp(event.get("ts")) or datetime.now(UTC)


def _plural_phrase(count: int, singular: str, plural: str) -> str:
    word = singular if count == 1 else plural
    return f"**{count}** {word}"


def _list_time(stamp: datetime, long_window: bool) -> str:
    if long_window:
        return f"{discord_tag(stamp, 'd')} {discord_tag(stamp, 't')}"
    return discord_tag(stamp, "t")


def _cap_drop_groups(groups: list[_Group], cap: int) -> tuple[list[_Group], int]:
    kept: list[_Group] = []
    used = 0
    omitted = 0
    for group in groups:
        if used >= cap:
            omitted += len(group.lines)
            continue
        room = cap - used
        if len(group.lines) <= room:
            kept.append(group)
            used += len(group.lines)
        else:
            kept.append(_Group(group.header, group.lines[:room]))
            omitted += len(group.lines) - room
            used = cap
    return kept, omitted


def _build_drops(
    events: list[dict[str, Any]], long_window: bool
) -> _Section | None:
    ordered = sorted(
        (event for event in events if event.get("type") == "drop_received"),
        key=_event_stamp,
    )
    if not ordered:
        return None
    grouped: dict[tuple[str, str], _Group] = {}
    order: list[tuple[str, str]] = []
    for event in ordered:
        data = event.get("data") or {}
        game = str(data.get("game") or "")
        campaign = str(data.get("campaign") or "")
        key = (game, campaign)
        if key not in grouped:
            grouped[key] = _Group(
                header=f"**{escape_discord(game)}** — {escape_discord(campaign)}",
                lines=[],
            )
            order.append(key)
        benefits = data.get("benefits") or []
        raw_benefits = ", ".join(
            str(benefit).strip() for benefit in benefits if str(benefit).strip()
        )
        raw_drop = str(data.get("drop") or "").strip()
        show_drop = bool(raw_drop) and raw_drop.casefold() != raw_benefits.casefold()
        body = escape_discord(raw_benefits) if raw_benefits else ""
        if show_drop:
            drop_text = escape_discord(raw_drop)
            body = f"{body} — {drop_text}" if body else drop_text
        if not body:
            body = "a drop"
        source_raw = str(data.get("channel") or INVENTORY_SOURCE).strip()
        source = (
            INVENTORY_SOURCE
            if source_raw.casefold() == INVENTORY_SOURCE
            else escape_discord(source_raw)
        )
        grouped[key].lines.append(
            f"• {body} · {source} · {_list_time(_event_stamp(event), long_window)}"
        )
    groups, omitted = _cap_drop_groups([grouped[key] for key in order], DROP_LINE_CAP)
    return _Section(
        kind="drops",
        title=f"🎁 Drops claimed ({len(ordered)})",
        color=DROPS_COLOR,
        items=groups,
        omitted=omitted,
        more_singular="drop",
        more_plural="drops",
    )


def _build_campaigns(
    events: list[dict[str, Any]], window_end: datetime
) -> _Section | None:
    campaigns = [event for event in events if event.get("type") == "new_campaign"]
    if not campaigns:
        return None

    def sort_key(event: dict[str, Any]) -> tuple[int, float]:
        ends = _parse_stamp((event.get("data") or {}).get("ends_at"))
        if ends is None:
            return (1, 0.0)
        return (0, ends.timestamp())

    lines: list[str] = []
    for event in sorted(campaigns, key=sort_key):
        data = event.get("data") or {}
        game = escape_discord(data.get("game") or "")
        name = escape_discord(data.get("campaign") or "")
        starts = _parse_stamp(data.get("starts_at"))
        ends = _parse_stamp(data.get("ends_at"))
        timing = ""
        if starts is not None and starts > window_end and ends is not None:
            timing = (
                f" · starts {discord_tag(starts, 'd')}, ends {discord_tag(ends, 'd')}"
            )
        elif ends is not None:
            timing = f" · ends {discord_tag(ends, 'd')}"
        lines.append(f"• **{game}** — {name}{timing}")
    omitted = max(0, len(lines) - CAMPAIGN_LINE_CAP)
    return _Section(
        kind="campaigns",
        title=f"🆕 New campaigns ({len(campaigns)})",
        color=CAMPAIGNS_COLOR,
        items=lines[:CAMPAIGN_LINE_CAP],
        omitted=omitted,
        more_singular="campaign",
        more_plural="campaigns",
    )


def _build_unlinked(events: list[dict[str, Any]]) -> _Section | None:
    names = sorted(
        {
            str((event.get("data") or {}).get("game") or "").strip()
            for event in events
            if event.get("type") == "unlinked_tracked_game"
        },
        key=str.casefold,
    )
    names = [name for name in names if name]
    if not names:
        return None
    omitted = max(0, len(names) - UNLINKED_LINE_CAP)
    return _Section(
        kind="unlinked",
        title=f"🔗 Unlinked tracked games ({len(names)})",
        color=UNLINKED_COLOR,
        fixed=[f"*{UNLINKED_EXPLANATION}*"],
        items=[f"• **{escape_discord(name)}**" for name in names[:UNLINKED_LINE_CAP]],
        omitted=omitted,
        more_singular="game",
        more_plural="games",
    )


def _build_attention(
    events: list[dict[str, Any]],
    error_groups: list[dict[str, Any]],
    overflow_types: int,
    long_window: bool,
) -> _Section | None:
    urgent = sorted(
        (
            event
            for event in events
            if event.get("type") in ("auth_attention", "mining_stalled")
        ),
        key=_event_stamp,
    )
    errors = [group for group in error_groups if str(group.get("level")) == "ERROR"]
    warnings = [group for group in error_groups if str(group.get("level")) != "ERROR"]
    errors.sort(key=lambda group: int(group.get("count") or 0), reverse=True)
    warnings.sort(key=lambda group: int(group.get("count") or 0), reverse=True)
    ordered_groups = errors + warnings
    if not urgent and not ordered_groups and overflow_types <= 0:
        return None

    fixed: list[str] = []
    for event in urgent:
        data = event.get("data") or {}
        label = (
            "**Mining stalled**"
            if event.get("type") == "mining_stalled"
            else "**Sign-in needed**"
        )
        parts = [label]
        reason = str(data.get("reason") or "").strip()
        if reason:
            parts.append(escape_discord(reason, LOG_CHAR_CAP))
        parts.append(_list_time(_event_stamp(event), long_window))
        if data.get("alerted"):
            parts.append("alerted at the time")
        fixed.append(" · ".join(parts))

    group_lines: list[str] = []
    for group in ordered_groups:
        count = int(group.get("count") or 1)
        message = escape_discord(group.get("latest") or "", LOG_CHAR_CAP)
        stamp = _parse_stamp(group.get("last_ts")) or datetime.now(UTC)
        when = _list_time(stamp, long_window)
        if str(group.get("level")) == "ERROR":
            group_lines.append(f"×{count} **Error:** {message} · last {when}")
        else:
            group_lines.append(f"×{count} {message} · last {when}")

    shown = group_lines[:WARNING_GROUP_CAP]
    omitted = max(0, len(group_lines) - WARNING_GROUP_CAP) + max(0, overflow_types)
    group_total = len(ordered_groups) + max(0, overflow_types)
    high = bool(urgent) or bool(errors)
    return _Section(
        kind="attention",
        title=f"⚠️ Needs attention ({len(urgent) + group_total})",
        color=ATTENTION_URGENT_COLOR if high else ATTENTION_WARNING_COLOR,
        fixed=fixed,
        items=shown,
        omitted=omitted,
        more_singular="warning type",
        more_plural="warning types",
        more_extra=f"(full details in {LOG_PATH_HINT})",
    )


def _build_progress(progress: dict[str, Any] | None) -> _Section | None:
    if not progress:
        return None
    state = str(progress.get("state") or "idle")
    if state == "watching" and progress.get("channel") and progress.get("game"):
        line = (
            f"Watching **{escape_discord(progress.get('channel'))}**"
            f" for **{escape_discord(progress.get('game'))}**"
        )
    elif state == "stalled":
        stalled = _parse_stamp(progress.get("stalled_since")) or datetime.now(UTC)
        line = f"**Stalled** — no progress since {discord_tag(stalled, 'R')}"
    elif state in ("idle", "watching", "stalled"):
        line = "Idle — nothing to mine right now"
    else:
        return None

    campaigns = list(progress.get("campaigns") or [])
    campaigns.sort(
        key=lambda item: (
            0 if item.get("mining_now") else 1,
            int(item.get("remaining_minutes") or 0),
        )
    )
    rows: list[str] = []
    for item in campaigns:
        percent = max(0, min(100, int(item.get("percent") or 0)))
        filled = max(0, min(10, percent // 10))
        bar = "▰" * filled + "▱" * (10 - filled)
        remaining = format_remaining(int(item.get("remaining_minutes") or 0))
        rows.append(
            f"{bar} {percent}% · **{escape_discord(item.get('game') or '')}**"
            f" — {escape_discord(item.get('drop') or '')} · {remaining} left"
        )
    omitted = max(0, len(rows) - PROGRESS_LINE_CAP)
    return _Section(
        kind="progress",
        title="📈 Progress right now",
        color=PROGRESS_COLOR,
        fixed=[line],
        items=rows[:PROGRESS_LINE_CAP],
        omitted=omitted,
        more_singular="active campaign",
        more_plural="active campaigns",
    )


def _build_header(
    *,
    interval_minutes: int,
    window_start: datetime,
    window_end: datetime,
    next_at: datetime | None,
    preview: bool,
    drop_count: int,
    campaign_count: int,
    alert_count: int,
    warning_count: int,
    unlinked_count: int,
    dropped_count: int,
) -> _Section:
    period = period_label(interval_minutes)
    title = f"TwitchDropsMiner digest · {period}"
    if preview:
        title = f"Preview · {title}"
    phrases: list[str] = []
    if drop_count:
        phrases.append(_plural_phrase(drop_count, "drop claimed", "drops claimed"))
    if campaign_count:
        phrases.append(_plural_phrase(campaign_count, "new campaign", "new campaigns"))
    if alert_count:
        phrases.append(_plural_phrase(alert_count, "alert", "alerts"))
    if warning_count:
        phrases.append(_plural_phrase(warning_count, "warning", "warnings"))
    if unlinked_count:
        phrases.append(_plural_phrase(unlinked_count, "unlinked game", "unlinked games"))
    lines = [
        f"{discord_tag(window_start, 'f')} – {discord_tag(window_end, 'f')}",
        " · ".join(phrases) if phrases else "Nothing new in this period.",
    ]
    if dropped_count > 0:
        if dropped_count == 1:
            lines.append("Queue limit reached: 1 older event wasn't kept.")
        else:
            lines.append(f"Queue limit reached: {dropped_count} older events weren't kept.")
    if next_at is not None:
        lines.append(f"Next digest {discord_tag(next_at, 'f')}")
    else:
        lines.append(f"Next digest {discord_tag(window_end, 'R')}")
    return _Section(kind="header", title=title, color=HEADER_COLOR, fixed=lines)


def _total_chars(sections: list[_Section], footer: str) -> int:
    return sum(len(section.title) + len(section.description()) for section in sections) + len(
        footer
    )


def _trim_to_fit(sections: list[_Section], footer: str) -> None:
    """Longest-description-first trimming, then a hard cut so one message always fits."""
    for _ in range(20000):
        over_desc = [
            section
            for section in sections
            if section.kind != "header"
            and len(section.description()) > MAX_DESCRIPTION_CHARS
            and section.can_trim()
        ]
        if over_desc:
            max(over_desc, key=lambda section: len(section.description())).trim()
            continue
        if _total_chars(sections, footer) <= TOTAL_CHAR_TARGET and all(
            len(section.description()) <= MAX_DESCRIPTION_CHARS for section in sections
        ):
            break
        if _total_chars(sections, footer) <= TOTAL_CHAR_TARGET:
            break
        trimmable = [
            section for section in sections if section.kind != "header" and section.can_trim()
        ]
        if not trimmable:
            break
        max(trimmable, key=lambda section: len(section.description())).trim()

    for _ in range(20000):
        too_long = [
            section for section in sections if len(section.description()) > DESCRIPTION_HARD_MAX
        ]
        over_budget = _total_chars(sections, footer) > MAX_TOTAL_CHARS
        if not too_long and not over_budget:
            return
        trimmable = [
            section for section in sections if section.kind != "header" and section.can_trim()
        ]
        if trimmable:
            pool = [
                section for section in trimmable if len(section.description()) > DESCRIPTION_HARD_MAX
            ] or trimmable
            max(pool, key=lambda section: len(section.description())).trim()
            continue
        victim = max(sections, key=lambda section: len(section.description()))
        others = _total_chars(sections, footer) - len(victim.description())
        budget = min(DESCRIPTION_HARD_MAX, MAX_TOTAL_CHARS - others)
        if budget < 1:
            if victim.kind != "header":
                sections.remove(victim)
                continue
            return
        text = victim.description()
        shortened = text[: budget - 1] + "…"
        if shortened == text or len(shortened) >= len(text):
            if victim.kind != "header":
                sections.remove(victim)
                continue
            return
        victim.forced = shortened
        if _total_chars(sections, footer) <= MAX_TOTAL_CHARS and all(
            len(section.description()) <= DESCRIPTION_HARD_MAX for section in sections
        ):
            return


def render_digest(
    *,
    events: list[dict[str, Any]],
    error_groups: list[dict[str, Any]] | None = None,
    error_overflow_types: int = 0,
    error_overflow_count: int = 0,
    window_start: datetime,
    window_end: datetime,
    next_at: datetime | None,
    interval_minutes: int,
    dropped_count: int = 0,
    progress: dict[str, Any] | None = None,
    include_progress: bool = True,
    include_errors: bool = True,
    version: str,
    preview: bool = False,
) -> list[dict[str, Any]]:
    """
    Build the one Discord message for this digest window.

    `events` are serialized NotificationEvent dicts. `error_groups` are the
    aggregated WARNING/ERROR records (`level`, `count`, `latest`, `last_ts`).
    Counts in the header are the real totals, including lines later trimmed.
    """
    groups = list(error_groups or []) if include_errors else []
    overflow_types = error_overflow_types if include_errors else 0
    overflow_count = error_overflow_count if include_errors else 0
    if window_start.tzinfo is None:
        window_start = window_start.replace(tzinfo=UTC)
    if window_end.tzinfo is None:
        window_end = window_end.replace(tzinfo=UTC)
    long_window = (window_end - window_start) > timedelta(hours=24)

    drop_count = sum(1 for event in events if event.get("type") == "drop_received")
    campaign_count = sum(1 for event in events if event.get("type") == "new_campaign")
    alert_count = sum(
        1 for event in events if event.get("type") in ("auth_attention", "mining_stalled")
    )
    unlinked_count = len(
        {
            str((event.get("data") or {}).get("game") or "").strip()
            for event in events
            if event.get("type") == "unlinked_tracked_game"
            and str((event.get("data") or {}).get("game") or "").strip()
        }
    )
    warning_count = (
        sum(int(group.get("count") or 0) for group in groups) + max(0, overflow_count)
        if include_errors
        else 0
    )

    header = _build_header(
        interval_minutes=interval_minutes,
        window_start=window_start,
        window_end=window_end,
        next_at=next_at,
        preview=preview,
        drop_count=drop_count,
        campaign_count=campaign_count,
        alert_count=alert_count,
        warning_count=warning_count,
        unlinked_count=unlinked_count,
        dropped_count=max(0, dropped_count),
    )
    attention = _build_attention(events, groups, overflow_types, long_window)
    attention_first = False
    if attention is not None:
        has_error = any(str(group.get("level")) == "ERROR" for group in groups)
        has_urgent = alert_count > 0
        attention_first = has_urgent or has_error

    sections: list[_Section] = [header]
    if attention is not None and attention_first:
        sections.append(attention)
    drops = _build_drops(events, long_window)
    if drops is not None:
        sections.append(drops)
    campaigns = _build_campaigns(events, window_end)
    if campaigns is not None:
        sections.append(campaigns)
    if include_progress:
        progress_section = _build_progress(progress)
        if progress_section is not None:
            sections.append(progress_section)
    unlinked = _build_unlinked(events)
    if unlinked is not None:
        sections.append(unlinked)
    if attention is not None and not attention_first:
        sections.append(attention)

    footer = f"{FOOTER_PREFIX}{version}"
    _trim_to_fit(sections, footer)
    sections = [section for section in sections if section.description() or section.kind == "header"]
    sections = sections[:MAX_EMBEDS]

    embeds: list[dict[str, Any]] = []
    for index, section in enumerate(sections):
        embed: dict[str, Any] = {
            "title": section.title[:MAX_TITLE_CHARS],
            "description": section.description()[:DESCRIPTION_HARD_MAX],
            "color": section.color,
        }
        if index == len(sections) - 1:
            embed["footer"] = {"text": footer}
            embed["timestamp"] = window_end.astimezone(UTC).isoformat()
        embeds.append(embed)
    return embeds
