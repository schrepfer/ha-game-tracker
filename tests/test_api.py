"""Unit tests for the multi-sport `game_tracker` API parser and active/game-day logic."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from zoneinfo import ZoneInfo


def _load_api():
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

    return sys.modules["custom_components.game_tracker.api"]


api = _load_api()


SAMPLE_TEAMS_DIRECTORY = {
    "sports": [
        {
            "leagues": [
                {
                    "teams": [
                        {
                            "team": {
                                "id": "26",
                                "abbreviation": "SEA",
                                "name": "Seahawks",
                                "nickname": "Seahawks",
                                "shortDisplayName": "Seahawks",
                                "displayName": "Seattle Seahawks",
                                "color": "002244",
                                "alternateColor": "69be28",
                            }
                        },
                        {
                            "team": {
                                "id": "13",
                                "abbreviation": "LV",
                                "name": "Raiders",
                                "nickname": "Raiders",
                                "shortDisplayName": "Raiders",
                                "displayName": "Las Vegas Raiders",
                                "color": "000000",
                                "alternateColor": "a5acaf",
                            }
                        },
                    ]
                }
            ]
        }
    ]
}


SAMPLE_SCOREBOARD = {
    "events": [
        {
            "id": "401873000",
            "date": "2026-10-25T17:00Z",  # Sun Oct 25 10:00 AM PDT
            "name": "Seattle Seahawks at Carolina Panthers",
            "shortName": "SEA @ CAR",
            "timeValid": True,
            "competitions": [
                {
                    "id": "401873000",
                    "date": "2026-10-25T17:00Z",
                    "timeValid": True,
                    "venue": {"fullName": "Bank of America Stadium"},
                    "broadcasts": [{"market": "national", "names": ["FOX"]}],
                    "status": {
                        "period": 0,
                        "displayClock": "0:00",
                        "type": {
                            "name": "STATUS_SCHEDULED",
                            "state": "pre",
                            "completed": False,
                            "shortDetail": "10/25 - 1:00 PM EDT",
                        },
                    },
                    "competitors": [
                        {
                            "homeAway": "away",
                            "team": {
                                "id": "26",
                                "abbreviation": "SEA",
                                "name": "Seahawks",
                                "displayName": "Seattle Seahawks",
                            },
                            "score": "0",
                        },
                        {
                            "homeAway": "home",
                            "team": {
                                "id": "29",
                                "abbreviation": "CAR",
                                "name": "Panthers",
                                "displayName": "Carolina Panthers",
                            },
                            "score": "0",
                        },
                    ],
                }
            ],
        },
        {
            "id": "401873999",
            "date": "2027-01-10T05:00Z",
            "name": "Las Vegas Raiders at Kansas City Chiefs",
            "shortName": "LV @ KC",
            "timeValid": False,
            "competitions": [
                {
                    "id": "401873999",
                    "date": "2027-01-10T05:00Z",
                    "timeValid": False,
                    "status": {
                        "type": {
                            "name": "STATUS_SCHEDULED",
                            "state": "pre",
                            "completed": False,
                            "shortDetail": "TBD",
                        }
                    },
                    "competitors": [
                        {
                            "homeAway": "away",
                            "team": {
                                "id": "13",
                                "abbreviation": "LV",
                                "name": "Raiders",
                                "displayName": "Las Vegas Raiders",
                            },
                        },
                        {
                            "homeAway": "home",
                            "team": {
                                "id": "12",
                                "abbreviation": "KC",
                                "name": "Chiefs",
                                "displayName": "Kansas City Chiefs",
                            },
                        },
                    ],
                }
            ],
        },
    ]
}


class GameTrackerApiTests(unittest.TestCase):
    """Tests for flexible team keys, timedelta active windows, and game-day detection."""

    def test_team_matching_by_abbr_nickname_or_full_name(self) -> None:
        for query in ("sea", "SEA", "seahawks", "Seattle Seahawks"):
            info = api.match_team_from_directory(
                SAMPLE_TEAMS_DIRECTORY, query, "football", "nfl"
            )
            self.assertIsNotNone(info)
            assert info is not None
            self.assertEqual(info.team_id, "26")
            self.assertEqual(info.default_slug, "seahawks")
            self.assertEqual(info.primary_color_hex, "#002244")
            self.assertEqual(info.secondary_color_rgb, [105, 190, 40])

    def test_mls_sounders_slug(self) -> None:
        mls_dir = {
            "sports": [
                {
                    "leagues": [
                        {
                            "teams": [
                                {
                                    "team": {
                                        "id": "9726",
                                        "abbreviation": "SEA",
                                        "name": "Seattle Sounders FC",
                                        "nickname": "Sounders",
                                        "displayName": "Seattle Sounders FC",
                                    }
                                }
                            ]
                        }
                    ]
                }
            ]
        }
        info = api.match_team_from_directory(mls_dir, "sounders", "soccer", "usa.1")
        self.assertIsNotNone(info)
        assert info is not None
        self.assertEqual(info.default_slug, "sounders")

    def test_active_windows_with_timedeltas_and_tbd_protection(self) -> None:
        sea_info = api.match_team_from_directory(
            SAMPLE_TEAMS_DIRECTORY, "seahawks", "football", "nfl"
        )
        assert sea_info is not None
        games = api.parse_scoreboard_for_team(SAMPLE_SCOREBOARD, sea_info, sport="football")
        self.assertEqual(len(games), 1)
        g = games[0]  # Kickoff 2026-10-25T17:00:00Z
        buf = timedelta(minutes=15, seconds=30)

        self.assertFalse(
            g.is_active(datetime(2026, 10, 25, 16, 40, tzinfo=timezone.utc), buf, buf)
        )
        self.assertTrue(
            g.is_active(datetime(2026, 10, 25, 16, 44, 30, tzinfo=timezone.utc), buf, buf)
        )

        # Live overtime 4 hours after kickoff -> ON
        g.state = "in"
        self.assertTrue(
            g.is_active(datetime(2026, 10, 25, 21, 10, tzinfo=timezone.utc), buf, buf)
        )

        # Post-game buffer after completion
        g.state = "post"
        g.completed = True
        done_at = datetime(2026, 10, 25, 21, 15, tzinfo=timezone.utc)
        self.assertTrue(g.is_active(done_at + timedelta(minutes=15), buf, buf, done_at))
        self.assertFalse(g.is_active(done_at + timedelta(minutes=16), buf, buf, done_at))

        # TBD game must never trigger `is_active` at placeholder time
        lv_info = api.match_team_from_directory(
            SAMPLE_TEAMS_DIRECTORY, "raiders", "football", "nfl"
        )
        assert lv_info is not None
        lv_games = api.parse_scoreboard_for_team(SAMPLE_SCOREBOARD, lv_info, sport="football")
        self.assertTrue(lv_games[0].time_tbd)
        self.assertFalse(
            lv_games[0].is_active(
                datetime(2027, 1, 10, 5, 5, tzinfo=timezone.utc), buf, buf
            )
        )

    def test_game_day_sensor_logic(self) -> None:
        pacific = ZoneInfo("America/Los_Angeles")
        sea_info = api.match_team_from_directory(
            SAMPLE_TEAMS_DIRECTORY, "seahawks", "football", "nfl"
        )
        assert sea_info is not None
        games = api.parse_scoreboard_for_team(SAMPLE_SCOREBOARD, sea_info, sport="football")
        g = games[0]  # Sun Oct 25 10:00 AM PDT

        # Saturday Oct 24 11:59 PM PDT -> OFF
        self.assertFalse(
            g.is_on_game_day(datetime(2026, 10, 25, 6, 59, tzinfo=timezone.utc), pacific)
        )
        # Sunday Oct 25 12:01 AM PDT (hours before kickoff) -> ON
        self.assertTrue(
            g.is_on_game_day(datetime(2026, 10, 25, 7, 1, tzinfo=timezone.utc), pacific)
        )
        # Sunday Oct 25 8:30 PM PDT (hours after morning game ended) -> still ON all evening!
        g.state = "post"
        g.completed = True
        self.assertTrue(
            g.is_on_game_day(datetime(2026, 10, 26, 3, 30, tzinfo=timezone.utc), pacific)
        )
        # Monday Oct 26 12:01 AM PDT -> OFF
        self.assertFalse(
            g.is_on_game_day(datetime(2026, 10, 26, 7, 1, tzinfo=timezone.utc), pacific)
        )
        # Unless a night game is still `in` progress past midnight -> ON
        g.state = "in"
        g.completed = False
        self.assertTrue(
            g.is_on_game_day(datetime(2026, 10, 26, 7, 15, tzinfo=timezone.utc), pacific)
        )
        # Postponed games do not count as game day
        g.state = "pre"
        g.status_name = "STATUS_POSTPONED"
        self.assertFalse(
            g.is_on_game_day(datetime(2026, 10, 25, 18, 0, tzinfo=timezone.utc), pacific)
        )

        # TBD flex game still knows its calendar date (Jan 10, NOT Jan 9 in Pacific!)
        lv_info = api.match_team_from_directory(
            SAMPLE_TEAMS_DIRECTORY, "raiders", "football", "nfl"
        )
        assert lv_info is not None
        tbd_game = api.parse_scoreboard_for_team(
            SAMPLE_SCOREBOARD, lv_info, sport="football"
        )[0]
        self.assertEqual(tbd_game.game_date(pacific), date(2027, 1, 10))
        self.assertFalse(
            tbd_game.is_on_game_day(
                datetime(2027, 1, 10, 5, 30, tzinfo=timezone.utc),  # Sat Jan 9 9:30 PM PST
                pacific,
            )
        )
        self.assertTrue(
            tbd_game.is_on_game_day(
                datetime(2027, 1, 10, 20, 0, tzinfo=timezone.utc),  # Sun Jan 10 12:00 PM PST
                pacific,
            )
        )

    def test_merge_scoreboard_payloads_deduplicates(self) -> None:
        p1 = {"events": [{"id": "1", "shortName": "A"}, {"id": "2", "shortName": "B"}]}
        p2 = {"events": [{"id": "2", "shortName": "B2"}, {"id": "3", "shortName": "C"}]}
        merged = api.merge_scoreboard_payloads(p1, p2)
        self.assertEqual([e["id"] for e in merged["events"]], ["1", "2", "3"])
        self.assertEqual(merged["events"][1]["shortName"], "B2")


if __name__ == "__main__":
    unittest.main()
