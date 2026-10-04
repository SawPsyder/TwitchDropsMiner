"""
Discord digest copy, colours and limits.

One purple card per game, packed greedily across messages. Needs attention is
the last embed of the last message. Totals sit on the first message only.
The window and the next send sit in the footer of that message's last embed.
"""

from __future__ import annotations


# Game cards. Needs attention is red when it holds a stall or a sign-in,
# otherwise amber.
GAME_COLOR = 0x9146FF
ATTENTION_URGENT_COLOR = 0xE74C3C
ATTENTION_WARNING_COLOR = 0xF1C40F

# Discord's hard limits. Each message aims at 5800 including its content line,
# which is outside Discord's 6000 embed budget, so a normal message keeps a margin.
# At most 10 embeds per message, and at most 5 messages per digest.
DISCORD_MAX_EMBEDS = 10
MAX_DIGEST_MESSAGES = 5
MAX_TOTAL_CHARS = 6000
TOTAL_CHAR_TARGET = 5800
DESCRIPTION_HARD_MAX = 4096
MAX_TITLE_CHARS = 256
CONTENT_HARD_MAX = 2000
FOOTER_HARD_MAX = 2048

CLAIM_LINE_CAP = 4
PROGRESS_LINE_CAP = 2
CAMPAIGN_LINE_CAP = 2
LOG_GROUP_CAP = 5
UNLINKED_NAME_CAP = 10

PROGRESS_BAR_CELLS = 10
NAME_CHAR_CAP = 80
LOG_CHAR_CAP = 100

QUEUE_CAP = 500
ERROR_GROUP_CAP = 100
LOG_PATH_HINT = "logs/TDM.log"
BOX_ART_SIZE = "144x192"
