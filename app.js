/**
 * app.js — IA-BetPredict Frontend
 *
 * 1. Charge les coupons depuis l'API FastAPI
 * 2. Affiche les cartes avec jauge de confiance
 * 3. Gère les filtres par ligue
 */

// ── Config ────────────────────────────────────────────────
// En production, définis window.ENV_API_BASE avant ce script :
// <script>window.ENV_API_BASE = "https://ia-betpredict-api.onrender.com";</script>
// En développement local : http://127.0.0.1:8000
//|| window.location.origin || "http://127.0.0.1:8000"
const RENDER_API_URL = "https://plateforme-de-prediction-sportive-ia.onrender.com";

const API_BASE = window.ENV_API_BASE || RENDER_API_URL;
console.log(`[app] API_BASE=${API_BASE}`);

// Icônes des ligues
const LEAGUE_FLAGS = {
  "Veikkausliiga":   "🇫🇮",
  "Eliteserien":     "🇳🇴",
  "MLS":             "🇺🇸",
  "USL Championship": "🇺🇸",
  "USL League One":   "🇺🇸",
  "USL League Two":   "🇺🇸",
  "NPSL":             "🇺🇸",
  "NPSL Founders Cup": "🇺🇸",
  "Serie A Brasil":  "🇧🇷",
  "Club Friendlies": "🤝",
};

// ── State ─────────────────────────────────────────────────
let allCoupons  = [];
let activeLeague = "all";

// ── DOM ───────────────────────────────────────────────────
const $loading = document.getElementById("loading");
const $empty   = document.getElementById("empty");
const $grid    = document.getElementById("coupons-grid");
const $total   = document.getElementById("stat-total");
const $avg     = document.getElementById("stat-avg");
const $best    = document.getElementById("stat-best");

// ── Init ──────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", async () => {
  setTodayLabel();
  setupFilters();
  await loadCoupons();
});

// ── Date header ───────────────────────────────────────────
function setTodayLabel() {
  const label = document.getElementById("today-label");
  label.textContent = new Date().toLocaleDateString("fr-FR", {
    weekday: "short", day: "numeric", month: "short"
  });
}

// ── Fetch coupons ─────────────────────────────────────────
async function loadCoupons() {
  showState("loading");

  try {
    // 1. Charger les coupons de la base de données
    const dailyRes = await fetch(`${API_BASE}/coupons`);
    if (!dailyRes.ok) throw new Error(`Erreur coupons (${dailyRes.status})`);
    const dailyData = await dailyRes.json();
    
    // 2. Charger le live de manière ISOLEEE (ne bloque pas le reste si échec)
    let liveCoupons = [];
    try {
      const liveRes = await fetch(`${API_BASE}/predictions/live`);
      if (liveRes.ok) {
        const liveData = await liveRes.json();
        liveCoupons = liveData.coupons || [];
      }
    } catch (liveErr) {
      console.warn("[app] Live indisponible ou vide :", liveErr);
    }

    // 3. Fusionner les données reçues
    const dailyCoupons = dailyData.coupons || [];
    allCoupons = [...dailyCoupons, ...liveCoupons];
    
    // 4. Mettre à jour l'IHM
    renderCoupons();
    updateStats();

  } catch (err) {
    console.error("[app] Erreur API globale :", err);
    showState("error", `Impossible de joindre l'API. (${err.message})`);
  }
}


// ── Render ────────────────────────────────────────────────
function renderCoupons() {
  const filtered = activeLeague === "all"
    ? allCoupons
    : allCoupons.filter(c => c.league === activeLeague);

  if (filtered.length === 0) {
    showState("empty");
    return;
  }

  showState("grid");
  $grid.innerHTML = filtered.map(couponCard).join("");

  // Animation des barres de confiance
  requestAnimationFrame(() => {
    document.querySelectorAll(".confidence-bar-fill").forEach(bar => {
      const w = bar.dataset.width;
      bar.style.width = w + "%";
    });
  });
}

// ── Card HTML ─────────────────────────────────────────────
function couponCard(c) {
  const confidence = Number(c.confidence_rate) || 0;
  const pct      = Math.round(confidence * 100);
  const isHigh   = pct >= 70;
  const tierClass = isHigh ? "tier-high" : "tier-mid";
  const pctClass  = isHigh ? "high"      : "mid";
  const barClass  = isHigh ? ""          : "mid";
  const flag      = LEAGUE_FLAGS[c.league] || "⚽";
  const statusText = typeof c.status === "string" ? c.status : "En attente";
  const score = Number.isFinite(Number(c.home_score)) && Number.isFinite(Number(c.away_score))
    ? ` ${Number(c.home_score)}-${Number(c.away_score)}`
    : "";

  const isLive = Boolean(c.is_live);
  const liveBadge = isLive ? `<span class="live-badge">🔴 LIVE${score}</span>` : "";

  const statusClass = {
    "En attente": "attente",
    "Gagné":      "gagne",
    "Perdu":      "perdu",
    "Annulé":     "annule",
  }[statusText] || "attente";

  return `
  <div class="coupon-card ${tierClass}">
    <div class="coupon-header">
      <span class="league-badge">${flag} ${escapeHtml(c.league || "")}</span>
      <span class="match-time">${liveBadge || escapeHtml(c.match_time || "--:--")}</span>
    </div>
  
    <div class="teams-row">
      <span class="team-name home">${escapeHtml(c.home_team || "")}</span>
      <span class="vs-label">VS</span>
      <span class="team-name away">${escapeHtml(c.away_team || "")}</span>
    </div>

    <div class="coupon-divider"></div>

    <div class="prediction-row">
      <div>
        <div class="prediction-label">Pari recommandé</div>
        <div class="prediction-type">${escapeHtml(c.prediction_type || "")}</div>
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
  return String(value).replace(/[&<>"']/g, char => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[char]));
}

// ── Stats ─────────────────────────────────────────────────
function updateStats() {
  if (allCoupons.length === 0) {
    $total.textContent = "0";
    $avg.textContent   = "—";
    $best.textContent  = "—";
    return;
  }
  const rates = allCoupons.map(c => c.confidence_rate);
  $total.textContent = allCoupons.length;
  $avg.textContent   = Math.round((rates.reduce((a,b) => a+b, 0) / rates.length) * 100) + "%";
  $best.textContent  = Math.round(Math.max(...rates) * 100) + "%";
}

// ── Filtres ───────────────────────────────────────────────
function setupFilters() {
  document.querySelectorAll(".filter-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".filter-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      activeLeague = btn.dataset.league;
      renderCoupons();
    });
  });
}

// ── UI states ─────────────────────────────────────────────
function showState(state, message = "") {
  $loading.style.display = state === "loading" ? "flex"  : "none";
  $empty.style.display   = state === "empty"   ? "block" : "none";
  $grid.style.display    = state === "grid"    ? "flex"  : "none";

  const $err = document.getElementById("error-state");
  if ($err) {
    $err.style.display = state === "error" ? "block" : "none";
    if (state === "error" && message) $err.querySelector(".error-msg").textContent = message;
  }
}

// ── Données de démo supprimées ────────────────────────────
// Le frontend affiche désormais une vraie erreur si l'API
// n'est pas disponible, au lieu de données fictives.
