/**
 * app.js — IA-BetPredict Frontend
 */

const RENDER_API_URL = "https://plateforme-de-prediction-sportive-ia.onrender.com";
const API_BASE = window.ENV_API_BASE || RENDER_API_URL;
console.log(`[app] API_BASE=${API_BASE}`);

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

let allCoupons   = [];
let activeLeague = "all";

const $loading = document.getElementById("loading");
const $empty   = document.getElementById("empty");
const $grid    = document.getElementById("coupons-grid");
const $total   = document.getElementById("stat-total");
const $avg     = document.getElementById("stat-avg");
const $best    = document.getElementById("stat-best");

document.addEventListener("DOMContentLoaded", async () => {
  setTodayLabel();
  setupFilters();
  await loadCoupons();
});

// ── Fetch avec Retry & Timeout corrigé ────────────────────
async function fetchWithRetry(url, options = {}, retries = 3, backoff = 2000, timeoutMs = 35000) {
  const controller = new AbortController();
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

    // Si on a encore des retries, on retente MEME en cas d'AbortError (Timeout Render)
    if (retries > 0) {
      const isTimeout = err.name === 'AbortError';
      console.warn(`[app] ${isTimeout ? 'Timeout dépassé' : 'Erreur réseau'}. Nouvelle tentative (${retries} restante(s))...`);
      
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

// ── Load Coupons ──────────────────────────────────────────
async function loadCoupons() {
  showState("loading");

  try {
    // Premier appel vers /coupons (BDD Neon) avec 35s de timeout
    const dailyData = await fetchWithRetry(`${API_BASE}/coupons`, {}, 3, 2000, 35000);
    const dailyCoupons = dailyData.coupons || [];

    allCoupons = [...dailyCoupons];
    renderCoupons();
    updateStats();

  } catch (err) {
    console.error("[app] Erreur lors du chargement BDD Neon :", err);
    showState("error", `Le serveur Render met du temps à démarrer. Veuillez rafraîchir dans quelques secondes.`);
    return;
  }

  // Chargement du Live en arrière-plan
  fetchLiveBackground();
}

async function fetchLiveBackground() {
  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 15000);

    const liveRes = await fetch(`${API_BASE}/predictions/live`, {
      signal: controller.signal,
      headers: { 'Accept': 'application/json' }
    });

    clearTimeout(timeoutId);

    if (liveRes.ok) {
      const liveData = await liveRes.json();
      const liveCoupons = liveData.coupons || [];

      if (liveCoupons.length > 0) {
        const couponsMap = new Map();

        allCoupons.forEach(c => {
          const key = `${c.match_name}_${c.prediction_type}`;
          couponsMap.set(key, c);
        });

        liveCoupons.forEach(c => {
          const key = `${c.match_name}_${c.prediction_type}`;
          couponsMap.set(key, c);
        });

        allCoupons = Array.from(couponsMap.values());
        renderCoupons();
        updateStats();
      }
    }
  } catch (liveErr) {
    console.warn("[app] Live ignoré ou trop lent :", liveErr.message);
  }
}

// ── Affichage & Helpers ───────────────────────────────────
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