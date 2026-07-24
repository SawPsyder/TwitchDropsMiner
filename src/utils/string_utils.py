"""String manipulation utility functions."""

from __future__ import annotations

import random
import string
from collections import OrderedDict, abc
from typing import TypeVar


# Character sets for nonce generation
CHARS_ASCII = string.ascii_letters + string.digits
CHARS_HEX_LOWER = string.digits + "abcdef"
CHARS_HEX_UPPER = string.digits + "ABCDEF"

_T = TypeVar("_T")


def create_nonce(chars: str, length: int) -> str:
    """Generate a random nonce string of specified length from given characters."""
    return "".join(random.choices(chars, k=length))


def chunk(to_chunk: abc.Iterable[_T], chunk_length: int) -> abc.Generator[list[_T], None, None]:
    """Split an iterable into chunks of a specified length."""
    list_to_chunk: list[_T] = list(to_chunk)
    for i in range(0, len(list_to_chunk), chunk_length):
        yield list_to_chunk[i : i + chunk_length]


def deduplicate(iterable: abc.Iterable[_T]) -> list[_T]:
    """Remove duplicates from an iterable while preserving order."""
    return list(OrderedDict.fromkeys(iterable).keys())


def parse_version(version: str) -> tuple[int, ...]:
    """
    Parse a dotted version string into a tuple of integers for ordered comparison.

    Leading ``v`` and any pre-release/build suffix (e.g. ``1.7.1-rc2``) are
    stripped; only the leading numeric dotted segment is compared. This avoids
    the string-comparison bug where ``"10.0" < "9.0"`` because ``"1" < "9"``.
    Unparseable segments stop parsing, so ``"1.7.1-rc2"`` -> ``(1, 7, 1)``.
    """
    cleaned = version.strip().lstrip("vV")
    parts: list[int] = []
    for segment in cleaned.split("."):
        # take the leading run of digits in the segment (handles "1rc2" -> 1)
        digits = ""
        for ch in segment:
            if ch.isdigit():
                digits += ch
            else:
                break
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)
