"""
Grades previously-recorded projections against final box scores, once
their games have actually finished. Fills in `actual`, `error`,
`absError`, `settledAt` on matching docs/data/history.json records,
then writes docs/data/accuracy_summary.json — the aggregate
calibration stats the dashboard's Track Record section reads.

Runs every scheduled cycle, not just once — most of the time there's
nothing new to settle and it's a fast no-op. Reuses fetch_data.py's
existing team-game-stats fetch and parsing rather than duplicating it.

A KNOWN GAP: if a player's game log doesn't include a row for a stat
in a game where they recorded a zero (e.g. no touchdown passes that
game), that game's actual is invisible via the normal lookup — this
is the same gap noted in the README for the live projection model
itself. After GIVE_UP_AFTER_DAYS with no matching row found, this
script assumes the actual was 0 and settles the record with
assumedZero=True so it doesn't hang around as "pending" forever — but
that assumption is exactly as uncertain as the gap it's working
around. The accuracy summary reports assumed-zero records separately
from the main calibration numbers rather than silently blending them
in, specifically so an assumption doesn't quietly distort the
headline accuracy figures.
"""

import json
import logging
import os
import statistics
from datetime import datetime, timedelta, timezone

import config
import fetch_data  # reuses cfbd_get / get_team_game_stats / build_player_game_logs

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("settle_projections")

HISTORY_PATH = "docs/data/history.json"
SUMMARY_PATH = "docs/data/accuracy_summary.json"

# Wait this long past kickoff before trying to settle at all — games
# run ~3.5 hours and stats can take a bit longer to post.
SETTLE_BUFFER_HOURS = 6

# If still no matching stat row after this many days, assume a zero
# rather than leaving the record "pending" indefinitely (see module docstring).
GIVE_UP_AFTER_DAYS = 5


def load_history():
    if not os.path.exists(HISTORY_PATH):
        return {"records": []}
    with open(HISTORY_PATH) as f:
        return json.load(f)


def _game_start(record):
    start = record.get("gameStartDate")
    if not start:
        return None
    try:
        return datetime.fromisoformat(start.replace("Z", "+00:00"))
    except ValueError:
        return None


def is_overdue(record, now):
    start_dt = _game_start(record)
    return start_dt is not None and now >= start_dt + timedelta(hours=SETTLE_BUFFER_HOURS)


def should_give_up(record, now):
    start_dt = _game_start(record)
    return start_dt is not None and now >= start_dt + timedelta(days=GIVE_UP_AFTER_DAYS)


def main():
    history = load_history()
    now = datetime.now(timezone.utc)

    pending = [r for r in history["records"] if r.get("actual") is None and is_overdue(r, now)]
    if not pending:
        log.info("Nothing overdue to settle this run.")
        write_summary(history)
        return

    log.info(f"{len(pending)} record(s) are overdue for settlement.")

    teams_needed = {r["team"] for r in pending}
    team_games_cache = {team: fetch_data.get_team_game_stats(team) for team in teams_needed}

    settled_count = 0
    assumed_zero_count = 0
    for record in pending:
        team_games = team_games_cache.get(record["team"], [])
        logs_by_player = fetch_data.build_player_game_logs(team_games, record["team"], [record["playerId"]])
        game_log = logs_by_player.get(record["playerId"], [])

        actual = None
        for row in game_log:
            if (
                row["gameId"] == record["gameId"]
                and row["category"] == record["category"]
                and row["statType"] == record["statType"]
            ):
                try:
                    actual = float(row["stat"])
                except (TypeError, ValueError):
                    actual = None
                break

        assumed_zero = False
        if actual is None:
            if not should_give_up(record, now):
                continue  # not posted yet — try again next run
            actual = 0.0
            assumed_zero = True
            assumed_zero_count += 1
            log.warning(
                f"No stat row found for {record['playerName']} ({record['label']}) "
                f"after {GIVE_UP_AFTER_DAYS} days — assuming 0 (see module docstring)."
            )

        record["actual"] = actual
        record["error"] = round(actual - record["projected"], 2)
        record["absError"] = round(abs(actual - record["projected"]), 2)
        record["settledAt"] = now.isoformat()
        record["assumedZero"] = assumed_zero
        settled_count += 1

    with open(HISTORY_PATH, "w") as f:
        json.dump(history, f, indent=2)
    log.info(f"Settled {settled_count} of {len(pending)} overdue record(s) ({assumed_zero_count} assumed-zero).")

    write_summary(history)


def write_summary(history):
    all_settled = [r for r in history["records"] if r.get("actual") is not None]
    confident = [r for r in all_settled if not r.get("assumedZero")]  # excludes assumed-zero from headline stats

    summary = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "settledCount": len(all_settled),
        "assumedZeroCount": len(all_settled) - len(confident),
        "pendingCount": len([r for r in history["records"] if r.get("actual") is None]),
    }

    if confident:
        errors = [r["error"] for r in confident]
        abs_errors = [r["absError"] for r in confident]
        summary["meanError"] = round(statistics.mean(errors), 2)  # bias: positive = model under-projects on average
        summary["meanAbsError"] = round(statistics.mean(abs_errors), 2)
        summary["medianAbsError"] = round(statistics.median(abs_errors), 2)

        by_confidence = {}
        for level in ["High", "Medium", "Low"]:
            level_records = [r for r in confident if r.get("confidence") == level]
            if level_records:
                by_confidence[level] = {
                    "count": len(level_records),
                    "meanAbsError": round(statistics.mean(r["absError"] for r in level_records), 2),
                }
        summary["byConfidence"] = by_confidence

        by_category = {}
        for category in sorted({r["category"] for r in confident}):
            cat_records = [r for r in confident if r["category"] == category]
            by_category[category] = {
                "count": len(cat_records),
                "meanAbsError": round(statistics.mean(r["absError"] for r in cat_records), 2),
            }
        summary["byCategory"] = by_category

    os.makedirs(os.path.dirname(SUMMARY_PATH), exist_ok=True)
    with open(SUMMARY_PATH, "w") as f:
        json.dump(summary, f, indent=2)
    log.info(f"Wrote accuracy summary: {summary['settledCount']} settled ({summary['assumedZeroCount']} assumed-zero), {summary['pendingCount']} pending.")


if __name__ == "__main__":
    main()