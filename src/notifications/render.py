"""
Pure renderer: queued events plus a progress snapshot become Discord messages.

One card per game, in the same rank order as before, packed greedily into
messages of at most 10 embeds and 5800 UTF-16 units. A game that only has a
new campaign is a card too: its New: lines, and its box art when one exists.
Needs attention is the last embed of the last message. The totals line is the
first message's content. The window and the next send are the footer of the
last embed. Follow-up messages have no content. Past five messages, the last
one ends the game list with "…and N more games".

`-#` subtext is not clearly supported inside embed descriptions (Discord's
formatting reference describes it for chat messages; embed guides that list
working syntax stop at bold, italic, code, quotes and links). These lines use
italics instead, which embeds do render.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from src.notifications.digest_style import (
    ATTENTION_URGENT_COLOR,
    ATTENTION_WARNING_COLOR,
    BOX_ART_SIZE,
    CAMPAIGN_LINE_CAP,
    CLAIM_LINE_CAP,
    CONTENT_HARD_MAX,
    DESCRIPTION_HARD_MAX,
    DISCORD_MAX_EMBEDS,
    FOOTER_HARD_MAX,
    GAME_COLOR,
    LOG_CHAR_CAP,
    LOG_GROUP_CAP,
    LOG_PATH_HINT,
    MAX_DIGEST_MESSAGES,
    MAX_TITLE_CHARS,
    MAX_TOTAL_CHARS,
    NAME_CHAR_CAP,
    PROGRESS_BAR_CELLS,
    PROGRESS_LINE_CAP,
    TOTAL_CHAR_TARGET,
    UNLINKED_NAME_CAP,
)
from src.notifications.schedule import local_timezone


# Bump when the shape of a saved digest part changes. An in-flight digest
# rendered with a different schema is discarded and built again from the queue.
RENDER_SCHEMA = 1


_MARKDOWN = re.compile(r"([\\*_~|>#`])")
_GENERIC_DROP_NAME = re.compile(r"^(drop|reward)?\s*#?\d*$", re.IGNORECASE)
_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTHS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)

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
    """The "last …" phrase for a digest interval."""
    if interval_minutes in PERIOD_LABELS:
        return PERIOD_LABELS[interval_minutes]
    if interval_minutes < 48 * 60:
        hours = max(1, round(interval_minutes / 60))
        unit = "hour" if hours == 1 else "hours"
        return f"last {hours} {unit}"
    days = max(1, round(interval_minutes / 1440))
    unit = "day" if days == 1 else "days"
    return f"last {days} {unit}"


def discord_units(text: str) -> int:
    """UTF-16 code units. Counting this way stays inside Discord's limit either way."""
    return len(text.encode("utf-16-le")) // 2


def message_char_count(embeds: list[dict[str, Any]], content: str = "") -> int:
    """UTF-16 units of content plus every embed title, description and footer."""
    total = discord_units(content)
    for embed in embeds:
        total += discord_units(embed.get("title") or "")
        total += discord_units(embed.get("description") or "")
        footer = embed.get("footer") or {}
        total += discord_units(footer.get("text") or "")
    return total


def _embed_units(embeds: list[dict[str, Any]]) -> int:
    """Discord's 6000-character embed budget. The content line is not part of it."""
    return message_char_count(embeds)


def _utf16_prefix(text: str, units: int) -> str:
    """The longest prefix of `text` that is at most `units` UTF-16 code units."""
    if units <= 0 or not text:
        return ""
    encoded = text.encode("utf-16-le")
    chunk = encoded[: min(len(encoded), units * 2)]
    if len(chunk) >= 2:
        last = int.from_bytes(chunk[-2:], "little")
        # don't leave a dangling high surrogate
        if 0xD800 <= last <= 0xDBFF:
            chunk = chunk[:-2]
    return chunk.decode("utf-16-le")


def _unclosed_timestamp(text: str) -> bool:
    start = text.rfind("<t:")
    return start >= 0 and ">" not in text[start:]


def _safe_truncate(text: str, budget: int) -> str:
    """
    Shorten `text` to `budget` UTF-16 units without cutting a timestamp tag or
    leaving an unbalanced `**`.
    """
    if discord_units(text) <= budget:
        return text
    clipped = _utf16_prefix(text, budget)
    newline = clipped.rfind("\n")
    if newline > 0:
        clipped = clipped[:newline]
    while clipped:
        if _unclosed_timestamp(clipped) or clipped.count("**") % 2 == 1:
            tag = clipped.rfind("<t:")
            bold = clipped.rfind("**")
            cut = max(tag, bold)
            clipped = clipped[:cut] if cut > 0 else clipped[:-1]
            continue
        clipped = clipped.rstrip()
        break
    if not clipped:
        return ""
    if discord_units(clipped) + discord_units("…") <= budget and not clipped.endswith("…"):
        return clipped + "…"
    return clipped


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


def _as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: object) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _aware(stamp: datetime) -> datetime:
    return stamp if stamp.tzinfo is not None else stamp.replace(tzinfo=UTC)


# Discord rejects a bad embed image with HTTP 400 for the whole message.
_MAX_THUMBNAIL_URL = 2048


def _valid_https_url(value: object) -> str | None:
    """An https URL Discord can use as an embed image, or None.

    Requires a non-empty host, no whitespace or control characters, and at
    most 2048 characters. Anything else is skipped so the next image in the
    chain can be tried.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > _MAX_THUMBNAIL_URL:
        return None
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in text):
        return None
    try:
        parts = urlsplit(text)
        host = parts.hostname
    except ValueError:
        return None
    if parts.scheme.lower() != "https" or not host:
        return None
    return text


def _first_https(images: object) -> str | None:
    """The first usable https URL in a benefit-image list. Anything else is skipped."""
    if isinstance(images, str):
        images = [images]
    if not isinstance(images, list):
        return None
    for item in images:
        url = _valid_https_url(item)
        if url:
            return url
    return None


def _box_art_url(value: object) -> str | None:
    """An https box-art URL, with Twitch's `{width}x{height}` template filled in."""
    if not isinstance(value, str):
        return None
    text = value.strip().replace("{width}x{height}", BOX_ART_SIZE)
    return _valid_https_url(text)


