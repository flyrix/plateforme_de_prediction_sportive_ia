"""
main.py
-------
API FastAPI optimisée pour Vercel & Render (Mode 100% BDD Neon / Zero ScraperAPI en Live).
"""

import os
import sys
import datetime
import asyncio

_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware

# Imports locaux
from scraper import fetch_matches_with_features, _get, BASE_URL
from predictor import generate_coupons
from db import execute, execute_batch

app = FastAPI(
    title="IA-BetPredict API",
    description="Prédictions sportives par XGBoost - Historique et Résultats",
    version="1.2.0",
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["*"],
)

@app.options("/{full_path:path}")
async def options_handler(request: Request, full_path: str):
    return Response(
        status_code=200,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
            "Access-Control-Allow-Headers": "*",
        },
    )


# ---------------------------------------------------------------------------
# Endpoints BDD (Lecture ultra-rapide)
# ---------------------------------------------------------------------------

@app.get("/", tags=["Health"])
async def root():
    return {"status": "ok", "message": "IA-BetPredict API is running"}


@app.get("/coupons", tags=["Coupons"])
async def get_todays_coupons(
    league: str | None = Query(default=None),
    status: str | None = Query(default=None),
    min_confidence: float = Query(default=0.0, ge=0, le=1),
):
    """Récupère les coupons du jour stockés en base Neon."""
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    return _fetch_coupons(today, league, status, min_confidence)


@app.get("/coupons/{date}", tags=["Coupons"])
async def get_coupons_by_date(
    date: str,
    league: str | None = Query(default=None),
    status: str | None = Query(default=None),
    min_confidence: float = Query(default=0.0, ge=0, le=1),
):
    """Récupère les coupons d'une date spécifique (ex: résultats d'hier)."""
    try:
        datetime.date.fromisoformat(date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Format de date invalide. Utilisez YYYY-MM-DD.")
    return _fetch_coupons(date, league, status, min_confidence)


def _fetch_coupons(match_date: str, league: str | None, status: str | None, min_confidence: float) -> dict:
    try:
        query = "SELECT * FROM predictions_history WHERE match_date = %s AND confidence_rate >= %s"
        params = [match_date, min_confidence]

        if league:
            query += " AND league = %s"
            params.append(league)

        if status:
            query += " AND status = %s"
            params.append(status)

        query += " ORDER BY confidence_rate DESC"

        rows = execute(query, tuple(params), fetch=True)
        return {"date": match_date, "count": len(rows), "coupons": rows}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Erreur Neon : {exc}")


# ---------------------------------------------------------------------------
# Jobs d'arrière-plan (Exécutés uniquement par Cron / GitHub Actions)
# ---------------------------------------------------------------------------

async def daily_prediction_job():
    """Génération des prédictions (exécuté par GitHub Actions)."""
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    print(f"\n[Cron] ▶ Lancement de la génération des prédictions — {today}")

    try:
        raw_matches = fetch_matches_with_features(today)
    except Exception as exc:
        print(f"[Cron] ❌ Erreur scraping : {exc}")
        raise exc

    upcoming_matches = [m for m in raw_matches if m.get("status") == "notstarted"]

    if not upcoming_matches:
        print(f"[Cron] Aucun match 'notstarted' trouvé pour {today}.")
        return

    all_coupons = []
    for match in upcoming_matches:
        try:
            coupons = generate_coupons(match)
            for c in coupons:
                # Injection garantie des métadonnées d'identification
                c["event_id"] = match.get("event_id")
                c["match_name"] = match.get("match_name") or f"{match.get('home_team')} - {match.get('away_team')}"
            all_coupons.extend(coupons)
        except Exception as exc:
            print(f"[Cron] ⚠️ Erreur prédiction {match.get('match_name')} : {exc}")

    if not all_coupons:
        print("[Cron] Aucun coupon généré.")
        return

    sql = """
        INSERT INTO predictions_history
            (match_date, match_name, league, home_team, away_team,
             match_time, prediction_type, confidence_rate, status, event_id)
        VALUES
            (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
            "En attente",
            c.get("event_id")
        ))

    try:
        inserted = execute_batch(sql, params_list)
        print(f"[Cron] ✅ {inserted} coupon(s) insérés avec succès.")
    except Exception as exc:
        print(f"[Cron] ❌ Échec transaction BDD : {exc}")
        raise exc


async def settle_finished_predictions():
    """Vérification et mise à jour du statut et du score des matchs terminés."""
    print("\n[Settler] 🔄 Mise à jour des résultats (Gagné / Perdu) et des scores...")
    
    pending = execute(
        """
        SELECT id, match_name, match_date, prediction_type, event_id, home_team, away_team 
        FROM predictions_history 
        WHERE status = 'En attente'
        """, 
        fetch=True
    )
    
    if not pending:
        print("[Settler] Aucun coupon en attente.")
        return

    from scraper import fetch_all_matches

    # Regrouper les coupons par date
    dates_to_check = set(str(p["match_date"]) for p in pending if p.get("match_date"))
    
    matches_by_date = {}
    for date_str in dates_to_check:
        try:
            matches_by_date[date_str] = fetch_all_matches(date_str)
        except Exception as err:
            print(f"[Settler] ⚠️ Erreur récupération matchs pour {date_str}: {err}")

    for p in pending:
        m_date = str(p.get("match_date"))
        matches_today = matches_by_date.get(m_date, [])
        
        p_event_id = str(p.get("event_id")) if p.get("event_id") else None
        p_name = p.get("match_name", "").strip().lower() if p.get("match_name") else ""
        p_home = p.get("home_team", "").strip().lower() if p.get("home_team") else ""
        p_away = p.get("away_team", "").strip().lower() if p.get("away_team") else ""

        # 1. Matching prioritaire par event_id
        match_info = None
        if p_event_id:
            match_info = next(
                (m for m in matches_today if str(m.get("event_id")) == p_event_id),
                None
            )

        # 2. Fallback 1: Matching par nom exact de l'affiche (match_name)
        if not match_info and p_name:
            match_info = next(
                (m for m in matches_today if m.get("match_name", "").strip().lower() == p_name),
                None
            )

        # 3. Fallback 2: Matching sous-chaîne sur home_team et away_team
        if not match_info and p_home and p_away:
            match_info = next(
                (m for m in matches_today 
                 if p_home in m.get("home_team", "").lower() 
                 and p_away in m.get("away_team", "").lower()),
                None
            )

        if not match_info:
            continue

        event_id = match_info.get("event_id")
        if not event_id:
            continue

        data = _get(f"{BASE_URL}/event/{event_id}")
        if not data or "event" not in data:
            continue

        ev = data["event"]
        status_type = ev.get("status", {}).get("type")

        if status_type in ["finished", "ended"]:
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

            formatted_score = f"{home_score} - {away_score}"

            # Mise à jour synchronisée du Statut ET du Score
            execute(
                "UPDATE predictions_history SET status = %s, score = %s WHERE id = %s",
                (status_val, formatted_score, p["id"])
            )
            print(f"[Settler] ✅ Coupon #{p['id']} ({p.get('match_name')} - {pred}) -> Statut: {status_val}, Score: {formatted_score}")


if __name__ == "__main__":
    async def run_cron():
        await daily_prediction_job()
        await settle_finished_predictions()

    asyncio.run(run_cron())