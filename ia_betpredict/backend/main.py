"""
main.py
-------
Point d'entrée FastAPI optimisé pour Vercel Serverless & Render.
"""

import os
import sys
import datetime

# Vercel et Render exécutent ce fichier depuis la racine du projet.
_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from fastapi import FastAPI, HTTPException, Query, Header
from fastapi.middleware.cors import CORSMiddleware

# Imports locaux
from scraper import fetch_matches_with_features, fetch_all_matches, fetch_inplay_matches, compute_features
from predictor import generate_coupons
from db import execute


# ---------------------------------------------------------------------------
# Initialisation de l'application FastAPI
# ---------------------------------------------------------------------------

app = FastAPI(
    title="IA-BetPredict API",
    description="Prédictions sportives par XGBoost sur ligues cibles",
    version="1.0.0",
)

_allowed_origins = os.environ.get("ALLOWED_ORIGINS", "*")
_CORS_ORIGINS = [origin.strip() for origin in _allowed_origins.split(",") if origin.strip()]
if not _CORS_ORIGINS:
    _CORS_ORIGINS = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

_CRON_SECRET = os.environ.get("CRON_SECRET", "")
_ALLOW_UNAUTHENTICATED_ADMIN = os.environ.get("ALLOW_UNAUTHENTICATED_ADMIN", "").lower() in {
    "1", "true", "yes", "on"
}


# ---------------------------------------------------------------------------
# La fonction du Job Quotidien (Exécutée par GitHub Actions)
# ---------------------------------------------------------------------------

async def daily_prediction_job():
    """Exécute le cycle complet : Scraping -> Prédiction -> Sauvegarde Neon"""
    today = datetime.date.today().isoformat()
    print(f"\n[GitHub Actions] ▶ Lancement du job quotidien — {today}")

    try:
        matches = fetch_matches_with_features(today)
    except Exception as exc:
        print(f"[GitHub Actions] ❌ Erreur scraping : {exc}")
        raise exc

    if not matches:
        print("[GitHub Actions] Aucun match trouvé pour aujourd'hui sur les ligues cibles.")
        return

    all_coupons = []
    for match in matches:
        try:
            coupons = generate_coupons(match)
            all_coupons.extend(coupons)
        except Exception as exc:
            print(f"[GitHub Actions] ⚠️ Erreur prédiction {match.get('match_name')} : {exc}")

    if not all_coupons:
        print("[GitHub Actions] Aucun coupon éligible généré.")
        return

    sql = """
        INSERT INTO predictions_history
            (match_date, match_name, league, home_team, away_team,
             match_time, prediction_type, confidence_rate, status)
        VALUES
            (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (match_date, match_name, prediction_type) DO NOTHING
    """
    inserted = 0
    skipped = 0
    for c in all_coupons:
        try:
            rows = execute(sql, (
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
            if rows:
                inserted += 1
            else:
                skipped += 1
        except Exception as exc:
            print(f"[GitHub Actions] ⚠️ Erreur insertion BDD {c.get('match_name')} : {exc}")

    print(f"[GitHub Actions] ✅ {inserted} coupon(s) insérés, {skipped} doublon(s) ignorés.")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _verify_cron_secret(provided: str | None) -> None:
    if not _CRON_SECRET:
        if _ALLOW_UNAUTHENTICATED_ADMIN:
            return
        raise HTTPException(
            status_code=503,
            detail="CRON_SECRET non configuré : endpoint admin désactivé.",
        )
    if provided != _CRON_SECRET:
        raise HTTPException(status_code=401, detail="Non autorisé : X-Cron-Secret invalide.")


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


# ---------------------------------------------------------------------------
# Routes / Endpoints
# ---------------------------------------------------------------------------

@app.get("/", tags=["Health"])
async def root():
    return {"status": "ok", "message": "IA-BetPredict API is running"}


@app.post("/run-daily-job", tags=["Admin"])
async def run_daily_job():
    """Endpoint désactivé publiquement. Seul GitHub Actions exécute daily_prediction_job()."""
    return {
        "status": "disabled",
        "message": "Cet endpoint est désactivé sur Vercel/Render. Le job est exécuté en local/CLI par GitHub Actions."
    }


@app.get("/predictions/live", tags=["Predictions"])
async def get_live_predictions():
    try:
        matches = fetch_inplay_matches()
        all_coupons = []
        
        for match in matches:
            match["features"] = compute_features(match)
            coupons = generate_coupons(match)
            for coupon in coupons:
                coupon.update({
                    "is_live": True,
                    "live_status": match.get("live_status"),
                    "home_score": match.get("home_score"),
                    "away_score": match.get("away_score"),
                })
            all_coupons.extend(coupons)
            
        return {"date": datetime.date.today().isoformat(), "count": len(all_coupons), "coupons": all_coupons}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Erreur prédiction live : {exc}")


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
    try:
        datetime.date.fromisoformat(date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Format de date invalide. Utilisez YYYY-MM-DD.")
    return _fetch_coupons(date, league, min_confidence)


@app.patch("/coupons/{coupon_id}/status", tags=["Coupons"])
async def update_coupon_status(
    coupon_id: str,
    status: str = Query(..., pattern="^(Gagné|Perdu|En attente|Annulé)$"),
    x_cron_secret: str | None = Header(default=None),
):
    _verify_cron_secret(x_cron_secret)
    sql = "UPDATE predictions_history SET status = %s WHERE id = %s::uuid"
    updated = execute(sql, (status, coupon_id))
    if not updated:
        raise HTTPException(status_code=404, detail=f"Coupon {coupon_id} introuvable.")
    return {"status": "ok", "coupon_id": coupon_id, "new_status": status}


@app.get("/matches/today", tags=["Matches"])
async def get_todays_matches():
    today = datetime.date.today().isoformat()
    try:
        matches = fetch_all_matches(today)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Erreur scraping : {exc}")
    return {"date": today, "count": len(matches), "matches": matches}


@app.get("/matches/inplay", tags=["Matches"])
async def get_inplay_matches():
    try:
        matches = fetch_inplay_matches()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Erreur inplay scraping : {exc}")
    return {"date": datetime.date.today().isoformat(), "count": len(matches), "matches": matches}