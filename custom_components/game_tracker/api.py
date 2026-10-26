"""ESPN multi-sport scoreboard client, team resolver, and active-window logic."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone, tzinfo
import re
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

try:
    import aiohttp
except ImportError:  # pragma: no cover - allows standalone CLI/unit tests without HA venv
    aiohttp = None  # type: ignore[assignment]

if TYPE_CHECKING:
    import aiohttp

from .const import DEFAULT_GAME_DURATION, ESPN_BASE_URL, SPORT_DURATIONS

EASTERN_TZ = ZoneInfo("America/New_York")
CANCELLED_STATUSES = {"STATUS_POSTPONED", "STATUS_CANCELED", "STATUS_SUSPENDED"}


class GameTrackerError(Exception):
    """Raised when fetching or resolving ESPN scoreboard/team data fails."""


def slugify_name(value: str) -> str:
    """Convert a team name into a clean Home Assistant entity slug."""
    slug = re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
    return slug or "team"


def hex_to_colors(raw_hex: str | None) -> tuple[str | None, list[int] | None]:
    """Convert an ESPN 6-digit hex color (e.g. '69be28') into ('#69BE28', [105, 190, 40])."""
    if not raw_hex:
        return None, None
    cleaned = raw_hex.strip().lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", cleaned):
        return None, None
    rgb = [int(cleaned[i : i + 2], 16) for i in (0, 2, 4)]
    return f"#{cleaned.upper()}", rgb


def parse_iso_utc(raw_dt: str) -> datetime:
    """Parse an ESPN ISO-8601 timestamp into a UTC-aware datetime."""
    cleaned = raw_dt.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    if re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}[+-]\d{2}:\d{2}$", cleaned):
        cleaned = f"{cleaned[:16]}:00{cleaned[16:]}"
    dt = datetime.fromisoformat(cleaned)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _extract_score(raw_score: Any) -> int | None:
    """Extract an integer score from ESPN's competitor object."""
    if raw_score is None:
        return None
    if isinstance(raw_score, (int, float)):
        return int(raw_score)
    if isinstance(raw_score, str):
        raw_score = raw_score.strip()
        if not raw_score:
            return None
        try:
            return int(float(raw_score))
        except ValueError:
            return None
    if isinstance(raw_score, dict):
        for key in ("value", "displayValue"):
            if raw_score.get(key) is not None:
                try:
                    return int(float(raw_score[key]))
                except (TypeError, ValueError):
                    pass
    return None


def _extract_broadcasts(competition: dict[str, Any]) -> str | None:
    """Extract TV/streaming network names from an ESPN competition."""
    names: list[str] = []
    for item in competition.get("broadcasts") or []:
        if not isinstance(item, dict):
            continue
        media = item.get("media")
        if isinstance(media, dict) and media.get("shortName"):
            val = str(media["shortName"]).strip()
            if val and val not in names:
                names.append(val)
        for net in item.get("names") or []:
            val = str(net).strip()
            if val and val not in names:
                names.append(val)
    return ", ".join(names) if names else None


def merge_scoreboard_payloads(*payloads: dict[str, Any] | None) -> dict[str, Any]:
    """Merge multiple ESPN scoreboard payloads, deduplicating events by ID."""
    events_by_id: dict[str, dict[str, Any]] = {}
    ordered_ids: list[str] = []
    for payload in payloads:
        if not payload:
            continue
        for ev in payload.get("events") or []:
            ev_id = str(ev.get("id") or "")
            if not ev_id:
                continue
            if ev_id not in events_by_id:
                ordered_ids.append(ev_id)
            events_by_id[ev_id] = ev
    return {"events": [events_by_id[eid] for eid in ordered_ids]}


@dataclass(slots=True)
class TeamInfo:
    """Metadata for a resolved team in a specific sport/league."""

    team_id: str
    abbreviation: str
    name: str
    display_name: str
    default_slug: str
    sport: str
    league: str
    logo: str | None = None
    primary_color_hex: str | None = None
    primary_color_rgb: list[int] | None = None
    secondary_color_hex: str | None = None
    secondary_color_rgb: list[int] | None = None


