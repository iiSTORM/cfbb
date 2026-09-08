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
import sys
from datetime import datetime, timedelta, timezone

import requests

import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("fetch_data")


def cfbd_get(path, params=None):
    """GET a CFBD endpoint. Returns parsed JSON, or None on failure."""
    if not config.CFBD_API_KEY:
        log.error("CFBD_API_KEY is not set. Get a free key at https://collegefootballdata.com/key")
        sys.exit(1)

    url = f"{config.CFBD_BASE_URL}{path}"
    headers = {"Authorization": f"Bearer {config.CFBD_API_KEY}", "Accept": "application/json"}

    try:
        resp = requests.get(url, headers=headers, params=params or {}, timeout=config.REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.HTTPError as e:
        log.error(f"HTTP error on {path} {params}: {e} — response body: {resp.text[:500]}")
        return None
    except requests.exceptions.RequestException as e:
        log.error(f"Request failed on {path} {params}: {e}")
        return None
    except ValueError as e:
        log.error(f"Failed to parse JSON from {path} {params}: {e} — raw text: {resp.text[:500]}")
        return None


def get_upcoming_games():
    """All season games, filtered to the lookahead window and not yet completed."""
    log.info(f"Fetching {config.SEASON_YEAR} schedule...")
    games = cfbd_get("/games", {"year": config.SEASON_YEAR, "seasonType": config.SEASON_TYPE})
    if not games:
        log.warning("No games returned — check SEASON_YEAR/SEASON_TYPE, or that the season has started.")
        return []

    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=config.LOOKAHEAD_DAYS)
    upcoming = []

    for g in games:
        completed = g.get("completed", False)
        start_date = g.get("startDate") or g.get("start_date")
        if completed or not start_date:
            continue
        try:
            start_dt = datetime.fromisoformat(start_date.replace("Z", "+00:00"))
        except ValueError:
            continue
        if now <= start_dt <= cutoff:
            upcoming.append(g)

    upcoming.sort(key=lambda g: g.get("startDate") or g.get("start_date") or "")
    log.info(f"Found {len(upcoming)} upcoming games in the next {config.LOOKAHEAD_DAYS} days.")
    return upcoming[: config.MAX_GAMES]


def get_season_stat_leaders(team, category):
    """Top players for a team in a stat category this season (season totals)."""
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

    # Rank by the primary stat for this category (first one in STAT_TARGETS)
    primary_stat_type = config.STAT_TARGETS.get(category, [(None, None)])[0][0]
    players.sort(key=lambda p: p["stats"].get(primary_stat_type, 0), reverse=True)

    top_n = config.TOP_PLAYERS_PER_CATEGORY.get(category, 1)
    return players[:top_n]


def get_player_game_log(player_id):
    """Recent per-game stat lines for a player this season."""
    data = cfbd_get(
        "/stats/player/game",
        {"year": config.SEASON_YEAR, "playerId": player_id, "seasonType": config.SEASON_TYPE},
    )
    if not data:
        return []
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
        "games": [],
    }

    teams_processed = set()
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

            for category in config.STAT_TARGETS:
                if (team, category) in teams_processed:
                    continue
                teams_processed.add((team, category))

                leaders = get_season_stat_leaders(team, category)
                for player in leaders:
                    game_log = get_player_game_log(player["playerId"])
                    team_players.append(
                        {
                            "playerId": player["playerId"],
                            "name": player["player"],
                            "category": category,
                            "seasonStats": player["stats"],
                            "gameLog": game_log,
                        }
                    )

            game_entry["teams"].append({"team": team, "opponent": opponent, "players": team_players})

        raw["games"].append(game_entry)

    with open(config.RAW_DATA_PATH, "w") as f:
        json.dump(raw, f, indent=2)
    log.info(f"Wrote raw data for {len(raw['games'])} games to {config.RAW_DATA_PATH}")


if __name__ == "__main__":
    main()
