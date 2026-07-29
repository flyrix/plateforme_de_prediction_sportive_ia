/**
 * app.js — IA-BetPredict Frontend
 * Mode 100% BDD Neon + Filtres Dynamiques & Affichage du Score
 */

const RENDER_API_URL = "https://plateforme-de-prediction-sportive-ia.onrender.com";
const API_BASE = window.ENV_API_BASE || RENDER_API_URL;
console.log(`[app] API_BASE=${API_BASE}`);

const LEAGUE_FLAGS = {
  "USA":               "🇺🇸",
  "MLS":               "🇺🇸",
  "USL Championship":  "🇺🇸",
  "USL League One":    "🇺🇸",
  "USL League Two":    "🇺🇸",
  "NPSL":              "🇺🇸",
  "Veikkausliiga":     "🇫🇮",
  "Eliteserien":       "🇳🇴",
  "Serie A Brasil":    "🇧🇷",
  "Club Friendlies":   "🤝",
};

let allCoupons   = [];
let activeLeague = "all";
let activeDate   = new Date().toISOString().split("T")[0];
let activeStatus = "";

const $loading      = document.getElementById("loading");
const $empty        = document.getElementById("empty");
const $grid         = document.getElementById("coupons-grid");
const $total        = document.getElementById("stat-total");
const $avg          = document.getElementById("stat-avg");
const $best         = document.getElementById("stat-best");
const $dateInput    = document.getElementById("date-select");
const $statusSelect = document.getElementById("status-filter");

function getFiltersContainer() {
  return document.getElementById("filters-container") || document.querySelector(".filters-inner");
}

document.addEventListener("DOMContentLoaded", async () => {
  setTodayLabel();
  setupFilterDelegation();
  setupControls();

  if ($dateInput) {
    $dateInput.value = activeDate;
  }

  await loadCoupons();
});

function setupControls() {
  if ($dateInput) {
    $dateInput.addEventListener("change", (e) => {
      activeDate = e.target.value;
      loadCoupons();
    });
  }

  if ($statusSelect) {
    $statusSelect.addEventListener("change", (e) => {
      activeStatus = e.target.value;
      loadCoupons();
    });
  }
}

function isUSALeague(leagueName) {
  if (!leagueName) return false;
  const l = String(leagueName).toUpperCase();
  return l.includes("MLS") || l.includes("USL") || l.includes("NPSL") || l.includes("USA");
}

function setupFilterDelegation() {
  const container = getFiltersContainer();
  if (!container) return;

  container.addEventListener("click", (e) => {
    const btn = e.target.closest(".filter-btn");
    if (!btn) return;

    container.querySelectorAll(".filter-btn").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");

    activeLeague = btn.dataset.league;
    renderCoupons();
  });
}

async function fetchWithRetry(url, options = {}, retries = 3, backoff = 3000, timeoutMs = 60000) {
  const controller = new AbortController();
  // 60 secondes pour laisser le temps au cold-start de Render de se terminer
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);

  try {
    const response = await fetch(url, {
      ...options,
      signal: controller.signal,
      headers: {
        'Accept': 'application/json',
        'Content-Type': 'application/json',
        ...(options.headers || {}),
      },
    });

    clearTimeout(timeoutId);

    if (!response.ok) {
      throw new Error(`Erreur HTTP (${response.status})`);
    }

    return await response.json();
  } catch (err) {
    clearTimeout(timeoutId);

    // Si la requête a été interrompue par timeout ou navigation, on retente
    if (retries > 0) {
      console.warn(`[app] Réveil / Connexion API (${err.message})... Tentative restante: ${retries}`);
      await new Promise(resolve => setTimeout(resolve, backoff));
      return fetchWithRetry(url, options, retries - 1, backoff * 1.5, timeoutMs);
    }
    
    throw err;
  }
}

function setTodayLabel() {
  const label = document.getElementById("today-label");
  if (label) {
    label.textContent = new Date().toLocaleDateString("fr-FR", {
      weekday: "short", day: "numeric", month: "short"
    });
  }
}

async function loadCoupons() {
  showState("loading");

  try {
    let url = `${API_BASE}/coupons/${activeDate}`;
    if (activeStatus) {
      url += `?status=${encodeURIComponent(activeStatus)}`;
    }

    // Passage du délai à 60000ms (60s) pour éviter l'annulation prématurée
    const dailyData = await fetchWithRetry(url, {}, 3, 3000, 60000);
    allCoupons = dailyData.coupons || [];

    generateDynamicFilters();
    renderCoupons();
    updateStats();

  } catch (err) {
    console.error("[app] Erreur de chargement :", err);
    showState("error", "Le serveur prend du temps à répondre. Veuillez rafraîchir la page dans un instant.");
  }
}

