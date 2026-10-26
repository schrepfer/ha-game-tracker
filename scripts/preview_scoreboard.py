#!/usr/bin/env python3
"""Standalone CLI preview tool for `game_tracker` across explicit sport/league/team triples.

Usage examples:
  python3 scripts/preview_scoreboard.py
  python3 scripts/preview_scoreboard.py football/nfl/cardinals baseball/mlb/cardinals
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import sys
import types
import urllib.request
from zoneinfo import ZoneInfo


def _load_modules():
    root = Path(__file__).resolve().parent.parent
    pkg_dir = root / "custom_components" / "game_tracker"

    for mod_name in ("custom_components", "custom_components.game_tracker"):
        if mod_name not in sys.modules:
            pkg = types.ModuleType(mod_name)
            pkg.__path__ = [str(pkg_dir)]
            sys.modules[mod_name] = pkg

    for sub in ("const", "api"):
        full_name = f"custom_components.game_tracker.{sub}"
        spec = importlib.util.spec_from_file_location(full_name, pkg_dir / f"{sub}.py")
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[full_name] = module
        spec.loader.exec_module(module)

    return (
        sys.modules["custom_components.game_tracker.const"],
        sys.modules["custom_components.game_tracker.api"],
    )


def _fetch_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "HomeAssistant-GameTracker/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main(argv: list[str]) -> None:
    const, api = _load_modules()
    local_tz = ZoneInfo("America/Los_Angeles")
    now = datetime.now(timezone.utc)
    today_str = now.astimezone(local_tz).strftime("%Y%m%d")

    specs = argv or [
        "football/nfl/seahawks",
        "baseball/mlb/mariners",
        "hockey/nhl/kraken",
        "soccer/usa.1/sounders",
    ]

    teams_cache: dict[tuple[str, str], dict] = {}
    sb_cache: dict[tuple[str, str], dict] = {}
    resolved = []
    slug_counts: dict[str, int] = {}

    for raw in specs:
        parts = raw.split("/", 2)
        if len(parts) != 3:
            print(f"Invalid spec '{raw}' — expected '<sport>/<league>/<team>' (e.g. football/nfl/seahawks)")
            continue
        sport, league, team_query = (p.strip().lower() for p in parts)

        key = (sport, league)
        if key not in teams_cache:
            teams_cache[key] = _fetch_json(f"{const.ESPN_BASE_URL}/{sport}/{league}/teams?limit=500")
            base_sb = _fetch_json(f"{const.ESPN_BASE_URL}/{sport}/{league}/scoreboard")
            today_sb = _fetch_json(f"{const.ESPN_BASE_URL}/{sport}/{league}/scoreboard?dates={today_str}")
            sb_cache[key] = api.merge_scoreboard_payloads(base_sb, today_sb)

        info = api.match_team_from_directory(teams_cache[key], team_query, sport, league)
        if info is None:
            print(f"[{sport}/{league}] '{team_query}' -> NOT FOUND")
            continue

        slug_counts[info.default_slug] = slug_counts.get(info.default_slug, 0) + 1
        resolved.append((key, info))

    buf = timedelta(minutes=15)

    for key, info in resolved:
        sport, league = key
        entity_slug = (
            f"{info.default_slug}_{api.slugify_name(league)}"
            if slug_counts[info.default_slug] > 1
            else info.default_slug
        )
        games = api.parse_scoreboard_for_team(sb_cache[key], info, sport=sport)
        active = api.select_active_game(games, now, pre_game=buf, post_game=buf)
        game_day = api.select_game_day_game(games, now, local_tz)

        if games:
            g = active or game_day or games[-1]
            when = (
                f"{g.game_date(local_tz).isoformat()} (TBD)"
                if g.time_tbd
                else g.start_utc.astimezone(local_tz).strftime("%a %m/%d %I:%M %p %Z")
            )
            score = (
                f"{info.abbreviation} {g.team_score} - {g.opponent_abbr} {g.opponent_score} ({g.status_detail})"
                if g.team_score is not None and g.opponent_score is not None
                else g.status_detail
            )
            summary = f"{g.short_name} @ {when} | {score}"
        else:
            summary = "No game on current scoreboard (bye / off-day / offseason)"

        label = f"binary_sensor.{entity_slug}_game"
        print(
            f"{label:36s}"
            + f" | game={'ON ' if active else 'OFF'} | game_day={'ON ' if game_day else 'OFF'}"
            + f" | {info.display_name:20s} | {summary}"
        )


if __name__ == "__main__":
    main(sys.argv[1:])
