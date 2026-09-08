// ============================================================
// Pure helpers (kept separate from DOM code so they're testable
// with a plain Node script — see the project's test notes)
// ============================================================

function matchupBadge(matchupFactor) {
  if (matchupFactor >= 1.08) return { label: "Favorable matchup", cls: "badge-favorable" };
  if (matchupFactor <= 0.92) return { label: "Tough matchup", cls: "badge-tough" };
  return { label: "Neutral matchup", cls: "badge-neutral" };
}

function confidenceClass(confidence) {
  if (confidence === "High") return "badge-conf-high";
  if (confidence === "Medium") return "badge-conf-medium";
  return "badge-conf-low";
}

function formatDate(isoString) {
  if (!isoString) return "Date TBD";
  const d = new Date(isoString);
  if (isNaN(d.getTime())) return "Date TBD";
  return d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

// Builds an SVG string visualizing a player's recent games as dots
// along a scale, with the projection marked as a diamond. Deliberately
// not a generic bar/line chart component — reads like a simple
// yardage/production scale instead.
function buildGameLogSVG(recentGames, projected, width = 600, height = 46) {
  const values = [...recentGames, projected].filter((v) => typeof v === "number");
  if (values.length === 0) {
    return `<svg class="game-log-svg" viewBox="0 0 ${width} ${height}"></svg>`;
  }
  const max = Math.max(...values) * 1.15 || 1;
  const min = 0;
  const padX = 12;
  const usableWidth = width - padX * 2;
  const axisY = height - 14;

  const scaleX = (v) => padX + (v / (max - min)) * usableWidth;

  let dots = "";
  recentGames.forEach((v, i) => {
    const isLast = i === recentGames.length - 1;
    const x = scaleX(v);
    const r = isLast ? 4.5 : 3;
    const opacity = 0.35 + (i / Math.max(recentGames.length - 1, 1)) * 0.5;
    dots += `<circle cx="${x.toFixed(1)}" cy="${axisY}" r="${r}" fill="var(--chalk)" fill-opacity="${opacity.toFixed(2)}" />`;
  });

  const projX = scaleX(projected);
  const projMarker = `
    <line x1="${projX.toFixed(1)}" y1="4" x2="${projX.toFixed(1)}" y2="${height - 2}" stroke="var(--green)" stroke-width="1" stroke-dasharray="2,2" />
    <path d="M ${projX.toFixed(1)} 4 l 5 6 l -5 6 l -5 -6 z" fill="var(--green)" />
  `;

  const axisLine = `<line x1="${padX}" y1="${axisY}" x2="${width - padX}" y2="${axisY}" stroke="var(--line)" stroke-width="1" />`;

  return `<svg class="game-log-svg" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none">${axisLine}${dots}${projMarker}</svg>`;
}

// ============================================================
// Rendering
// ============================================================

let projectionsData = null;
let activeGameIndex = 0;
let searchQuery = "";

// ============================================================
// CSV export — pure function kept separate from the download
// mechanics so it's testable without a browser.
// ============================================================

function csvEscape(value) {
  const s = String(value ?? "");
  if (/[",\n]/.test(s)) return `"${s.replace(/"/g, '""')}"`;
  return s;
}

function generateCSV(data) {
  const headers = [
    "team", "opponent", "homeAway", "player", "category", "label",
    "seasonAvg", "recentAvg", "projected", "matchupFactor", "confidence",
    "opponentRank", "totalTeamsRanked", "sportsbookLine", "edge", "gameStartDate",
  ];
  const rows = [headers.join(",")];

  for (const game of (data.games || [])) {
    for (const t of (game.teams || [])) {
      for (const p of (t.players || [])) {
        rows.push(
          [
            t.team, t.opponent, t.homeAway ?? "", p.name, p.category, p.label,
            p.seasonAvg ?? "", p.recentAvg ?? "", p.projected ?? "", p.matchupFactor ?? "", p.confidence ?? "",
            p.opponentRank ?? "", p.totalTeamsRanked ?? "", p.sportsbookLine ?? "", p.edge ?? "", game.startDate ?? "",
          ]
            .map(csvEscape)
            .join(",")
        );
      }
    }
  }
  return rows.join("\n");
}

function downloadCSV() {
  if (!projectionsData) return;
  const csv = generateCSV(projectionsData);
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  const dateStamp = new Date().toISOString().slice(0, 10);
  a.href = url;
  a.download = `cfb-prop-projections-${dateStamp}.csv`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

// If the last successful run is older than this, show a staleness
// warning rather than silently displaying old data as current. Set
// comfortably above the twice-daily (~12h) schedule to allow for
// normal timing slack without false alarms.
const STALE_AFTER_HOURS = 20;

function renderFreshnessBanner(generatedAt) {
  const el = document.getElementById("freshness-banner");
  if (!generatedAt) {
    el.innerHTML = "";
    return;
  }
  const ageHours = (Date.now() - new Date(generatedAt).getTime()) / 36e5;
  if (ageHours <= STALE_AFTER_HOURS) {
    el.innerHTML = "";
    return;
  }
  const ageLabel = ageHours >= 48 ? `${Math.floor(ageHours / 24)} days` : `${Math.floor(ageHours)} hours`;
  el.innerHTML = `
    <div class="freshness-banner">
      <strong>Data may be stale</strong> — last updated ${ageLabel} ago. Check the repo's
      Actions tab to confirm the scheduled run is still succeeding.
    </div>
  `;
}

async function loadData() {
  try {
    const res = await fetch("data/projections.json", { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    projectionsData = await res.json();
  } catch (e) {
    console.error("Failed to load projections.json", e);
    renderFetchError();
    return;
  }
  render();
}

function renderFetchError() {
  document.getElementById("sidebar-sub").textContent = "No data yet";
  document.getElementById("main").innerHTML = `
    <div class="empty-state">
      <div class="empty-state-title display">No report generated yet</div>
      <p>Run the fetch + compute scripts (or wait for the next scheduled run) to populate this dashboard.</p>
    </div>
  `;
}

function render() {
  const games = (projectionsData && projectionsData.games) || [];

  const sidebarSub = document.getElementById("sidebar-sub");
  const generated = projectionsData.generatedAt ? new Date(projectionsData.generatedAt) : null;
  sidebarSub.textContent = generated
    ? `Updated ${generated.toLocaleDateString(undefined, { month: "short", day: "numeric" })}, ${generated.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })}`
    : "";

  renderFreshnessBanner(projectionsData.generatedAt);

  if (games.length === 0) {
    document.getElementById("matchup-list").innerHTML = "";
    const neverRun = !projectionsData.generatedAt;
    document.getElementById("main").innerHTML = neverRun
      ? `
        <div class="empty-state">
          <div class="empty-state-title display">No report generated yet</div>
          <p>This dashboard is waiting on its first scheduled run, or you can trigger
          one manually from the repo's Actions tab (Update CFB Prop Projections → Run workflow).</p>
        </div>
      `
      : `
        <div class="empty-state">
          <div class="empty-state-title display">No upcoming games in range</div>
          <p>Nothing found in the lookahead window as of the last run. Check back closer
          to kickoff, or widen CFB_LOOKAHEAD_DAYS in the scraper config.</p>
        </div>
      `;
    return;
  }

  if (activeGameIndex >= games.length) activeGameIndex = 0;
  renderSidebar(games);

  if (searchQuery) {
    renderSearchResults(games, searchQuery);
  } else {
    renderMain(games[activeGameIndex]);
    renderLeaderboard(games);
  }
}

// Player search — unlike the leaderboard, this shows every match
// regardless of confidence (a search is a direct lookup, not a
// recommendation), across every tracked game.
function renderSearchResults(games, query) {
  const q = query.toLowerCase();
  const matches = [];
  games.forEach((game, gameIndex) => {
    (game.teams || []).forEach((t) => {
      (t.players || []).forEach((p) => {
        if (p.name.toLowerCase().includes(q)) {
          matches.push({ ...p, gameIndex, team: t.team, opponent: t.opponent });
        }
      });
    });
  });

  const main = document.getElementById("main");
  main.innerHTML = `
    <div class="report-header">
      <div class="report-title display">Search: "${query}"</div>
      <button class="clear-search-link" id="clear-search-btn">Clear search</button>
    </div>
    ${
      matches.length === 0
        ? `<div class="empty-state"><div class="empty-state-title display">No players found</div><p>No tracked player matches "${query}" this week.</p></div>`
        : `<div class="leaderboard-grid">
            ${matches
              .map((p) => {
                const badge = matchupBadge(p.matchupFactor);
                return `
                  <button class="leaderboard-item" data-game-index="${p.gameIndex}">
                    <div class="leaderboard-item-top">
                      <span class="leaderboard-name">${p.name}</span>
                      <span class="leaderboard-number display">${p.projected}</span>
                    </div>
                    <div class="leaderboard-meta">${p.label} · ${p.team} vs ${p.opponent}</div>
                    <div class="leaderboard-meta"><span class="badge ${badge.cls}">${badge.label}</span></div>
                  </button>
                `;
              })
              .join("")}
          </div>`
    }
  `;

  document.getElementById("clear-search-btn").addEventListener("click", () => {
    document.getElementById("player-search").value = "";
    searchQuery = "";
    render();
  });

  main.querySelectorAll(".leaderboard-item").forEach((btn) => {
    btn.addEventListener("click", () => {
      activeGameIndex = parseInt(btn.dataset.gameIndex, 10);
      document.getElementById("player-search").value = "";
      searchQuery = "";
      render();
    });
  });
}

// Flattens every player across every tracked game, ranks the most
// favorable matchups (Medium/High confidence only — a favorable
// matchup on an inconsistent player isn't a strong signal), and shows
// a glanceable top-N instead of requiring a click into every game.
function renderLeaderboard(games) {
  const existing = document.getElementById("leaderboard");
  if (existing) existing.remove();

  const entries = [];
  games.forEach((game, gameIndex) => {
    (game.teams || []).forEach((t) => {
      (t.players || []).forEach((p) => {
        if (p.confidence === "Low") return;
        entries.push({ ...p, gameIndex, team: t.team, opponent: t.opponent });
      });
    });
  });

  if (entries.length === 0) return;

  entries.sort((a, b) => b.matchupFactor - a.matchupFactor);
  const top = entries.slice(0, 8);

  const html = `
    <div class="leaderboard" id="leaderboard">
      <div class="leaderboard-title display">Top Matchups This Week</div>
      <div class="leaderboard-sub">Most favorable matchups among Medium/High-confidence projections, across every tracked game.</div>
      <div class="leaderboard-grid">
        ${top
          .map((p) => {
            const badge = matchupBadge(p.matchupFactor);
            return `
              <button class="leaderboard-item" data-game-index="${p.gameIndex}">
                <div class="leaderboard-item-top">
                  <span class="leaderboard-name">${p.name}</span>
                  <span class="leaderboard-number display">${p.projected}</span>
                </div>
                <div class="leaderboard-meta">${p.label} · ${p.team} vs ${p.opponent}</div>
                <div class="leaderboard-meta"><span class="badge ${badge.cls}">${badge.label}</span></div>
              </button>
            `;
          })
          .join("")}
      </div>
    </div>
  `;

  const main = document.getElementById("main");
  main.insertAdjacentHTML("afterbegin", html);

  document.querySelectorAll("#leaderboard .leaderboard-item").forEach((btn) => {
    btn.addEventListener("click", () => {
      activeGameIndex = parseInt(btn.dataset.gameIndex, 10);
      render();
    });
  });
}

function renderSidebar(games) {
  const list = document.getElementById("matchup-list");
  list.innerHTML = games
    .map((g, i) => {
      const activeCls = i === activeGameIndex ? " active" : "";
      return `
        <button class="matchup-item${activeCls}" data-index="${i}">
          <div class="matchup-teams">${g.awayTeam} @ ${g.homeTeam}</div>
          <div class="matchup-date">${formatDate(g.startDate)}</div>
        </button>
        <div class="yard-tick"></div>
      `;
    })
    .join("");

  list.querySelectorAll(".matchup-item").forEach((btn) => {
    btn.addEventListener("click", () => {
      activeGameIndex = parseInt(btn.dataset.index, 10);
      render();
    });
  });
}

function renderPlayerRow(p) {
  const badge = matchupBadge(p.matchupFactor);
  const confCls = confidenceClass(p.confidence);
  const svg = buildGameLogSVG(p.recentGames || [], p.projected);
  const lineHtml = renderSportsbookLine(p);

  return `
    <div class="player-row">
      <div class="player-row-top">
        <div>
          <div class="player-name">${p.name}</div>
          <div class="player-stat-label">${p.label}</div>
        </div>
        <div class="projected-figure">
          <div class="projected-number display">${p.projected}</div>
          <div class="projected-caption">projected</div>
        </div>
      </div>
      ${lineHtml}
      <div class="game-log-viz">
        ${svg}
        <div class="viz-legend">
          <span class="viz-legend-item"><span class="legend-swatch" style="background:var(--chalk)"></span>Recent games</span>
          <span class="viz-legend-item"><span class="legend-swatch" style="background:var(--green)"></span>Projection</span>
        </div>
      </div>
      <div class="matchup-note">
        <span class="badge ${badge.cls}">${badge.label}</span>
        &nbsp;·&nbsp;
        <span class="badge ${confCls}">Confidence: ${p.confidence}</span>
        &nbsp;·&nbsp;
        Season avg ${p.seasonAvg ?? "—"}, recent avg ${p.recentAvg ?? "—"}
        &nbsp;·&nbsp;
        ${rankText(p)}
      </div>
    </div>
  `;
}

function rankText(p) {
  const recentNote = p.opponentRecentValue != null ? ` (recent form: ${p.opponentRecentValue}, season: ${p.opponentValue ?? "—"})` : "";
  if (p.opponentRank == null || p.totalTeamsRanked == null) {
    return `Opponent allows ${p.opponentValue ?? "—"} vs league avg ${p.leagueAvgValue ?? "—"}${recentNote}`;
  }
  return `Opponent ranks ${ordinal(p.opponentRank)} of ${p.totalTeamsRanked} on this defensive metric (season avg ${p.opponentValue ?? "—"} vs league avg ${p.leagueAvgValue ?? "—"})${recentNote}`;
}

function ordinal(n) {
  const s = ["th", "st", "nd", "rd"];
  const v = n % 100;
  return n + (s[(v - 20) % 10] || s[v] || s[0]);
}

// Sportsbook line data is optional (only present if fetch_odds.py ran
// with a key configured, and only for players it found a matching
// line for) — renders nothing at all when absent, rather than an
// empty/placeholder row.
function renderSportsbookLine(p) {
  if (p.sportsbookLine == null) return "";
  const edgeColor = p.edge > 0 ? "var(--green)" : p.edge < 0 ? "var(--red)" : "var(--chalk-dim)";
  const edgeSign = p.edge > 0 ? "+" : "";
  return `
    <div class="row" style="margin-bottom:10px;">
      <span class="label">Sportsbook line (${p.sportsbookBook ?? "book"})</span>
      <span class="outcome">${p.sportsbookLine} <span style="color:${edgeColor}">(${edgeSign}${p.edge} edge)</span></span>
    </div>
  `;
}

function renderMain(game) {
  const main = document.getElementById("main");

  const teamsHtml = (game.teams || [])
    .map((t) => {
      const playersHtml = (t.players || []).length
        ? t.players.map(renderPlayerRow).join("")
        : `<div class="matchup-note">No usable player props found for ${t.team} this run.</div>`;
      const homeAwayLabel = t.homeAway === "home" ? "Home" : t.homeAway === "away" ? "Away" : null;
      return `
        <div class="team-block">
          <div class="team-heading">
            <div class="team-name display">${t.team}</div>
            ${homeAwayLabel ? `<span class="badge badge-neutral">${homeAwayLabel}</span>` : ""}
            <div class="vs-opponent">vs ${t.opponent}</div>
          </div>
          <div class="yard-tick" style="margin: 8px 0 0 0;"></div>
          ${playersHtml}
        </div>
      `;
    })
    .join("");

  main.innerHTML = `
    <div class="report-header">
      <div class="report-title display">${game.awayTeam} @ ${game.homeTeam}</div>
      <div class="report-meta">${formatDate(game.startDate)}</div>
    </div>
    ${teamsHtml}
    <div class="footer-note">
      Projections blend each player's recent-game average with their season average,
      then apply an opponent matchup factor derived from the opponent's defensive
      success rate allowed vs. the national average for that play type. This is a
      transparent heuristic, not a fitted predictive model — treat it as a
      structured starting point for your own judgment, not a forecast to bet on
      directly. Confidence reflects how consistent the player's recent games have
      been, not how accurate the model is.
    </div>
  `;
}

function renderTrackRecord(summary) {
  const el = document.getElementById("track-record");
  if (!summary || summary.settledCount === 0) {
    el.innerHTML = "";
    return;
  }

  const confRows = Object.entries(summary.byConfidence || {})
    .map(([level, stat]) => `<div class="track-record-stat"><span>${level} conf. (n=${stat.count})</span><span class="value">${stat.meanAbsError} avg error</span></div>`)
    .join("");

  el.innerHTML = `
    <div class="track-record">
      <div class="track-record-title">Track Record</div>
      <div class="track-record-stat"><span>Graded projections</span><span class="value">${summary.settledCount}</span></div>
      <div class="track-record-stat"><span>Mean absolute error</span><span class="value">${summary.meanAbsError}</span></div>
      <div class="track-record-stat"><span>Bias (+ = under-projects)</span><span class="value">${summary.meanError > 0 ? "+" : ""}${summary.meanError}</span></div>
      ${confRows}
      <div class="track-record-note">
        ${summary.pendingCount} projection(s) awaiting results${summary.assumedZeroCount ? `, ${summary.assumedZeroCount} settled via a no-data-found assumption (excluded above)` : ""}.
      </div>
    </div>
  `;
}

async function loadAccuracySummary() {
  try {
    const res = await fetch("data/accuracy_summary.json", { cache: "no-store" });
    if (!res.ok) return; // fine if it doesn't exist yet — nothing settled so far
    const summary = await res.json();
    renderTrackRecord(summary);
  } catch (e) {
    // Track record is a bonus panel, not core functionality — fail silently
    console.warn("Could not load accuracy_summary.json", e);
  }
}

function initControls() {
  const searchInput = document.getElementById("player-search");
  searchInput.addEventListener("input", () => {
    searchQuery = searchInput.value.trim();
    render();
  });

  document.getElementById("csv-export-btn").addEventListener("click", downloadCSV);
}

initControls();
loadData();
loadAccuracySummary();