@dataclass(slots=True)
class TeamGame:
    """Normalized state of a single game on the scoreboard for a tracked team."""

    event_id: str
    name: str
    short_name: str
    start_utc: datetime
    end_utc: datetime
    time_tbd: bool
    is_home: bool | None
    opponent_name: str | None
    opponent_abbr: str | None
    venue: str | None
    broadcast: str | None
    state: str  # "pre" | "in" | "post"
    status_name: str
    status_detail: str
    completed: bool
    team_score: int | None
    opponent_score: int | None
    period: int | None
    display_clock: str | None

    def game_date(self, local_tz: tzinfo) -> date:
        """Return the calendar date of this game in `local_tz` (US Eastern for TBD flex dates)."""
        if self.time_tbd:
            return self.start_utc.astimezone(EASTERN_TZ).date()
        return self.start_utc.astimezone(local_tz).date()

    def is_active(
        self,
        now: datetime,
        pre_game: timedelta = timedelta(0),
        post_game: timedelta = timedelta(0),
        completed_at: datetime | None = None,
    ) -> bool:
        """Return True if this game (including pre/post buffers) is currently active."""
        if self.time_tbd or self.status_name in CANCELLED_STATUSES:
            return False

        # Live in-progress games stay active regardless of overtime, extra innings, or delays.
        if self.state == "in":
            return True

        pre_delta = max(timedelta(0), pre_game)
        post_delta = max(timedelta(0), post_game)
        window_start = self.start_utc - pre_delta

        if self.completed or self.state == "post":
            if post_delta <= timedelta(0):
                return False
            end_anchor = completed_at or self.end_utc
            return window_start <= now < (end_anchor + post_delta)

        return window_start <= now < self.end_utc

    def is_on_game_day(self, now: datetime, local_tz: tzinfo) -> bool:
        """Return True all day (00:00–23:59:59 in `local_tz`) on game day, or while live past midnight."""
        if self.status_name in CANCELLED_STATUSES:
            return False
        if self.state == "in":
            return True
        return self.game_date(local_tz) == now.astimezone(local_tz).date()


def match_team_from_directory(
    teams_payload: dict[str, Any],
    query: str,
    sport: str,
    league: str,
) -> TeamInfo | None:
    """Resolve a user team query (e.g. 'seahawks', 'sea', 'mariners') from ESPN /teams."""
    needle = query.strip().lower()
    if not needle:
        return None

    raw_teams: list[dict[str, Any]] = []
    for sp in teams_payload.get("sports") or []:
        for lg in sp.get("leagues") or []:
            for wrapper in lg.get("teams") or []:
                if isinstance(wrapper, dict) and isinstance(wrapper.get("team"), dict):
                    raw_teams.append(wrapper["team"])

    matched: dict[str, Any] | None = None
    for t in raw_teams:
        candidates = {
            str(t.get(field) or "").strip().lower()
            for field in (
                "id",
                "abbreviation",
                "name",
                "nickname",
                "shortDisplayName",
                "displayName",
                "location",
                "slug",
            )
        }
        if needle in candidates:
            matched = t
            break

    if matched is None:
        for t in raw_teams:
            disp = str(t.get("displayName") or "").lower()
            if re.search(rf"\b{re.escape(needle)}\b", disp):
                matched = t
                break

    if matched is None:
        return None

    return _build_team_info(matched, sport=sport, league=league)