def drop_thumbnail(data: dict[str, Any]) -> str | None:
    """
    Image for one drop_received embed.

    Same order as a game card, without a progress row: the first https benefit
    image, otherwise the game's box art.
    """
    image = _first_https(data.get("benefit_images"))
    if image:
        return image
    return _box_art_url(data.get("game_box_art"))


def progress_bar(percent: int) -> str:
    """
    A fixed-width bar with the percentage, in inline code.

    Inline code renders monospaced, so every row's bar and percentage line up.
    The geometric ▰/▱ glyphs fall back to mismatched boxes in Discord's font.
    """
    clamped = max(0, min(100, int(percent)))
    filled = min(PROGRESS_BAR_CELLS, round(clamped * PROGRESS_BAR_CELLS / 100))
    # a started drop always shows at least one cell, a finished one never looks full early
    if clamped > 0 and filled == 0:
        filled = 1
    if clamped < 100 and filled == PROGRESS_BAR_CELLS:
        filled -= 1
    bar = "█" * filled + "░" * (PROGRESS_BAR_CELLS - filled)
    return f"`{bar} {clamped:>3}%`"


def _progress_label(item: dict[str, Any]) -> str:
    """
    What the row is about besides the game.

    Twitch often names timed drops just "Drop" or "Drop 2", which says nothing and
    makes a game's campaigns indistinguishable, so the campaign name stands in.
    """
    game = str(item.get("game") or "").strip()
    drop = str(item.get("drop") or "").strip()
    campaign = str(item.get("campaign") or "").strip()
    if drop and not _GENERIC_DROP_NAME.match(drop) and drop.casefold() != game.casefold():
        return drop
    if campaign and campaign.casefold() != game.casefold():
        return campaign
    return drop


def _italics(line: str) -> str:
    """Subtext stand-in. `-#` is not clearly rendered inside embed descriptions."""
    return f"*{line}*"


def _join(lines: list[str]) -> str:
    return "\n".join(line for line in lines if line).strip()


def _game_id(value: object) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    return text or None


def _reward_names(data: dict[str, Any]) -> list[str]:
    raw = data.get("benefits")
    if isinstance(raw, str):
        raw = [raw]
    names = [str(item).strip() for item in _as_list(raw) if str(item).strip()]
    if names:
        return names
    drop = str(data.get("drop") or "").strip()
    if drop:
        return [drop]
    return ["a drop"]


def _campaign_label(data: dict[str, Any]) -> str:
    return " ".join(str(data.get("campaign") or "").split())


def _forced_reward_names(data: dict[str, Any]) -> set[str]:
    """Benefit names a previous digest already showed for this game under another campaign."""
    raw = data.get("disambiguate")
    if isinstance(raw, str):
        raw = [raw]
    return {" ".join(str(item).split()).casefold() for item in _as_list(raw) if str(item).strip()}


def _collapsed_rewards(claims: list[dict[str, Any]]) -> list[tuple[str, int]]:
    """Oldest claim first. Identical names collapse, and the line stays where it first appeared.

    The same benefit name from two campaigns stays as two lines, with the
    campaign on each, so a new daily drop is not mistaken for the previous one.
    A name flagged on the event is labelled even when it is the only claim in
    this window.
    """
    ordered = sorted(
        enumerate(claims),
        key=lambda pair: (_event_stamp(pair[1]), pair[0]),
    )
    rows: list[tuple[str, str, bool]] = []
    for _order, event in ordered:
        data = _as_dict(event.get("data"))
        campaign = _campaign_label(data)
        forced = _forced_reward_names(data)
        for name in _reward_names(data):
            rows.append((name, campaign, name.casefold() in forced))
    campaigns_for: dict[str, set[str]] = {}
    forced_names: set[str] = set()
    for name, campaign, is_forced in rows:
        if campaign:
            campaigns_for.setdefault(name.casefold(), set()).add(campaign.casefold())
        if is_forced:
            forced_names.add(name.casefold())

    def label(name: str, campaign: str) -> str:
        key = name.casefold()
        distinct = campaigns_for.get(key, set())
        if campaign and (len(distinct) > 1 or key in forced_names):
            return f"{name} · {campaign}"
        return name

    index: dict[str, int] = {}
    lines: list[tuple[str, int]] = []
    for name, campaign, _forced in rows:
        shown = label(name, campaign)
        slot = index.get(shown.casefold())
        if slot is None:
            index[shown.casefold()] = len(lines)
            lines.append((shown, 1))
        else:
            current, count = lines[slot]
            lines[slot] = (current, count + 1)
    return lines


def _reward_text(name: str, count: int) -> str:
    shown = escape_discord(name)
    if count > 1:
        return f"{shown} ×{count}"
    return shown


def _claim_lines(claims: list[dict[str, Any]], cap: int) -> list[str]:
    collapsed = _collapsed_rewards(claims)
    shown = collapsed[:cap]
    remaining = sum(count for _name, count in collapsed[cap:])
    lines = [f"✓ {_reward_text(name, count)}" for name, count in shown]
    if remaining:
        lines.append(f"✓ +{remaining} more")
    return lines


def _ordered_progress(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: (
            0 if row.get("mining_now") else 1,
            int(row.get("remaining_minutes") or 0),
            str(row.get("campaign") or "").casefold(),
        ),
    )


