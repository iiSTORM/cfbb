"""
Fetches raw data from CollegeFootballData.com:
  - Upcoming games within the lookahead window
  - Season stat leaders (passing/rushing/receiving) for each team involved
  - Recent per-game logs for those leaders
  - Season advanced defensive stats for every team (for matchup factors)

Writes everything to docs/data/raw.json for compute_projections.py to
consume. Kept deliberately dumb (fetch + store) — all the projection
math lives in compute_projections.py so the two are easy to debug
independently.

Run locally with:
    CFBD_API_KEY=your_key python scripts/fetch_data.py
"""

import json
import logging
import random
import sys
import time
from datetime import datetime, timedelta, timezone
import os

import requests

import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("fetch_data")

# Retried: connection errors, timeouts, 5xx, and 429 (rate limit).
# NOT retried: 404 and other 4xx — those mean the endpoint/params are
# wrong, and hammering them again just burns quota for no benefit.
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
MAX_RETRIES = 3
BACKOFF_BASE_SECONDS = 1.5


def cfbd_get(path, params=None):
    """GET a CFBD endpoint. Returns parsed JSON, or None on failure
    (after retries for transient errors)."""
    if not config.CFBD_API_KEY:
        log.error("CFBD_API_KEY is not set. Get a free key at https://collegefootballdata.com/key")
        sys.exit(1)

    url = f"{config.CFBD_BASE_URL}{path}"
    headers = {"Authorization": f"Bearer {config.CFBD_API_KEY}", "Accept": "application/json"}

    last_error_summary = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers=headers, params=params or {}, timeout=config.REQUEST_TIMEOUT)

            if resp.status_code in RETRYABLE_STATUS_CODES and attempt < MAX_RETRIES:
                wait = _backoff_seconds(attempt, resp.headers.get("Retry-After"))
                log.warning(
                    f"{resp.status_code} on {path} {params} (attempt {attempt}/{MAX_RETRIES}) — "
                    f"retrying in {wait:.1f}s"
                )
                time.sleep(wait)
                continue

            resp.raise_for_status()
            return resp.json()

        except requests.exceptions.HTTPError as e:
            # 404s and other non-retryable 4xx land here on the final attempt
            # (or immediately, since they're not in RETRYABLE_STATUS_CODES).
            log.error(f"HTTP error on {path} {params}: {e} — response body: {resp.text[:500]}")
            return None
        except requests.exceptions.RequestException as e:
            last_error_summary = str(e)
            if attempt < MAX_RETRIES:
                wait = _backoff_seconds(attempt)
                log.warning(
                    f"Request failed on {path} {params} (attempt {attempt}/{MAX_RETRIES}): {e} — "
                    f"retrying in {wait:.1f}s"
                )
                time.sleep(wait)
                continue
            log.error(f"Request failed on {path} {params} after {MAX_RETRIES} attempts: {e}")
            return None
        except ValueError as e:
            log.error(f"Failed to parse JSON from {path} {params}: {e} — raw text: {resp.text[:500]}")
            return None

    log.error(f"Giving up on {path} {params} after {MAX_RETRIES} attempts: {last_error_summary}")
    return None


def _backoff_seconds(attempt, retry_after_header=None):
    """Exponential backoff with jitter, honoring a server-provided
    Retry-After header when present (typical on 429s)."""
    if retry_after_header:
        try:
            return float(retry_after_header)
        except ValueError:
            pass
    base = BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
    return base + random.uniform(0, base * 0.3)


def get_upcoming_games():
    """All season games, filtered to the lookahead window, not yet
    completed, and (if configured) restricted to TEAM_ALLOWLIST /
    CONFERENCE_ALLOWLIST."""
    log.info(f"Fetching {config.SEASON_YEAR} schedule...")
    games = cfbd_get("/games", {"year": config.SEASON_YEAR, "seasonType": config.SEASON_TYPE})
    if not games:
        log.warning("No games returned — check SEASON_YEAR/SEASON_TYPE, or that the season has started.")
        return []

    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=config.LOOKAHEAD_DAYS)
    upcoming = []

    team_allowlist = {t.lower() for t in config.TEAM_ALLOWLIST}
    conf_allowlist = {c.lower() for c in config.CONFERENCE_ALLOWLIST}

    for g in games:
        completed = g.get("completed", False)
        start_date = g.get("startDate") or g.get("start_date")
        if completed or not start_date:
            continue
        try:
            start_dt = datetime.fromisoformat(start_date.replace("Z", "+00:00"))
        except ValueError:
            continue
        if not (now <= start_dt <= cutoff):
            continue

        if team_allowlist:
            home = (g.get("homeTeam") or g.get("home_team") or "").lower()
            away = (g.get("awayTeam") or g.get("away_team") or "").lower()
            if home not in team_allowlist and away not in team_allowlist:
                continue

        if conf_allowlist:
            home_conf = (g.get("homeConference") or g.get("home_conference") or "").lower()
            away_conf = (g.get("awayConference") or g.get("away_conference") or "").lower()
            if home_conf not in conf_allowlist and away_conf not in conf_allowlist:
                continue

        upcoming.append(g)

    upcoming.sort(key=lambda g: g.get("startDate") or g.get("start_date") or "")
    filter_note = ""
    if team_allowlist or conf_allowlist:
        filter_note = " (after allowlist filtering)"
    log.info(f"Found {len(upcoming)} upcoming games in the next {config.LOOKAHEAD_DAYS} days{filter_note}.")
    return upcoming[: config.MAX_GAMES]


