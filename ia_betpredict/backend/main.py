"""
main.py
-------
Point d'entrée FastAPI pour IA-BetPredict.

Endpoints :
  GET  /                        → healthcheck
  GET  /coupons                 → coupons du jour depuis Neon
  GET  /coupons/{date}          → coupons d'une date (YYYY-MM-DD)
  POST /run-daily-job           → déclenche le job manuellement (dev)
"""

from contextlib import asynccontextmanager
from datetime import date

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from scheduler import create_scheduler, daily_prediction_job
from db import execute


# ---------------------------------------------------------------------------
# Cycle de vie : démarrage / arrêt du scheduler
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = create_scheduler()
    scheduler.start()
    print("[main] ✅ Scheduler démarré — job quotidien programmé à 00:00 UTC")
    yield
    scheduler.shutdown()
    print("[main] Scheduler arrêté.")


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="IA-BetPredict API",
    description="Prédictions sportives par XGBoost sur 4 ligues d'été",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # En prod, restreins à ton domaine frontend
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/", tags=["Health"])
async def root():
    return {"status": "ok", "message": "IA-BetPredict API is running"}


@app.get("/coupons", tags=["Coupons"])
async def get_todays_coupons(
    league: str | None = Query(default=None, description="Filtre par ligue"),
    min_confidence: float = Query(default=0.0, ge=0, le=1, description="Confiance minimale"),
):
    """Retourne les coupons du jour triés par confiance décroissante."""
    today = date.today().isoformat()
    return _fetch_coupons(today, league, min_confidence)


@app.get("/coupons/{match_date}", tags=["Coupons"])
async def get_coupons_by_date(
    match_date: str,
    league: str | None = Query(default=None),
    min_confidence: float = Query(default=0.0, ge=0, le=1),
):
    """Retourne les coupons pour une date spécifique (YYYY-MM-DD)."""
    try:
        date.fromisoformat(match_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Format de date invalide. Utilise YYYY-MM-DD.")
    return _fetch_coupons(match_date, league, min_confidence)


@app.post("/run-daily-job", tags=["Admin"])
async def run_daily_job():
    """
    Déclenche manuellement le job de scraping + prédiction.
    Utile en développement pour tester sans attendre minuit.
    """
    try:
        await daily_prediction_job()
        return {"status": "ok", "message": "Job exécuté avec succès"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fetch_coupons(match_date: str, league: str | None, min_confidence: float) -> dict:
    try:
        if league:
            sql = """
                SELECT * FROM predictions_history
                WHERE match_date = %s
                  AND confidence_rate >= %s
                  AND league = %s
                ORDER BY confidence_rate DESC
            """
            rows = execute(sql, (match_date, min_confidence, league), fetch=True)
        else:
            sql = """
                SELECT * FROM predictions_history
                WHERE match_date = %s
                  AND confidence_rate >= %s
                ORDER BY confidence_rate DESC
            """
            rows = execute(sql, (match_date, min_confidence), fetch=True)

        return {"date": match_date, "count": len(rows), "coupons": rows}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Erreur base de données : {exc}")