"""
Reads docs/data/raw.json (from fetch_data.py) and computes a projection
for each player/stat, blending season average with recent-game form and
adjusting for the opponent's defensive matchup. Writes docs/data/projections.json
for the dashboard to render.

THE MODEL (read this before trusting the output):

  recent_avg   = average of the player's last N games (config.RECENT_GAMES_WINDOW)
  season_avg   = season total / games played
  blended_base = RECENT_WEIGHT * recent_avg + SEASON_WEIGHT * season_avg
  matchup_factor = clamp(opponent_success_rate_allowed / league_avg_success_rate_allowed,
                          MATCHUP_FACTOR_MIN, MATCHUP_FACTOR_MAX)
  projected    = blended_base * matchup_factor

This is a transparent heuristic, not a fitted statistical model — there's
no backtesting behind the specific weights, and "success rate allowed"
is a proxy for matchup difficulty, not a direct predictor of yardage.
Treat the output as a structured starting point for your own judgment,
not a forecast to bet on directly.

Confidence is based on how consistent the player's recent games are
(coefficient of variation) and how many games of data are available —
it says nothing about whether the model itself is accurate.
"""

import json
import logging
import os
import statistics

import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("compute_projections")


def league_average(advanced_by_team, field_path):
    """Average of a nested field (e.g. defense.passingPlays.successRate) across all teams."""
    section, subfield, metric = field_path
    values = []
    for team_data in advanced_by_team.values():
        try:
            v = team_data[section][subfield][metric]
            if v is not None:
                values.append(float(v))
        except (KeyError, TypeError, ValueError):
            continue
    if not values:
        return None
    return sum(values) / len(values)


def opponent_value(advanced_by_team, opponent, field_path):
    section, subfield, metric = field_path
    team_data = advanced_by_team.get(opponent)
    if not team_data:
        return None
    try:
        v = team_data[section][subfield][metric]
        return float(v) if v is not None else None
    except (KeyError, TypeError, ValueError):
        return None


def extract_game_values(game_log, category, stat_type):
    """Pull the numeric values for one stat type out of a player's game log,
    most recent last. CFBD's /stats/player/game shape has shifted between
    a flat statType/stat row format and a nested category dict in the past
    — this handles both defensively."""
    values = []
    for game in game_log:
        value = None
        # Nested shape: game["categories"] -> [{name, types: [{name, stat}]}]
        if "categories" in game:
            for cat in game.get("categories", []):
                if cat.get("name") != category:
                    continue
                for t in cat.get("types", []):
                    if t.get("name") == stat_type:
                        value = t.get("stat")
        # Flat shape: game itself has category/statType/stat
        elif game.get("category") == category and game.get("statType") == stat_type:
            value = game.get("stat")

        if value is not None:
            try:
                values.append(float(value))
            except (TypeError, ValueError):
                pass
    return values


def count_games_played(game_log):
    """Distinct games in the log, by game/week identifier — NOT by counting
    rows for one stat type. A player's game log has one row per (game, stat
    type) combination, so counting rows for e.g. "TD" alone undercounts
    games played whenever a game produced no touchdown row, which silently
    inflates that stat's season average. This scans the whole log instead."""
    game_ids = set()
    for g in game_log:
        gid = g.get("gameId") or g.get("game_id") or g.get("week")
        if gid is not None:
            game_ids.add(gid)
    return len(game_ids) or None


def confidence_label(recent_games):
    n = len(recent_games)
    if n < 2:
        return "Low"  # not enough games to judge consistency
    mean = statistics.mean(recent_games)
    if mean == 0:
        return "Low"
    cv = statistics.pstdev(recent_games) / mean
    if cv < 0.2 and n >= 3:
        return "High"
    if cv < 0.4:
        return "Medium"
    return "Low"


def build_projection(player, category, stat_type, label, opponent, advanced_by_team):
    game_values_all = extract_game_values(player["gameLog"], category, stat_type)
    recent_games = game_values_all[-config.RECENT_GAMES_WINDOW:]

    season_total = player["seasonStats"].get(stat_type)
    games_played = count_games_played(player["gameLog"]) or len(game_values_all) or 1
    season_avg = (season_total / games_played) if season_total is not None else None

    if not recent_games and season_avg is None:
        return None  # nothing usable for this player/stat

    recent_avg = statistics.mean(recent_games) if recent_games else season_avg
    base_season_avg = season_avg if season_avg is not None else recent_avg

    blended_base = config.RECENT_WEIGHT * recent_avg + config.SEASON_WEIGHT * base_season_avg

    field_path = config.MATCHUP_FACTOR_FIELD[category]
    opp_val = opponent_value(advanced_by_team, opponent, field_path)
    league_avg = league_average(advanced_by_team, field_path)

    if opp_val is not None and league_avg not in (None, 0):
        raw_factor = opp_val / league_avg
        matchup_factor = max(config.MATCHUP_FACTOR_MIN, min(config.MATCHUP_FACTOR_MAX, raw_factor))
    else:
        matchup_factor = 1.0  # no defensive data available — no adjustment, not a guess

    projected = blended_base * matchup_factor

    return {
        "playerId": player["playerId"],
        "name": player["name"],
        "category": category,
        "statType": stat_type,
        "label": label,
        "seasonAvg": round(base_season_avg, 1) if base_season_avg is not None else None,
        "recentGames": [round(v, 1) for v in recent_games],
        "recentAvg": round(recent_avg, 1) if recent_avg is not None else None,
        "opponentMatchupMetric": ".".join(field_path),
        "opponentValue": round(opp_val, 3) if opp_val is not None else None,
        "leagueAvgValue": round(league_avg, 3) if league_avg is not None else None,
        "matchupFactor": round(matchup_factor, 3),
        "projected": round(projected, 1),
        "confidence": confidence_label(recent_games),
    }


def main():
    try:
        with open(config.RAW_DATA_PATH) as f:
            raw = json.load(f)
    except FileNotFoundError:
        log.error(f"{config.RAW_DATA_PATH} not found — run fetch_data.py first.")
        return

    advanced_by_team = raw.get("advancedStatsByTeam", {})
    output_games = []

    for game in raw.get("games", []):
        output_teams = []
        for team_entry in game.get("teams", []):
            opponent = team_entry["opponent"]
            output_players = []

            for player in team_entry.get("players", []):
                category = player["category"]
                for stat_type, label in config.STAT_TARGETS.get(category, []):
                    if stat_type not in player.get("seasonStats", {}) and not player.get("gameLog"):
                        continue
                    projection = build_projection(player, category, stat_type, label, opponent, advanced_by_team)
                    if projection:
                        output_players.append(projection)

            output_teams.append({"team": team_entry["team"], "opponent": opponent, "players": output_players})

        output_games.append(
            {
                "gameId": game.get("gameId"),
                "startDate": game.get("startDate"),
                "homeTeam": game.get("homeTeam"),
                "awayTeam": game.get("awayTeam"),
                "teams": output_teams,
            }
        )

    output = {
        "generatedAt": raw.get("fetchedAt"),
        "season": raw.get("season"),
        "seasonType": raw.get("seasonType"),
        "games": output_games,
    }

    os.makedirs(os.path.dirname(config.PROJECTIONS_PATH), exist_ok=True)
    with open(config.PROJECTIONS_PATH, "w") as f:
        json.dump(output, f, indent=2)

    total_projections = sum(len(t["players"]) for g in output_games for t in g["teams"])
    log.info(f"Wrote {total_projections} projections across {len(output_games)} games to {config.PROJECTIONS_PATH}")


if __name__ == "__main__":
    main()