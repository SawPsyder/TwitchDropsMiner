from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from src.config.settings import Settings
from src.library_sync.service import LibrarySyncService
from src.models.campaign import DropsCampaign
from src.models.game import Game


# Tags identifying where a wanted-queue entry came from.
SOURCE_MANUAL = "manual"
SOURCE_AUTO = "auto"
SOURCE_IDLE = "idle"


class StreamSelector:
    def _get_wanted_game_tree(
        self,
        settings: Settings,
        campaigns: list[DropsCampaign],
        games_order: list[str] | None = None,
        campaign_filter: Callable[[DropsCampaign], bool] | None = None,
    ) -> list[dict]:
        """
        Get the hierarchical tree of wanted items (Games -> Campaigns -> Drops -> Benefits).
        Ignoring 'can earn within' time constraint.

        games_order overrides the game name priority order (e.g. the two-tier
        watch list including library-detected games); defaults to the user's
        games_to_watch setting.

        campaign_filter overrides which campaigns qualify for a game; defaults
        to "can earn within the next hour" (requires the account to be linked
        or the campaign to grant a badge/emote - see DropsCampaign.eligible).
        """
        wanted_games = []
        games_to_watch = games_order if games_order is not None else settings.games_to_watch
        mining_benefits = settings.mining_benefits
        next_hour = datetime.now(timezone.utc) + timedelta(hours=1)
        is_campaign_wanted = campaign_filter or (lambda campaign: campaign.can_earn_within(next_hour))

        for game_name in games_to_watch:
            wanted_campaigns = []
            game_obj = None
            game_name_lower = game_name.lower()

            # Find all campaigns for this game
            for campaign in campaigns:
                if campaign.game.name.lower() != game_name_lower:
                    continue

                if game_obj is None:
                    game_obj = campaign.game

                if not is_campaign_wanted(campaign):
                    continue

                wanted_drops = []
                for drop in campaign.drops:
                    if drop.is_claimed:
                        continue

                    filtered_benefits = drop.get_wanted_unclaimed_benefits(mining_benefits)

                    if len(filtered_benefits) > 0:
                        wanted_drops.append({"name": drop.name, "benefits": filtered_benefits})

                if len(wanted_drops) > 0:
                    wanted_campaigns.append(
                        {
                            "id": campaign.id,
                            "name": campaign.name,
                            "url": campaign.campaign_url,
                            "drops": wanted_drops,
                            "linked": campaign.linked,
                            "link_url": campaign.link_url,
                        }
                    )

            if len(wanted_campaigns) > 0:
                wanted_games.append(
                    {
                        "game_id": game_obj.id if game_obj else None,
                        "game_name": game_name,
                        "game_icon": game_obj.box_art_url if game_obj else None,
                        "game_obj": game_obj,
                        "campaigns": wanted_campaigns,
                    }
                )

        return wanted_games

    def _get_primary_tree(
        self,
        settings: Settings,
        campaigns: list[DropsCampaign],
        manual_games: list[str] | None,
        auto_games: list[str] | None,
    ) -> list[dict]:
        """
        The two-tier watch list tree (user's manual games_to_watch first,
        then auto-detected library games), tagged with a "source" of
        "manual" or "auto".
        """
        manual_games = manual_games if manual_games is not None else settings.games_to_watch
        auto_games = list(auto_games) if auto_games else []
        games_order = LibrarySyncService.combine_watch_lists(manual_games, auto_games)
        primary_tree = self._get_wanted_game_tree(settings, campaigns, games_order)

        manual_set = {name.casefold() for name in manual_games}
        auto_set = {name.casefold() for name in auto_games}
        for entry in primary_tree:
            name_cf = entry["game_name"].casefold()
            entry["source"] = SOURCE_MANUAL if name_cf in manual_set else (
                SOURCE_AUTO if name_cf in auto_set else SOURCE_IDLE
            )
        return primary_tree

    def _get_idle_tree(
        self,
        settings: Settings,
        campaigns: list[DropsCampaign],
        exclude: list[dict],
    ) -> list[dict]:
        """
        Every earnable game not already in `exclude`, tagged "idle". Empty if
        idle_behavior.mine_all_when_idle is disabled.
        """
        if not settings.idle_behavior["mine_all_when_idle"]:
            return []
        all_games = sorted({campaign.game.name for campaign in campaigns})
        full_tree = self._get_wanted_game_tree(settings, campaigns, all_games)
        already_queued = {entry["game_name"].casefold() for entry in exclude}
        idle_tree = [
            entry for entry in full_tree if entry["game_name"].casefold() not in already_queued
        ]
        for entry in idle_tree:
            entry["source"] = SOURCE_IDLE
        return idle_tree

    def get_wanted_game_tree(
        self,
        settings: Settings,
        campaigns: list[DropsCampaign],
        manual_games: list[str] | None = None,
        auto_games: list[str] | None = None,
    ) -> list[dict]:
        """
        The full display queue: the two-tier watch list first, followed by
        the idle_behavior preview (every other earnable game) so the user
        can see what will be mined automatically once the active games run
        out - even while there's still something active to mine.
        """
        primary_tree = self._get_primary_tree(settings, campaigns, manual_games, auto_games)
        idle_tree = self._get_idle_tree(settings, campaigns, primary_tree)
        return [{**game, "game_obj": None} for game in primary_tree + idle_tree]

    def get_wanted_games(
        self,
        settings: Settings,
        campaigns: list[DropsCampaign],
        manual_games: list[str] | None = None,
        auto_games: list[str] | None = None,
    ) -> list[Game]:
        """
        The actual mining priority list: the two-tier watch list, falling
        back to every other earnable game only when that list is completely
        empty (unlike get_wanted_game_tree, this doesn't keep tracking the
        idle preview games once there's something active to mine).
        """
        primary_tree = self._get_primary_tree(settings, campaigns, manual_games, auto_games)
        tree = primary_tree if primary_tree else self._get_idle_tree(settings, campaigns, primary_tree)
        return [game["game_obj"] for game in tree]

    def get_unlinked_auto_tracked_tree(
        self,
        settings: Settings,
        campaigns: list[DropsCampaign],
        manual_games: list[str] | None = None,
        auto_games: list[str] | None = None,
    ) -> list[dict]:
        """
        Games being watched - manually, or auto-detected by a library
        tracker (Steam, Ubisoft, ...) - that have at least one campaign
        whose Twitch account isn't linked yet, so nothing will actually be
        mined until the user links it. Manual games come first, followed by
        auto-detected ones not already on the manual list (case-insensitive,
        no duplicates); each entry is tagged with a "source" of "manual" or
        "auto". Only unlinked campaigns are kept per game.

        This intentionally bypasses the "can earn within" / eligible check
        used by the main wanted queue: DropsCampaign.eligible is only True
        when the account is linked OR the campaign grants a badge/emote, so
        an unlinked campaign without a badge/emote (e.g. an in-game item)
        would otherwise never show up anywhere - defeating the purpose of
        this list, which exists precisely to flag that case.
        """
        manual_games = manual_games if manual_games is not None else settings.games_to_watch
        auto_games = list(auto_games) if auto_games else []
        games_order = LibrarySyncService.combine_watch_lists(manual_games, auto_games)
        if not games_order:
            return []

        manual_set = {name.casefold() for name in manual_games}
        tree = self._get_wanted_game_tree(
            settings,
            campaigns,
            games_order,
            campaign_filter=lambda campaign: not campaign.linked and not campaign.expired,
        )
        for entry in tree:
            entry["source"] = SOURCE_MANUAL if entry["game_name"].casefold() in manual_set else SOURCE_AUTO
        return [{**game, "game_obj": None} for game in tree]
