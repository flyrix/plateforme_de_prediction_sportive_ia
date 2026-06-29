/**
 * app.js — IA-BetPredict Frontend
 *
 * 1. Charge les coupons depuis l'API FastAPI
 * 2. Affiche les cartes avec jauge de confiance
 * 3. Gère les filtres par ligue
 */

// ── Config ────────────────────────────────────────────────
// Change cette URL par l'URL de ton API déployée (Railway / Render)
// En développement : http://127.0.0.1:8000
const API_BASE = "http://127.0.0.1:8000";

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
  } catch (err) {
    console.error("[app] Erreur API :", err);
    // Données de démo si l'API n'est pas encore lancée
    allCoupons = getDemoCoupons();
  }

  renderCoupons();
  updateStats();
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
function showState(state) {
  $loading.style.display = state === "loading" ? "flex"  : "none";
  $empty.style.display   = state === "empty"   ? "block" : "none";
  $grid.style.display    = state === "grid"    ? "flex"  : "none";
}

// ── Données de démo (si l'API n'est pas encore lancée) ────
function getDemoCoupons() {
  return [
    { match_name: "Inter Miami vs LA Galaxy", league: "MLS", home_team: "Inter Miami", away_team: "LA Galaxy", match_time: "01:30", prediction_type: "Double Chance 1X", confidence_rate: 0.741, status: "En attente" },
    { match_name: "HJK Helsinki vs SJK", league: "Veikkausliiga", home_team: "HJK Helsinki", away_team: "SJK", match_time: "17:00", prediction_type: "Over 2.5", confidence_rate: 0.682, status: "En attente" },
    { match_name: "Flamengo vs Palmeiras", league: "Serie A Brasil", home_team: "Flamengo", away_team: "Palmeiras", match_time: "23:00", prediction_type: "BTTS", confidence_rate: 0.631, status: "En attente" },
    { match_name: "Bodø/Glimt vs Rosenborg", league: "Eliteserien", home_team: "Bodø/Glimt", away_team: "Rosenborg", match_time: "19:00", prediction_type: "Double Chance 1X", confidence_rate: 0.798, status: "En attente" },
    { match_name: "Seattle Sounders vs Portland", league: "MLS", home_team: "Seattle Sounders", away_team: "Portland Timbers", match_time: "03:00", prediction_type: "Over 2.5", confidence_rate: 0.611, status: "En attente" },
  ];
}