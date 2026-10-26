"""Binary sensor platform creating `binary_sensor.<team>_game` and `binary_sensor.<team>_game_day`."""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from . import LeagueScoreboardCoordinator, TrackedTeamConfig
from .api import TeamGame, select_active_game, select_game_day_game
from .const import DOMAIN

SPORT_ICONS: dict[str, str] = {
    "football": "mdi:football",
    "baseball": "mdi:baseball",
    "hockey": "mdi:hockey-puck",
    "basketball": "mdi:basketball",
    "soccer": "mdi:soccer",
}


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up `<slug>_game` and `<slug>_game_day` binary sensors per configured team."""
    if discovery_info is None:
        return

    coordinators: list[LeagueScoreboardCoordinator] = hass.data.get(DOMAIN, [])
    entities: list[BinarySensorEntity] = []
    for coordinator in coordinators:
        for team_cfg in coordinator.tracked_teams:
            entities.append(GameTrackerBinarySensor(coordinator, team_cfg))
            entities.append(GameTrackerGameDayBinarySensor(coordinator, team_cfg))

    async_add_entities(entities)


class _BaseTeamBinarySensor(
    CoordinatorEntity[LeagueScoreboardCoordinator], BinarySensorEntity
):
    """Shared base functionality and attribute formatting for team binary sensors."""

    def __init__(
        self,
        coordinator: LeagueScoreboardCoordinator,
        team_cfg: TrackedTeamConfig,
    ) -> None:
        super().__init__(coordinator)
        self._cfg = team_cfg
        self._unsub_timer: CALLBACK_TYPE | None = None

    @property
    def _games(self) -> list[TeamGame]:
        if not self.coordinator.data:
            return []
        return self.coordinator.data.get(self._cfg.entity_slug, [])

    def _next_or_latest_game(self, now: datetime) -> TeamGame | None:
        for g in self._games:
            if not g.completed and g.state != "post" and g.end_utc >= now:
                return g
        return self._games[-1] if self._games else None

    def _cancel_timer(self) -> None:
        if self._unsub_timer is not None:
            self._unsub_timer()
            self._unsub_timer = None

    async def async_will_remove_from_hass(self) -> None:
        self._cancel_timer()
        await super().async_will_remove_from_hass()

    def _format_attributes(self, target: TeamGame | None) -> dict[str, Any]:
        info = self._cfg.team_info
        local_tz = dt_util.get_default_time_zone()
        attrs: dict[str, Any] = {
            "team": info.display_name,
            "abbreviation": info.abbreviation,
            "sport": info.sport,
            "league": info.league,
            "logo": info.logo,
            "primary_color_hex": info.primary_color_hex,
            "primary_color_rgb": info.primary_color_rgb,
            "secondary_color_hex": info.secondary_color_hex,
            "secondary_color_rgb": info.secondary_color_rgb,
        }
        if target is not None:
            attrs.update(
                {
                    "matchup": target.name,
                    "short_name": target.short_name,
                    "opponent": target.opponent_name,
                    "opponent_abbr": target.opponent_abbr,
                    "is_home": target.is_home,
                    "game_date": target.game_date(local_tz).isoformat(),
                    "start_utc": None if target.time_tbd else target.start_utc.isoformat(),
                    "time_tbd": target.time_tbd,
                    "venue": target.venue,
                    "broadcast": target.broadcast,
                    "game_state": target.state,
                    "status_detail": target.status_detail,
                    "team_score": target.team_score,
                    "opponent_score": target.opponent_score,
                    "period": target.period,
                    "clock": target.display_clock,
                }
            )
        return attrs


class GameTrackerBinarySensor(_BaseTeamBinarySensor):
    """Binary sensor turning ON while a tracked team's game is live (plus pre/post buffers)."""

    def __init__(
        self,
        coordinator: LeagueScoreboardCoordinator,
        team_cfg: TrackedTeamConfig,
    ) -> None:
        super().__init__(coordinator, team_cfg)
        info = team_cfg.team_info
        self._attr_unique_id = f"game_tracker_{info.sport}_{info.league}_{info.team_id}"
        self._attr_name = f"{info.display_name} Game"
        self._attr_icon = SPORT_ICONS.get(info.sport, "mdi:trophy")
        self.entity_id = f"binary_sensor.{team_cfg.entity_slug}_game"

    def _active_game(self, now: datetime | None = None) -> TeamGame | None:
        ref_now = now or datetime.now(timezone.utc)
        return select_active_game(
            self._games,
            ref_now,
            pre_game=self._cfg.pre_game,
            post_game=self._cfg.post_game,
            completed_timestamps=self.coordinator.completed_timestamps,
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._schedule_next_boundary()

    @callback
    def _handle_coordinator_update(self) -> None:
        self._schedule_next_boundary()
        super()._handle_coordinator_update()

    @callback
    def _on_boundary_reached(self, _now: datetime) -> None:
        self._unsub_timer = None
        self.async_write_ha_state()
        self._schedule_next_boundary()
        self.hass.async_create_task(self.coordinator.async_request_refresh())

    @callback
    def _schedule_next_boundary(self) -> None:
        """Schedule exact wakeup at the next pre-game start or post-game end."""
        self._cancel_timer()
        now = datetime.now(timezone.utc)
        pre_delta = max(timedelta(0), self._cfg.pre_game)
        post_delta = max(timedelta(0), self._cfg.post_game)

        candidates: list[datetime] = []
        for g in self._games:
            if g.time_tbd:
                continue
            win_start = g.start_utc - pre_delta
            end_anchor = self.coordinator.completed_timestamps.get(g.event_id, g.end_utc)
            win_end = end_anchor + post_delta
            if win_start > now:
                candidates.append(win_start)
            if win_end > now:
                candidates.append(win_end)

        if candidates:
            self._unsub_timer = async_track_point_in_utc_time(
                self.hass,
                self._on_boundary_reached,
                min(candidates) + timedelta(milliseconds=100),
            )

    @property
    def is_on(self) -> bool:
        """Return True while a game (including pre/post buffers) is active."""
        return self._active_game() is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose team colors, buffer durations, and current/upcoming scoreboard info."""
        now = datetime.now(timezone.utc)
        target = self._active_game(now) or self._next_or_latest_game(now)
        attrs = self._format_attributes(target)
        attrs["pre_game"] = str(self._cfg.pre_game)
        attrs["post_game"] = str(self._cfg.post_game)
        return attrs


class GameTrackerGameDayBinarySensor(_BaseTeamBinarySensor):
    """Binary sensor turning ON all day (00:00–23:59:59 local time) on game day."""

    def __init__(
        self,
        coordinator: LeagueScoreboardCoordinator,
        team_cfg: TrackedTeamConfig,
    ) -> None:
        super().__init__(coordinator, team_cfg)
        info = team_cfg.team_info
        self._attr_unique_id = (
            f"game_tracker_{info.sport}_{info.league}_{info.team_id}_game_day"
        )
        self._attr_name = f"{info.display_name} Game Day"
        self._attr_icon = "mdi:calendar-today"
        self.entity_id = f"binary_sensor.{team_cfg.entity_slug}_game_day"

    def _today_game(self, now: datetime | None = None) -> TeamGame | None:
        ref_now = now or datetime.now(timezone.utc)
        local_tz = dt_util.get_default_time_zone()
        return select_game_day_game(self._games, ref_now, local_tz)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._schedule_next_midnight()

    @callback
    def _handle_coordinator_update(self) -> None:
        self._schedule_next_midnight()
        super()._handle_coordinator_update()

    @callback
    def _on_midnight_reached(self, _now: datetime) -> None:
        """Flip ON/OFF promptly at local midnight and refresh today's scoreboard."""
        self._unsub_timer = None
        self.async_write_ha_state()
        self._schedule_next_midnight()
        self.hass.async_create_task(self.coordinator.async_request_refresh())

    @callback
    def _schedule_next_midnight(self) -> None:
        """Schedule wakeup right after 00:00:00 in Home Assistant's local timezone."""
        self._cancel_timer()
        local_tz = dt_util.get_default_time_zone()
        now_local = dt_util.now(local_tz)
        next_midnight_local = datetime.combine(
            now_local.date() + timedelta(days=1),
            time.min,
            tzinfo=local_tz,
        )
        self._unsub_timer = async_track_point_in_utc_time(
            self.hass,
            self._on_midnight_reached,
            next_midnight_local.astimezone(timezone.utc) + timedelta(milliseconds=100),
        )

    @property
    def is_on(self) -> bool:
        """Return True all day in local time when the tracked team has a game today."""
        return self._today_game() is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose team colors and today's (or next) game info."""
        now = datetime.now(timezone.utc)
        target = self._today_game(now) or self._next_or_latest_game(now)
        return self._format_attributes(target)
