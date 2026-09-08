# College Football Player Prop Matchup Analyzer

An automated pipeline, same shape as your esports project: a scheduled
scraper pulls player and defensive stats twice a day, a projection
engine turns them into matchup-adjusted stat projections, and a static
dashboard (deployed via GitHub Pages) shows the results — no server to
run yourself.

**What it predicts:** for each notable player in an upcoming game, an
estimated stat line (passing/rushing/receiving yards, TDs, receptions)
blending their recent form with their season average, adjusted by how
the opponent's defense has performed against that type of play. It's a
transparent heuristic, not a fitted statistical model — see "How the
projection works" below before trusting it.

## Setup

### 1. Get a free CollegeFootballData.com API key

Sign up at https://collegefootballdata.com/key. The free tier covers
this use case (scheduled stat pulls, not live in-game data).

**Important — this already bit us once:** CFBD's docs (as of Sept
2026) note they're mid-transition to a new API reference, with the old
Swagger docs marked "legacy." That transition turned out to be real:
an earlier version of this project used `/stats/player/game?playerId=`
for per-game stats, and that endpoint now returns a bare 404. It's
been replaced with `/games/players?team=` (fetched once per team
instead of once per player — also cuts API usage substantially). If
another endpoint breaks the same way, the Action's log will show the
exact failing path and a `404`/`Cannot GET` response — that's the
signal to check https://api.collegefootballdata.com's current
reference for a renamed path. `scripts/config.py` and
`scripts/fetch_data.py` are commented at every point where this
matters most.

### 2. Create the GitHub repo

Push this project to a new GitHub repository.

### 3. Add your API key as a repo secret

Repo → **Settings** → **Secrets and variables** → **Actions** → **New
repository secret**:
- Name: `CFBD_API_KEY`
- Value: your key from step 1

### 4. Enable GitHub Pages

Repo → **Settings** → **Pages** → **Source**: Deploy from a branch →
**Branch**: `main`, folder **`/docs`** → **Save**.

Your dashboard will be live at `https://<your-username>.github.io/<repo-name>/`
within a minute or two of the next successful run.

### 5. Trigger the first run

Repo → **Actions** tab → **Update CFB Prop Projections** → **Run
workflow**. This does the same thing as the twice-daily schedule, just
on demand — use it to confirm everything works before waiting for the
next scheduled run.

## How the projection works

```
recent_avg     = average of the player's last 5 games
season_avg     = season total ÷ games played
blended_base   = 0.6 × recent_avg + 0.4 × season_avg
matchup_factor = clamp(opponent_success_rate_allowed ÷ league_avg_success_rate_allowed, 0.75, 1.35)
projected      = blended_base × matchup_factor
```

- **Matchup factor** uses the opponent's defensive "success rate
  allowed" on that play type (passing or rushing) from CFBD's advanced
  season stats, relative to the national average. A defense that
  allows successful plays more often than average nudges the
  projection up; a stingier-than-average defense nudges it down.
  Receiving yards are matched against pass defense, since receiving
  production rides on the passing game.
- **Confidence** (High/Medium/Low) reflects how consistent the
  player's last 5 games have been (coefficient of variation), not how
  accurate the model is. A "High confidence" projection just means the
  player's recent production has been steady — it's not a claim about
  the projection's precision.
- **This is a heuristic, not a backtested model.** The 0.6/0.4 blend
  weight and the 0.75–1.35 clamp are reasonable starting points, not
  numbers tuned against historical outcomes. Treat the output as a
  structured starting point for your own judgment — a fast way to see
  "is this player trending up, and is the matchup favorable" — not a
  number to bet directly against a sportsbook line.
- **A known gap:** if a player's game log doesn't include a row for a
  stat in a game where they recorded a zero (e.g. no touchdown passes
  that game), that game is invisible to the average rather than
  counted as a zero — which would bias the average upward. Whether
  CFBD's game log omits zero-stat rows or includes them isn't
  something this project could confirm without hitting the live API,
  so it's worth spot-checking a player you know well against CFBD's
  website directly.

## Project structure

```
.github/workflows/update.yml   — scheduled + manual trigger, runs the pipeline, commits results
scripts/
  config.py                    — all the tunable constants (weights, clamps, lookahead window, stat targets)
  fetch_data.py                — pulls games, player stats, and defensive stats from CFBD → docs/data/raw.json
  compute_projections.py       — turns raw.json into docs/data/projections.json
docs/                          — GitHub Pages source
  index.html, styles.css, app.js
  data/
    raw.json                   — intermediate, committed so you can inspect what was fetched
    projections.json           — what the dashboard reads
requirements.txt
```

## Customizing

Everything tunable lives in `scripts/config.py`:

- **`SEASON_YEAR` / `SEASON_TYPE`** — override if auto-detection ever
  picks up the wrong season.
- **`MAX_GAMES`** — how many upcoming games to build reports for per
  run. Each game costs multiple API calls; keep this bounded on a free
  tier.
- **`LOOKAHEAD_DAYS`** — how far ahead to look for upcoming games.
- **`TOP_PLAYERS_PER_CATEGORY`** — how many players per stat category,
  per team, get a report (e.g. top 1 passer, top 2 rushers, top 3
  pass-catchers).
- **`STAT_TARGETS`** — which specific stats to project per category.
- **`RECENT_GAMES_WINDOW`, `RECENT_WEIGHT`, `SEASON_WEIGHT`** — the
  recent-form blend.
- **`MATCHUP_FACTOR_MIN` / `MAX`** — how far the matchup adjustment is
  allowed to swing the projection.

## Running locally

```bash
pip install -r requirements.txt
export CFBD_API_KEY=your_key_here
python scripts/fetch_data.py
python scripts/compute_projections.py
# then open docs/index.html via a local server (fetch() needs http://, not file://)
python -m http.server --directory docs 8000
```

## What this doesn't do (yet)

It doesn't pull live sportsbook prop lines — the dashboard shows a
projection and a matchup grade, not an "edge" against a specific book
or app's number. You'd cross-reference the projection against whatever
line you're actually looking at. If you want that layer added back in
(similar to the earlier player-prop bot), that's a reasonable next
step, but it's a separate data source (The Odds API) and wasn't part
of this build.