def _progress_lines(rows: list[dict[str, Any]]) -> list[str]:
    ordered = _ordered_progress(rows)
    lines: list[str] = []
    for item in ordered[:PROGRESS_LINE_CAP]:
        label = _progress_label(item)
        about = f" {escape_discord(label)}" if label else ""
        remaining = format_remaining(int(item.get("remaining_minutes") or 0))
        lines.append(f"{progress_bar(int(item.get('percent') or 0))}{about} · {remaining} left")
    extra = len(ordered) - PROGRESS_LINE_CAP
    if extra > 0:
        lines.append(f"+{extra} more in progress")
    return lines


def _campaign_not_started(data: dict[str, Any], window_end: datetime) -> bool:
    starts = _parse_stamp(data.get("starts_at"))
    return starts is not None and starts > window_end


def _campaign_sort_key(data: dict[str, Any]) -> tuple[int, float, str, str]:
    """Soonest end first. Equal ends break by name, then campaign id."""
    ends = _parse_stamp(data.get("ends_at"))
    name = " ".join(str(data.get("campaign") or "").split()).casefold()
    campaign_id = str(data.get("campaign_id") or data.get("id") or "")
    if ends is None:
        return (1, 0.0, name, campaign_id)
    return (0, ends.timestamp(), name, campaign_id)


def _new_lines(
    campaigns: list[dict[str, Any]],
    progress_rows: list[dict[str, Any]],
    window_end: datetime,
) -> list[str]:
    shown_campaigns = {
        str(row.get("campaign") or "").strip().casefold()
        for row in _ordered_progress(progress_rows)[:PROGRESS_LINE_CAP]
        if str(row.get("campaign") or "").strip()
    }
    pending = [
        data
        for data in campaigns
        if str(data.get("campaign") or "").strip().casefold() not in shown_campaigns
    ]
    pending.sort(key=_campaign_sort_key)
    lines: list[str] = []
    for data in pending[:CAMPAIGN_LINE_CAP]:
        name = escape_discord(data.get("campaign") or "")
        if _campaign_not_started(data, window_end):
            starts = _parse_stamp(data.get("starts_at"))
            when = f" · starts {discord_tag(starts, 'R')}" if starts is not None else ""
        else:
            ends = _parse_stamp(data.get("ends_at"))
            when = f" · ends {discord_tag(ends, 'R')}" if ends is not None else ""
        lines.append(f"New: {name}{when}")
    extra = len(pending) - CAMPAIGN_LINE_CAP
    if extra > 0:
        noun = "campaign" if extra == 1 else "campaigns"
        lines.append(f"+{extra} more new {noun}")
    return lines


@dataclass
class _Game:
    key: str
    name: str
    game_id: str | None = None
    claims: list[dict[str, Any]] = field(default_factory=list)
    campaigns: list[dict[str, Any]] = field(default_factory=list)
    progress: list[dict[str, Any]] = field(default_factory=list)
    event_seqs: list[int] = field(default_factory=list)
    mining_now: bool = False

    def remember_name(self, name: object) -> None:
        text = " ".join(str(name).split())
        if text and not self.name:
            self.name = text


def _claim_line_total(games: Iterable[_Game]) -> int:
    """Claims these cards account for. A ×N line counts as N, including +N more."""
    return sum(
        sum(count for _name, count in _collapsed_rewards(game.claims)) for game in games
    )


def _normal_game_name(name: object) -> str:
    text = unicodedata.normalize("NFC", str(name))
    return " ".join(text.split()).casefold()


def _bucket_key(game_id: str | None, name: str) -> str:
    if game_id:
        return f"id:{game_id}"
    return f"name:{_normal_game_name(name)}"


def _fold_legacy_buckets(games: dict[str, _Game]) -> dict[str, _Game]:
    """Join a name-only bucket onto the one id bucket with the same game name.

    Claims queued before game ids were stored have no id. Two id buckets that
    share a name are left alone: there is no single card to join.
    """
    by_name: dict[str, list[_Game]] = {}
    for game in games.values():
        if game.game_id:
            by_name.setdefault(_normal_game_name(game.name), []).append(game)
    folded: dict[str, _Game] = {}
    for game in games.values():
        if game.game_id:
            folded[game.key] = game
            continue
        matches = by_name.get(_normal_game_name(game.name), [])
        if len(matches) != 1:
            folded[game.key] = game
            continue
        target = matches[0]
        target.claims.extend(game.claims)
        target.campaigns.extend(game.campaigns)
        target.progress.extend(game.progress)
        target.event_seqs.extend(game.event_seqs)
        target.mining_now = target.mining_now or game.mining_now
        target.remember_name(game.name)
    return folded


def _event_seq(event: dict[str, Any]) -> int | None:
    """Queue seq stamped on an event, or None when this render has no queue."""
    seq = event.get("seq")
    if isinstance(seq, bool) or not isinstance(seq, int) or seq <= 0:
        return None
    return seq


