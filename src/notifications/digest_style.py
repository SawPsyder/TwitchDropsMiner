"""
Discord digest copy, colours and limits.

v2 (2026-10-01): one purple card per game, an optional More games embed, and
Needs attention last. Two colours. Totals sit on the message content line.
The window and the next send sit in the last embed's footer.
"""

from __future__ import annotations


# Game cards and More games. Needs attention is red when it holds a stall or
# a sign-in, otherwise amber.
GAME_COLOR = 0x9146FF
ATTENTION_URGENT_COLOR = 0xE74C3C
ATTENTION_WARNING_COLOR = 0xF1C40F

# Discord's hard limits. The trimmer aims at 5800 including the content line,
# which is outside Discord's 6000 embed budget, so a normal message keeps a margin.
# At most 8 embeds: 6 cards, More games, and Needs attention.
MAX_EMBEDS = 8
DISCORD_MAX_EMBEDS = 10
MAX_TOTAL_CHARS = 6000
TOTAL_CHAR_TARGET = 5800
DESCRIPTION_HARD_MAX = 4096
MAX_TITLE_CHARS = 256
CONTENT_HARD_MAX = 2000

CARD_CAP = 6
CARD_FLOOR = 3
MORE_GAMES_CAP = 15
MORE_GAMES_FLOOR = 5
CLAIM_LINE_CAP = 4
CLAIM_LINE_FLOOR = 2
PROGRESS_LINE_CAP = 2
CAMPAIGN_LINE_CAP = 2
LOG_GROUP_CAP = 5
LOG_GROUP_FLOOR = 2
UNLINKED_NAME_CAP = 10
MORE_BENEFIT_CAP = 3
MORE_LINE_UNITS = 120

PROGRESS_BAR_CELLS = 10
NAME_CHAR_CAP = 80
LOG_CHAR_CAP = 100

QUEUE_CAP = 500
ERROR_GROUP_CAP = 100
LOG_PATH_HINT = "logs/TDM.log"
BOX_ART_SIZE = "144x192"
