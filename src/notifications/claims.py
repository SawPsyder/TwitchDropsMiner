"""Identity of a drop claim that has already been announced.

A notification is one claim of one drop. The key is the campaign id, the drop
id, and each benefit id. Twitch's ``dropInstanceID`` — the inventory
``self.dropInstanceID`` and the websocket ``drop_instance_id`` — is
``userId#campaignId#dropId`` (see ``BaseDrop.generate_claim``). That string
repeats the campaign and the drop, so it is not an extra segment. A claim id
in any other shape is ignored. Keeping it would let the websocket path and the
inventory path notify twice for the same claim.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any


CLAIM_TTL = timedelta(days=7)
CLAIM_HISTORY_MAX = 2000
_FIELD = "\x1f"


def _text(value: object) -> str:
    if value is None or isinstance(value, bool):
        return ""
    return str(value).strip()


def _norm(value: object) -> str:
    return " ".join(_text(value).split()).casefold()


def is_twitch_claim_id(claim_id: str, campaign_id: str, drop_id: str) -> bool:
    """True when ``claim_id`` is Twitch's ``userId#campaignId#dropId``."""
    suffix = f"#{campaign_id}#{drop_id}"
    if not claim_id.endswith(suffix):
        return False
    prefix = claim_id[: -len(suffix)]
    return bool(prefix) and "#" not in prefix


# The old name. Callers and tests still say "synthetic" for this shape.
is_synthetic_claim_id = is_twitch_claim_id


def claim_keys(
    campaign_id: object,
    drop_id: object,
    claim_id: object,
    benefit_ids: list[str],
) -> list[str]:
    """One key per benefit. Empty when the campaign or drop id is missing.

    Missing ids cannot be deduped. Callers still announce those claims, which
    is how events queued before this key existed keep working.
    """
    campaign = _text(campaign_id)
    drop = _text(drop_id)
    if not campaign or not drop:
        return []
    # Inventory stores ``self.dropInstanceID`` (``drop.py``). The websocket
    # copies ``drop_instance_id`` onto the drop before ``claim()``
    # (``message_handlers.py``). When Twitch minted the claim both are
    # ``userId#campaignId#dropId``, which repeats the campaign and the drop,
    # so a matching id is not its own segment. Any other id is dropped too
    # (``is_twitch_claim_id``): appending it would make that websocket report
    # a different key from the inventory report of the same claim.
    claim = _text(claim_id)
    if (claim and not is_twitch_claim_id(claim, campaign, drop)) or is_twitch_claim_id(
        claim, campaign, drop
    ):
        claim = ""
    instance = claim
    benefits = benefit_ids or [""]
    keys: list[str] = []
    seen: set[str] = set()
    for benefit_id in benefits:
        key = _FIELD.join((campaign, drop, benefit_id, instance))
        if key in seen:
            continue
        seen.add(key)
        keys.append(key)
    return keys


def _id_list(values: object) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        text = values.strip()
        return [text] if text else []
    if not isinstance(values, list | tuple):
        return []
    ids: list[str] = []
    for value in values:
        text = _text(value)
        if text:
            ids.append(text)
    return ids


def _stamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        stamp = datetime.fromisoformat(value)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return stamp


def coerce_claim_history(raw: object) -> list[dict[str, Any]]:
    """Drop entries that cannot be a claim record. Unknown extras are ignored."""
    if not isinstance(raw, list):
        return []
    clean: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        key = item.get("k")
        if not isinstance(key, str) or not key or _stamp(item.get("at")) is None:
            continue
        game_id = _text(item.get("game_id"))
        clean.append(
            {
                "k": key,
                "at": _text(item.get("at")),
                "game": _text(item.get("game")),
                "game_id": game_id,
                "campaign": _text(item.get("campaign")),
                "benefit": _text(item.get("benefit")),
            }
        )
    return clean


def _claim_group(key: str) -> str:
    """Campaign, drop, and instance. Benefit keys of one claim share this."""
    parts = key.split(_FIELD)
    if len(parts) >= 4:
        return _FIELD.join((parts[0], parts[1], parts[3]))
    return key