def _games_from(
    events: list[dict[str, Any]],
    progress: dict[str, Any] | None,
) -> dict[str, _Game]:
    games: dict[str, _Game] = {}

    def bucket(game_id: str | None, name: str) -> _Game:
        key = _bucket_key(game_id, name)
        game = games.get(key)
        if game is None:
            game = _Game(key=key, name=name, game_id=game_id)
            games[key] = game
        elif game_id and not game.game_id:
            game.game_id = game_id
        game.remember_name(name)
        return game

    for event in events:
        kind = str(event.get("type") or "")
        if kind not in ("drop_received", "new_campaign"):
            continue
        data = _as_dict(event.get("data"))
        name = " ".join(str(data.get("game") or "").split())
        game_id = _game_id(data.get("game_id"))
        if not name and not game_id:
            continue
        game = bucket(game_id, name)
        seq = _event_seq(event)
        if seq is not None:
            game.event_seqs.append(seq)
        if kind == "drop_received":
            game.claims.append(event)
        else:
            game.campaigns.append(data)

    snapshot = _as_dict(progress)
    for item in _as_list(snapshot.get("campaigns")):
        row = _as_dict(item)
        name = " ".join(str(row.get("game") or "").split())
        game_id = _game_id(row.get("game_id"))
        if not name and not game_id:
            continue
        game = bucket(game_id, name)
        game.progress.append(row)
        if row.get("mining_now"):
            game.mining_now = True

    focus_id = _game_id(snapshot.get("game_id"))
    focus_name = " ".join(str(snapshot.get("game") or "").split()).casefold()
    state = str(snapshot.get("state") or "")
    if state == "watching" and (focus_id or focus_name):
        for game in games.values():
            id_match = bool(focus_id) and game.game_id == focus_id
            name_match = bool(focus_name) and game.name.casefold() == focus_name
            if id_match or name_match:
                game.mining_now = True
    return _fold_legacy_buckets(games)


def _matches_focus(game: _Game, progress: dict[str, Any]) -> bool:
    focus_id = _game_id(progress.get("game_id"))
    if focus_id and game.game_id:
        return game.game_id == focus_id
    focus_name = " ".join(str(progress.get("game") or "").split()).casefold()
    return bool(focus_name) and game.name.casefold() == focus_name


def _status_line(game: _Game, progress: dict[str, Any] | None, *, show: bool) -> str:
    if not show or not progress:
        return ""
    if not game.mining_now and not _matches_focus(game, progress):
        return ""
    state = str(progress.get("state") or "")
    if state == "stalled":
        stalled = _parse_stamp(progress.get("stalled_since")) or datetime.now(UTC)
        return _italics(f"Stalled since {discord_tag(stalled, 'R')}")
    if state == "watching" and game.mining_now:
        channel = " ".join(str(progress.get("channel") or "").split())
        if not channel:
            return ""
        return _italics(f"Mining now on {escape_discord(channel)}")
    return ""


def _thumbnail(game: _Game, *, use_claims: bool) -> str | None:
    if use_claims and game.claims:
        latest = max(
            enumerate(game.claims),
            key=lambda pair: (_event_stamp(pair[1]), pair[0]),
        )[1]
        image = _first_https(_as_dict(latest.get("data")).get("benefit_images"))
        if image:
            return image
    current = None
    ordered = _ordered_progress(game.progress)
    for row in ordered:
        if row.get("mining_now"):
            current = row
            break
    if current is None and ordered:
        current = ordered[0]
    if current is not None:
        image = _first_https(current.get("benefit_images"))
        if image:
            return image
    sources: list[object] = []
    if use_claims and game.claims:
        latest = max(
            enumerate(game.claims),
            key=lambda pair: (_event_stamp(pair[1]), pair[0]),
        )[1]
        sources.append(_as_dict(latest.get("data")).get("game_box_art"))
    sources.extend(row.get("game_box_art") for row in ordered)
    sources.extend(data.get("game_box_art") for data in game.campaigns)
    for source in sources:
        url = _box_art_url(source)
        if url:
            return url
    return None


def _card_description(
    game: _Game,
    *,
    claim_cap: int,
    include_drops: bool,
    include_progress: bool,
    include_campaigns: bool,
    progress: dict[str, Any] | None,
    window_end: datetime,
) -> str:
    lines: list[str] = []
    status = _status_line(game, progress, show=include_progress)
    if status:
        lines.append(status)
    if include_drops and game.claims:
        lines.extend(_claim_lines(game.claims, claim_cap))
    if include_progress and game.progress:
        lines.extend(_progress_lines(game.progress))
    if include_campaigns and game.campaigns:
        visible_progress = game.progress if include_progress else []
        lines.extend(_new_lines(game.campaigns, visible_progress, window_end))
    return _join(lines)


# Spec section 5: the stall block, then sign-in. Within a block, newest first.
_URGENT_KIND_ORDER = {"mining_stalled": 0, "auth_attention": 1}


def _urgent_display_key(group: dict[str, Any]) -> tuple[int, float]:
    kind = str(group.get("type") or "")
    latest = group.get("latest")
    stamp = latest.timestamp() if isinstance(latest, datetime) else 0.0
    return (_URGENT_KIND_ORDER.get(kind, 9), -stamp)


