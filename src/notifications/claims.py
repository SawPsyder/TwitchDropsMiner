"""Identity of a drop claim that has already been announced.

A notification is one claim of one drop instance. The key is the campaign id,
the drop id, and each benefit id. Twitch's usual ``dropInstanceID`` is
``userId#campaignId#dropId`` (see ``BaseDrop.generate_claim``), so it does not
name a second grant of the same drop. A claim id in any other shape is a new
instance and is part of the key, which lets a drop that re-arms under a fresh
instance id notify again. A repeatable drop that keeps the same campaign, drop,
benefit, and synthetic instance id cannot be told apart from a second report
of the first grant, so it stays quiet until the record expires.
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


def is_synthetic_claim_id(claim_id: str, campaign_id: str, drop_id: str) -> bool:
    """True when ``claim_id`` is only ``userId#campaignId#dropId``."""
    suffix = f"#{campaign_id}#{drop_id}"
    if not claim_id.endswith(suffix):
        return False
    prefix = claim_id[: -len(suffix)]
    return bool(prefix) and "#" not in prefix


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
    claim = _text(claim_id)
    instance = ""
    if claim and not is_synthetic_claim_id(claim, campaign, drop):
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


def prune_claim_history(
    entries: list[dict[str, Any]],
    now: datetime,
    *,
    ttl: timedelta = CLAIM_TTL,
    limit: int = CLAIM_HISTORY_MAX,
) -> list[dict[str, Any]]:
    """Drop expired records, then the oldest ones past ``limit``. Newest last."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    cutoff = now - ttl
    kept: list[tuple[datetime, dict[str, Any]]] = []
    for entry in entries:
        stamp = _stamp(entry.get("at"))
        if stamp is None or stamp <= cutoff:
            continue
        kept.append((stamp, entry))
    kept.sort(key=lambda pair: pair[0])
    if len(kept) > limit:
        kept = kept[-limit:]
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
