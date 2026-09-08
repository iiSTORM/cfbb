"""
Appends/updates this run's projections into docs/data/history.json —
a running record used to later grade projections against what
actually happened (see settle_projections.py) and track how
well-calibrated the model is over time.

Records are upserted by (gameId, playerId, category, statType): while
a game is still upcoming, each run's projection overwrites the
previous one, since the projection improves as kickoff approaches
with fresher recent-game data. Once a record is settled (see
settle_projections.py), it's left untouched here — CFBD stops
returning a completed game from /games' "upcoming" filter, so no new
projection run will ever try to overwrite a settled record anyway.
"""

import json
import logging
import os

import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("record_projections")

HISTORY_PATH = "docs/data/history.json"


def load_history():
    if not os.path.exists(HISTORY_PATH):
        return {"records": []}
    with open(HISTORY_PATH) as f:
        return json.load(f)


def record_key(gameId, playerId, category, statType):
    return (gameId, playerId, category, statType)


def main():
    try:
        with open(config.PROJECTIONS_PATH) as f:
            projections = json.load(f)
    except FileNotFoundError:
        log.error(f"{config.PROJECTIONS_PATH} not found — run compute_projections.py first.")
        return

    history = load_history()
    by_key = {
        record_key(r["gameId"], r["playerId"], r["category"], r["statType"]): r for r in history["records"]
    }

    upserted = 0
    for game in projections.get("games", []):
        for team_entry in game.get("teams", []):
            for p in team_entry.get("players", []):
                key = record_key(game.get("gameId"), p["playerId"], p["category"], p["statType"])
                existing = by_key.get(key)
                if existing is not None and existing.get("actual") is not None:
                    continue  # already settled — never touch again

                by_key[key] = {
                    "gameId": game.get("gameId"),
                    "gameStartDate": game.get("startDate"),
                    "playerId": p["playerId"],
                    "playerName": p["name"],
                    "team": team_entry["team"],
                    "opponent": team_entry["opponent"],
                    "category": p["category"],
                    "statType": p["statType"],
                    "label": p["label"],
                    "projectedAt": projections.get("generatedAt"),
                    "projected": p["projected"],
                    "seasonAvg": p.get("seasonAvg"),
                    "recentAvg": p.get("recentAvg"),
                    "opponentValue": p.get("opponentValue"),
                    "opponentRecentValue": p.get("opponentRecentValue"),
                    "leagueAvgValue": p.get("leagueAvgValue"),
                    "matchupFactor": p.get("matchupFactor"),
                    "confidence": p.get("confidence"),
                    "actual": None,
                    "settledAt": None,
                    "error": None,
                    "absError": None,
                    "assumedZero": False,
                }
                upserted += 1

    history["records"] = list(by_key.values())
    os.makedirs(os.path.dirname(HISTORY_PATH), exist_ok=True)
    with open(HISTORY_PATH, "w") as f:
        json.dump(history, f, indent=2)
    log.info(f"Upserted {upserted} projection record(s). History now has {len(history['records'])} total.")


if __name__ == "__main__":
    main()