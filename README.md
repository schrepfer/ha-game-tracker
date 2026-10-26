# Sports Game Tracker (`game_tracker`)

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2024.4%2B-41BDF5.svg)](https://www.home-assistant.io/)
[![Version](https://img.shields.io/badge/version-1.0.0-blue.svg)](custom_components/game_tracker/manifest.json)
[![Validate](https://github.com/schrepfer/ha-game-tracker/actions/workflows/validate.yml/badge.svg)](https://github.com/schrepfer/ha-game-tracker/actions/workflows/validate.yml)

A lightweight Home Assistant custom integration that tracks live sports schedules and game states across **NFL, MLB, NHL, NBA, WNBA, MLS, NWSL, College Football/Basketball, European Soccer**, and other ESPN-covered leagues.

For each configured team, `game_tracker` automatically creates **two** `binary_sensor` entities:
* **`binary_sensor.<slug>_game`** — Turns **`on`** while your team is playing (from `pre_game` before kickoff/first pitch through `post_game` after the final whistle) and **`off`** when it ends.
* **`binary_sensor.<slug>_game_day`** — Turns **`on`** all day (`00:00:00` – `23:59:59` in your Home Assistant server's local timezone) whenever your team has a game scheduled that day (ideal for keeping evening house lights in team colors even when the game kicked off at 10:00 AM).

---

## Features

* **No API Keys or External Dependencies** — Uses ESPN's public `/scoreboard` and `/teams` JSON endpoints with Home Assistant's built-in `aiohttp` session.
* **Live Overtime & Delay Aware** — Stays **`on`** as long as ESPN reports the game `in` progress (covering overtime, extra innings, shootouts, weather delays, and late games running past midnight).
* **`TBD` Flex-Schedule Safe** — `binary_sensor.<slug>_game` never falsely triggers at placeholder midnight timestamps on `TBD` flex games, while `binary_sensor.<slug>_game_day` still accurately detects the game's calendar date.
* **Efficient Shared Polling** — Groups teams by `(sport, league)` so tracking multiple teams in the same league shares a single coordinator (`30 min` when idle; `60 sec` within 2 hours of start or during active games, plus exact second-level boundary and local-midnight timers).
* **Dynamic Team Colors & Metadata** — Exposes each team's official primary/secondary hex & RGB colors, logo, live score, TV broadcast, and opponent in entity attributes.

---

## Installation

You can install **Sports Game Tracker** on any Home Assistant server using **HACS** or standard terminal commands.

### Method 1: HACS (Recommended)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=schrepfer&repository=ha-game-tracker&category=integration)

Or add it manually in HACS:
1. In Home Assistant, open **HACS** → click the top-right menu (`⋮`) → **Custom repositories**.
2. Paste `https://github.com/schrepfer/ha-game-tracker`, select **Integration** as the category, and click **Add**.
3. Search for **Sports Game Tracker** in HACS, click **Download**, and **restart Home Assistant**.

---

### Method 2: Home Assistant OS / Supervised (Terminal & SSH Add-on)

Paste this single command directly into the Home Assistant **Terminal & SSH** add-on (works for both fresh installs and upgrades, no `git` required):

```bash
tmp=$(mktemp -d) && \
  curl -fsSL https://github.com/schrepfer/ha-game-tracker/archive/refs/heads/main.tar.gz | tar -xz -C "$tmp" && \
  mkdir -p /config/custom_components && \
  rm -rf /config/custom_components/game_tracker && \
  mv "$tmp"/ha-game-tracker-main/custom_components/game_tracker /config/custom_components/ && \
  rm -rf "$tmp" && \
  ha core restart
```

---

### Method 3: Home Assistant Container (Docker) or Core

Set `HA_CONFIG` to your Home Assistant configuration directory (for example `/opt/homeassistant/config` or `~/.homeassistant`), extract `custom_components/game_tracker`, and restart Home Assistant:

```bash
HA_CONFIG=/path/to/homeassistant/config

tmp=$(mktemp -d) && \
  curl -fsSL https://github.com/schrepfer/ha-game-tracker/archive/refs/heads/main.tar.gz | tar -xz -C "$tmp" && \
  mkdir -p "$HA_CONFIG/custom_components" && \
  rm -rf "$HA_CONFIG/custom_components/game_tracker" && \
  mv "$tmp"/ha-game-tracker-main/custom_components/game_tracker "$HA_CONFIG/custom_components/" && \
  rm -rf "$tmp"

# Restart your Home Assistant container or service:
docker restart homeassistant
```

---

### Method 4: Git Clone + SCP from Another Machine

> **Important:** Inside `<config>/custom_components/`, the directory **must** be named `game_tracker` (matching `"domain": "game_tracker"` in [`manifest.json`](custom_components/game_tracker/manifest.json)).

```bash
git clone https://github.com/schrepfer/ha-game-tracker.git
scp -r ha-game-tracker/custom_components/game_tracker root@homeassistant.local:/config/custom_components/
```

Then restart Home Assistant (**Developer Tools → YAML → Restart**).

---

## Configuration

Add the `game_tracker:` block to your Home Assistant `configuration.yaml` file (YAML changes require a Home Assistant restart).

### Minimal Example

```yaml
# configuration.yaml
game_tracker:
  pre_game:
    minutes: 15
  post_game:
    minutes: 15
  teams:
    - sport: football
      league: nfl
      team: seahawks
```

This creates:
* **`binary_sensor.seahawks_game`** (`on` during the game + buffers)
* **`binary_sensor.seahawks_game_day`** (`on` all day on Seahawks game days)

### Multi-Sport & Advanced Example

```yaml
# configuration.yaml
game_tracker:
  pre_game: "00:15:00"
  post_game:
    minutes: 15
  teams:
    - sport: football
      league: nfl
      team: seahawks          # -> binary_sensor.seahawks_game & binary_sensor.seahawks_game_day

    - sport: baseball
      league: mlb
      team: mariners          # -> binary_sensor.mariners_game & binary_sensor.mariners_game_day
      pre_game:               # Optional per-team buffer override
        minutes: 5
      post_game:
        minutes: 10

    # Teams sharing a nickname across leagues disambiguate automatically
    # (`cardinals_nfl_*` / `cardinals_mlb_*`), or accept a custom `slug`:
    - sport: football
      league: nfl
      team: cardinals
      slug: az_cardinals      # -> binary_sensor.az_cardinals_game & _game_day

    - sport: baseball
      league: mlb
      team: cardinals
      slug: stl_cardinals     # -> binary_sensor.stl_cardinals_game & _game_day
```

### Configuration Variables

| Key | Type | Required | Default | Description |
| :--- | :--- | :--- | :--- | :--- |
| `teams` | list | **Yes** | — | List of team mappings to track (see below). |
| `pre_game` | time period | No | `00:00:00` | Default duration before start time to turn `binary_sensor.<slug>_game` `on`. Accepts `{days, hours, minutes, seconds, milliseconds}`, `"HH:MM:SS"`, or integer seconds. |
| `post_game` | time period | No | `00:00:00` | Default duration after game completion to keep `binary_sensor.<slug>_game` `on`. |

#### `teams` Item Variables

| Key | Type | Required | Default | Description |
| :--- | :--- | :--- | :--- | :--- |
| `sport` | string | **Yes** | — | ESPN sport path segment (e.g., `football`, `baseball`, `hockey`, `basketball`, `soccer`). |
| `league` | string | **Yes** | — | ESPN league path segment (e.g., `nfl`, `mlb`, `nhl`, `nba`, `usa.1`). |
| `team` | string | **Yes** | — | Team abbreviation (`sea`, `lv`), nickname (`seahawks`, `raiders`), or full name (`Seattle Seahawks`) — case-insensitive. |
| `slug` | string | No | Auto | Custom entity ID slug resulting in `binary_sensor.<slug>_game` and `binary_sensor.<slug>_game_day`. |
| `pre_game` | time period | No | Global `pre_game` | Per-team override for `pre_game`. |
| `post_game` | time period | No | Global `post_game` | Per-team override for `post_game`. |

### Common ESPN `sport` & `league` Identifiers

Because `sport` and `league` map directly to `https://site.api.espn.com/apis/site/v2/sports/<sport>/<league>/scoreboard`, some non-obvious leagues (especially soccer and college sports) use specific ESPN slugs:

| League | `sport:` | `league:` | Example `team:` |
| :--- | :--- | :--- | :--- |
| **NFL** | `football` | `nfl` | `seahawks` (`sea`), `raiders` (`lv`) |
| **NCAA Football** | `football` | `college-football` | `washington` (`wash`), `oregon` (`ore`) |
| **MLB** | `baseball` | `mlb` | `mariners` (`sea`), `dodgers` (`lad`) |
| **NHL** | `hockey` | `nhl` | `kraken` (`sea`), `bruins` (`bos`) |
| **NBA** | `basketball` | `nba` | `celtics` (`bos`), `lakers` (`lal`) |
| **WNBA** | `basketball` | `wnba` | `storm` (`sea`), `liberty` (`ny`) |
| **NCAA Men's / Women's Basketball** | `basketball` | `mens-college-basketball` / `womens-college-basketball` | `gonzaga` (`gonz`), `washington` (`wash`) |
| **MLS** | `soccer` | `usa.1` | `sounders` (`sea`), `timbers` (`por`) |
| **NWSL** | `soccer` | `usa.nwsl` | `reign` (`sea`), `thorns` (`por`) |
| **English Premier League** | `soccer` | `eng.1` | `arsenal` (`ars`), `liverpool` (`liv`) |
| **UEFA Champions League** | `soccer` | `uefa.champions` | `real madrid`, `bayern munich` |

---

## Finding Team Keys & Determining `<slug>` (Using `jq`)

### How `<slug>` Is Determined

1. **Custom `slug:` provided** → Uses your exact value (`slug: az_cardinals` → `binary_sensor.az_cardinals_game` & `binary_sensor.az_cardinals_game_day`).
2. **Pro leagues (default)** → Uses ESPN's short `name` lowercased with underscores (`Seahawks` → `seahawks`, `Red Sox` → `red_sox`, `Sounders` → `sounders`).
3. **College leagues (`college` in `league`)** → Uses ESPN's full `displayName` (`Washington Huskies` → `washington_huskies`) since many schools share mascots like `Wildcats` or `Tigers`.
4. **Shared nicknames across leagues (e.g., Cardinals, Giants, Panthers, Jets, Rangers, Kings)**:
   * If you only configure **one** of them (e.g., just NFL Cardinals), `<slug>` is `cardinals`.
   * If you configure **both** without setting `slug:`, **both** automatically append `_{league}` so neither depends on list order (`cardinals_nfl` and `cardinals_mlb`).

### Inspecting ESPN's Team Directory with `curl` + `jq`

You can query ESPN's `/teams` endpoint directly for any `<sport>/<league>` to list every valid `team:` identifier (`abbreviation`, `name`, `nickname`, `displayName`) and see the default `<slug>`:

```bash
# List all teams in a league (e.g., football/nfl or baseball/mlb):
curl -s "https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams?limit=500" | \
  jq -r '.sports[0].leagues[0].teams[].team | "\(.abbreviation)\t\(.name)\t\(.displayName)\t-> slug: \(.name | ascii_downcase | gsub("[^a-z0-9]+"; "_"))"'
```

Search for a specific team name (such as `cardinals` or `giants`) across one or more leagues:

```bash
# Search for "cardinals" across NFL and MLB:
for sl in "football/nfl" "baseball/mlb"; do
  curl -s "https://site.api.espn.com/apis/site/v2/sports/$sl/teams?limit=500" | \
    jq -r --arg sl "$sl" '.sports[0].leagues[0].teams[].team
      | select(.displayName | test("cardinals"; "i"))
      | "\($sl)\tabbr=\(.abbreviation)\tname=\(.name)\tfull=\(.displayName)"'
done
# Output:
# football/nfl    abbr=ARI    name=Cardinals    full=Arizona Cardinals
# baseball/mlb    abbr=STL    name=Cardinals    full=St. Louis Cardinals
```

Or run the included preview script with your candidate `<sport>/<league>/<team>` entries to see the exact states for both `_game` and `_game_day`:

```bash
python3 scripts/preview_scoreboard.py football/nfl/cardinals baseball/mlb/cardinals
# Output:
# binary_sensor.cardinals_nfl_game       | game=OFF | game_day=OFF | Arizona Cardinals ...
# binary_sensor.cardinals_mlb_game       | game=OFF | game_day=OFF | St. Louis Cardinals ...
```

---

## Entities & State Attributes

Each entry in `teams` creates two binary sensors:
* **`binary_sensor.<slug>_game`** — `on` during `pre_game` → live broadcast (including OT/delays) → `post_game`, `off` otherwise.
* **`binary_sensor.<slug>_game_day`** — `on` from `00:00:00` to `23:59:59` local time on days with a scheduled (non-postponed/non-canceled) game, and stays `on` past midnight if a night game is still `in` progress.

Both entities expose the following state attributes:

| Attribute | Example Value | Description |
| :--- | :--- | :--- |
| `team` | `"Seattle Seahawks"` | Resolved full team name. |
| `abbreviation` | `"SEA"` | Official team abbreviation. |
| `sport` / `league` | `"football"` / `"nfl"` | Configured ESPN sport and league. |
| `logo` | `"https://a.espncdn.com/..."` | Team logo URL. |
| `primary_color_hex` / `primary_color_rgb` | `"#002244"` / `[0, 34, 68]` | Official primary team color (ready for `rgb_color` light actions). |
| `secondary_color_hex` / `secondary_color_rgb` | `"#69BE28"` / `[105, 190, 40]` | Official secondary team color. |
| `pre_game` / `post_game` | `"0:15:00"` | Configured active buffer durations *(present on `<slug>_game`)*. |
| `game_date` | `"2026-10-25"` | Local calendar date (`YYYY-MM-DD`) of the current or next game. |
| `matchup` / `short_name` | `"Chicago Bears at Seattle Seahawks"` / `"CHI @ SEA"` | Current or next game on the scoreboard. |
| `opponent` / `opponent_abbr` | `"Chicago Bears"` / `"CHI"` | Opponent name and abbreviation. |
| `is_home` | `true` | Whether your team is the home team. |
| `start_utc` | `"2026-11-03T01:15:00+00:00"` | Scheduled UTC start time (`null` if `time_tbd` is `true`). |
| `time_tbd` | `false` | `true` when start time has not been announced yet. |
| `venue` / `broadcast` | `"Lumen Field"` / `"ESPN"` | Stadium name and TV/streaming broadcast network(s). |
| `game_state` / `status_detail` | `"in"` / `"6:12 - 3rd"` | ESPN state (`pre`, `in`, `post`) and human-readable clock/status. |
| `team_score` / `opponent_score` | `20` / `3` | Live or final scores. |
| `period` / `clock` | `3` / `"6:12"` | Current quarter/inning/period and game clock. |

---

## Usage Examples

### 1. Live Game Window vs. All-Day Game Day

```jinja2
{# True only while the Seahawks game (+ pre/post buffers) is actively on: #}
{{ is_state('binary_sensor.seahawks_game', 'on') }}

{# True all day Sunday (even after a 10:00 AM kickoff finishes) for evening house lights: #}
{{ is_state('binary_sensor.seahawks_game_day', 'on') and is_state('sun.sun', 'below_horizon') }}
```

### 2. Evening Game-Day House Lights (Sunset → Midnight)

Turn on exterior lights at sunset on Seahawks game days — regardless of whether kickoff was at 10:00 AM, 1:25 PM, or 5:20 PM:

```yaml
automation:
  - alias: "Seahawks Game Day Evening Lights"
    triggers:
      - trigger: sun
        event: sunset
    conditions:
      - condition: state
        entity_id: binary_sensor.seahawks_game_day
        state: "on"
    actions:
      - action: light.turn_on
        target:
          entity_id: light.front_porch
        data:
          rgb_color: "{{ state_attr('binary_sensor.seahawks_game_day', 'secondary_color_rgb') }}"
          brightness_pct: 100
```

### 3. Live Game Trigger Across Multiple Teams (Dynamic Team Colors)

```yaml
automation:
  - alias: "Live Game Exterior Lights"
    triggers:
      - trigger: state
        entity_id:
          - binary_sensor.seahawks_game
          - binary_sensor.mariners_game
        from: "off"
        to: "on"
    actions:
      - action: light.turn_on
        target:
          entity_id: light.front_porch
        data:
          rgb_color: "{{ state_attr(trigger.entity_id, 'secondary_color_rgb') or [255, 255, 255] }}"
          brightness_pct: 100
```

### 4. Conditional Dashboard Card (Only Visible During a Game)

```yaml
type: conditional
conditions:
  - condition: state
    entity_id: binary_sensor.seahawks_game
    state: "on"
card:
  type: markdown
  content: >-
    ### 🏈 {{ state_attr('binary_sensor.seahawks_game', 'short_name') }}
    **SEA {{ state_attr('binary_sensor.seahawks_game', 'team_score') }}** –
    **{{ state_attr('binary_sensor.seahawks_game', 'opponent_abbr') }} {{ state_attr('binary_sensor.seahawks_game', 'opponent_score') }}**
    ({{ state_attr('binary_sensor.seahawks_game', 'status_detail') }} · {{ state_attr('binary_sensor.seahawks_game', 'broadcast') }})
```

---

## Troubleshooting & Local Testing

### Enable Debug Logging in Home Assistant

```yaml
# configuration.yaml
logger:
  default: info
  logs:
    custom_components.game_tracker: debug
```

### Test Scoreboard Feeds Locally (No Home Assistant Required)

```bash
python3 scripts/preview_scoreboard.py football/nfl/seahawks baseball/mlb/mariners soccer/usa.1/sounders
python3 -m unittest discover -s tests -v
```