def get_team_roster(team):
    """Roster with position for each player, used to sanity-check that
    stat-category leaders are plausible for that category (see
    ALLOWED_POSITIONS_BY_CATEGORY). Returns {player_id: position}."""
    data = cfbd_get("/roster", {"team": team, "year": config.SEASON_YEAR})
    if not data:
        return {}

    lookup = {}
    for player in data:
        player_id = player.get("id") or player.get("playerId")
        position = player.get("position")
        if player_id is not None and position:
            lookup[player_id] = position
    return lookup


def get_season_stat_leaders(team, category, position_lookup=None):
    """Top players for a team in a stat category this season (season totals).

    position_lookup (player_id -> position), if given, filters out
    candidates whose roster position doesn't fit the category — e.g. a
    lineman's stray fumble-return yardage showing up under rushing.
    A player who isn't found in position_lookup at all is KEPT rather
    than dropped, since the goal is filtering obvious noise, not
    strictly gating on a roster/stats ID match that might not always line up.
    """
    data = cfbd_get(
        "/stats/player/season",
        {"year": config.SEASON_YEAR, "team": team, "category": category, "seasonType": config.SEASON_TYPE},
    )
    if not data:
        return []

    # Historically CFBD returns rows like:
    # {season, playerId, player, team, conference, category, statType, stat}
    # Group by player, keep the statTypes we care about.
    wanted_stat_types = {st for st, _label in config.STAT_TARGETS.get(category, [])}
    by_player = {}
    for row in data:
        stat_type = row.get("statType")
        if stat_type not in wanted_stat_types:
            continue
        player_id = row.get("playerId") or row.get("athleteId")
        if player_id is None:
            continue
        try:
            stat_value = float(row.get("stat", 0))
        except (TypeError, ValueError):
            continue
        entry = by_player.setdefault(
            player_id,
            {"playerId": player_id, "player": row.get("player"), "team": team, "stats": {}},
        )
        entry["stats"][stat_type] = stat_value

    players = list(by_player.values())

    allowed_positions = config.ALLOWED_POSITIONS_BY_CATEGORY.get(category)
    if position_lookup and allowed_positions:
        filtered = []
        for p in players:
            position = position_lookup.get(p["playerId"])
            if position is None or position in allowed_positions:
                filtered.append(p)
            else:
                log.info(f"Filtered {p['player']} ({position}) out of {category} leaders for {team} — unexpected position")
        players = filtered

    # Rank by the primary stat for this category (first one in STAT_TARGETS)
    primary_stat_type = config.STAT_TARGETS.get(category, [(None, None)])[0][0]
    players.sort(key=lambda p: p["stats"].get(primary_stat_type, 0), reverse=True)

    top_n = config.TOP_PLAYERS_PER_CATEGORY.get(category, 1)
    return players[:top_n]


def get_team_game_stats(team):
    """
    All of this team's games this season, with full player box scores
    per category (passing/rushing/receiving/etc). This is the correct,
    long-stable endpoint for per-game player stats — /stats/player/game
    (used in earlier drafts of this script) returns a bare 404 as of
    this API generation; /games/players is what CFBD's own official
    client libraries use for this data.

    One call per TEAM (not per player) — the response includes every
    player's box score for every game, so we fetch it once and pull
    out whichever players we care about.
    """
    data = cfbd_get(
        "/games/players",
        {"year": config.SEASON_YEAR, "team": team, "seasonType": config.SEASON_TYPE},
    )
    if not data:
        return []
    return data


def build_player_game_logs(team_games, team, player_ids):
    """
    Normalizes /games/players' nested shape (game -> team -> category ->
    statType -> athletes) into a flat per-player log:
        {player_id: [{"gameId": ..., "category": ..., "statType": ..., "stat": ...}, ...]}
    so compute_projections.py only ever has to deal with one simple shape,
    regardless of how CFBD nests the raw response.
    """
    logs = {pid: [] for pid in player_ids}
    for game in team_games:
        game_id = game.get("id")
        for team_block in game.get("teams", []):
            if team_block.get("team") != team:
                continue
            for cat in team_block.get("categories", []):
                category = cat.get("name")
                for stat_type_block in cat.get("types", []):
                    stat_type = stat_type_block.get("name")
                    for athlete in stat_type_block.get("athletes", []):
                        pid = athlete.get("id")
                        if pid in logs:
                            logs[pid].append(
                                {
                                    "gameId": game_id,
                                    "category": category,
                                    "statType": stat_type,
                                    "stat": athlete.get("stat"),
                                }
                            )
    return logs


