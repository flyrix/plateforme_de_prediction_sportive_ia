/**
 * app.js — IA-BetPredict Frontend
 *
 * 1. Charge les coupons depuis l'API FastAPI
 * 2. Affiche les cartes avec jauge de confiance
 * 3. Gère les filtres par ligue
 */

// ── Config ────────────────────────────────────────────────
const RENDER_API_URL = "https://plateforme-de-prediction-sportive-ia.onrender.com";

const API_BASE = window.ENV_API_BASE || RENDER_API_URL;
console.log(`[app] API_BASE=${API_BASE}`);

// Icônes des ligues
const LEAGUE_FLAGS = {
  "Veikkausliiga":     "🇫🇮",
  "Eliteserien":       "🇳🇴",
  "MLS":               "🇺🇸",
  "USL Championship":  "🇺🇸",
  "USL League One":    "🇺🇸",
  "USL League Two":    "🇺🇸",
  "NPSL":              "🇺🇸",
  "NPSL Founders Cup": "🇺🇸",
  "Serie A Brasil":    "🇧🇷",
  "Club Friendlies":   "🤝",
};

// ── State ─────────────────────────────────────────────────
let allCoupons   = [];
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
  if (label) {
    label.textContent = new Date().toLocaleDateString("fr-FR", {
      weekday: "short", day: "numeric", month: "short"
    });
  }
}

// ── Fetch coupons ─────────────────────────────────────────
async function loadCoupons() {
  showState("loading");

  try {
    // 1. Charger les coupons de la base de données Neon
    const dailyRes = await fetch(`${API_BASE}/coupons`);
    if (!dailyRes.ok) throw new Error(`Erreur coupons (${dailyRes.status})`);
    const dailyData = await dailyRes.json();
    const dailyCoupons = dailyData.coupons || [];

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

    // 3. Fusionner et dédoublonner (si un match passe en live, la version Live prend le dessus)
    const couponsMap = new Map();

    // Insérer d'abord les coupons du jour
    dailyCoupons.forEach(c => {
      const key = `${c.match_name}_${c.prediction_type}`;
      couponsMap.set(key, c);
    });

    // Écraser ou ajouter avec les matchs Live
    liveCoupons.forEach(c => {
      const key = `${c.match_name}_${c.prediction_type}`;
      couponsMap.set(key, c);
    });

    allCoupons = Array.from(couponsMap.values());
    
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

  // Animation fluide des barres de confiance
  requestAnimationFrame(() => {
    document.querySelectorAll(".confidence-bar-fill").forEach(bar => {
      const w = bar.dataset.width;
      bar.style.width = w + "%";
    });
  });
}

// ── Helper pour extraire la confiance (0.0 à 1.0) ───────
function getConfidenceRate(c) {
  const raw = c.confidence_rate !== undefined ? c.confidence_rate : c.confidence;
  const num = Number(raw) || 0;
  // Si déjà en pourcentage (ex: 85), ramener entre 0 et 1
  return num > 1 ? num / 100 : num;
}

// ── Card HTML ─────────────────────────────────────────────
function couponCard(c) {
  const confidence = getConfidenceRate(c);
  const pct        = Math.round(confidence * 100);
  const isHigh     = pct >= 70;
  const tierClass  = isHigh ? "tier-high" : "tier-mid";
  const pctClass   = isHigh ? "high"      : "mid";
  const barClass   = isHigh ? ""          : "mid";
  const flag       = LEAGUE_FLAGS[c.league] || "⚽";
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

// ── Stats ─────────────────────────────────────────────────
function updateStats() {
  if (allCoupons.length === 0) {
    if ($total) $total.textContent = "0";
    if ($avg)   $avg.textContent   = "—";
    if ($best)  $best.textContent  = "—";
    return;
  }
  
  // Extraction sécurisée des taux de confiance sous forme numérique
  const rates = allCoupons.map(c => getConfidenceRate(c));
  
  const sum  = rates.reduce((acc, curr) => acc + curr, 0);
  const max  = Math.max(...rates);
  const avg  = sum / rates.length;

  if ($total) $total.textContent = allCoupons.length;
  if ($avg)   $avg.textContent   = Math.round(avg * 100) + "%";
  if ($best)  $best.textContent  = Math.round(max * 100) + "%";
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
  if ($loading) $loading.style.display = state === "loading" ? "flex"  : "none";
  if ($empty)   $empty.style.display   = state === "empty"   ? "block" : "none";
  if ($grid)    $grid.style.display    = state === "grid"    ? "grid"  : "none"; // Changé en "grid" si la mise en page CSS utilise un grid

  const $err = document.getElementById("error-state");
  if ($err) {
    $err.style.display = state === "error" ? "block" : "none";
    if (state === "error" && message) {
      const msgEl = $err.querySelector(".error-msg");
      if (msgEl) msgEl.textContent = message;
    }
  }
}