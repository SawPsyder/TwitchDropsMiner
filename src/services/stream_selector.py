from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from src.config.settings import Settings
from src.library_sync.service import LibrarySyncService
from src.models.campaign import DropsCampaign
from src.models.drop import TimedDrop
from src.models.game import Game


# Tags identifying where a wanted-queue entry came from.
SOURCE_FAVORITE = "favorite"
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
        drop_filter: Callable[[DropsCampaign, TimedDrop], bool] | None = None,
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

        drop_filter, if given, additionally restricts which of a campaign's
        unclaimed drops are considered (e.g. trimming a favorited game down to
        just the path leading to its favorited drop - see
        _make_favorite_drop_filter); defaults to considering every unclaimed
        drop with a wanted benefit.
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
                    # drops without a watch-time requirement ("manual" in the
                    # inventory) can't be mined by watching - don't queue them
                    if drop.required_minutes <= 0:
                        continue
                    if drop_filter is not None and not drop_filter(campaign, drop):
                        continue

                    filtered_benefits = drop.get_wanted_unclaimed_benefits(mining_benefits)

                    if len(filtered_benefits) > 0:
                        # Same shape as the inventory manager's benefit payload,
                        # so the web GUI can render benefit icons + tooltips.
                        wanted_drops.append(
                            {
                                "name": drop.name,
                                "benefits": [
                                    {
                                        "name": benefit.name,
                                        "type": benefit.type.name,
                                        "image_url": (
                                            str(benefit.image_url) if benefit.image_url else None
                                        ),
                                    }
                                    for benefit in filtered_benefits
                                ],
                            }
                        )

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

    def _get_favorite_games(
        self, campaigns: list[DropsCampaign], favorite_keys: set[str]
    ) -> list[str]:
        """
        Games with at least one drop marked favorite (settings.favorite_drops,
        keyed "{campaign_id}#{drop_id}") that hasn't been claimed yet, in
        campaign-list order, deduped. A favorited drop stops holding its game
        in this tier the moment that specific drop is claimed - other drops
        in the same campaign don't keep it here.
        """
        if not favorite_keys:
            return []
        favorite_games: list[str] = []
        seen: set[str] = set()
        for campaign in campaigns:
            name_cf = campaign.game.name.casefold()
            if name_cf in seen:
                continue
            for drop in campaign.drops:
                if not drop.is_claimed and f"{campaign.id}#{drop.id}" in favorite_keys:
                    favorite_games.append(campaign.game.name)
                    seen.add(name_cf)
                    break
        return favorite_games

    def _favorite_path_drop_ids(
        self, campaign: DropsCampaign, favorite_keys: set[str]
    ) -> set[str]:
        """
        The favorited drop(s) in this campaign, plus their unclaimed
        precondition ancestors (the drops that must be earned first to reach
        them) - "the path up to the favorite", not sibling or descendant
        drops that don't gate it.
        """
        path_ids: set[str] = set()
        stack = [
            drop_id for drop_id in campaign.timed_drops if f"{campaign.id}#{drop_id}" in favorite_keys
        ]
        while stack:
            drop_id = stack.pop()
            if drop_id in path_ids:
                continue
            path_ids.add(drop_id)
            drop = campaign.timed_drops.get(drop_id)
            if drop is not None:
                stack.extend(drop.precondition_drops)
        return path_ids

    def _make_favorite_drop_filter(
        self, favorite_keys: set[str]
    ) -> Callable[[DropsCampaign, TimedDrop], bool]:
        # Cached per campaign since _get_wanted_game_tree calls this once per drop.
        path_cache: dict[str, set[str]] = {}

        def drop_filter(campaign: DropsCampaign, drop: TimedDrop) -> bool:
            path_ids = path_cache.get(campaign.id)
            if path_ids is None:
                path_ids = self._favorite_path_drop_ids(campaign, favorite_keys)
                path_cache[campaign.id] = path_ids
            return drop.id in path_ids

        return drop_filter

    def _get_primary_tree(
        self,
        settings: Settings,
        campaigns: list[DropsCampaign],
        manual_games: list[str] | None,
        auto_games: list[str] | None,
    ) -> list[dict]:
        """
        The watch list tree: favorited games first (regardless of watch list
        membership - see _get_favorite_games), then the user's manual
        games_to_watch, then auto-detected library games. Tagged with a
        "source" of "favorite", "manual", or "auto".

        Favorited games are trimmed to just the path leading to their
        favorited drop(s) (_favorite_path_drop_ids) rather than every drop of
        every campaign for that game - the other tiers show the full breadth.

        A game only claims the favorite tier once its favorite tree actually
        produced something (i.e. the favorited drop's own campaign is
        currently earnable) - otherwise it falls through to the normal
        manual/auto treatment (full breadth) instead of vanishing entirely.
        Without this, favoriting a drop whose campaign isn't earnable yet
        (not linked, not started, ...) would silently black out the game's
        other, perfectly mineable campaigns too, since they'd otherwise be
        excluded from both tiers at once.
        """
        manual_games = manual_games if manual_games is not None else settings.games_to_watch
        auto_games = list(auto_games) if auto_games else []
        favorite_keys = set(settings.favorite_drops)
        favorite_games = self._get_favorite_games(campaigns, favorite_keys)

        favorite_tree = (
            self._get_wanted_game_tree(
                settings,
                campaigns,
                favorite_games,
                drop_filter=self._make_favorite_drop_filter(favorite_keys),
            )
            if favorite_games
            else []
        )
        resolved_favorite_set = {entry["game_name"].casefold() for entry in favorite_tree}

        combined_games = LibrarySyncService.combine_watch_lists(manual_games, auto_games)
        rest_games = [name for name in combined_games if name.casefold() not in resolved_favorite_set]
        rest_tree = self._get_wanted_game_tree(settings, campaigns, rest_games)
        primary_tree = favorite_tree + rest_tree

        manual_set = {name.casefold() for name in manual_games}
        auto_set = {name.casefold() for name in auto_games}
        for entry in primary_tree:
            name_cf = entry["game_name"].casefold()
            if name_cf in resolved_favorite_set:
                entry["source"] = SOURCE_FAVORITE
            elif name_cf in manual_set:
                entry["source"] = SOURCE_MANUAL
            elif name_cf in auto_set:
                entry["source"] = SOURCE_AUTO
            else:
                entry["source"] = SOURCE_IDLE
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

    def _get_full_tree(
        self,
        settings: Settings,
        campaigns: list[DropsCampaign],
        manual_games: list[str] | None,
        auto_games: list[str] | None,
    ) -> list[dict]:
        """
        The complete wanted queue: the two-tier watch list (favorites, then
        manual, then auto) followed by the idle_behavior tier (every other
        earnable game) at the lowest priority.
        """
        primary_tree = self._get_primary_tree(settings, campaigns, manual_games, auto_games)
        return primary_tree + self._get_idle_tree(settings, campaigns, primary_tree)

    def get_wanted_game_tree(
        self,
        settings: Settings,
        campaigns: list[DropsCampaign],
        manual_games: list[str] | None = None,
        auto_games: list[str] | None = None,
    ) -> list[dict]:
        """
        The display queue: same games and order as get_wanted_games, as a
        hierarchical tree (Games -> Campaigns -> Drops -> Benefits) with
        source tags.
        """
        tree = self._get_full_tree(settings, campaigns, manual_games, auto_games)
        return [{**game, "game_obj": None} for game in tree]

    def get_wanted_games(
        self,
        settings: Settings,
        campaigns: list[DropsCampaign],
        manual_games: list[str] | None = None,
        auto_games: list[str] | None = None,
    ) -> list[Game]:
        """
        The actual mining priority list: the two-tier watch list followed by
        the idle_behavior tier (every other earnable game) at the lowest
        priority - idle games get their channels fetched, tracked and mined
        like any other tier, they just always sort behind the watch list.
        """
        tree = self._get_full_tree(settings, campaigns, manual_games, auto_games)
        return [game["game_obj"] for game in tree]

    def get_unlinked_auto_tracked_tree(
        self,
        settings: Settings,
        campaigns: list[DropsCampaign],
        manual_games: list[str] | None = None,
        auto_games: list[str] | None = None,
    ) -> list[dict]:
        """
        Games being watched - manually, auto-detected by a library tracker
        (Steam, Ubisoft, ...), or favorited (see _get_favorite_games) - that
        have at least one campaign whose Twitch account isn't linked yet, so
        nothing will actually be mined until the user links it. Favorited
        games come first (regardless of watch list membership), followed by
        manual games, then auto-detected ones not already on the manual list
        (case-insensitive, no duplicates); each entry is tagged with a
        "source" of "favorite", "manual", or "auto". Only unlinked campaigns
        are kept per game - favorited ones are further trimmed to just the
        path leading to the favorited drop (_favorite_path_drop_ids), same as
        the main wanted queue.

        This intentionally bypasses the "can earn within" / eligible check
        used by the main wanted queue: DropsCampaign.eligible is only True
        when the account is linked OR the campaign grants a badge/emote, so
        an unlinked campaign without a badge/emote (e.g. an in-game item)
        would otherwise never show up anywhere - defeating the purpose of
        this list, which exists precisely to flag that case.

        As with the main wanted queue, a game only claims the favorite tier
        once its favorite tree actually produced something here (i.e. it has
        an unlinked, non-expired campaign on the path to the favorited drop)
        - otherwise it falls through to the normal manual/auto treatment
        instead of hiding the game's other unlinked campaigns entirely.
        """
        manual_games = manual_games if manual_games is not None else settings.games_to_watch
        auto_games = list(auto_games) if auto_games else []
        favorite_keys = set(settings.favorite_drops)
        favorite_games = self._get_favorite_games(campaigns, favorite_keys)
        combined_games = LibrarySyncService.combine_watch_lists(manual_games, auto_games)
        if not favorite_games and not combined_games:
            return []

        favorite_tree = (
            self._get_wanted_game_tree(
                settings,
                campaigns,
                favorite_games,
                campaign_filter=lambda campaign: not campaign.linked and not campaign.expired,
                drop_filter=self._make_favorite_drop_filter(favorite_keys),
            )
            if favorite_games
            else []
        )
        resolved_favorite_set = {entry["game_name"].casefold() for entry in favorite_tree}

        rest_games = [name for name in combined_games if name.casefold() not in resolved_favorite_set]
        rest_tree = self._get_wanted_game_tree(
            settings,
            campaigns,
            rest_games,
            campaign_filter=lambda campaign: not campaign.linked and not campaign.expired,
        )
        tree = favorite_tree + rest_tree

        manual_set = {name.casefold() for name in manual_games}
        for entry in tree:
            name_cf = entry["game_name"].casefold()
            if name_cf in resolved_favorite_set:
                entry["source"] = SOURCE_FAVORITE
            elif name_cf in manual_set:
                entry["source"] = SOURCE_MANUAL
            else:
                entry["source"] = SOURCE_AUTO
        return [{**game, "game_obj": None} for game in tree]
