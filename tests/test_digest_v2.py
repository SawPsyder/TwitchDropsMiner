"""Discord digest v2: one card per game, gated at render time."""

from __future__ import annotations

import logging
import os
import tempfile
import unittest
import unittest.mock
from datetime import UTC, datetime, timedelta
from pathlib import Path

from src.notifications import NotificationService
from src.notifications.digest_style import TOTAL_CHAR_TARGET
from src.notifications.events import NotificationEvent
from src.notifications.render import discord_tag, message_char_count, render_digest
from tests.test_notifications import (
    FakeCampaign,
    FakeSettings,
    digest_notification_settings,
    make_notification_settings,
)


END = datetime(2026, 10, 1, 18, 0, tzinfo=UTC)
NEXT = datetime(2026, 10, 2, 18, 0, tzinfo=UTC)
VERSION = "1.10.1"

PREMIUM = "https://img.example/wot-premium.png"
NARAKA_XP = "https://img.example/naraka-xp.png"
SEED = "https://img.example/finals-seed.png"
ESPORTS = "https://img.example/r6-esports.png"
WOLF = "https://img.example/payday-wolf.png"
CAMO = "https://img.example/am-camo.png"


def _at(month: int, day: int, hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(2026, month, day, hour, minute, second, tzinfo=UTC)


def _drop(
    when: datetime,
    game: str,
    game_id: int,
    benefits: list[str],
    images: list[str | None] | None = None,
) -> dict:
    return {
        "type": "drop_received",
        "ts": when.isoformat(),
        "data": {
            "game": game,
            "game_id": game_id,
            "campaign": "Camp",
            "drop": benefits[0] if benefits else "",
            "benefits": benefits,
            "benefit_images": list(images) if images is not None else [None] * len(benefits),
            "channel": "inventory",
            "game_box_art": f"https://img.example/{game_id}-{{width}}x{{height}}.jpg",
        },
    }


def _campaign(
    game: str,
    game_id: int,
    name: str,
    ends: datetime,
    *,
    starts: datetime | None = None,
) -> dict:
    return {
        "type": "new_campaign",
        "ts": END.isoformat(),
        "data": {
            "game": game,
            "game_id": game_id,
            "campaign": name,
            "starts_at": starts.isoformat() if starts is not None else None,
            "ends_at": ends.isoformat(),
            "game_box_art": f"https://img.example/{game_id}-{{width}}x{{height}}.jpg",
        },
    }


def _screenshot_inputs() -> dict:
    """The 30 Sep 18:00–1 Oct 18:00 window from the v1 screenshot, as v2 should render it.

    Section 8 writes both NARAKA campaign lines as "NBPL 10.1". The screenshot
    itself distinguishes NBPL 101 and NBPL 10.1; the mock is the render target.
    """
    events = [
        _drop(
            _at(9, 30, 20, 0),
            "World of Tanks",
            1,
            ["5x Consumables", "5x Consumables", "5x Consumables", "1d of WoT Premium Account"],
            [None, None, None, PREMIUM],
        ),
        _drop(_at(9, 30, 20, 30), "World of Tanks: HEAT", 9, ["Vehicle XP Booster"], ["https://img.example/heat-1.png"]),
        _drop(_at(9, 30, 22, 20), "World of Tanks: HEAT", 9, ["Booster Pack"], ["https://img.example/heat-2.png"]),
        _drop(_at(9, 30, 19, 50), "Warframe", 8, ["Built Forma"], ["https://img.example/wf-1.png"]),
        _drop(_at(9, 30, 23, 20), "Warframe", 8, ["Beach Kavat Floof"], ["https://img.example/wf-2.png"]),
        _drop(_at(10, 1, 1, 50), "Rocket League", 7, ["RLCS 2025 Very Rare"], ["https://img.example/rl-1.png"]),
        _drop(_at(10, 1, 1, 54), "Rocket League", 7, ["RLCS 2025 Import Drop"], ["https://img.example/rl-2.png"]),
        _drop(_at(10, 1, 2, 33), "NARAKA: BLADEPOINT", 2, ["Serene Treasure Choice Gift"], ["https://img.example/naraka-serene.png"]),
        _drop(_at(10, 1, 3, 45), "Active Matter", 6, ["Prime x250"], ["https://img.example/am-prime.png"]),
        _drop(_at(10, 1, 3, 45, 1), "Active Matter", 6, ["Camouflage: Wave"], [CAMO]),
        _drop(_at(10, 1, 4, 3), "Dungeons & Dragons", 13, ["/dnd d20 Badge"], ["https://img.example/dnd.png"]),
        _drop(_at(10, 1, 4, 6), "PAYDAY 3", 5, ["Hoxton"], ["https://img.example/payday-hoxton.png"]),
        _drop(_at(10, 1, 4, 34), "PAYDAY 3", 5, ["Wolf"], [WOLF]),
        _drop(_at(10, 1, 7, 14), "Rainbow Six Siege", 4, ["OL'CLANKER"], ["https://img.example/r6-clanker.png"]),
        _drop(_at(10, 1, 13, 13), "Rainbow Six Siege", 4, ["Esports Pack"], [ESPORTS]),
        _drop(_at(10, 1, 9, 44), "World of Warships", 12, ["15.8 CC Mission #4"], ["https://img.example/wows.png"]),
        _drop(_at(10, 1, 10, 28), "NARAKA: BLADEPOINT", 2, ["Tae*200"], ["https://img.example/naraka-tae.png"]),
        _drop(_at(10, 1, 10, 43), "NARAKA: BLADEPOINT", 2, ["Spectral Silk*200"], ["https://img.example/naraka-silk.png"]),
        _drop(_at(10, 1, 11, 3), "NARAKA: BLADEPOINT", 2, ["10x XP Bonus"], [NARAKA_XP]),
        _drop(_at(10, 1, 11, 43), "Alien: Isolation", 11, ["SEEGSON Synthetics"], ["https://img.example/alien.png"]),
        _drop(_at(10, 1, 14, 13), "THE FINALS", 3, ["Forage Fortune"], ["https://img.example/finals-forage.png"]),
        _drop(_at(10, 1, 16, 44), "THE FINALS", 3, ["Seed Money"], [SEED]),
        _drop(_at(10, 1, 16, 14), "Black Desert", 10, ["2 Hour Reward"], ["https://img.example/bdo.png"]),
        _campaign("World of Tanks", 1, "DOOM Drops#1", END + timedelta(hours=12)),
        _campaign("NARAKA: BLADEPOINT", 2, "NBPL 10.1", END + timedelta(hours=7)),
        _campaign("NARAKA: BLADEPOINT", 2, "NBPL 10.1", END + timedelta(days=3)),
        _campaign("THE FINALS", 3, "FALL IS HERE", END + timedelta(days=14)),
        _campaign("Warframe", 8, "Emisson Tenno #347 Raid", END + timedelta(days=2)),
        _campaign("Warframe", 8, "Emisson Tenno #347", END + timedelta(days=4)),
        _campaign("Rocket League", 7, "The General's RL", END + timedelta(days=1)),
        _campaign("World of Tanks: HEAT", 9, "HEAT Season 2 - Week 5", END + timedelta(days=6)),
        _campaign("Black Desert", 10, "2026 BDO Drops", END + timedelta(days=2)),
        _campaign("World of Warships", 12, "Worth Their Salt", END + timedelta(days=7)),
        _campaign("Alien: Isolation", 11, "Alien: Isolation Horror", END + timedelta(days=28)),
        _campaign("Madden NFL 27", 14, "Madden Twitch", END + timedelta(days=10)),
        _campaign("Escape from Tarkov", 15, "Slowly 1st", END + timedelta(hours=7)),
        _campaign("ELDEN RING", 16, "Bloody Finger", END + timedelta(days=28)),
        {
            "type": "unlinked_tracked_game",
            "ts": END.isoformat(),
            "data": {"game": "Alien: Isolation", "campaign": "Alien: Isolation Horror"},
        },
    ]
    progress = {
        "state": "watching",
        "channel": "quickybaby",
        "game": "World of Tanks",
        "game_id": 1,
        "campaigns": [
            {
                "game": "World of Tanks",
                "game_id": 1,
                "campaign": "DOOM Drops#1",
                "drop": "DOOM Drops#1",
                "percent": 25,
                "remaining_minutes": 135,
                "mining_now": True,
                "benefit_images": ["https://img.example/doom.png"],
                "game_box_art": "https://img.example/1-{width}x{height}.jpg",
            },
            {
                "game": "THE FINALS",
                "game_id": 3,
                "campaign": "Havoc Week",
                "drop": "Havoc Raker Sledgehammer",
                "percent": 62,
                "remaining_minutes": 90,
                "mining_now": False,
                "benefit_images": ["https://img.example/finals-progress.png"],
                "game_box_art": "https://img.example/3-{width}x{height}.jpg",
            },
            {
                "game": "Rainbow Six Siege",
                "game_id": 4,
                "campaign": "Wasteland",
                "drop": "Esports Pack",
                "percent": 33,
                "remaining_minutes": 120,
                "mining_now": False,
                "benefit_images": ["http://insecure.example/r6.png"],
                "game_box_art": "https://img.example/4-{width}x{height}.jpg",
            },
            {
                "game": "Madden NFL 27",
                "game_id": 14,
                "campaign": "Madden Twitch",
                "drop": "Madden Twitch Pack",
                "percent": 0,
                "remaining_minutes": 15,
                "mining_now": False,
                "benefit_images": [None],
                "game_box_art": "https://img.example/14-{width}x{height}.jpg",
            },
        ],
    }
    groups = [
        {"level": "WARNING", "count": 25, "latest": "Stream state change for a non-existing channel: 65119151", "last_ts": END.isoformat()},
        {"level": "WARNING", "count": 24, "latest": "Websocket[0] connection to wss://pubsub-edge.twitch.tv/v1 reconnecting…", "last_ts": END.isoformat()},
        {"level": "WARNING", "count": 9, "latest": "Websocket[0] didn't receive a PONG, reconnecting…", "last_ts": END.isoformat()},
        {"level": "WARNING", "count": 4, "latest": "Websocket[0] requested reconnect.", "last_ts": END.isoformat()},
        {
            "level": "WARNING",
            "count": 2,
            "latest": (
                "Xbox library sync: entitlements unavailable: Xbox: the account's authorization was "
                "revoked after the refresh token stopped working"
            ),
            "last_ts": END.isoformat(),
        },
    ]
    return {"events": events, "progress": progress, "error_groups": groups}


def screenshot_payload(**overrides) -> dict:
    inputs = _screenshot_inputs()
    kwargs = {
        "events": inputs["events"],
        "error_groups": inputs["error_groups"],
        "window_start": END - timedelta(hours=24),
        "window_end": END,
        "next_at": NEXT,
        "interval_minutes": 1440,
        "progress": inputs["progress"],
        "version": VERSION,
    }
    kwargs.update(overrides)
    return render_digest(**kwargs)


def _overflow_inputs() -> dict:
    """19 claim games, 2 progress-only, 4 new-only. 41 claim events, 9 campaigns, one stall."""
    base = END - timedelta(hours=12)
    events: list[dict] = []
    # Top 6 by claim-event count, newest activity last so recency doesn't reshuffle them.
    cards = [
        ("Mined", 1, 8),
        ("Bravo", 2, 5),
        ("Charlie", 3, 4),
        ("Delta", 4, 3),
        ("Echo", 5, 3),
        ("Foxtrot", 6, 2),
    ]
    for name, game_id, count in cards:
        for index in range(count):
            when = base + timedelta(minutes=game_id * 100 + index)
            events.append(
                _drop(when, name, game_id, [f"Card {name} {index}"], [f"https://img.example/{game_id}-{index}.png"])
            )
    # Three remainder games with 2 older claims. Game G's benefits spill past 3 names.
    events.append(
        _drop(
            base - timedelta(hours=5),
            "Game G",
            7,
            ["Reward A", "Reward B", "Reward C"],
            ["https://img.example/g.png", None, None],
        )
    )
    events.append(_drop(base - timedelta(hours=4), "Game G", 7, ["Reward D"], ["https://img.example/g2.png"]))
    events.append(_drop(base - timedelta(hours=6), "Game H", 8, ["Reward E"], ["https://img.example/h.png"]))
    events.append(_drop(base - timedelta(hours=5, minutes=30), "Game H", 8, ["Reward F"], ["https://img.example/h2.png"]))
    events.append(_drop(base - timedelta(hours=7), "Game I", 9, ["Reward G"], ["https://img.example/i.png"]))
    events.append(_drop(base - timedelta(hours=6, minutes=30), "Game I", 9, ["Reward H"], ["https://img.example/i2.png"]))
    singles = ["Game J", "Game K", "Game L", "Game M", "Game N", "Game O", "Game P", "Game Q", "Game R", "Game S"]
    for offset, name in enumerate(singles):
        events.append(
            _drop(
                base - timedelta(hours=8, minutes=offset),
                name,
                20 + offset,
                [f"Solo {name}"],
                [f"https://img.example/solo-{offset}.png"],
            )
        )
    # 5 extra campaigns sit on claim games, so they count but don't add a More games row.
    for name, game_id in (("Bravo", 2), ("Charlie", 3), ("Delta", 4), ("Echo", 5), ("Foxtrot", 6)):
        events.append(_campaign(name, game_id, f"{name} extra", END + timedelta(days=game_id)))
    new_only = [
        ("Game W", 30, END + timedelta(hours=3)),
        ("Game X", 31, END + timedelta(days=2)),
        ("Game Y", 32, END + timedelta(days=5)),
        ("Game Z", 33, END + timedelta(days=9)),
    ]
    for name, game_id, ends in new_only:
        events.append(_campaign(name, game_id, f"{name} launch", ends))
    events.append(
        {
            "type": "mining_stalled",
            "ts": (END - timedelta(hours=2)).isoformat(),
            "data": {"reason": "no channels", "alerted": True},
        }
    )
    progress = {
        "state": "watching",
        "channel": "streamer",
        "game": "Mined",
        "game_id": 1,
        "stalled_since": (END - timedelta(hours=2)).isoformat(),
        "campaigns": [
            {
                "game": "Mined",
                "game_id": 1,
                "campaign": "Live",
                "drop": "Live Drop",
                "percent": 10,
                "remaining_minutes": 40,
                "mining_now": True,
                "benefit_images": ["https://img.example/mined-progress.png"],
                "game_box_art": "https://img.example/mined-{width}x{height}.jpg",
            },
            {
                "game": "Game U",
                "game_id": 40,
                "campaign": "Side",
                "drop": "Reward X",
                "percent": 40,
                "remaining_minutes": 80,
                "mining_now": False,
                "benefit_images": ["https://img.example/u.png"],
                "game_box_art": "https://img.example/u-{width}x{height}.jpg",
            },
            {
                "game": "Game V",
                "game_id": 41,
                "campaign": "Side",
                "drop": "Reward Y",
                "percent": 10,
                "remaining_minutes": 20,
                "mining_now": False,
                "benefit_images": [None],
                "game_box_art": "https://img.example/v-{width}x{height}.jpg",
            },
        ],
    }
    groups = [
        {
            "level": "WARNING",
            "count": 3,
            "latest": "Websocket[0] requested reconnect.",
            "last_ts": END.isoformat(),
        }
    ]
    return {"events": events, "progress": progress, "error_groups": groups}


def overflow_payload(**overrides) -> dict:
    inputs = _overflow_inputs()
    kwargs = {
        "events": inputs["events"],
        "error_groups": inputs["error_groups"],
        "window_start": END - timedelta(hours=24),
        "window_end": END,
        "next_at": NEXT,
        "interval_minutes": 1440,
        "progress": inputs["progress"],
        "version": VERSION,
    }
    kwargs.update(overrides)
    return render_digest(**kwargs)


def _heavy_payload() -> dict:
    """Long enough that More games has to shrink, and short of the later trim steps."""
    now = END
    events: list[dict] = []
    for game_index in range(6):
        benefits = [f"Benefit {game_index}-{line} " + ("B" * 60) for line in range(4)]
        for line, benefit in enumerate(benefits):
            events.append(
                _drop(
                    now - timedelta(minutes=game_index * 10 + line),
                    f"Card {game_index}",
                    game_index + 1,
                    [benefit],
                    [f"https://img.example/c{game_index}.png"],
                )
            )
        events.append(
            _campaign(
                f"Card {game_index}",
                game_index + 1,
                "Launch " + ("C" * 60),
                now + timedelta(days=game_index + 1),
            )
        )
        events.append(
            _campaign(
                f"Card {game_index}",
                game_index + 1,
                "Follow " + ("D" * 60),
                now + timedelta(days=game_index + 8),
            )
        )
    for index in range(18):
        benefit = f"Overflow {index} " + ("M" * 70)
        events.append(
            _drop(
                now - timedelta(hours=5, minutes=index),
                f"More {index:02d}",
                100 + index,
                [benefit],
                [f"https://img.example/m{index}.png"],
            )
        )
    progress = {
        "state": "watching",
        "channel": "streamer",
        "game": "Card 0",
        "game_id": 1,
        "campaigns": [
            {
                "game": f"Card {game_index}",
                "game_id": game_index + 1,
                "campaign": f"Prog {row} " + ("P" * 50),
                "drop": f"Row {row} " + ("R" * 40),
                "percent": 50 - game_index - row,
                "remaining_minutes": 30 + game_index + row * 15,
                "mining_now": game_index == 0 and row == 0,
                "benefit_images": [f"https://img.example/p{game_index}-{row}.png"],
            }
            for game_index in range(6)
            for row in range(2)
        ],
    }
    groups = [
        {
            "level": "ERROR" if index == 0 else "WARNING",
            "count": 20 - index,
            "latest": f"log-{index} " + ("Z" * 140),
            "last_ts": now.isoformat(),
        }
        for index in range(8)
    ]
    return render_digest(
        events=events,
        error_groups=groups,
        window_start=now - timedelta(hours=24),
        window_end=now,
        next_at=NEXT,
        interval_minutes=1440,
        progress=progress,
        version=VERSION,
    )


class DigestV2Tests(unittest.TestCase):
    def setUp(self):
        self._old_tz = os.environ.get("TZ")
        os.environ["TZ"] = "UTC"

    def tearDown(self):
        if self._old_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = self._old_tz

    def test_screenshot_data_case(self):
        payload = screenshot_payload()
        embeds = payload["embeds"]
        self.assertEqual(
            payload["content"],
            "23 drops claimed · 14 new campaigns · 1 unlinked game · 64 warnings",
        )
        self.assertEqual(len(embeds), 8)
        titles = [embed["title"] for embed in embeds]
        self.assertEqual(
            titles,
            [
                "World of Tanks",
                "NARAKA: BLADEPOINT",
                "THE FINALS",
                "Rainbow Six Siege",
                "PAYDAY 3",
                "Active Matter",
                "More games",
                "⚠️ Needs attention",
            ],
        )
        self.assertTrue(all(embed["color"] == 0x9146FF for embed in embeds[:7]))
        self.assertEqual(embeds[-1]["color"], 0xF1C40F)
        thumbs = {
            "World of Tanks": PREMIUM,
            "NARAKA: BLADEPOINT": NARAKA_XP,
            "THE FINALS": SEED,
            "Rainbow Six Siege": ESPORTS,
            "PAYDAY 3": WOLF,
            "Active Matter": CAMO,
        }
        for embed in embeds[:6]:
            self.assertEqual(embed["thumbnail"]["url"], thumbs[embed["title"]])
        self.assertNotIn("thumbnail", embeds[6])
        by_title = {embed["title"]: embed["description"] for embed in embeds}
        self.assertEqual(
            by_title["World of Tanks"],
            "\n".join(
                [
                    "*Mining now on quickybaby*",
                    "✓ 5x Consumables ×3",
                    "✓ 1d of WoT Premium Account",
                    "`██░░░░░░░░  25%` DOOM Drops\\#1 · 2 h 15 min left",
                ]
            ),
        )
        self.assertNotIn("New:", by_title["World of Tanks"])
        self.assertEqual(
            by_title["NARAKA: BLADEPOINT"],
            "\n".join(
                [
                    "✓ Serene Treasure Choice Gift",
                    r"✓ Tae\*200",
                    r"✓ Spectral Silk\*200",
                    "✓ 10x XP Bonus",
                    f"New: NBPL 10.1 · ends {discord_tag(END + timedelta(hours=7), 'R')}",
                    f"New: NBPL 10.1 · ends {discord_tag(END + timedelta(days=3), 'R')}",
                ]
            ),
        )
        self.assertEqual(
            by_title["THE FINALS"],
            "\n".join(
                [
                    "✓ Forage Fortune",
                    "✓ Seed Money",
                    "`██████░░░░  62%` Havoc Raker Sledgehammer · 1 h 30 min left",
                    f"New: FALL IS HERE · ends {discord_tag(END + timedelta(days=14), 'R')}",
                ]
            ),
        )
        self.assertEqual(
            by_title["Rainbow Six Siege"],
            "\n".join(
                [
                    "✓ OL'CLANKER",
                    "✓ Esports Pack",
                    "`███░░░░░░░  33%` Esports Pack · 2 h 00 min left",
                ]
            ),
        )
        self.assertEqual(by_title["PAYDAY 3"], "✓ Hoxton\n✓ Wolf")
        self.assertEqual(by_title["Active Matter"], "✓ Prime x250\n✓ Camouflage: Wave")
        more_lines = by_title["More games"].splitlines()
        self.assertEqual(
            more_lines,
            [
                "**Rocket League** · RLCS 2025 Very Rare, RLCS 2025 Import Drop",
                "**Warframe** · Built Forma, Beach Kavat Floof",
                "**World of Tanks: HEAT** · Vehicle XP Booster, Booster Pack",
                "**Black Desert** · 2 Hour Reward",
                "**Alien: Isolation** · SEEGSON Synthetics",
                "**World of Warships** · 15.8 CC Mission \\#4",
                "**Dungeons & Dragons** · /dnd d20 Badge",
                "**Madden NFL 27** · `  0%` Madden Twitch Pack",
                f"**Escape from Tarkov** · new campaign, ends {discord_tag(END + timedelta(hours=7), 'R')}",
                f"**ELDEN RING** · new campaign, ends {discord_tag(END + timedelta(days=28), 'R')}",
            ],
        )
        attention = by_title["⚠️ Needs attention"].splitlines()
        self.assertEqual(attention[0], "**Link your account:** Alien: Isolation")
        self.assertTrue(attention[1].startswith("×25 Stream state change"))
        self.assertTrue(attention[2].startswith("×24 Websocket[0] connection"))
        self.assertTrue(attention[3].startswith("×9 Websocket[0] didn't receive a PONG"))
        self.assertEqual(attention[4], "×4 Websocket[0] requested reconnect.")
        self.assertTrue(attention[5].startswith("×2 Xbox library sync: entitlements unavailable:"))
        self.assertIn("…", attention[5])
        self.assertNotIn("Error:", by_title["⚠️ Needs attention"])
        self.assertNotIn("alerted at the time", by_title["⚠️ Needs attention"])
        self.assertNotIn("-#", "\n".join(embed["description"] for embed in embeds))
        for embed in embeds[:-1]:
            self.assertNotIn("footer", embed)
            self.assertNotIn("timestamp", embed)
        self.assertEqual(
            embeds[-1]["footer"]["text"],
            "Last 24 hours · next digest tomorrow, 18:00 · v1.10.1",
        )
        self.assertEqual(embeds[-1]["timestamp"], "2026-10-01T18:00:00+00:00")
        self.assertLessEqual(message_char_count(embeds, payload["content"]), TOTAL_CHAR_TARGET)
        self.assertLessEqual(len(_screenshot_inputs()["events"]), 80)

    def test_screenshot_errors_off_drops_the_warning_block_and_count(self):
        payload = screenshot_payload(include_errors=False)
        self.assertEqual(
            payload["content"],
            "23 drops claimed · 14 new campaigns · 1 unlinked game",
        )
        attention = payload["embeds"][-1]
        self.assertEqual(attention["title"], "⚠️ Needs attention")
        self.assertEqual(attention["description"], "**Link your account:** Alien: Isolation")
        self.assertEqual(attention["color"], 0xF1C40F)
        self.assertNotIn("Websocket", attention["description"])

    def test_overflow_shows_top_six_and_hides_four(self):
        inputs = _overflow_inputs()
        self.assertEqual(sum(1 for event in inputs["events"] if event["type"] == "drop_received"), 41)
        self.assertEqual(sum(1 for event in inputs["events"] if event["type"] == "new_campaign"), 9)
        payload = overflow_payload()
        embeds = payload["embeds"]
        self.assertEqual(
            payload["content"],
            "41 drops claimed · 9 new campaigns · stall alert · 3 warnings",
        )
        self.assertNotIn("1 stall", payload["content"])
        self.assertEqual(len(embeds), 8)
        self.assertEqual(
            [embed["title"] for embed in embeds[:6]],
            ["Mined", "Bravo", "Charlie", "Echo", "Delta", "Foxtrot"],
        )
        self.assertTrue(all(embed["color"] == 0x9146FF for embed in embeds[:7]))
        self.assertTrue(all("thumbnail" in embed for embed in embeds[:6]))
        self.assertEqual(embeds[-1]["color"], 0xE74C3C)
        self.assertIn("**Mining stalled** · no progress since", embeds[-1]["description"])
        self.assertIn("×3 Websocket[0] requested reconnect.", embeds[-1]["description"])
        more = next(embed for embed in embeds if embed["title"] == "More games")["description"]
        lines = more.splitlines()
        self.assertEqual(len(lines), 16)
        self.assertTrue(lines[0].startswith("**Game G** · Reward A, Reward B, Reward C, +1"))
        self.assertIn("` 40%` Reward X", more)
        self.assertEqual(lines[-1], "…and 4 more games")
        for hidden in ("Game W", "Game X", "Game Y", "Game Z"):
            self.assertNotIn(hidden, more)
        self.assertLessEqual(message_char_count(embeds, payload["content"]), TOTAL_CHAR_TARGET)
        self.assertLessEqual(len(embeds), 8)

    def test_grouping_puts_one_game_on_one_card(self):
        now = END
        events = [
            _drop(now - timedelta(minutes=5), "Kindred", 5, ["Badge"]),
            _campaign("Kindred", 5, "Spring Sale", now + timedelta(days=2)),
        ]
        progress = {
            "state": "idle",
            "campaigns": [
                {
                    "game": "Kindred",
                    "game_id": 5,
                    "campaign": "Spring Sale",
                    "drop": "Cape",
                    "percent": 12,
                    "remaining_minutes": 40,
                    "mining_now": False,
                }
            ],
        }
        # The new campaign matches the shown progress row, so it stays off the card.
        # A second campaign with a different name is the new line.
        events.append(_campaign("Kindred", 5, "Winter Sale", now + timedelta(days=4)))
        payload = render_digest(
            events=events,
            window_start=now - timedelta(hours=6),
            window_end=now,
            next_at=now + timedelta(hours=6),
            interval_minutes=360,
            progress=progress,
            version=VERSION,
        )
        embeds = payload["embeds"]
        self.assertEqual([embed["title"] for embed in embeds], ["Kindred"])
        text = embeds[0]["description"]
        self.assertIn("✓ Badge", text)
        self.assertIn("Cape", text)
        self.assertIn("New: Winter Sale", text)
        self.assertNotIn("Spring Sale", text)
        blob = payload["content"] + "\n" + embeds[0]["title"] + "\n" + text
        self.assertEqual(blob.count("Kindred"), 1)

    def test_same_name_with_different_ids_stays_split(self):
        now = END
        events = [
            _drop(now, "Same", 1, ["One"]),
            _drop(now, "Same", 2, ["Two"]),
            {
                "type": "drop_received",
                "ts": now.isoformat(),
                "data": {"game": "Same", "benefits": ["Three"]},
            },
        ]
        payload = render_digest(
            events=events,
            window_start=now - timedelta(hours=1),
            window_end=now,
            next_at=None,
            interval_minutes=60,
            progress={"state": "idle", "campaigns": []},
            version=VERSION,
        )
        benefit_lines = [embed["description"] for embed in payload["embeds"]]
        self.assertEqual(len(payload["embeds"]), 3)
        self.assertIn("✓ One", benefit_lines)
        self.assertIn("✓ Two", benefit_lines)
        self.assertIn("✓ Three", benefit_lines)

    def test_ranking_breaks_ties_by_recency_then_progress_then_name(self):
        now = END
        events = []
        # Mined has one claim but is being watched, so it leads.
        events.append(_drop(now - timedelta(hours=5), "Mined", 1, ["Only"]))
        events.append(_drop(now - timedelta(minutes=1), "Many", 2, ["A"]))
        events.append(_drop(now - timedelta(minutes=2), "Many", 2, ["B"]))
        events.append(_drop(now - timedelta(minutes=3), "Many", 2, ["C"]))
        events.append(_drop(now - timedelta(minutes=10), "Recent", 3, ["A"]))
        events.append(_drop(now - timedelta(minutes=11), "Recent", 3, ["B"]))
        events.append(_drop(now - timedelta(hours=3), "Older", 4, ["A"]))
        events.append(_drop(now - timedelta(hours=4), "Older", 4, ["B"]))
        events.append(_drop(now - timedelta(hours=1), "Seventh", 5, ["A"]))
        events.append(_campaign("OnlyNew", 9, "Launch", now + timedelta(hours=2)))
        progress = {
            "state": "watching",
            "channel": "live",
            "game": "Mined",
            "game_id": 1,
            "campaigns": [
                {
                    "game": "Zebra",
                    "game_id": 7,
                    "drop": "Hat",
                    "campaign": "Z",
                    "percent": 80,
                    "remaining_minutes": 10,
                    "mining_now": False,
                },
                {
                    "game": "Alpha",
                    "game_id": 8,
                    "drop": "Hat",
                    "campaign": "A",
                    "percent": 80,
                    "remaining_minutes": 10,
                    "mining_now": False,
                },
            ],
        }
        payload = render_digest(
            events=events,
            window_start=now - timedelta(days=1),
            window_end=now,
            next_at=now + timedelta(days=1),
            interval_minutes=1440,
            progress=progress,
            version=VERSION,
        )
        titles = [embed["title"] for embed in payload["embeds"] if embed["title"] != "More games"]
        self.assertEqual(titles[:6], ["Mined", "Many", "Recent", "Older", "Seventh", "Alpha"])
        more = next(embed["description"] for embed in payload["embeds"] if embed["title"] == "More games")
        self.assertTrue(more.splitlines()[0].startswith("**Zebra**"))
        self.assertIn("**OnlyNew** · new campaign, ends", more)
        self.assertIn("*Mining now on live*", payload["embeds"][0]["description"])

    def test_thumbnail_fallback_skips_non_https(self):
        now = END
        box = "https://img.example/game-{width}x{height}.jpg"
        filled = "https://img.example/game-144x192.jpg"

        def render(events, campaigns):
            return render_digest(
                events=events,
                window_start=now - timedelta(hours=1),
                window_end=now,
                next_at=None,
                interval_minutes=60,
                progress={"state": "idle", "campaigns": campaigns},
                version=VERSION,
                include_campaigns=False,
            )["embeds"][0]

        latest_wins = render(
            [
                _drop(now - timedelta(minutes=2), "G", 1, ["Old"], ["https://img.example/old.png"]),
                _drop(now, "G", 1, ["New"], ["http://img.example/skip.png", "https://img.example/new.png"]),
            ],
            [
                {
                    "game": "G",
                    "game_id": 1,
                    "drop": "Row",
                    "percent": 10,
                    "remaining_minutes": 5,
                    "benefit_images": ["https://img.example/progress.png"],
                    "game_box_art": box,
                }
            ],
        )
        self.assertEqual(latest_wins["thumbnail"]["url"], "https://img.example/new.png")

        progress_wins = render(
            [_drop(now, "G", 1, ["New"], ["http://img.example/skip.png", ""])],
            [
                {
                    "game": "G",
                    "game_id": 1,
                    "drop": "Row",
                    "percent": 10,
                    "remaining_minutes": 5,
                    "mining_now": True,
                    "benefit_images": [None, "https://img.example/progress.png"],
                    "game_box_art": box,
                }
            ],
        )
        self.assertEqual(progress_wins["thumbnail"]["url"], "https://img.example/progress.png")

        box_wins = render(
            [
                {
                    "type": "drop_received",
                    "ts": now.isoformat(),
                    "data": {
                        "game": "G",
                        "game_id": 1,
                        "benefits": ["New"],
                        "benefit_images": ["not a url"],
                    },
                }
            ],
            [
                {
                    "game": "G",
                    "game_id": 1,
                    "drop": "Row",
                    "percent": 4,
                    "remaining_minutes": 5,
                    "benefit_images": ["ftp://img.example/nope.png"],
                    "game_box_art": box,
                }
            ],
        )
        self.assertEqual(box_wins["thumbnail"]["url"], filled)

        bare = render(
            [
                {
                    "type": "drop_received",
                    "ts": now.isoformat(),
                    "data": {"game": "G", "benefits": ["New"]},
                }
            ],
            [{"game": "G", "drop": "Row", "percent": 4, "remaining_minutes": 5}],
        )
        self.assertNotIn("thumbnail", bare)

    def test_legacy_event_without_image_fields_still_renders(self):
        raw = {
            "type": "drop_received",
            "ts": "2026-09-30T12:00:00+00:00",
            "data": {"game": "Legacy", "benefits": ["Badge"], "drop": "Other"},
        }
        loaded = NotificationEvent.from_dict(raw)
        self.assertNotIn("benefit_images", loaded.data)
        self.assertNotIn("game_id", loaded.data)
        self.assertNotIn("game_box_art", loaded.data)
        now = END
        payload = render_digest(
            events=[loaded.to_dict()],
            window_start=now - timedelta(hours=24),
            window_end=now,
            next_at=None,
            interval_minutes=1440,
            progress={"state": "idle", "campaigns": []},
            version=VERSION,
        )
        embed = payload["embeds"][0]
        self.assertEqual(embed["title"], "Legacy")
        self.assertEqual(embed["description"], "✓ Badge")
        self.assertNotIn("thumbnail", embed)
        self.assertNotIn("Other", embed["description"])
        self.assertNotIn("next digest", embed["footer"]["text"])
        self.assertIn("v1.10.1", embed["footer"]["text"])

    def test_each_gate_hides_its_block_and_its_count(self):
        now = END
        events = [
            _drop(now, "Claimed", 1, ["Badge"]),
            _campaign("Fresh", 2, "Launch", now + timedelta(days=1)),
            _campaign("Claimed", 1, "Also New", now + timedelta(days=2)),
            {
                "type": "mining_stalled",
                "ts": now.isoformat(),
                "data": {"reason": "no channels"},
            },
            {
                "type": "mining_stalled",
                "ts": (now - timedelta(minutes=5)).isoformat(),
                "data": {"reason": "no channels"},
            },
            {"type": "auth_attention", "ts": now.isoformat(), "data": {"reason": "login expired"}},
            {"type": "unlinked_tracked_game", "ts": now.isoformat(), "data": {"game": "Unlinked", "campaign": "C"}},
        ]
        progress = {
            "state": "watching",
            "channel": "live",
            "game": "Claimed",
            "game_id": 1,
            "campaigns": [
                {
                    "game": "Claimed",
                    "game_id": 1,
                    "drop": "Cape",
                    "campaign": "Shown",
                    "percent": 20,
                    "remaining_minutes": 30,
                    "mining_now": True,
                },
                {
                    "game": "Progress Only",
                    "game_id": 3,
                    "drop": "Hat",
                    "campaign": "Side",
                    "percent": 5,
                    "remaining_minutes": 10,
                    "mining_now": False,
                },
            ],
        }
        groups = [{"level": "WARNING", "count": 4, "latest": "slow disk", "last_ts": now.isoformat()}]
        base = {
            "events": events,
            "error_groups": groups,
            "window_start": now - timedelta(hours=6),
            "window_end": now,
            "next_at": now + timedelta(hours=6),
            "interval_minutes": 360,
            "progress": progress,
            "dropped_count": 37,
            "version": VERSION,
        }
        full = render_digest(**base)
        blob = _blob(full)
        self.assertIn("1 drop claimed", full["content"])
        self.assertIn("2 new campaigns", full["content"])
        self.assertIn("2 stall alerts", full["content"])
        self.assertIn("sign-in needed", full["content"])
        self.assertIn("1 unlinked game", full["content"])
        self.assertIn("4 warnings", full["content"])
        self.assertIn("**Mining stalled** ×2", blob)
        self.assertIn("**Sign-in needed** · login expired", blob)
        self.assertIn("**Link your account:** Unlinked", blob)
        self.assertIn("*Queue was full: 37 older events weren't kept.*", blob)
        self.assertIn("×4 slow disk", blob)
        self.assertIn("✓ Badge", blob)
        self.assertIn("New:", blob)
        self.assertIn("*Mining now on live*", blob)
        self.assertIn("Progress Only", blob)

        stalled = render_digest(**base, include_stalled=False)
        self.assertNotIn("stall", stalled["content"])
        self.assertNotIn("Mining stalled", _blob(stalled))
        self.assertIn("sign-in needed", stalled["content"])

        auth = render_digest(**base, include_auth=False)
        self.assertNotIn("sign-in needed", auth["content"])
        self.assertNotIn("Sign-in needed", _blob(auth))
        self.assertIn("stall", auth["content"])

        unlinked = render_digest(**base, include_unlinked=False)
        self.assertNotIn("unlinked", unlinked["content"])
        self.assertNotIn("Link your account", _blob(unlinked))

        errors = render_digest(**base, include_errors=False)
        self.assertNotIn("warning", errors["content"])
        self.assertNotIn("slow disk", _blob(errors))
        self.assertIn("*Queue was full:", _blob(errors))

        drops = render_digest(**base, include_drops=False)
        self.assertNotIn("drop", drops["content"])
        self.assertNotIn("✓ ", _blob(drops))
        self.assertIn("Progress Only", _blob(drops))

        campaigns = render_digest(**base, include_campaigns=False)
        self.assertNotIn("campaign", campaigns["content"])
        self.assertNotIn("New:", _blob(campaigns))
        self.assertNotIn("Fresh", _blob(campaigns))

        quiet = render_digest(**base, include_progress=False)
        self.assertNotIn("Mining now", _blob(quiet))
        self.assertNotIn("Progress Only", _blob(quiet))
        self.assertNotIn("`██", _blob(quiet))
        self.assertIn("✓ Badge", _blob(quiet))

        queue_only = render_digest(
            **base,
            include_drops=False,
            include_campaigns=False,
            include_progress=False,
            include_unlinked=False,
            include_stalled=False,
            include_auth=False,
            include_errors=False,
        )
        self.assertIn("*Queue was full: 37 older events weren't kept.*", _blob(queue_only))
        self.assertEqual(queue_only["content"], "Nothing new in the last 6 hours.")

        one = render_digest(
            events=[],
            window_start=now - timedelta(hours=1),
            window_end=now,
            next_at=None,
            interval_minutes=60,
            dropped_count=1,
            progress={"state": "idle", "campaigns": []},
            version=VERSION,
            include_errors=False,
        )
        self.assertIn("*Queue was full: 1 older event wasn't kept.*", one["embeds"][0]["description"])

    def test_footer_today_tomorrow_and_none(self):
        now = datetime(2026, 10, 1, 18, 0, tzinfo=UTC)
        events = [_drop(now, "G", 1, ["Badge"])]

        def footer(next_at, *, preview=False, end=now):
            payload = render_digest(
                events=events,
                window_start=end - timedelta(hours=24),
                window_end=end,
                next_at=next_at,
                interval_minutes=1440,
                progress={"state": "idle", "campaigns": []},
                version=VERSION,
                preview=preview,
            )
            self.assertEqual(len(payload["embeds"]), 1)
            return payload["embeds"][0]["footer"]["text"]

        self.assertEqual(
            footer(datetime(2026, 10, 1, 21, 0, tzinfo=UTC)),
            "Last 24 hours · next digest today, 21:00 · v1.10.1",
        )
        self.assertEqual(
            footer(datetime(2026, 10, 2, 9, 0, tzinfo=UTC)),
            "Last 24 hours · next digest tomorrow, 09:00 · v1.10.1",
        )
        self.assertEqual(
            footer(datetime(2026, 10, 5, 18, 0, tzinfo=UTC)),
            "Last 24 hours · next digest Mon 5 Oct, 18:00 · v1.10.1",
        )
        self.assertEqual(footer(None), "Last 24 hours · v1.10.1")
        self.assertEqual(
            footer(datetime(2026, 10, 2, 18, 0, tzinfo=UTC), preview=True),
            "Preview so far · next digest tomorrow, 18:00 · v1.10.1",
        )
        empty = render_digest(
            events=[],
            window_start=now - timedelta(hours=24),
            window_end=now,
            next_at=now + timedelta(days=1),
            interval_minutes=1440,
            progress={"state": "idle", "campaigns": []},
            version=VERSION,
        )
        self.assertEqual(empty["embeds"], [])
        self.assertEqual(empty["content"], "Nothing new in the last 24 hours.")

    def test_error_groups_list_errors_before_warnings(self):
        now = END
        payload = render_digest(
            events=[_drop(now, "G", 1, ["Badge"])],
            error_groups=[
                {"level": "WARNING", "count": 50, "latest": "chatty", "last_ts": now.isoformat()},
                {"level": "ERROR", "count": 1, "latest": "boom", "last_ts": now.isoformat()},
            ],
            error_overflow_types=2,
            window_start=now - timedelta(hours=1),
            window_end=now,
            next_at=None,
            interval_minutes=60,
            progress={"state": "idle", "campaigns": []},
            version=VERSION,
        )
        text = payload["embeds"][-1]["description"]
        self.assertLess(text.index("×1 boom"), text.index("×50 chatty"))
        self.assertIn("*…and 2 more in logs/TDM.log*", text)
        self.assertEqual(payload["embeds"][-1]["color"], 0xF1C40F)

    def test_trim_shrinks_more_games_before_claims_or_logs(self):
        payload = _heavy_payload()
        embeds = payload["embeds"]
        self.assertLessEqual(len(embeds), 8)
        self.assertLessEqual(message_char_count(embeds, payload["content"]), TOTAL_CHAR_TARGET)
        cards = [embed for embed in embeds if embed["title"].startswith("Card ")]
        self.assertGreaterEqual(len(cards), 3)
        benefit_lines = [
            line
            for line in cards[0]["description"].splitlines()
            if line.startswith("✓ ") and "more" not in line
        ]
        self.assertEqual(len(benefit_lines), 4)
        more = next(embed for embed in embeds if embed["title"] == "More games")
        more_lines = more["description"].splitlines()
        self.assertGreaterEqual(len(more_lines), 5)
        self.assertLess(len(more_lines), 16)
        self.assertTrue(more_lines[-1].startswith("…and "))
        attention = next(embed for embed in embeds if embed["title"].startswith("⚠️"))
        log_lines = [line for line in attention["description"].splitlines() if line.startswith("×")]
        self.assertEqual(len(log_lines), 5)

    def test_singular_more_games_line(self):
        now = END
        events = [_drop(now - timedelta(minutes=index), f"Game {index}", index + 1, ["Badge"]) for index in range(21)]
        events.append(_campaign("Extra", 50, "Launch", now + timedelta(days=1)))
        payload = render_digest(
            events=events,
            window_start=now - timedelta(days=1),
            window_end=now,
            next_at=None,
            interval_minutes=1440,
            progress={"state": "idle", "campaigns": []},
            version=VERSION,
        )
        more = next(embed for embed in payload["embeds"] if embed["title"] == "More games")
        self.assertTrue(more["description"].endswith("…and 1 more game"))


def _blob(payload: dict) -> str:
    return payload["content"] + "\n" + "\n".join(
        f"{embed.get('title', '')}\n{embed.get('description', '')}" for embed in payload["embeds"]
    )


class DigestV2ServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state_path = Path(self._tmp.name) / "notifications_state.json"

    def _service(self, **overrides):
        settings = FakeSettings(digest_notification_settings(**overrides))
        service = NotificationService(settings, state_path=self.state_path)
        provider = service.get_provider("discord")
        provider.send = unittest.mock.AsyncMock()
        provider.send_digest = unittest.mock.AsyncMock()
        return service, provider

    async def test_notify_stores_image_fields(self):
        service, _provider = self._service()
        await service.notify_drop_received(
            "Game",
            ["Badge", "Emote"],
            benefit_images=[" https://img.example/a.png ", "", None],
            game_id=9,
            game_box_art="https://img.example/box-{width}x{height}.jpg",
        )
        data = service._state["digest_queue"][0]["data"]
        self.assertEqual(data["benefit_images"], ["https://img.example/a.png", None, None])
        self.assertEqual(data["game_id"], 9)
        self.assertEqual(data["game_box_art"], "https://img.example/box-{width}x{height}.jpg")

    async def test_new_campaign_stores_game_art(self):
        service, provider = self._service()
        first = FakeCampaign("camp-1", "Campaign 1", "Game A")
        await service.track_new_campaigns([first], ["Game A"])
        fresh = FakeCampaign("camp-2", "Campaign 2", "Game A")
        fresh.game.id = 42
        fresh.game.box_art_url = "https://img.example/a-{width}x{height}.jpg"
        await service.track_new_campaigns([first, fresh], ["Game A"])
        provider.send.assert_not_awaited()
        data = service._state["digest_queue"][-1]["data"]
        self.assertEqual(data["game_id"], 42)
        self.assertEqual(data["game_box_art"], "https://img.example/a-{width}x{height}.jpg")

    async def test_toggle_off_after_queue_hides_block_and_count(self):
        service, provider = self._service()
        await service.notify_drop_received("Game", ["Badge"])
        await service.notify_mining_stalled("no channels")
        logging.getLogger("TwitchDrops").warning("slow disk on %s", "ssd")
        shown = service._render(preview=False)
        self.assertIn("stall alert", shown["content"])
        self.assertIn("1 drop claimed", shown["content"])
        self.assertIn("warning", shown["content"])
        self.assertIn("Mining stalled", _blob(shown))
        self.assertIn("slow disk", _blob(shown))

        service._settings.notifications["digest_sections"]["errors"] = False
        service._settings.notifications["discord"]["events"]["mining_stalled"] = False
        service._settings.notifications["discord"]["events"]["drop_received"] = False
        hidden = service._render(preview=True)
        self.assertNotIn("stall", hidden["content"])
        self.assertNotIn("drop", hidden["content"])
        self.assertNotIn("warning", hidden["content"])
        self.assertNotIn("Mining stalled", _blob(hidden))
        self.assertNotIn("slow disk", _blob(hidden))
        self.assertTrue(hidden["content"].startswith("Preview so far"))
        provider.send.assert_awaited()

    async def test_immediate_rechecks_the_toggle_and_adds_a_thumbnail(self):
        settings = FakeSettings(make_notification_settings(mode="immediate"))
        service = NotificationService(settings, state_path=self.state_path)
        provider = service.get_provider("discord")
        provider.send = unittest.mock.AsyncMock()
        await service.notify_drop_received(
            "Game",
            ["Badge"],
            benefit_images=["http://img.example/nope.png"],
            game_box_art="https://img.example/box-{width}x{height}.jpg",
        )
        self.assertEqual(
            provider.send.await_args.kwargs["thumbnail_url"],
            "https://img.example/box-144x192.jpg",
        )

        provider.send.reset_mock()
        calls = {"n": 0}

        def event_enabled(event_type):
            calls["n"] += 1
            return calls["n"] == 1

        provider.event_enabled = event_enabled
        await service.notify_drop_received(
            "Game",
            ["Badge"],
            benefit_images=["https://img.example/badge.png"],
        )
        provider.send.assert_not_awaited()

    async def test_warning_recorded_then_errors_off_skips_the_window(self):
        service, provider = self._service()
        logging.getLogger("TwitchDrops").warning("slow disk on %s", "ssd")
        self.assertIn("slow disk", _blob(service._render(preview=False)))
        self.assertTrue(service.has_digest_content())
        self.assertEqual(service.queued_count(), 1)

        service._settings.notifications["digest_sections"]["errors"] = False
        hidden = service._render(preview=True)
        self.assertNotIn("slow disk", _blob(hidden))
        self.assertNotIn("warning", hidden["content"])
        self.assertFalse(service.has_digest_content())
        self.assertEqual(service.queued_count(), 0)
        self.assertTrue(service._state["digest_error_groups"])

        service._state["digest_next_at"] = "2000-01-01T00:00:00+00:00"
        sent = await service.flush_digest()
        self.assertFalse(sent)
        provider.send_digest.assert_not_awaited()
        self.assertEqual(service._state["digest_error_groups"], {})
        self.assertTrue(service._state["last_digest"]["skipped"])

    async def test_event_toggles_off_after_queue_skip_the_window(self):
        service, provider = self._service()
        await service.notify_drop_received("Game", ["Badge"])
        await service.notify_mining_stalled("no channels")
        await service.notify("auth_attention", "Sign in", "token expired")
        await service.notify("new_campaign", "New campaign", "Camp", data={"game": "Camp"})
        await service.notify(
            "unlinked_tracked_game", "Link", "Game X", data={"game": "Game X"}
        )
        provider.send.assert_awaited()
        self.assertEqual(service.queued_count(), 5)

        events = service._settings.notifications["discord"]["events"]
        for key in (
            "drop_received",
            "mining_stalled",
            "auth_attention",
            "new_campaign",
            "unlinked_tracked_game",
        ):
            events[key] = False
        hidden = service._render(preview=False)
        blob = _blob(hidden)
        self.assertEqual(hidden["content"], "Nothing new in the last 24 hours.")
        for needle in ("Badge", "Mining stalled", "Sign-in", "Camp", "Game X"):
            self.assertNotIn(needle, blob)
        self.assertFalse(service.has_digest_content())
        self.assertEqual(service.queued_count(), 0)
        self.assertEqual(len(service._state["digest_queue"]), 5)

        service._settings.notifications["mode"] = "immediate"
        service.schedule_mode_switch_flush()
        self.assertFalse(service._state.get("digest_flush_pending"))
        self.assertIsNone(service._mode_switch_task)
        service._settings.notifications["mode"] = "digest"

        service._state["digest_next_at"] = "2000-01-01T00:00:00+00:00"
        sent = await service.flush_digest()
        self.assertFalse(sent)
        provider.send_digest.assert_not_awaited()
        self.assertEqual(service._state["digest_queue"], [])
        self.assertTrue(service._state["last_digest"]["skipped"])

    async def test_one_toggle_off_leaves_the_other_event(self):
        service, _provider = self._service()
        await service.notify_drop_received("Game", ["Badge"])
        await service.notify_mining_stalled("no channels")
        service._settings.notifications["discord"]["events"]["mining_stalled"] = False
        shown = service._render(preview=False)
        self.assertIn("1 drop claimed", shown["content"])
        self.assertNotIn("stall", shown["content"])
        self.assertIn("Badge", _blob(shown))
        self.assertNotIn("Mining stalled", _blob(shown))
        self.assertTrue(service.has_digest_content())
        self.assertEqual(service.queued_count(), 1)

    async def test_queue_overflow_stays_content_when_events_are_gated(self):
        service, _provider = self._service()
        await service.notify_drop_received("Game", ["Badge"])
        service._settings.notifications["discord"]["events"]["drop_received"] = False
        service._state["digest_dropped"] = 2
        self.assertEqual(service.queued_count(), 0)
        self.assertTrue(service.has_digest_content())
        shown = _blob(service._render(preview=False))
        self.assertIn("Queue was full: 2 older events weren't kept.", shown)

    async def test_unknown_queue_type_is_not_content(self):
        service, _provider = self._service()
        service._state["digest_queue"] = [{"type": "mystery", "data": {}, "seq": 1}, "nope"]
        self.assertFalse(service.has_digest_content())
        self.assertEqual(service.queued_count(), 0)

    async def test_urgent_immediate_off_still_queues(self):
        service, provider = self._service(digest_urgent_immediate=False)
        await service.notify_mining_stalled("no channels")
        provider.send.assert_not_awaited()
        self.assertEqual(len(service._state["digest_queue"]), 1)
        self.assertFalse(service._state["digest_queue"][0]["data"]["alerted"])
        self.assertIn("stall alert", service._render(preview=False)["content"])