def _collapse_urgent(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group events that would display as the same line.

    A stall line never shows its reason, so every stall shares one group and
    the latest time. Sign-in lines group on the reason text. The ×N line
    includes that reason, so two reasons do not look like the same line.
    Stall groups come first, and each kind is newest first.
    """
    groups: list[dict[str, Any]] = []
    index: dict[tuple[str, str], dict[str, Any]] = {}
    ordered = sorted(enumerate(events), key=lambda pair: (_event_stamp(pair[1]), pair[0]))
    for _order, event in ordered:
        data = _as_dict(event.get("data"))
        kind = str(event.get("type") or "")
        if kind == "mining_stalled":
            identity = ""
        else:
            identity = escape_discord(data.get("reason") or "", LOG_CHAR_CAP)
        stamp = _event_stamp(event)
        key = (kind, identity)
        group = index.get(key)
        if group is None:
            group = {"type": kind, "reason": identity, "count": 0, "latest": stamp}
            index[key] = group
            groups.append(group)
        group["count"] = int(group["count"]) + 1
        if stamp >= group["latest"]:
            group["latest"] = stamp
    groups.sort(key=_urgent_display_key)
    return groups


def _format_urgent(group: dict[str, Any]) -> str:
    count = int(group["count"])
    latest: datetime = group["latest"]
    if group.get("type") == "mining_stalled":
        label = "**Mining stalled**"
        if count == 1:
            return f"{label} · no progress since {discord_tag(latest, 'R')}"
        return f"{label} ×{count}, last {discord_tag(latest, 't')}"
    label = "**Sign-in needed**"
    reason = str(group.get("reason") or "")
    if count == 1:
        return f"{label} · {reason}" if reason else label
    if reason:
        return f"{label} ×{count} · {reason}, last {discord_tag(latest, 't')}"
    return f"{label} ×{count}, last {discord_tag(latest, 't')}"


def _fit_urgent(
    groups: list[dict[str, Any]], tail: list[str], budget: int
) -> list[str]:
    """Newest groups that still leave the tail inside `budget`.

    `budget` is the whole attention description, so it can be tighter than
    4096 when the rest of the message is already close to 5800. The oldest
    group is dropped first, across both kinds. What remains is stall lines,
    then sign-in lines, newest first within each. The tail (queue note, log
    lines) is never dropped. The marker counts the events that were dropped.
    """
    kept = list(groups)
    omitted = 0
    while True:
        lines = [_format_urgent(group) for group in sorted(kept, key=_urgent_display_key)]
        if omitted:
            lines.append(f"…and {omitted} more urgent")
        if discord_units(_join(lines + tail)) <= budget or not kept:
            return lines
        oldest = min(
            kept,
            key=lambda group: (
                group["latest"],
                -_URGENT_KIND_ORDER.get(str(group.get("type") or ""), 9),
            ),
        )
        kept.remove(oldest)
        omitted += int(oldest["count"])


def _log_lines(
    error_groups: list[dict[str, Any]],
    overflow_types: int,
    cap: int,
) -> list[str]:
    ordered = sorted(
        error_groups,
        key=lambda group: (
            0 if str(group.get("level")) == "ERROR" else 1,
            -int(group.get("count") or 0),
            str(group.get("latest") or ""),
        ),
    )
    lines: list[str] = []
    for group in ordered[:cap]:
        count = int(group.get("count") or 1)
        message = escape_discord(group.get("latest") or "", LOG_CHAR_CAP)
        lines.append(f"×{count} {message}".rstrip())
    hidden = max(0, len(ordered) - cap) + max(0, overflow_types)
    if hidden:
        lines.append(_italics(f"…and {hidden} more in {LOG_PATH_HINT}"))
    return lines


def _unlinked_line(events: list[dict[str, Any]]) -> str:
    seen: dict[str, str] = {}
    for event in events:
        if event.get("type") != "unlinked_tracked_game":
            continue
        name = " ".join(str(_as_dict(event.get("data")).get("game") or "").split())
        if not name:
            continue
        seen.setdefault(name.casefold(), name)
    names = [seen[key] for key in sorted(seen)]
    if not names:
        return ""
    shown = names[:UNLINKED_NAME_CAP]
    body = ", ".join(escape_discord(name) for name in shown)
    extra = len(names) - len(shown)
    if extra:
        body = f"{body}, +{extra}"
    return f"**Link your account:** {body}"


def _queue_line(dropped_count: int) -> str:
    if dropped_count <= 0:
        return ""
    if dropped_count == 1:
        text = "Queue was full: 1 older event wasn't kept."
    else:
        text = f"Queue was full: {dropped_count} older events weren't kept."
    return _italics(text)


def _attention(
    events: list[dict[str, Any]],
    error_groups: list[dict[str, Any]],
    overflow_types: int,
    dropped_count: int,
    *,
    include_stalled: bool,
    include_auth: bool,
    include_unlinked: bool,
    include_errors: bool,
    log_cap: int,
    description_budget: int,
) -> tuple[str, int] | None:
    tail: list[str] = []
    if include_unlinked:
        unlinked = _unlinked_line(events)
        if unlinked:
            tail.append(unlinked)
    queue = _queue_line(dropped_count)
    if queue:
        tail.append(queue)
    if include_errors:
        tail.extend(_log_lines(error_groups, overflow_types, log_cap))
    selected: list[dict[str, Any]] = []
    if include_stalled:
        selected.extend(event for event in events if event.get("type") == "mining_stalled")
    if include_auth:
        selected.extend(event for event in events if event.get("type") == "auth_attention")
    lines = _fit_urgent(_collapse_urgent(selected), tail, description_budget)
    lines.extend(tail)
    description = _join(lines)
    if not description:
        return None
    # colour follows what this embed actually holds, after the toggles
    holds_stall = include_stalled and any(event.get("type") == "mining_stalled" for event in events)
    holds_auth = include_auth and any(event.get("type") == "auth_attention" for event in events)
    color = ATTENTION_URGENT_COLOR if holds_stall or holds_auth else ATTENTION_WARNING_COLOR
    return description, color


def _rank_key(game: _Game, *, include_drops: bool, include_progress: bool) -> tuple[Any, ...]:
    claims = game.claims if include_drops else []
    latest = max((_event_stamp(event) for event in claims), default=None)
    rows = game.progress if include_progress else []
    best = max((int(row.get("percent") or 0) for row in rows), default=-1)
    return (
        0 if game.mining_now else 1,
        -len(claims),
        -(latest.timestamp() if latest is not None else 0.0),
        -best,
        game.name.casefold(),
    )


def _content_line(
    *,
    preview: bool,
    interval_minutes: int,
    drop_count: int,
    campaign_count: int,
    stall_count: int,
    auth_count: int,
    unlinked_count: int,
    warning_count: int,
) -> str:
    parts: list[str] = []
    if drop_count:
        noun = "drop claimed" if drop_count == 1 else "drops claimed"
        parts.append(f"{drop_count} {noun}")
    if campaign_count:
        noun = "new campaign" if campaign_count == 1 else "new campaigns"
        parts.append(f"{campaign_count} {noun}")
    if stall_count == 1:
        parts.append("stall alert")
    elif stall_count > 1:
        parts.append(f"{stall_count} stall alerts")
    if auth_count:
        parts.append("sign-in needed")
    if unlinked_count:
        noun = "unlinked game" if unlinked_count == 1 else "unlinked games"
        parts.append(f"{unlinked_count} {noun}")
    if warning_count:
        noun = "warning" if warning_count == 1 else "warnings"
        parts.append(f"{warning_count} {noun}")
    period = period_label(interval_minutes)
    body = " · ".join(parts) if parts else f"Nothing new in the {period}."
    if preview:
        return f"Preview so far · {body}"
    return body


def _format_next(next_at: datetime, now: datetime) -> str:
    zone = local_timezone()
    local_next = _aware(next_at).astimezone(zone)
    local_now = _aware(now).astimezone(zone)
    clock = f"{local_next.hour:02d}:{local_next.minute:02d}"
    if local_next.date() == local_now.date():
        return f"today, {clock}"
    if local_next.date() == local_now.date() + timedelta(days=1):
        return f"tomorrow, {clock}"
    weekday = _WEEKDAYS[local_next.weekday()]
    month = _MONTHS[local_next.month - 1]
    return f"{weekday} {local_next.day} {month}, {clock}"


def _footer(
    *,
    preview: bool,
    interval_minutes: int,
    next_at: datetime | None,
    window_end: datetime,
    version: str,
) -> str:
    if preview:
        parts = ["Preview so far"]
    else:
        label = period_label(interval_minutes)
        parts = [label[:1].upper() + label[1:]]
    if next_at is not None:
        parts.append(f"next digest {_format_next(next_at, window_end)}")
    parts.append(f"v{version}")
    return " · ".join(parts)


def _within_budget(payload: dict[str, Any]) -> bool:
    embeds = payload["embeds"]
    content = str(payload.get("content") or "")
    if message_char_count(embeds, content) > TOTAL_CHAR_TARGET:
        return False
    if _embed_units(embeds) > MAX_TOTAL_CHARS:
        return False
    if discord_units(content) > CONTENT_HARD_MAX:
        return False
    for embed in embeds:
        if discord_units(embed.get("description") or "") > DESCRIPTION_HARD_MAX:
            return False
        if discord_units(embed.get("title") or "") > MAX_TITLE_CHARS:
            return False
        footer = embed.get("footer") or {}
        footer_text = footer.get("text") if isinstance(footer, dict) else ""
        if discord_units(footer_text or "") > FOOTER_HARD_MAX:
            return False
    return True


def _place_footer(embeds: list[dict[str, Any]], footer: str, window_end: datetime) -> None:
    for embed in embeds:
        embed.pop("footer", None)
        embed.pop("timestamp", None)
    if not embeds:
        return
    embeds[-1]["footer"] = {"text": footer}
    embeds[-1]["timestamp"] = _aware(window_end).astimezone(UTC).isoformat()


def _clone_embeds(embeds: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return [dict(embed) for embed in embeds]


def _fits(
    embeds: Sequence[dict[str, Any]],
    content: str,
    footer: str | None,
    window_end: datetime,
) -> bool:
    """True when these embeds fit in one Discord message, footer included when set."""
    if len(embeds) > DISCORD_MAX_EMBEDS:
        return False
    trial = _clone_embeds(embeds)
    if footer is not None:
        if not trial:
            return False
        _place_footer(trial, footer, window_end)
    return _within_budget({"content": content, "embeds": trial})


def _finish_message(
    embeds: Sequence[dict[str, Any]],
    content: str,
    footer: str | None,
    window_end: datetime,
) -> dict[str, Any]:
    body = _clone_embeds(embeds)
    if footer and body:
        _place_footer(body, footer, window_end)
    message: dict[str, Any] = {"embeds": body}
    if content:
        message["content"] = content
    return message


def _overflow_embed(hidden: int) -> dict[str, Any]:
    noun = "game" if hidden == 1 else "games"
    return {"description": f"…and {hidden} more {noun}", "color": GAME_COLOR}


def _description_budget(title: str, content: str, footer: str, extra: int = 0) -> int:
    """UTF-16 units left for one description beside the content line and footer."""
    used = discord_units(content) + discord_units(title) + discord_units(footer) + extra
    embed_used = discord_units(title) + discord_units(footer) + extra
    return max(
        0,
        min(DESCRIPTION_HARD_MAX, TOTAL_CHAR_TARGET - used, MAX_TOTAL_CHARS - embed_used),
    )


def _shrink_description(embed: dict[str, Any], budget: int) -> dict[str, Any]:
    clone = dict(embed)
    clone["description"] = _safe_truncate(str(clone.get("description") or ""), budget)
    return clone


def _max_prefix(cards: Sequence[dict[str, Any]], content: str, window_end: datetime) -> int:
    """How many leading cards fit in one message that is not the last one."""
    best = 0
    lo = 1
    hi = min(len(cards), DISCORD_MAX_EMBEDS)
    while lo <= hi:
        mid = (lo + hi) // 2
        if _fits(cards[:mid], content, None, window_end):
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return best


def _try_pack(
    cards: Sequence[dict[str, Any]],
    tail: Sequence[dict[str, Any]],
    content: str,
    footer: str,
    window_end: datetime,
    max_messages: int,
) -> tuple[list[dict[str, Any]], list[int]] | None:
    """Pack greedily. `tail` (the overflow line, then Needs attention) stays on the last message.

    The second list is how many game cards each message holds. Tail embeds are not cards.
    """
    remaining = list(cards)
    messages: list[dict[str, Any]] = []
    counts: list[int] = []
    while True:
        if len(messages) >= max_messages:
            return None
        msg_content = content if not messages else ""
        closing = remaining + list(tail)
        if _fits(closing, msg_content, footer if closing else None, window_end):
            if closing:
                messages.append(_finish_message(closing, msg_content, footer, window_end))
            elif msg_content:
                messages.append({"content": msg_content, "embeds": []})
            else:
                messages.append({"embeds": []})
            counts.append(len(remaining))
            return messages, counts
        if len(messages) == max_messages - 1:
            return None
        take = _max_prefix(remaining, msg_content, window_end)
        if take <= 0:
            return None
        messages.append(_finish_message(remaining[:take], msg_content, None, window_end))
        counts.append(take)
        remaining = remaining[take:]


def _pack_messages(
    cards: Sequence[dict[str, Any]],
    attention: dict[str, Any] | None,
    content: str,
    footer: str,
    window_end: datetime,
) -> tuple[list[dict[str, Any]], int, list[int]]:
    """Messages in send order, how many leading cards they include, and cards per message."""

    def attempt(shown: int) -> tuple[list[dict[str, Any]], list[int]] | None:
        hidden = len(cards) - shown
        tail: list[dict[str, Any]] = []
        if hidden:
            tail.append(_overflow_embed(hidden))
        if attention is not None:
            tail.append(attention)
        return _try_pack(cards[:shown], tail, content, footer, window_end, MAX_DIGEST_MESSAGES)

    packed = attempt(len(cards))
    if packed is not None:
        messages, counts = packed
        return messages, len(cards), counts

    best_messages: list[dict[str, Any]] | None = None
    best_counts: list[int] = []
    best = 0
    lo = 0
    hi = max(0, len(cards) - 1)
    while lo <= hi:
        mid = (lo + hi) // 2
        trial = attempt(mid)
        if trial is not None:
            best = mid
            best_messages, best_counts = trial
            lo = mid + 1
        else:
            hi = mid - 1
    if best_messages is not None:
        return best_messages, best, best_counts

    # Attention alone was still over the budget. Cut its description and keep the games line.
    hidden = len(cards)
    overflow = _overflow_embed(hidden) if hidden else None
    extra = discord_units((overflow or {}).get("description") or "")
    title = str((attention or {}).get("title") or "")
    shrunk = attention
    if attention is not None:
        shrunk = _shrink_description(
            attention, _description_budget(title, content, footer, extra)
        )
    tail = [embed for embed in (overflow, shrunk) if embed is not None]
    fallback = _try_pack([], tail, content, footer, window_end, MAX_DIGEST_MESSAGES)
    if fallback is not None:
        messages, counts = fallback
        return messages, 0, counts
    if content:
        return [{"content": _safe_truncate(content, TOTAL_CHAR_TARGET), "embeds": []}], 0, [0]
    return [{"embeds": []}], 0, [0]


def _ordered_games(
    games: dict[str, _Game],
    *,
    include_drops: bool,
    include_progress: bool,
    include_campaigns: bool,
) -> list[_Game]:
    """Claim and progress games first, in rank order, then campaign-only games."""

    def eligible(game: _Game) -> bool:
        if include_drops and game.claims:
            return True
        return bool(include_progress and game.progress)

    def new_only(game: _Game) -> bool:
        return bool(include_campaigns and game.campaigns and not eligible(game))

    ranked = sorted(
        (game for game in games.values() if eligible(game)),
        key=lambda game: _rank_key(
            game, include_drops=include_drops, include_progress=include_progress
        ),
    )
    fresh = sorted(
        (game for game in games.values() if new_only(game)),
        key=lambda game: (
            _campaign_sort_key(min(game.campaigns, key=_campaign_sort_key))
            if game.campaigns
            else (1, 0.0, "", ""),
            game.name.casefold(),
        ),
    )
    return ranked + fresh


def _game_card(
    game: _Game,
    *,
    include_drops: bool,
    include_progress: bool,
    include_campaigns: bool,
    progress: dict[str, Any] | None,
    window_end: datetime,
) -> dict[str, Any] | None:
    description = _card_description(
        game,
        claim_cap=CLAIM_LINE_CAP,
        include_drops=include_drops,
        include_progress=include_progress,
        include_campaigns=include_campaigns,
        progress=progress,
        window_end=window_end,
    )
    if not description:
        return None
    embed: dict[str, Any] = {
        "title": _utf16_prefix(escape_discord(game.name or "a game"), MAX_TITLE_CHARS),
        "description": description,
        "color": GAME_COLOR,
    }
    image = _thumbnail(game, use_claims=include_drops)
    if image:
        embed["thumbnail"] = {"url": image}
    return embed


def _attention_embed(
    events: list[dict[str, Any]],
    error_groups: list[dict[str, Any]],
    overflow_types: int,
    dropped_count: int,
    *,
    include_stalled: bool,
    include_auth: bool,
    include_unlinked: bool,
    include_errors: bool,
) -> dict[str, Any] | None:
    attention = _attention(
        events,
        error_groups,
        overflow_types,
        dropped_count,
        include_stalled=include_stalled,
        include_auth=include_auth,
        include_unlinked=include_unlinked,
        include_errors=include_errors,
        log_cap=LOG_GROUP_CAP,
        description_budget=DESCRIPTION_HARD_MAX,
    )
    if attention is None:
        return None
    description, color = attention
    return {
        "title": "⚠️ Needs attention",
        "description": _safe_truncate(description, DESCRIPTION_HARD_MAX),
        "color": color,
    }


def _attention_seqs(
    events: list[dict[str, Any]],
    *,
    include_stalled: bool,
    include_auth: bool,
    include_unlinked: bool,
) -> list[int]:
    """Queue seqs that land in Needs attention, which is always the last message."""
    wanted: set[str] = set()
    if include_stalled:
        wanted.add("mining_stalled")
    if include_auth:
        wanted.add("auth_attention")
    if include_unlinked:
        wanted.add("unlinked_tracked_game")
    seqs: list[int] = []
    for event in events:
        if event.get("type") not in wanted:
            continue
        seq = _event_seq(event)
        if seq is not None:
            seqs.append(seq)
    return seqs


def _stamp_part_seqs(
    messages: list[dict[str, Any]],
    counts: list[int],
    paired: Sequence[tuple[_Game, dict[str, Any]]],
    attention_seqs: list[int],
) -> None:
    """Remember which queue seqs each message delivered, so a later reset can skip them."""
    offset = 0
    last = len(messages) - 1
    for index, message in enumerate(messages):
        count = counts[index] if index < len(counts) else 0
        seqs: list[int] = []
        for game, _card in paired[offset : offset + count]:
            seqs.extend(game.event_seqs)
        offset += count
        if index == last:
            seqs.extend(attention_seqs)
        message["_event_seqs"] = seqs


def _count(events: list[dict[str, Any]], kind: str) -> int:
    return sum(1 for event in events if event.get("type") == kind)


def _warning_total(groups: list[dict[str, Any]], overflow_count: int) -> int:
    return sum(int(group.get("count") or 0) for group in groups) + max(0, overflow_count)


def _unlinked_total(events: list[dict[str, Any]]) -> int:
    names = {
        " ".join(str(_as_dict(event.get("data")).get("game") or "").split()).casefold()
        for event in events
        if event.get("type") == "unlinked_tracked_game"
    }
    names.discard("")
    return len(names)


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
    include_drops: bool = True,
    include_campaigns: bool = True,
    include_unlinked: bool = True,
    include_stalled: bool = True,
    include_auth: bool = True,
    version: str,
    preview: bool = False,
) -> list[dict[str, Any]]:
    """
    Build the Discord messages for this digest window, in send order.

    Every gate is the caller's settings at render time. A toggle turned off
    after the event was queued still hides that block and its content-line count.
    `events` are serialized NotificationEvent dicts. `error_groups` are the
    aggregated WARNING/ERROR records (`level`, `count`, `latest`, `last_ts`).
    The drop total counts every claim in the window, including claims on games
    the five-message cap leaves off the card list. A ×N line counts as N, and
    +N more counts the claims hidden inside a card. New campaigns are counted
    the same way, including campaigns on games that did not get a card.
    """
    # The footer names the period. The embed timestamp is the end of the window.
    window_end = _aware(window_end)
    if next_at is not None:
        next_at = _aware(next_at)

    groups = [_as_dict(group) for group in (error_groups or [])] if include_errors else []
    overflow_types = error_overflow_types if include_errors else 0
    overflow_count = error_overflow_count if include_errors else 0

    # Progress rows stay on the buckets so a claim-eligible game still ranks
    # first while it is being mined. Display and eligibility follow the toggle.
    games = _games_from(events, progress)
    ordered = _ordered_games(
        games,
        include_drops=include_drops,
        include_progress=include_progress,
        include_campaigns=include_campaigns,
    )
    paired: list[tuple[_Game, dict[str, Any]]] = []
    for game in ordered:
        card = _game_card(
            game,
            include_drops=include_drops,
            include_progress=include_progress,
            include_campaigns=include_campaigns,
            progress=progress,
            window_end=window_end,
        )
        if card is not None:
            paired.append((game, card))

    campaign_count = _count(events, "new_campaign") if include_campaigns else 0
    stall_count = _count(events, "mining_stalled") if include_stalled else 0
    auth_count = _count(events, "auth_attention") if include_auth else 0
    unlinked_count = _unlinked_total(events) if include_unlinked else 0
    warning_count = _warning_total(groups, overflow_count) if include_errors else 0

    def totals(drop_count: int) -> str:
        return _content_line(
            preview=preview,
            interval_minutes=interval_minutes,
            drop_count=drop_count,
            campaign_count=campaign_count,
            stall_count=stall_count,
            auth_count=auth_count,
            unlinked_count=unlinked_count,
            warning_count=warning_count,
        )

    # The header counts every claim, including games the five-message cap hides.
    # Pack against that same line so the message that is sent still fits.
    upper_drops = _claim_line_total(game for game, _card in paired) if include_drops else 0
    content = totals(upper_drops)
    footer = _footer(
        preview=preview,
        interval_minutes=interval_minutes,
        next_at=next_at,
        window_end=window_end,
        version=version,
    )
    attention = _attention_embed(
        events,
        groups,
        overflow_types,
        max(0, dropped_count),
        include_stalled=include_stalled,
        include_auth=include_auth,
        include_unlinked=include_unlinked,
        include_errors=include_errors,
    )
    if attention is not None:
        extra = discord_units(_overflow_embed(max(len(paired), 1))["description"])
        budget = _description_budget(str(attention["title"]), content, footer, extra)
        if discord_units(str(attention.get("description") or "")) > budget:
            attention = _shrink_description(attention, budget)

    if not paired and attention is None:
        return [{"content": content, "embeds": []}]

    messages, _shown, counts = _pack_messages(
        [card for _game, card in paired],
        attention,
        content,
        footer,
        window_end,
    )
    attention_seqs = (
        _attention_seqs(
            events,
            include_stalled=include_stalled,
            include_auth=include_auth,
            include_unlinked=include_unlinked,
        )
        if attention is not None
        else []
    )
    _stamp_part_seqs(messages, counts, paired, attention_seqs)
    return messages
