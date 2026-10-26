"""Constants for the Sports Game Tracker integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "game_tracker"

ESPN_BASE_URL = "https://site.api.espn.com/apis/site/v2/sports"

# Configuration keys
CONF_TEAMS = "teams"
CONF_TEAM = "team"
CONF_SPORT = "sport"
CONF_LEAGUE = "league"
CONF_SLUG = "slug"
CONF_PRE_GAME = "pre_game"
CONF_POST_GAME = "post_game"

# Defaults (no buffer unless specified)
DEFAULT_PRE_GAME = timedelta(0)
DEFAULT_POST_GAME = timedelta(0)

# Estimated fallback game durations per sport (live ESPN state overrides during play/OT/extra innings)
SPORT_DURATIONS: dict[str, timedelta] = {
    "football": timedelta(hours=3, minutes=15),
    "baseball": timedelta(hours=2, minutes=50),
    "hockey": timedelta(hours=2, minutes=40),
    "basketball": timedelta(hours=2, minutes=30),
    "soccer": timedelta(hours=2, minutes=10),
}
DEFAULT_GAME_DURATION = timedelta(hours=3)

# Adaptive polling intervals
IDLE_SCAN_INTERVAL = timedelta(minutes=30)
ACTIVE_SCAN_INTERVAL = timedelta(seconds=60)
