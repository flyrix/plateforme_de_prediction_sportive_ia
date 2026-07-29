"""
main.py
-------
Point d'entrée FastAPI optimisé pour Vercel Serverless & GitHub Actions.
"""

import os
import sys
import datetime

_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from fastapi import FastAPI, HTTPException, Query, Header
from fastapi.middleware.cors import CORSMiddleware

# Imports locaux
from scraper import fetch_matches_with_features, fetch_all_matches, fetch_inplay_matches, compute_features
from predictor import generate_coupons
from db import execute, execute_batch


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
# Job Quotidien (GitHub Actions)
# ---------------------------------------------------------------------------

async def daily_prediction_job():
    """Exécute le cycle complet : Scraping -> Prédiction sur MATCHS FUTURS -> Sauvegarde Neon"""
    today = datetime.date.today().isoformat()
    print(f"\n[GitHub Actions] ▶ Lancement du job quotidien — {today}")

    try:
        raw_matches = fetch_matches_with_features(today)
    except Exception as exc:
        print(f"[GitHub Actions] ❌ Erreur scraping : {exc}")
        raise exc

    # 💡 1. FILTRE STRICT : On élimine les matchs déjà commencés (Live)
    upcoming_matches = [m for m in raw_matches if not m.get("is_live")]

    if not upcoming_matches:
        print("[GitHub Actions] Aucun match programmé (non démarré) trouvé pour aujourd'hui.")
        return

    print(f"[GitHub Actions] 🎯 {len(upcoming_matches)} match(s) futur(s) retenu(s) pour prédiction.")

    all_coupons = []
    for match in upcoming_matches:
        try:
            coupons = generate_coupons(match)
            all_coupons.extend(coupons)
        except Exception as exc:
            print(f"[GitHub Actions] ⚠️ Erreur prédiction {match.get('match_name')} : {exc}")

    if not all_coupons:
        print("[GitHub Actions] Aucun coupon éligible généré.")
        return

    # 💡 2. Préparation des tuples pour insertion dans Neon
    sql = """
        INSERT INTO predictions_history
            (match_date, match_name, league, home_team, away_team,
             match_time, prediction_type, confidence_rate, status)
        VALUES
            (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (match_date, match_name, prediction_type) DO NOTHING;
    """
    
    params_list = []
    for c in all_coupons:
        confidence = round(float(c["confidence_rate"]), 4)
        status_val = c.get("status", "En attente")
        if status_val not in ['En attente', 'Gagné', 'Perdu', 'Annulé']:
            status_val = 'En attente'
            
        params_list.append((
            today,
            c["match_name"],
            c["league"],
            c["home_team"],
            c["away_team"],
            c.get("match_time", ""),
            c["prediction_type"],
            confidence,
            status_val
        ))

    # 💡 3. Insertion en 1 seule transaction
    try:
        inserted = execute_batch(sql, params_list)
        print(f"[GitHub Actions] ✅ {inserted} coupon(s) insérés avec succès dans Neon.")
    except Exception as exc:
        print(f"[GitHub Actions] ❌ Échec de la transaction BDD Neon : {exc}")
        raise exc


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
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/", tags=["Health"])
async def root():
    return {"status": "ok", "message": "IA-BetPredict API is running"}


@app.get("/coupons", tags=["Coupons"])
async def get_todays_coupons(
    league: str | None = Query(default=None),
    min_confidence: float = Query(default=0.0, ge=0, le=1),
):
    """C'est cet endpoint que ton frontend doit appeler pour afficher les coupons programmés du jour !"""
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


@app.get("/predictions/live", tags=["Predictions"])
async def get_live_predictions():
    """Endpoint secondaire uniquement pour la section 'Matchs en direct' de ton site."""
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