def prune_claim_history(
    entries: list[dict[str, Any]],
    now: datetime,
    *,
    ttl: timedelta = CLAIM_TTL,
    limit: int = CLAIM_HISTORY_MAX,
) -> list[dict[str, Any]]:
    """Drop expired records, then the oldest claims past ``limit``.

    A claim is every benefit key that shares a campaign, drop, and instance.
    Those rows leave together. The newest claim is kept whole even when it
    alone is wider than ``limit``. Newest last.
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    cutoff = now - ttl
    groups: dict[str, list[tuple[datetime, dict[str, Any]]]] = {}
    for entry in entries:
        stamp = _stamp(entry.get("at"))
        if stamp is None or stamp <= cutoff:
            continue
        group = _claim_group(str(entry.get("k") or ""))
        groups.setdefault(group, []).append((stamp, entry))
    ordered = sorted(groups, key=lambda group: min(stamp for stamp, _entry in groups[group]))
    sizes = {group: len(groups[group]) for group in ordered}
    total = sum(sizes.values())
    start = 0
    while start < len(ordered) - 1 and total > limit:
        total -= sizes[ordered[start]]
        start += 1
    kept = [pair for group in ordered[start:] for pair in groups[group]]
    kept.sort(key=lambda pair: pair[0])
    return [entry for _stamp_value, entry in kept]


def build_claim_records(
    *,
    campaign_id: object,
    drop_id: object,
    claim_id: object,
    benefit_ids: object,
    benefit_names: list[str],
    game: str,
    game_id: object,
    campaign: str,
    now: datetime,
) -> list[dict[str, Any]]:
    """History rows for one claim. Empty when there is no stable id."""
    ids = _id_list(benefit_ids)
    keys = claim_keys(campaign_id, drop_id, claim_id, ids)
    if not keys:
        return []
    names_for: dict[str, str] = {}
    for index, benefit_id in enumerate(ids):
        if index < len(benefit_names):
            names_for[benefit_id] = benefit_names[index]
    if not ids and benefit_names:
        names_for[""] = benefit_names[0]
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    stamp = now.astimezone(UTC).isoformat()
    game_id_text = "" if isinstance(game_id, bool) else _text(game_id)
    records: list[dict[str, Any]] = []
    for key in keys:
        benefit_id = key.split(_FIELD)[2]
        records.append(
            {
                "k": key,
                "at": stamp,
                "game": game,
                "game_id": game_id_text,
                "campaign": campaign,
                "benefit": names_for.get(benefit_id, ""),
            }
        )
    return records


def _same_game(entry: dict[str, Any], game: str, game_id: str) -> bool:
    entry_id = _text(entry.get("game_id"))
    if game_id and entry_id:
        return game_id == entry_id
    return _norm(entry.get("game")) == _norm(game)


def repeating_benefit_names(
    history: list[dict[str, Any]],
    *,
    game: str,
    game_id: object,
    campaign: str,
    benefits: list[str],
) -> list[str]:
    """Benefit names this game already announced under a different campaign.

    The names keep their original spelling so a digest line can show them.
    """
    camp = _norm(campaign)
    if not camp:
        return []
    gid = "" if isinstance(game_id, bool) else _text(game_id)
    prior: set[tuple[str, str]] = set()
    for entry in history:
        if not _same_game(entry, game, gid):
            continue
        benefit = _norm(entry.get("benefit"))
        entry_camp = _norm(entry.get("campaign"))
        if benefit and entry_camp:
            prior.add((benefit, entry_camp))
    forced: list[str] = []
    seen: set[str] = set()
    for name in benefits:
        key = _norm(name)
        if not key or key in seen:
            continue
        campaigns = {entry_camp for benefit, entry_camp in prior if benefit == key}
        if any(entry_camp != camp for entry_camp in campaigns):
            forced.append(name.strip())
            seen.add(key)
    return forced
