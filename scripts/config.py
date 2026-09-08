"""
Shared configuration for the CFB player prop analyzer.

IMPORTANT: CollegeFootballData.com's docs (as of Sept 2026) note they're
mid-transition to a new API reference, with the old Swagger docs marked
"legacy." The endpoint paths and field names below reflect CFBD's
long-stable schema, but you should sanity-check a real response against
https://api.collegefootballdata.com's current reference after your first
run (the fetch step logs raw responses on parse failure specifically so
you can debug this).
"""

import os


def _int_env(name, default):
    """int(os.environ[name]) with a safe fallback — handles both an
    unset var and one set to an empty string (which GitHub Actions
    does for an undefined repo Variable passed through as env)."""
    val = os.environ.get(name, "").strip()
    return int(val) if val else default


CFBD_BASE_URL = "https://api.collegefootballdata.com"
CFBD_API_KEY = os.environ.get("CFBD_API_KEY", "")

# Season/week detection: override via env vars if auto-detection picks
# the wrong week (e.g. during bye weeks or conference championship gaps).
SEASON_YEAR = _int_env("CFB_SEASON_YEAR", 2026)
SEASON_TYPE = os.environ.get("CFB_SEASON_TYPE", "regular")  # "regular" or "postseason"

# How many upcoming games (within the lookahead window) to build
# matchup reports for, sorted by soonest kickoff — NOT by any notion
# of "importance." Each game costs roughly a dozen API calls (roster +
# season stats + game log per team, recent defense for the opponent),
# so this is the single biggest lever on API quota. See the "Free tier
# quota" section in README — the realistic budget for a twice-daily
# schedule on the free 500-credit/month tier is much lower than it
# might seem from this number alone. Tunable without a code change via
# the repo Variable CFB_MAX_GAMES (Settings → Secrets and variables →
# Actions → Variables).
MAX_GAMES = _int_env("CFB_MAX_GAMES", 10)
LOOKAHEAD_DAYS = _int_env("CFB_LOOKAHEAD_DAYS", 7)

# How many top players per stat category, per team, to build reports
# for (ranked by season total — e.g. top 1 passer, top 2 rushers).
TOP_PLAYERS_PER_CATEGORY = {
    "passing": 1,
    "rushing": 2,
    "receiving": 3,
}

# category -> (statType key from CFBD, human label, position filter hint)
STAT_TARGETS = {
    "passing": [("YDS", "Passing Yards"), ("TD", "Passing TDs")],
    "rushing": [("YDS", "Rushing Yards")],
    "receiving": [("YDS", "Receiving Yards"), ("REC", "Receptions")],
}

# Which positions are plausible for a "leader" in each category. Season
# stat leaders occasionally surface a player at an unexpected position
# (e.g. a lineman with fumble-recovery return yards misfiled under
# rushing, or a punter's stray return yardage). Roster position is
# cross-checked against this list — but a player who isn't found on
# the roster at all is kept rather than dropped, since the goal is
# filtering out clear noise, not being a strict gate that breaks on
# any roster/stats ID mismatch.
ALLOWED_POSITIONS_BY_CATEGORY = {
    "passing": {"QB"},
    "rushing": {"RB", "QB", "FB", "WR"},
    "receiving": {"WR", "TE", "RB", "FB"},
}

# Which advanced-defense field each offensive category is matched
# against when computing the matchup factor. All are successRate
# fields (bounded 0-1), which is more stable for ratio math than raw
# PPA (which can be negative). This is a transparent heuristic, not a
# claim of predictive precision — see README.
MATCHUP_FACTOR_FIELD = {
    "passing": ("defense", "passingPlays", "successRate"),
    "rushing": ("defense", "rushingPlays", "successRate"),
    "receiving": ("defense", "passingPlays", "successRate"),  # receiving yards ride on the pass defense
}

# Recent-form window for the game log (most recent N games)
RECENT_GAMES_WINDOW = 5

# Blend weight for recent form vs season average in the projection
RECENT_WEIGHT = 0.6
SEASON_WEIGHT = 0.4

# Matchup factor is clamped to avoid wild multipliers from small samples
MATCHUP_FACTOR_MIN = 0.75
MATCHUP_FACTOR_MAX = 1.35

# Recent defensive form: how many of the opponent's most recent games
# to average for the "recent" half of the matchup factor blend, and
# how much weight recent form gets vs. the season-long number. A
# defense can be trending well above or below its full-season average
# — 3 games is a small sample, so this leans toward the season number
# by default rather than overreacting to a couple of games.
DEF_RECENT_GAMES_WINDOW = 3
DEF_RECENT_WEIGHT = 0.4
DEF_SEASON_WEIGHT = 0.6

# Optional focus filters — leave both empty to track every team/game
# in range (default behavior). Comma-separated env var overrides, e.g.
# CFB_TEAM_ALLOWLIST="Ohio State,Michigan,Georgia". Team names must
# match CFBD's team naming exactly (as they appear in raw.json).
TEAM_ALLOWLIST = [t.strip() for t in os.environ.get("CFB_TEAM_ALLOWLIST", "").split(",") if t.strip()]
CONFERENCE_ALLOWLIST = [c.strip() for c in os.environ.get("CFB_CONFERENCE_ALLOWLIST", "").split(",") if c.strip()]

RAW_DATA_PATH = "docs/data/raw.json"
PROJECTIONS_PATH = "docs/data/projections.json"

REQUEST_TIMEOUT = 20