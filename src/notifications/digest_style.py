"""
Discord digest copy, colours and limits.

Every string and colour the digest message uses lives here so a copy or palette
change stays in one file. These match the UX spec (2026-09-25): description
lines rather than embed fields, spec hex colours, and a single message.
"""

from __future__ import annotations


# spec section colours. Drops, campaigns and unlinked use the hex from the spec
# (green / blue / orange) rather than the immediate-mode EVENT_COLORS map, whose
# current values are purple / green / amber and would paint drops the same colour
# as the header.
HEADER_COLOR = 0x9146FF
ATTENTION_URGENT_COLOR = 0xE74C3C
ATTENTION_WARNING_COLOR = 0xF1C40F
DROPS_COLOR = 0x2ECC71
CAMPAIGNS_COLOR = 0x3498DB
PROGRESS_COLOR = 0x95A5A6
UNLINKED_COLOR = 0xE67E22

# Discord's documented limits, and the tighter targets the trimmer aims for.
MAX_EMBEDS = 10
MAX_EMBED_SLOTS = 6
MAX_TOTAL_CHARS = 6000
TOTAL_CHAR_TARGET = 5800
MAX_DESCRIPTION_CHARS = 4000
DESCRIPTION_HARD_MAX = 4096
MAX_TITLE_CHARS = 256

DROP_LINE_CAP = 40
CAMPAIGN_LINE_CAP = 15
PROGRESS_LINE_CAP = 5
UNLINKED_LINE_CAP = 20
WARNING_GROUP_CAP = 10
NAME_CHAR_CAP = 80
LOG_CHAR_CAP = 150

QUEUE_CAP = 500
ERROR_GROUP_CAP = 100

# generalized from the immediate-mode unlinked notification, which names one
# game and one campaign. The digest lists every game underneath this sentence.
UNLINKED_EXPLANATION = (
    "A tracked game has an active campaign but its Twitch account isn't linked yet"
    " - link it to start earning."
)

INVENTORY_SOURCE = "inventory"
FOOTER_PREFIX = "TwitchDropsMiner v"
LOG_PATH_HINT = "logs/TDM.log"
