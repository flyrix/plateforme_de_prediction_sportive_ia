"""
main.py
-------
Point d'entrée FastAPI optimisé pour Vercel Serverless.
"""

import datetime
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from scraper import fetch_matches_with_features
from predictor import generate_coupons
from db import execute

app = FastAPI(
    title="IA-BetPredict API",
    description="Prédictions sportives par XGBoost sur 4 ligues d'été",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # À restreindre en production si besoin
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Le Job Quotidien (Anciennement dans scheduler.py)
# ---------------------------------------------------------------------------
async def daily_prediction_job():
    """Exécute le cycle complet : Scraping -> Prédiction -> Sauvegarde Neon"""
    today = datetime.date.today().isoformat()
    print(f"\n[Vercel Cron] ▶ Lancement du job — {today}")

    try:
        matches = fetch_matches_with_features(today)
    except Exception as exc:
        print(f"[Vercel Cron] ❌ Erreur scraping : {exc}")
        raise exc

    if not matches:
        print("[Vercel Cron] Aucun match trouvé pour aujourd'hui.")
        return

    all_coupons = []
    for match in matches:
        try:
            coupons = generate_coupons(match)
            all_coupons.extend(coupons)
        except Exception as exc:
            print(f"[Vercel Cron] ⚠️ Erreur prédiction {match['match_name']} : {exc}")

    if not all_coupons:
        print("[Vercel Cron] Aucun coupon éligible généré.")
        return

    sql = """
        INSERT INTO predictions_history
            (match_date, match_name, league, home_team, away_team,
             match_time, prediction_type, confidence_rate, status)
        VALUES
            (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    inserted = 0
    for c in all_coupons:
        try:
            execute(sql, (
                today,
                c["match_name"],
                c["league"],
                c["home_team"],
                c["away_team"],
                c.get("match_time", ""),
                c["prediction_type"],
                c["confidence_rate"],
                c["status"],
            ))
            inserted += 1
        except Exception as exc:
            print(f"[Vercel Cron] ⚠️ Erreur insertion {c['match_name']} : {exc}")

    print(f"[Vercel Cron] ✅ {inserted} coupon(s) insérés dans Neon")


# ---------------------------------------------------------------------------
# Routes / Endpoints
# ---------------------------------------------------------------------------

@app.get("/", tags=["Health"])
async def root():
    return {"status": "ok", "message": "IA-BetPredict API is running on Vercel"}


@app.get("/coupons", tags=["Coupons"])
async def get_todays_coupons(
    league: str | None = Query(default=None),
    min_confidence: float = Query(default=0.0, ge=0, le=1),
):
    today = datetime.date.today().isoformat()
    return _fetch_coupons(today, league, min_confidence)


@app.get("/coupons/{date}", tags=["Coupons"])
async def get_coupons_by_date(
    date: str,
    league: str | None = Query(default=None),
    min_confidence: float = Query(default=0.0, ge=0, le=1),
):
    """Retourne les coupons pour une date donnée au format YYYY-MM-DD."""
    try:
        datetime.date.fromisoformat(date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Format de date invalide. Utilisez YYYY-MM-DD.")
    return _fetch_coupons(date, league, min_confidence)


@app.post("/run-daily-job", tags=["Admin / Vercel Cron"])
async def run_daily_job():
    """Déclenché par le Vercel Cron toutes les nuits à 00:00 UTC"""
    try:
        await daily_prediction_job()
        return {"status": "ok", "message": "Job exécuté par Vercel avec succès"}
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
                WHERE match_date = %s AND confidence_rate >= %s AND league = %s
                ORDER BY confidence_rate DESC
            """
            rows = execute(sql, (match_date, min_confidence, league), fetch=True)
        else:
            sql = """
                SELECT * FROM predictions_history
                WHERE match_date = %s AND confidence_rate >= %s
                ORDER BY confidence_rate DESC
            """
            rows = execute(sql, (match_date, min_confidence), fetch=True)

        return {"date": match_date, "count": len(rows), "coupons": rows}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Erreur Neon : {exc}")