"""Tests for the inventory page's free-text search (replaced the game multi-select).

The matching logic lives in the static frontend, so the behavioural cases are exercised
by slicing the two helper functions out of app.js and running them under node (skipped
when node isn't installed). The remaining checks are static source assertions, the same
approach as tests/test_frontend_dom_safety.py.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path


WEB_DIR = Path(__file__).resolve().parents[1] / "web"
APP_JS = WEB_DIR / "static" / "app.js"
STYLES_CSS = WEB_DIR / "static" / "styles.css"
INDEX_HTML = WEB_DIR / "index.html"

RUST_CAMPAIGN = {
    "id": "c1",
    "name": "Twitch Drops Round 5",
    "game_name": "Rust",
    "drops": [
        {
            "id": "d1",
            "name": "Watch 2 hours",
            "benefits": [{"name": "Wood Pile Hoodie", "type": "DIRECT_ENTITLEMENT"}],
        }
    ],
}


def _extract_search_helpers() -> str:
    """Return the campaignSearchText/campaignMatchesSearch source block from app.js."""
    source = APP_JS.read_text(encoding="utf-8")
    start = source.index("function campaignSearchText(")
    end = source.index("function onInventoryFilterChange(")
    return source[start:end]


@unittest.skipUnless(shutil.which("node"), "node is required to exercise the frontend helpers")
class TestInventorySearchMatching(unittest.TestCase):
    def _matches(self, search_text: str, campaign: dict | None = None) -> bool:
        script = (
            f"{_extract_search_helpers()}\n"
            f"const campaign = {json.dumps(campaign if campaign is not None else RUST_CAMPAIGN)};\n"
            f"const filters = {{ search_text: {json.dumps(search_text)} }};\n"
            "process.stdout.write(JSON.stringify(campaignMatchesSearch(campaign, filters)));"
        )
        result = subprocess.run(
            [str(shutil.which("node")), "-e", script],
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(result.stdout)

    def test_empty_search_matches_everything(self):
        self.assertTrue(self._matches(""))
        self.assertTrue(self._matches("   "))

    def test_matches_game_campaign_drop_and_benefit_names(self):
        self.assertTrue(self._matches("rust"))  # game name
        self.assertTrue(self._matches("round 5"))  # campaign name
        self.assertTrue(self._matches("watch 2"))  # drop name
        self.assertTrue(self._matches("hoodie"))  # benefit name

    def test_search_is_case_insensitive(self):
        self.assertTrue(self._matches("RUST"))
        self.assertTrue(self._matches("wOoD pIlE"))

    def test_terms_are_anded_across_fields_and_order_independent(self):
        self.assertTrue(self._matches("rust hoodie"))
        self.assertTrue(self._matches("hoodie rust"))
        self.assertFalse(self._matches("rust dota"))

    def test_non_matching_search_is_rejected(self):
        self.assertFalse(self._matches("valorant"))

    def test_campaign_without_drops_still_matches_on_name(self):
        campaign = {"id": "c2", "name": "Season Pass", "game_name": "Dota 2"}
        self.assertTrue(self._matches("dota", campaign))
        self.assertFalse(self._matches("hoodie", campaign))


class TestInventorySearchMarkup(unittest.TestCase):
    def test_search_input_and_clear_button_exist(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        self.assertIn('id="inventory-search"', html)
        self.assertIn('id="inventory-search-clear"', html)

    def test_game_multiselect_is_fully_removed(self):
        leftovers = {
            "index.html": INDEX_HTML.read_text(encoding="utf-8"),
            "app.js": APP_JS.read_text(encoding="utf-8"),
            "styles.css": STYLES_CSS.read_text(encoding="utf-8"),
        }
        for name, source in leftovers.items():
            for token in ("game-dropdown", "game-tags-display", "selected-game-tags"):
                self.assertNotIn(token, source, f"{token} still referenced in {name}")