function generateDynamicFilters() {
  const container = getFiltersContainer();
  if (!container) return;

  const leagues = new Set();
  let hasUSA = false;

  allCoupons.forEach(c => {
    if (isUSALeague(c.league)) {
      hasUSA = true;
    } else if (c.league) {
      leagues.add(c.league);
    }
  });

  let buttonsHtml = `<button class="filter-btn ${activeLeague === 'all' ? 'active' : ''}" data-league="all">Toutes</button>`;

  if (hasUSA) {
    buttonsHtml += `<button class="filter-btn ${activeLeague === 'USA' ? 'active' : ''}" data-league="USA">🇺🇸 USA</button>`;
  }

  Array.from(leagues).sort().forEach(league => {
    const flag = LEAGUE_FLAGS[league] || "⚽";
    const isActive = activeLeague === league ? "active" : "";
    buttonsHtml += `<button class="filter-btn ${isActive}" data-league="${escapeHtml(league)}">${flag} ${escapeHtml(league)}</button>`;
  });

  container.innerHTML = buttonsHtml;
}

function renderCoupons() {
  let filtered = allCoupons;

  if (activeLeague === "USA") {
    filtered = allCoupons.filter(c => isUSALeague(c.league));
  } else if (activeLeague !== "all") {
    filtered = allCoupons.filter(c => c.league === activeLeague);
  }

  if (filtered.length === 0) {
    showState("empty");
    return;
  }

  showState("grid");
  $grid.innerHTML = filtered.map(couponCard).join("");

  requestAnimationFrame(() => {
    document.querySelectorAll(".confidence-bar-fill").forEach(bar => {
      const w = bar.dataset.width;
      bar.style.width = w + "%";
    });
  });
}

function getConfidenceRate(c) {
  const raw = c.confidence_rate !== undefined ? c.confidence_rate : c.confidence;
  const num = Number(raw) || 0;
  return num > 1 ? num / 100 : num;
}

function couponCard(c) {
  const confidence = getConfidenceRate(c);
  const pct        = Math.round(confidence * 100);
  const isHigh     = pct >= 70;
  const tierClass  = isHigh ? "tier-high" : "tier-mid";
  const pctClass   = isHigh ? "high"      : "mid";
  const barClass   = isHigh ? ""          : "mid";
  const flag       = LEAGUE_FLAGS[c.league] || (isUSALeague(c.league) ? "🇺🇸" : "⚽");
  
  const statusText = typeof c.status === "string" ? c.status : "En attente";
  
  const statusClass = {
    "En attente": "attente",
    "Gagné":      "gagne",
    "Perdu":      "perdu",
    "Annulé":     "annule",
  }[statusText] || "attente";

  // Détection du Score : remplace "VS" si présent
  const hasScore = c.score || (c.home_score !== undefined && c.away_score !== undefined && c.home_score !== null);
  const scoreDisplay = c.score || `${c.home_score} - ${c.away_score}`;
  const centerLabel = hasScore 
    ? `<span class="score-badge" style="background:#2d3748; color:#00ff88; font-weight:bold; padding:2px 8px; border-radius:6px;">${escapeHtml(scoreDisplay)}</span>`
    : `<span class="vs-label">VS</span>`;

  return `
  <div class="coupon-card ${tierClass}">
    <div class="coupon-header">
      <span class="league-badge">${flag} ${escapeHtml(c.league || "")}</span>
      <span class="match-time">${escapeHtml(c.match_time || "--:--")}</span>
    </div>
  
    <div class="teams-row">
      <span class="team-name home">${escapeHtml(c.home_team || "")}</span>
      ${centerLabel}
      <span class="team-name away">${escapeHtml(c.away_team || "")}</span>
    </div>

    <div class="coupon-divider"></div>

    <div class="prediction-row">
      <div>
        <div class="prediction-label">Pari recommandé</div>
        <div class="prediction-type">${escapeHtml(c.prediction_type || c.type || "")}</div>
        <span class="status-badge ${statusClass}">${escapeHtml(statusText)}</span>
      </div>
      <div class="confidence-block">
        <div class="confidence-pct ${pctClass}">${pct}<span style="font-size:13px;font-weight:400">%</span></div>
        <div class="confidence-bar-track">
          <div class="confidence-bar-fill ${barClass}" data-width="${pct}" style="width:0%"></div>
        </div>
      </div>
    </div>
  </div>`;
}

function escapeHtml(value) {
  return String(value || "").replace(/[&<>"']/g, char => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[char]));
}

function updateStats() {
  if (allCoupons.length === 0) {
    if ($total) $total.textContent = "0";
    if ($avg)   $avg.textContent   = "—";
    if ($best)  $best.textContent  = "—";
    return;
  }
  
  const rates = allCoupons.map(c => getConfidenceRate(c));
  const sum   = rates.reduce((acc, curr) => acc + curr, 0);
  const max   = Math.max(...rates);
  const avg   = sum / rates.length;

  if ($total) $total.textContent = allCoupons.length;
  if ($avg)   $avg.textContent   = Math.round(avg * 100) + "%";
  if ($best)  $best.textContent  = Math.round(max * 100) + "%";
}

function showState(state, message = "") {
  if ($loading) $loading.style.display = state === "loading" ? "flex"  : "none";
  if ($empty)   $empty.style.display   = state === "empty"   ? "block" : "none";
  if ($grid)    $grid.style.display    = state === "grid"    ? "grid"  : "none";

  const $err = document.getElementById("error-state");
  if ($err) {
    $err.style.display = state === "error" ? "block" : "none";
    if (state === "error" && message) {
      const msgEl = $err.querySelector(".error-msg");
      if (msgEl) msgEl.textContent = message;
    }
  }
}