def _build_team_info(raw_team: dict[str, Any], sport: str, league: str) -> TeamInfo:
    """Construct TeamInfo from an ESPN team JSON dict."""
    display_name = str(raw_team.get("displayName") or raw_team.get("name") or "Team")
    name = str(raw_team.get("name") or display_name)
    nickname = str(raw_team.get("nickname") or "").strip()
    abbr = str(raw_team.get("abbreviation") or name[:3]).upper()

    if "college" in league:
        slug_source = display_name
    elif name == display_name and nickname and len(nickname) < len(display_name):
        slug_source = nickname
    else:
        slug_source = name

    logos = raw_team.get("logos") or []
    logo = raw_team.get("logo") or (
        logos[0].get("href") if logos and isinstance(logos[0], dict) else None
    )
    p_hex, p_rgb = hex_to_colors(raw_team.get("color"))
    s_hex, s_rgb = hex_to_colors(raw_team.get("alternateColor"))

    return TeamInfo(
        team_id=str(raw_team.get("id") or abbr),
        abbreviation=abbr,
        name=name,
        display_name=display_name,
        default_slug=slugify_name(slug_source),
        sport=sport,
        league=league,
        logo=logo,
        primary_color_hex=p_hex,
        primary_color_rgb=p_rgb,
        secondary_color_hex=s_hex,
        secondary_color_rgb=s_rgb,
    )


def team_matches_competitor(comp_team: dict[str, Any], target: TeamInfo | str) -> bool:
    """Return True if a scoreboard competitor team dict matches TeamInfo or raw query."""
    if isinstance(target, TeamInfo):
        if str(comp_team.get("id") or "") == target.team_id:
            return True
        return str(comp_team.get("abbreviation") or "").upper() == target.abbreviation

    needle = target.strip().lower()
    candidates = {
        str(comp_team.get(k) or "").strip().lower()
        for k in ("id", "abbreviation", "name", "nickname", "shortDisplayName", "displayName")
    }
    return needle in candidates


def parse_scoreboard_for_team(
    scoreboard_payload: dict[str, Any],
    target: TeamInfo | str,
    sport: str,
) -> list[TeamGame]:
    """Extract all games for a specific team from an ESPN /scoreboard payload."""
    duration = SPORT_DURATIONS.get(sport, DEFAULT_GAME_DURATION)
    games: list[TeamGame] = []

    for event in scoreboard_payload.get("events") or []:
        competitions = event.get("competitions") or []
        if not competitions:
            continue
        comp = competitions[0]
        competitors = comp.get("competitors") or []

        my_comp: dict[str, Any] | None = None
        opp_comp: dict[str, Any] | None = None
        for c in competitors:
            c_team = c.get("team") or {}
            if my_comp is None and team_matches_competitor(c_team, target):
                my_comp = c
            else:
                opp_comp = c

        if my_comp is None:
            continue

        raw_date = comp.get("date") or event.get("date")
        if not raw_date:
            continue
        start_utc = parse_iso_utc(raw_date)
        end_utc = start_utc + duration

        status_obj = comp.get("status") or event.get("status") or {}
        status_type = status_obj.get("type") or {}
        state = str(status_type.get("state") or "pre").lower()
        status_name = str(status_type.get("name") or "STATUS_SCHEDULED")
        status_detail = str(
            status_type.get("shortDetail")
            or status_type.get("detail")
            or status_type.get("description")
            or "Scheduled"
        )
        completed = bool(status_type.get("completed", state == "post"))

        time_valid = event.get("timeValid", True) and comp.get("timeValid", True)
        is_tbd_flex = bool(status_obj.get("isTBDFlex", False))
        time_tbd = ((not time_valid) or is_tbd_flex or "TBD" in status_detail.upper()) and state == "pre"

        opp_team = (opp_comp or {}).get("team") or {}
        venue_name = (comp.get("venue") or {}).get("fullName")

        period = status_obj.get("period")
        if period is not None:
            try:
                period = int(period)
            except (TypeError, ValueError):
                period = None

        games.append(
            TeamGame(
                event_id=str(event.get("id") or comp.get("id")),
                name=str(event.get("name") or "Game"),
                short_name=str(event.get("shortName") or ""),
                start_utc=start_utc,
                end_utc=end_utc,
                time_tbd=time_tbd,
                is_home=(my_comp.get("homeAway") == "home") if my_comp.get("homeAway") else None,
                opponent_name=opp_team.get("displayName"),
                opponent_abbr=opp_team.get("abbreviation"),
                venue=venue_name,
                broadcast=_extract_broadcasts(comp),
                state=state,
                status_name=status_name,
                status_detail=status_detail,
                completed=completed,
                team_score=_extract_score(my_comp.get("score")),
                opponent_score=_extract_score((opp_comp or {}).get("score")),
                period=period,
                display_clock=status_obj.get("displayClock"),
            )
        )

    games.sort(key=lambda g: g.start_utc)
    return games