def get_team_recent_defense(team):
    """Per-game advanced stats for this team's games so far this season
    — used to blend recent defensive form into the matchup factor,
    rather than relying only on the season-long average (a defense
    can be trending well above or below its full-season number).
    Sorted oldest-to-newest by week so 'last N games' is well-defined."""
    data = cfbd_get("/stats/game/advanced", {"year": config.SEASON_YEAR, "team": team, "seasonType": config.SEASON_TYPE})
    if not data:
        return []
    data.sort(key=lambda g: g.get("week", 0))
    return data


def get_advanced_defense_for_all_teams():
    """Season advanced stats (offense + defense) for every team — used both
    for the specific opponent's numbers and for computing league averages."""
    log.info("Fetching league-wide advanced season stats...")
    data = cfbd_get("/stats/season/advanced", {"year": config.SEASON_YEAR, "seasonType": config.SEASON_TYPE})
    if not data:
        return {}
    return {row.get("team"): row for row in data if row.get("team")}


def main():
    games = get_upcoming_games()
    if not games:
        log.warning("No upcoming games found — writing an empty raw.json. "
                    "Check CFB_SEASON_YEAR / the lookahead window / that games haven't already started.")

    advanced_by_team = get_advanced_defense_for_all_teams()

    raw = {
        "fetchedAt": datetime.now(timezone.utc).isoformat(),
        "season": config.SEASON_YEAR,
        "seasonType": config.SEASON_TYPE,
        "advancedStatsByTeam": advanced_by_team,
        "recentDefenseByTeam": {},
        "games": [],
    }

    # Cached per (team, category) and per team — a team can appear in more
    # than one upcoming game in the lookahead window, and previously this
    # was handled by skipping (and silently dropping) the second
    # occurrence entirely. Caching the underlying fetches instead means
    # every game entry gets a full, correct report.
    leaders_cache = {}
    team_games_cache = {}
    roster_cache = {}
    recent_defense_cache = {}

    for game in games:
        home = game.get("homeTeam") or game.get("home_team")
        away = game.get("awayTeam") or game.get("away_team")
        if not home or not away:
            continue

        game_entry = {
            "gameId": game.get("id"),
            "startDate": game.get("startDate") or game.get("start_date"),
            "homeTeam": home,
            "awayTeam": away,
            "teams": [],
        }

        for team, opponent in [(home, away), (away, home)]:
            log.info(f"Processing {team} (vs {opponent})...")
            team_players = []

            if team not in roster_cache:
                roster_cache[team] = get_team_roster(team)

            # Opponent's recent defensive form (not team's) — this is what
            # team's players are facing, so it's what the matchup factor needs.
            if opponent not in recent_defense_cache:
                recent_defense_cache[opponent] = get_team_recent_defense(opponent)
                raw["recentDefenseByTeam"][opponent] = recent_defense_cache[opponent]

            all_leaders = []  # [(category, playerDict), ...]
            for category in config.STAT_TARGETS:
                cache_key = (team, category)
                if cache_key not in leaders_cache:
                    leaders_cache[cache_key] = get_season_stat_leaders(team, category, roster_cache[team])
                for player in leaders_cache[cache_key]:
                    all_leaders.append((category, player))

            if all_leaders:
                if team not in team_games_cache:
                    team_games_cache[team] = get_team_game_stats(team)
                player_ids = [player["playerId"] for _category, player in all_leaders]
                logs_by_player = build_player_game_logs(team_games_cache[team], team, player_ids)

                for category, player in all_leaders:
                    team_players.append(
                        {
                            "playerId": player["playerId"],
                            "name": player["player"],
                            "category": category,
                            "seasonStats": player["stats"],
                            "gameLog": logs_by_player.get(player["playerId"], []),
                        }
                    )

            game_entry["teams"].append(
                {"team": team, "opponent": opponent, "homeAway": "home" if team == home else "away", "players": team_players}
            )

        raw["games"].append(game_entry)

    os.makedirs(os.path.dirname(config.RAW_DATA_PATH), exist_ok=True)
    with open(config.RAW_DATA_PATH, "w") as f:
        json.dump(raw, f, indent=2)
    log.info(f"Wrote raw data for {len(raw['games'])} games to {config.RAW_DATA_PATH}")


if __name__ == "__main__":
    main()