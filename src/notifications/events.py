"""A single notification waiting to be delivered or folded into a digest."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass
class NotificationEvent:
    """Structured mining event. Rendering happens at send time, not at emit time."""

    type: str
    ts: datetime
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        stamp = self.ts if self.ts.tzinfo is not None else self.ts.replace(tzinfo=UTC)
        return {
            "type": self.type,
            "ts": stamp.astimezone(UTC).isoformat(),
            "data": dict(self.data),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> NotificationEvent:
        stamp = datetime.fromisoformat(str(raw.get("ts")))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=UTC)
        data = raw.get("data")
        return cls(
            type=str(raw.get("type", "")),
            ts=stamp,
            data=dict(data) if isinstance(data, dict) else {},
        )