def select_active_game(
    games: list[TeamGame],
    now: datetime,
    pre_game: timedelta = timedelta(0),
    post_game: timedelta = timedelta(0),
    completed_timestamps: dict[str, datetime] | None = None,
) -> TeamGame | None:
    """Return the active game (doubleheader-safe, preferring live 'in' games)."""
    completed_map = completed_timestamps or {}
    for g in games:
        if g.state == "in" and g.is_active(
            now, pre_game, post_game, completed_map.get(g.event_id)
        ):
            return g
    for g in games:
        if g.is_active(
            now, pre_game, post_game, completed_map.get(g.event_id)
        ):
            return g
    return None


def select_game_day_game(
    games: list[TeamGame],
    now: datetime,
    local_tz: tzinfo,
) -> TeamGame | None:
    """Return today's game in `local_tz` (preferring a live 'in' game if doubleheader/midnight)."""
    for g in games:
        if g.state == "in" and g.is_on_game_day(now, local_tz):
            return g
    for g in games:
        if g.is_on_game_day(now, local_tz):
            return g
    return None


class EspnLeagueClient:
    """Async ESPN client scoped to a single (sport, league) pair."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        sport: str,
        league: str,
        request_timeout: float = 15.0,
    ) -> None:
        self._session = session
        self.sport = sport
        self.league = league
        self._timeout = aiohttp.ClientTimeout(total=request_timeout)
        self._teams_payload: dict[str, Any] | None = None

    @property
    def scoreboard_url(self) -> str:
        """Scoreboard URL for this sport/league."""
        return f"{ESPN_BASE_URL}/{self.sport}/{self.league}/scoreboard"

    @property
    def teams_url(self) -> str:
        """Teams directory URL for this sport/league."""
        return f"{ESPN_BASE_URL}/{self.sport}/{self.league}/teams?limit=500"

    async def _fetch_json(
        self, url: str, params: dict[str, str] | None = None
    ) -> dict[str, Any]:
        try:
            async with self._session.get(url, params=params, timeout=self._timeout) as resp:
                resp.raise_for_status()
                return await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise GameTrackerError(f"Error fetching {url}: {err}") from err

    async def async_resolve_team(self, query: str) -> TeamInfo:
        """Resolve a team query against ESPN's /teams directory (cached per league)."""
        if self._teams_payload is None:
            self._teams_payload = await self._fetch_json(self.teams_url)

        info = match_team_from_directory(
            self._teams_payload, query=query, sport=self.sport, league=self.league
        )
        if info is not None:
            return info

        slug = slugify_name(query)
        return TeamInfo(
            team_id=query.strip(),
            abbreviation=query.strip().upper(),
            name=query.strip(),
            display_name=query.strip(),
            default_slug=slug,
            sport=self.sport,
            league=self.league,
        )

    async def async_fetch_scoreboard(
        self, local_date_str: str | None = None
    ) -> dict[str, Any]:
        """Fetch current scoreboard JSON (and merge explicit `dates=YYYYMMDD` for daily sports)."""
        default_payload = await self._fetch_json(self.scoreboard_url)
        if not local_date_str:
            return default_payload

        try:
            today_payload = await self._fetch_json(
                self.scoreboard_url, params={"dates": local_date_str}
            )
        except GameTrackerError:
            return default_payload

        return merge_scoreboard_payloads(default_payload, today_payload)
