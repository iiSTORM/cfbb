"""
OPTIONAL — only runs if ODDS_API_KEY is set. Pulls sportsbook player
prop lines from The Odds API and matches them to this week's tracked
players, so the dashboard can show a real edge (projected vs. the
actual line) instead of just a projection + matchup grade on its own.

COVERAGE CAVEAT: college football player props are covered far more
thinly by sportsbooks than NFL — expect most non-marquee games to have
few or no matching lines. This is a best-effort supplement, not a
guarantee every projected player will have a line to compare against.

MATCHING CAVEAT: CFBD and The Odds API are different data providers
with no shared ID space. Team and player names are matched with a
normalize + substring heuristic — real, but imperfect. Every failed
match is logged so a mismatch is visible rather than silently missing
data; add entries to TEAM_NAME_OVERRIDES below as you spot them.

QUOTA: this adds a SEPARATE API quota to manage — The Odds API's own
free tier (https://the-odds-api.com), independent of CFBD's. Keep an
eye on both if you enable this, on top of the CFBD budget discussion
already in the README.
"""

import json
import logging
import os
import re

import requests

import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("fetch_odds")

ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")
ODDS_API_BASE = "https://api.the-odds-api.com/v4"
SPORT_KEY = "americanfootball_ncaaf"
REGIONS = "us"

# Player prop market keys to request, by our internal category name.
# NCAAF prop coverage/market-key naming is UNVERIFIED against a live
# response — if odds come back empty across the board, check the
# Action log for the raw response and cross-reference
# https://the-odds-api.com/sports-odds-data/betting-markets.html
MARKETS_BY_CATEGORY = {
    "passing": "player_pass_yds",
    "rushing": "player_rush_yds",
    "receiving": "player_reception_yds",
}

# Manual overrides for team names that don't fuzzy-match cleanly
# between CFBD and The Odds API. Add entries here as you spot
# mismatches in the fetch log. Format: CFBD name -> Odds API name.
TEAM_NAME_OVERRIDES = {
    # "Ohio State": "Ohio State Buckeyes",
}


def normalize_name(name):
    """Lowercase, strip punctuation, collapse whitespace — enough to
    absorb small naming differences (mascots, periods, etc.) without
    being a full fuzzy-matching library."""
    if not name:
        return ""
    n = name.lower()
    n = re.sub(r"[^a-z0-9\s]", "", n)
    n = re.sub(r"\s+", " ", n).strip()
    return n


def names_match(a, b):
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb:
        return False
    return na == nb or na in nb or nb in na


def odds_get(path, params=None):
    if not ODDS_API_KEY:
        return None
    url = f"{ODDS_API_BASE}{path}"
    try:
        resp = requests.get(url, params={**(params or {}), "apiKey": ODDS_API_KEY}, timeout=config.REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException as e:
        log.error(f"Odds API request failed on {path} {params}: {e}")
        return None


def get_odds_events():
    events = odds_get(f"/sports/{SPORT_KEY}/events")
    return events or []


def find_matching_event(events, home_team, away_team):
    mapped_home = TEAM_NAME_OVERRIDES.get(home_team, home_team)
    mapped_away = TEAM_NAME_OVERRIDES.get(away_team, away_team)
    for event in events:
        eh, ea = event.get("home_team", ""), event.get("away_team", "")
        if names_match(mapped_home, eh) and names_match(mapped_away, ea):
            return event
        if names_match(mapped_home, ea) and names_match(mapped_away, eh):
            return event  # providers occasionally disagree on which side is "home"
    return None


def get_event_player_props(event_id, markets):
    if not markets:
        return None
    return odds_get(
        f"/sports/{SPORT_KEY}/events/{event_id}/odds",
        {"regions": REGIONS, "markets": ",".join(markets), "oddsFormat": "american"},
    )


def find_player_line(event_odds, player_name, market_key):
    """Best (highest-payout) Over line found for this player in this
    market, across every book offering it."""
    if not event_odds:
        return None
    best = None
    for book in event_odds.get("bookmakers", []):
        for market in book.get("markets", []):
            if market.get("key") != market_key:
                continue
            for outcome in market.get("outcomes", []):
                if outcome.get("name") != "Over":
                    continue
                if not names_match(player_name, outcome.get("description", "")):
                    continue
                point = outcome.get("point")
                price = outcome.get("price")
                if point is None or price is None:
                    continue
                if best is None or price > best["price"]:
                    best = {"point": point, "price": price, "book": book.get("title")}
    return best


def main():
    if not ODDS_API_KEY:
        log.info("ODDS_API_KEY not set — skipping sportsbook line matching (this step is optional).")
        return

    try:
        with open(config.PROJECTIONS_PATH) as f:
            projections = json.load(f)
    except FileNotFoundError:
        log.error(f"{config.PROJECTIONS_PATH} not found — run compute_projections.py first.")
        return

    events = get_odds_events()
    log.info(f"Found {len(events)} upcoming NCAAF events on The Odds API.")

    matched_lines = 0
    checked = 0
    for game in projections.get("games", []):
        event = find_matching_event(events, game.get("homeTeam", ""), game.get("awayTeam", ""))
        if not event:
            log.info(f"No Odds API event match for {game.get('awayTeam')} @ {game.get('homeTeam')}")
            continue

        needed_markets = {
            MARKETS_BY_CATEGORY[p["category"]]
            for t in game.get("teams", [])
            for p in t.get("players", [])
            if p["category"] in MARKETS_BY_CATEGORY
        }
        if not needed_markets:
            continue

        event_odds = get_event_player_props(event["id"], needed_markets)

        for team_entry in game.get("teams", []):
            for p in team_entry.get("players", []):
                market_key = MARKETS_BY_CATEGORY.get(p["category"])
                if not market_key:
                    continue
                checked += 1
                line = find_player_line(event_odds, p["name"], market_key)
                if line:
                    p["sportsbookLine"] = line["point"]
                    p["sportsbookOdds"] = line["price"]
                    p["sportsbookBook"] = line["book"]
                    p["edge"] = round(p["projected"] - line["point"], 1)
                    matched_lines += 1

    with open(config.PROJECTIONS_PATH, "w") as f:
        json.dump(projections, f, indent=2)
    log.info(f"Matched sportsbook lines for {matched_lines} of {checked} checked player prop(s).")


if __name__ == "__main__":
    main()