"""The Sports Game Tracker integration (`game_tracker`) for Home Assistant."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
from typing import Any

import voluptuous as vol

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.discovery import async_load_platform
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    EspnLeagueClient,
    GameTrackerError,
    TeamGame,
    TeamInfo,
    parse_scoreboard_for_team,
    select_active_game,
    slugify_name,
)
from .const import (
    ACTIVE_SCAN_INTERVAL,
    CONF_LEAGUE,
    CONF_POST_GAME,
    CONF_PRE_GAME,
    CONF_SLUG,
    CONF_SPORT,
    CONF_TEAM,
    CONF_TEAMS,
    DEFAULT_POST_GAME,
    DEFAULT_PRE_GAME,
    DOMAIN,
    IDLE_SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

TEAM_ITEM_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_SPORT): cv.string,
        vol.Required(CONF_LEAGUE): cv.string,
        vol.Required(CONF_TEAM): cv.string,
        vol.Optional(CONF_SLUG): cv.string,
        vol.Optional(CONF_PRE_GAME): cv.positive_time_period,
        vol.Optional(CONF_POST_GAME): cv.positive_time_period,
    }
)

CONFIG_SCHEMA = vol.Schema(
    {
        vol.Optional(DOMAIN): vol.Schema(
            {
                vol.Required(CONF_TEAMS): vol.All(cv.ensure_list, [TEAM_ITEM_SCHEMA]),
                vol.Optional(
                    CONF_PRE_GAME, default=DEFAULT_PRE_GAME
                ): cv.positive_time_period,
                vol.Optional(
                    CONF_POST_GAME, default=DEFAULT_POST_GAME
                ): cv.positive_time_period,
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)


@dataclass(slots=True)
class TrackedTeamConfig:
    """Resolved runtime configuration for a single tracked team."""

    team_info: TeamInfo
    entity_slug: str
    pre_game: timedelta
    post_game: timedelta


class LeagueScoreboardCoordinator(DataUpdateCoordinator[dict[str, list[TeamGame]]]):
    """Polls one ESPN `(sport, league)` scoreboard shared by all configured teams in it."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: EspnLeagueClient,
        tracked_teams: list[TrackedTeamConfig],
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{client.sport}_{client.league}",
            update_interval=IDLE_SCAN_INTERVAL,
        )
        self.client = client
        self.tracked_teams = tracked_teams
        self._previous_states: dict[str, str] = {}
        self.completed_timestamps: dict[str, datetime] = {}

    def _should_poll_frequently(
        self, games_by_slug: dict[str, list[TeamGame]], now: datetime
    ) -> bool:
        """Poll every 60s when any tracked team in this league is active or within 2h of start."""
        for cfg in self.tracked_teams:
            team_games = games_by_slug.get(cfg.entity_slug, [])
            if select_active_game(
                team_games,
                now,
                pre_game=cfg.pre_game,
                post_game=cfg.post_game,
                completed_timestamps=self.completed_timestamps,
            ):
                return True

            lookahead = timedelta(hours=2) + max(timedelta(0), cfg.pre_game)
            for game in team_games:
                if not game.time_tbd and not game.completed and game.state != "post":
                    if now <= game.start_utc <= (now + lookahead):
                        return True
        return False

    async def _async_update_data(self) -> dict[str, list[TeamGame]]:
        """Fetch league scoreboard and slice games for each tracked team."""
        try:
            payload = await self.client.async_fetch_scoreboard()
        except GameTrackerError as err:
            raise UpdateFailed(str(err)) from err

        now = datetime.now(timezone.utc)
        games_by_slug: dict[str, list[TeamGame]] = {}

        for cfg in self.tracked_teams:
            team_games = parse_scoreboard_for_team(
                payload, cfg.team_info, sport=self.client.sport
            )
            for g in team_games:
                prev = self._previous_states.get(g.event_id)
                if prev == "in" and (g.state == "post" or g.completed):
                    self.completed_timestamps[g.event_id] = now
                self._previous_states[g.event_id] = g.state
            games_by_slug[cfg.entity_slug] = team_games

        self.update_interval = (
            ACTIVE_SCAN_INTERVAL
            if self._should_poll_frequently(games_by_slug, now)
            else IDLE_SCAN_INTERVAL
        )
        return games_by_slug


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up `game_tracker:` from configuration.yaml."""
    if DOMAIN not in config:
        return True

    conf: dict[str, Any] = config[DOMAIN]
    default_pre: timedelta = conf[CONF_PRE_GAME]
    default_post: timedelta = conf[CONF_POST_GAME]
    raw_entries: list[dict[str, Any]] = conf[CONF_TEAMS]

    session = async_get_clientsession(hass)
    clients: dict[tuple[str, str], EspnLeagueClient] = {}
    grouped_teams: dict[tuple[str, str], list[TrackedTeamConfig]] = {}

    resolved_items: list[tuple[tuple[str, str], dict[str, Any], TeamInfo, str]] = []
    slug_counts: dict[str, int] = {}

    for entry in raw_entries:
        sport = entry[CONF_SPORT].strip().lower()
        league = entry[CONF_LEAGUE].strip().lower()
        key = (sport, league)

        if key not in clients:
            clients[key] = EspnLeagueClient(session=session, sport=sport, league=league)

        try:
            team_info = await clients[key].async_resolve_team(entry[CONF_TEAM])
        except GameTrackerError as err:
            _LOGGER.warning(
                "Could not fetch %s/%s team directory (%s); using raw query '%s'",
                sport,
                league,
                err,
                entry[CONF_TEAM],
            )
            slug = slugify_name(entry[CONF_TEAM])
            team_info = TeamInfo(
                team_id=entry[CONF_TEAM],
                abbreviation=entry[CONF_TEAM].upper(),
                name=entry[CONF_TEAM],
                display_name=entry[CONF_TEAM],
                default_slug=slug,
                sport=sport,
                league=league,
            )

        base_slug = (
            slugify_name(entry[CONF_SLUG])
            if entry.get(CONF_SLUG)
            else team_info.default_slug
        )
        slug_counts[base_slug] = slug_counts.get(base_slug, 0) + 1
        resolved_items.append((key, entry, team_info, base_slug))

    for key, entry, team_info, base_slug in resolved_items:
        entity_slug = (
            f"{base_slug}_{slugify_name(team_info.league)}"
            if slug_counts[base_slug] > 1
            else base_slug
        )
        tracked = TrackedTeamConfig(
            team_info=team_info,
            entity_slug=entity_slug,
            pre_game=entry.get(CONF_PRE_GAME, default_pre),
            post_game=entry.get(CONF_POST_GAME, default_post),
        )
        grouped_teams.setdefault(key, []).append(tracked)

    coordinators: list[LeagueScoreboardCoordinator] = []
    for key, team_list in grouped_teams.items():
        coord = LeagueScoreboardCoordinator(
            hass=hass, client=clients[key], tracked_teams=team_list
        )
        await coord.async_refresh()
        coordinators.append(coord)

    hass.data[DOMAIN] = coordinators
    hass.async_create_task(
        async_load_platform(hass, Platform.BINARY_SENSOR, DOMAIN, {}, config)
    )
    return True
