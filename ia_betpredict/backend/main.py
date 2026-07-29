"""
main.py
-------
Point d'entrée FastAPI optimisé pour Vercel Serverless & GitHub Actions.
"""

import os
import sys
import datetime
import asyncio

_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

# Imports locaux
from scraper import fetch_matches_with_features, _get, BASE_URL, fetch_inplay_matches, compute_features
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
# Job Quotidien et Settler
# ---------------------------------------------------------------------------

async def daily_prediction_job():
    """Exécute le cycle complet : Scraping -> Prédiction sur MATCHS FUTURS -> Sauvegarde Neon"""
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    print(f"\n[GitHub Actions] ▶ Lancement du job quotidien — {today}")

    try:
        raw_matches = fetch_matches_with_features(today)
    except Exception as exc:
        print(f"[GitHub Actions] ❌ Erreur scraping : {exc}")
        raise exc

    # Matchs réellement non démarrés
    upcoming_matches = [m for m in raw_matches if m.get("status") == "notstarted"]

    if not upcoming_matches:
        print(f"[GitHub Actions] Aucun match programmé ('notstarted') trouvé pour aujourd'hui ({today}).")
        return

    print(f"[GitHub Actions] 🎯 {len(upcoming_matches)} match(s) futur(s) retenu(s) pour prédiction.")

    all_coupons = []
    for match in upcoming_matches:
        try:
            coupons = generate_coupons(match)
            for c in coupons:
                c["event_id"] = match["event_id"]
            all_coupons.extend(coupons)
        except Exception as exc:
            print(f"[GitHub Actions] ⚠️ Erreur prédiction {match.get('match_name')} : {exc}")

    if not all_coupons:
        print("[GitHub Actions] Aucun coupon éligible généré par le modèle.")
        return

    # Respect strict du schéma predictions_history (UUID auto-généré par PostgreSQL)
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
        params_list.append((
            today,
            c["match_name"],
            c["league"],
            c["home_team"],
            c["away_team"],
            c.get("match_time", ""),
            c["prediction_type"],
            confidence,
            "En attente"
        ))

    try:
        inserted = execute_batch(sql, params_list)
        print(f"[GitHub Actions] ✅ {inserted} coupon(s) insérés avec succès dans Neon.")
    except Exception as exc:
        print(f"[GitHub Actions] ❌ Échec de la transaction BDD Neon : {exc}")
        raise exc


async def settle_finished_predictions():
    """Parcourt les coupons en attente en BDD et met à jour leur statut ('Gagné' / 'Perdu')."""
    print("\n[Settler] 🔄 Vérification des coupons en attente...")
    
    # Récupération des prédictions en attente
    pending = execute(
        "SELECT id, match_name, match_date, prediction_type FROM predictions_history WHERE status = 'En attente'", 
        fetch=True
    )
    
    if not pending:
        print("[Settler] Aucun coupon en attente de résultat.")
        return

    # Scraping des matchs du jour pour vérifier les résultats terminés
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    from scraper import fetch_all_matches
    matches_today = fetch_all_matches(today)
    
    # Création d'un dictionnaire par nom de match pour un accès rapide
    match_dict = {m["match_name"]: m for m in matches_today if m.get("status") in ["finished", "ended"]}

    for p in pending:
        match_info = match_dict.get(p["match_name"])
        if not match_info:
            continue

        # Récupération du détail complet de l'événement via son event_id
        event_id = match_info.get("event_id")
        if not event_id:
            continue

        data = _get(f"{BASE_URL}/event/{event_id}")
        if not data or "event" not in data:
            continue

        ev = data["event"]
        status_type = ev.get("status", {}).get("type")

        if status_type == "finished":
            home_score = ev.get("homeScore", {}).get("current", 0) or 0
            away_score = ev.get("awayScore", {}).get("current", 0) or 0
            pred = p["prediction_type"]

            total_goals = home_score + away_score
            btts = home_score > 0 and away_score > 0
            
            status_val = "Perdu"
            if pred == "Double Chance 1X" and home_score >= away_score:
                status_val = "Gagné"
            elif pred == "Double Chance X2" and away_score >= home_score:
                status_val = "Gagné"
            elif pred == "Over 2.5" and total_goals > 2.5:
                status_val = "Gagné"
            elif pred == "BTTS" and btts:
                status_val = "Gagné"

            execute(
                "UPDATE predictions_history SET status = %s WHERE id = %s",
                (status_val, p["id"])
            )
            print(f"[Settler] Coupon #{p['id']} ({p['match_name']} - {pred}) -> {status_val}")


# ---------------------------------------------------------------------------
# Endpoints FastAPI
# ---------------------------------------------------------------------------

@app.get("/", tags=["Health"])
async def root():
    return {"status": "ok", "message": "IA-BetPredict API is running"}


@app.get("/coupons", tags=["Coupons"])
async def get_todays_coupons(
    league: str | None = Query(default=None),
    min_confidence: float = Query(default=0.0, ge=0, le=1),
):
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
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
            
        return {
            "date": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d"), 
            "count": len(all_coupons), 
            "coupons": all_coupons
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Erreur prédiction live : {exc}")


# ---------------------------------------------------------------------------
# Exécution CLI (GitHub Actions / Local)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    async def run_cron():
        print("🚀 Lancement du cycle quotidien de prédictions...")
        await daily_prediction_job()
        await settle_finished_predictions()
        print("✅ Cycle terminé.")

    asyncio.run(run_cron())