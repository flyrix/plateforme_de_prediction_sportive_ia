/**
 * app.js — IA-BetPredict Frontend
 *
 * 1. Charge les coupons depuis l'API FastAPI
 * 2. Affiche les cartes avec jauge de confiance
 * 3. Gère les filtres par ligue
 */

// ── Config ────────────────────────────────────────────────
// En production, définis la variable d'environnement VITE_API_BASE ou
// remplace manuellement par l'URL Railway/Render de ton API déployée.
// Ex : const API_BASE = "https://ia-betpredict.up.railway.app";
const API_BASE = window.ENV_API_BASE || "http://127.0.0.1:8000";

// Icônes des ligues
const LEAGUE_FLAGS = {
  "Veikkausliiga":  "🇫🇮",
  "Eliteserien":    "🇳🇴",
  "MLS":            "🇺🇸",
  "Serie A Brasil": "🇧🇷",
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
    const res = await fetch(`${API_BASE}/coupons`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    allCoupons = data.coupons || [];
    renderCoupons();
    updateStats();
  } catch (err) {
    console.error("[app] Erreur API :", err);
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
  const pct      = Math.round(c.confidence_rate * 100);
  const isHigh   = pct >= 70;
  const tierClass = isHigh ? "tier-high" : "tier-mid";
  const pctClass  = isHigh ? "high"      : "mid";
  const barClass  = isHigh ? ""          : "mid";
  const flag      = LEAGUE_FLAGS[c.league] || "⚽";

  const statusClass = {
    "En attente": "attente",
    "Gagné":      "gagne",
    "Perdu":      "perdu",
  }[c.status] || "attente";

  return `
  <div class="coupon-card ${tierClass}">
    <div class="coupon-header">
      <span class="league-badge">${flag} ${c.league}</span>
      <span class="match-time">${c.match_time || "--:--"}</span>
    </div>

    <div class="teams-row">
      <span class="team-name home">${c.home_team}</span>
      <span class="vs-label">VS</span>
      <span class="team-name away">${c.away_team}</span>
    </div>

    <div class="coupon-divider"></div>

    <div class="prediction-row">
      <div>
        <div class="prediction-label">Pari recommandé</div>
        <div class="prediction-type">${c.prediction_type}</div>
        <span class="status-badge ${statusClass}">${c.status}</span>
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