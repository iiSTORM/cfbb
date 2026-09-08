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
  renderMain(games[activeGameIndex]);
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
        Opponent allows ${p.opponentValue ?? "—"} on this metric vs league avg ${p.leagueAvgValue ?? "—"}
      </div>
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
      return `
        <div class="team-block">
          <div class="team-heading">
            <div class="team-name display">${t.team}</div>
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

loadData();
