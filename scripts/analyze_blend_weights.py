"""
DIAGNOSTIC TOOL — run manually, not part of the scheduled pipeline.

Grid-searches alternate RECENT_WEIGHT/DEF_RECENT_WEIGHT combinations
against docs/data/history.json's settled (graded) records, and reports
which combination would have minimized mean absolute error historically.

This does NOT modify scripts/config.py. It prints a report; you decide
whether to update the weights yourself. Two reasons for that:

  1. A small sample can look like a clear signal and just be noise.
     This script refuses to recommend anything below MIN_SAMPLE_SIZE
     settled records, and even above that threshold, a "best" weight
     found by grid search on however many games have been graded so
     far is a hint worth treating skeptically, not a conclusion.
  2. Blend weights are a modeling choice with a rationale (see
     compute_projections.py's docstring) — changing them should be a
     deliberate decision after looking at the report, not something
     a script does silently on your behalf.

Run with:
    python scripts/analyze_blend_weights.py
"""

import json
import logging

import config

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("analyze_blend_weights")

HISTORY_PATH = "docs/data/history.json"

# Below this many settled (non-assumed-zero) records, refuse to
# recommend anything — a grid-search "best" weight from a handful of
# games is much more likely to be overfitting noise than signal.
MIN_SAMPLE_SIZE = 50

WEIGHT_GRID = [round(w, 1) for w in [i / 10 for i in range(0, 11)]]  # 0.0, 0.1, ..., 1.0


def load_confident_records():
    try:
        with open(HISTORY_PATH) as f:
            history = json.load(f)
    except FileNotFoundError:
        return []
    return [
        r
        for r in history.get("records", [])
        if r.get("actual") is not None and not r.get("assumedZero")
    ]


def reproject(record, recent_weight, def_recent_weight):
    """Recomputes what `projected` WOULD have been under a candidate
    pair of blend weights, using the components stored on the record
    at settle time. Returns None if a record is missing a component
    it needs (e.g. no recentAvg because the player had no game log
    yet at projection time) — such records are skipped for that
    weight combination rather than crashing the whole backtest."""
    season_avg = record.get("seasonAvg")
    recent_avg = record.get("recentAvg")
    if season_avg is None or recent_avg is None:
        return None
    blended_base = recent_weight * recent_avg + (1 - recent_weight) * season_avg

    season_def = record.get("opponentValue")
    recent_def = record.get("opponentRecentValue")
    league_avg = record.get("leagueAvgValue")

    if recent_def is not None and season_def is not None:
        blended_def = def_recent_weight * recent_def + (1 - def_recent_weight) * season_def
    elif season_def is not None:
        blended_def = season_def
    else:
        blended_def = None

    if blended_def is not None and league_avg not in (None, 0):
        raw_factor = blended_def / league_avg
        matchup_factor = max(config.MATCHUP_FACTOR_MIN, min(config.MATCHUP_FACTOR_MAX, raw_factor))
    else:
        matchup_factor = 1.0

    return blended_base * matchup_factor


def mean_abs_error(records, recent_weight, def_recent_weight):
    errors = []
    for r in records:
        reprojected = reproject(r, recent_weight, def_recent_weight)
        if reprojected is None:
            continue
        errors.append(abs(reprojected - r["actual"]))
    if not errors:
        return None, 0
    return sum(errors) / len(errors), len(errors)


def main():
    records = load_confident_records()
    log.info(f"{len(records)} settled, non-assumed-zero record(s) available for backtesting.")

    if len(records) < MIN_SAMPLE_SIZE:
        log.info(
            f"\nNeed at least {MIN_SAMPLE_SIZE} settled records before this tool will "
            f"recommend anything (currently {len(records)}). Let more of the season play "
            f"out and run this again — a 'best' weight from a small sample is much more "
            f"likely to be noise than a real pattern."
        )
        return

    # Baseline: current configured weights
    baseline_mae, baseline_n = mean_abs_error(records, config.RECENT_WEIGHT, config.DEF_RECENT_WEIGHT)
    log.info(
        f"\nCurrent config (RECENT_WEIGHT={config.RECENT_WEIGHT}, "
        f"DEF_RECENT_WEIGHT={config.DEF_RECENT_WEIGHT}): MAE={baseline_mae:.2f} (n={baseline_n})"
    )

    results = []
    for rw in WEIGHT_GRID:
        for dw in WEIGHT_GRID:
            mae, n = mean_abs_error(records, rw, dw)
            if mae is not None:
                results.append((rw, dw, mae, n))

    results.sort(key=lambda x: x[2])
    best_rw, best_dw, best_mae, best_n = results[0]

    log.info(f"\nBest found in grid search: RECENT_WEIGHT={best_rw}, DEF_RECENT_WEIGHT={best_dw} → MAE={best_mae:.2f} (n={best_n})")
    improvement = baseline_mae - best_mae
    log.info(f"Improvement over current config: {improvement:.2f} ({improvement / baseline_mae * 100:.1f}% lower MAE)")

    log.info("\nTop 10 combinations:")
    log.info(f"{'RECENT_WEIGHT':>14} {'DEF_RECENT_WEIGHT':>18} {'MAE':>8} {'n':>6}")
    for rw, dw, mae, n in results[:10]:
        marker = "  <- current" if (rw, dw) == (config.RECENT_WEIGHT, config.DEF_RECENT_WEIGHT) else ""
        log.info(f"{rw:>14} {dw:>18} {mae:>8.2f} {n:>6}{marker}")

    log.info(
        "\nThis is a suggestion, not an instruction. If you want to act on it, update "
        "RECENT_WEIGHT and DEF_RECENT_WEIGHT in scripts/config.py yourself — nothing here "
        "does that automatically. Re-run this periodically as more games get graded; the "
        "recommendation can and should shift as the sample grows."
    )


if __name__ == "__main__":
    main()