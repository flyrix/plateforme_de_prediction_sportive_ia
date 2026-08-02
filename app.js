/**
 * app.js — IA-BetPredict Frontend
 * Mode BDD Neon + Filtres Dynamiques, Score, Cotes & Value Bets
 */

const RENDER_API_URL = "https://plateforme-de-prediction-sportive-ia.onrender.com";
const API_BASE = window.ENV_API_BASE || RENDER_API_URL;
console.log(`[app] Initialisation avec API_BASE = ${API_BASE}`);

// Dictionnaire des drapeaux / icônes de ligue
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
  "Premier League":    "🏴󠁧󠁢󠁥󠁮󠁧󠁿",
  "LaLiga":            "🇪🇸",
  "Bundesliga":        "🇩🇪",
  "Serie A":           "🇮🇹",
  "Ligue 1":           "🇫🇷",
};

// État global de l'application
let allCoupons   = [];
let activeLeague = "all";
let activeDate   = new Date().toISOString().split("T")[0];
let activeStatus = "";

// Éléments du DOM
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

// Initialisation au chargement de la page
document.addEventListener("DOMContentLoaded", async () => {
  setTodayLabel();
  setupFilterDelegation();
  setupControls();

  if ($dateInput) {
    $dateInput.value = activeDate;
  }

  await loadCoupons();
});

/**
 * Configure les écouteurs d'événements sur les contrôles (Date et Statut)
 */
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

/**
 * Vérifie si la ligue correspond au groupe USA / Nord-Américain
 */
function isUSALeague(leagueName) {
  if (!leagueName) return false;
  const l = String(leagueName).toUpperCase();
  return l.includes("MLS") || l.includes("USL") || l.includes("NPSL") || l.includes("USA");
}

/**
 * Gestion événementielle déléguée pour les boutons de filtre de ligue
 */
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

/**
 * Requête fetch optimisée avec retry & désactivation du cache navigateur
 */
async function fetchWithRetry(url, options = {}, retries = 3, backoff = 3000, timeoutMs = 60000) {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);

  try {
    const response = await fetch(url, {
      ...options,
      signal: controller.signal,
      headers: {
        'Accept': 'application/json',
        'Content-Type': 'application/json',
        'Cache-Control': 'no-cache', // Force la récupération des derniers dépouillements
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

    if (retries > 0) {
      console.warn(`[app] Nouvelle tentative d'accès à l'API... Essais restants : ${retries}`);
      await new Promise(resolve => setTimeout(resolve, backoff));
      return fetchWithRetry(url, options, retries - 1, backoff * 1.5, timeoutMs);
    }
    
    throw err;
  }
}

/**
 * Met à jour la date courante affichée dans le header
 */
function setTodayLabel() {
  const label = document.getElementById("today-label");
  if (label) {
    label.textContent = new Date().toLocaleDateString("fr-FR", {
      weekday: "short", day: "numeric", month: "short"
    });
  }
}

/**
 * Charge la liste des coupons depuis le backend
 */
async function loadCoupons() {
  showState("loading");

  try {
    let url = `${API_BASE}/coupons/${activeDate}`;
    if (activeStatus) {
      url += `?status=${encodeURIComponent(activeStatus)}`;
    }

    const dailyData = await fetchWithRetry(url, {}, 3, 3000, 60000);
    allCoupons = dailyData.coupons || [];

    generateDynamicFilters();
    renderCoupons();
    updateStats();

  } catch (err) {
    console.error("[app] Échec du chargement des coupons :", err);
    showState("error", "Le serveur backend prend du temps à répondre (Cold Start). Veuillez rafraîchir dans un instant.");
  }
}

/**
 * Génération dynamique des boutons de filtres selon les ligues disponibles
 */
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

/**
 * Rendu des cartes de coupons filtrées dans le DOM
 */
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

  // Animation fluide de remplissage des barres de confiance
  requestAnimationFrame(() => {
    document.querySelectorAll(".confidence-bar-fill").forEach(bar => {
      const w = bar.dataset.width;
      bar.style.width = w + "%";
    });
  });
}

/**
 * Normalise la valeur de confiance entre 0.0 et 1.0
 */
function getConfidenceRate(c) {
  const raw = c.confidence_rate !== undefined ? c.confidence_rate : c.confidence;
  const num = Number(raw) || 0;
  return num > 1 ? num / 100 : num;
}

/**
 * Normalise et retourne le statut propre ainsi que la classe CSS associée
 */
function getNormalizedStatus(statusRaw) {
  const s = String(statusRaw || "En attente").trim().toLowerCase();

  if (s.includes("gagn") || s === "won" || s === "win") {
    return { text: "Gagné", class: "gagne" };
  }
  if (s.includes("perd") || s === "lost" || s === "loss") {
    return { text: "Perdu", class: "perdu" };
  }
  if (s.includes("annul") || s.includes("postpon") || s.includes("cancel")) {
    return { text: "Annulé", class: "annule" };
  }
  return { text: "En attente", class: "attente" };
}

/**
 * Génère la structure HTML d'une carte de coupon individuelle
 */
function couponCard(c) {
  const confidence = getConfidenceRate(c);
  const pct        = Math.round(confidence * 100);
  const isHigh     = pct >= 70;
  const tierClass  = isHigh ? "tier-high" : "tier-mid";
  const pctClass   = isHigh ? "high"      : "mid";
  const barClass   = isHigh ? ""          : "mid";
  const flag       = LEAGUE_FLAGS[c.league] || (isUSALeague(c.league) ? "🇺🇸" : "⚽");
  
  // Normalisation du statut (Résout le problème d'affichage Gagné / Perdu)
  const { text: statusText, class: statusClass } = getNormalizedStatus(c.status);

  // Score du match
  const hasScore = Boolean(c.score) || (c.home_score !== undefined && c.away_score !== undefined && c.home_score !== null);
  const scoreDisplay = c.score || `${c.home_score} - ${c.away_score}`;
  const centerLabel = hasScore 
    ? `<span class="score-badge" style="font-weight:700; padding:2px 8px; border-radius:4px; background:rgba(255,255,255,0.1);">${escapeHtml(scoreDisplay)}</span>`
    : `<span class="vs-label">VS</span>`;

  // Cotes & Expected Value (+EV)
  const oddsVal = Number(c.odds || 0);
  const evVal = Number(c.expected_value || c.ev || 0);
  const oddsDisplay = oddsVal > 1.0 ? `<span class="odds-badge">Cote: ${oddsVal.toFixed(2)}</span>` : '';
  const isValueBet = evVal > 0 ? `<span class="value-bet-badge" style="color:#10b981; font-weight:bold;">🔥 +EV (${(evVal * 100).toFixed(1)}%)</span>` : '';

  return `
  <div class="coupon-card ${tierClass}">
    <div class="coupon-header">
      <span class="league-badge">${flag} ${escapeHtml(c.league || "")}</span>
      <span class="match-time">🕒 ${escapeHtml(c.match_time || "--:--")}</span>
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
        <div class="prediction-type">
          ${escapeHtml(c.prediction_type || c.type || "")}
          ${oddsDisplay}
          ${isValueBet}
        </div>
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

/**
 * Échappe le texte pour prévenir les failles XSS
 */
function escapeHtml(value) {
  return String(value || "").replace(/[&<>"']/g, char => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[char]));
}

/**
 * Met à jour le bloc des statistiques globales de la page
 */
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

/**
 * Bascule l'affichage des conteneurs selon l'état actuel (Loading, Empty, Grid, Error)
 */
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