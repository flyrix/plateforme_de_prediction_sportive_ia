"""
main.py
-------
API FastAPI optimisée pour Vercel & Render (Mode 100% BDD Neon / Zero ScraperAPI en Live).
Gestion de la valeur attendue (EV), sélection dynamique des cotes et dépouillement universel.
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
    description="Prédictions sportives par XGBoost - EV & Historique de Value Bets",
    version="1.4.1",
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
# Fonction utilitaire d'arbitrage Double Chance
# ---------------------------------------------------------------------------

def filter_best_double_chance(coupons: list[dict]) -> list[dict]:
    """
    Conserve uniquement la meilleure Double Chance par match (ex: garde 1X a 80% et rejette 2X a 77%).
    Les autres types de paris sont conservés sans modification.
    """
    dc_candidates: dict[str, dict] = {}
    filtered_coupons: list[dict] = []

    for c in coupons:
        pred_type = str(c.get("prediction_type", "")).strip().upper()

        # Detection des paris de type Double Chance
        is_dc = any(dc in pred_type for dc in ["DOUBLE CHANCE", "1X", "X2", "2X", "12"])

        if is_dc:
            match_key = str(c.get("event_id") or c.get("match_name"))
            confidence = float(c.get("confidence_rate", 0))

            # Si pas encore enregistre ou si la confiance est supérieure, on remplace
            if match_key not in dc_candidates or confidence > float(dc_candidates[match_key].get("confidence_rate", 0)):
                dc_candidates[match_key] = c
        else:
            filtered_coupons.append(c)

    # Ajout des meilleures Double Chance sélectionnées
    filtered_coupons.extend(dc_candidates.values())
    return filtered_coupons


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
    min_ev: float = Query(default=0.0, description="Filtrer par EV minimum (ex: 0.05 pour +5%)"),
):
    """Récupère les coupons du jour stockés en base Neon."""
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    return _fetch_coupons(today, league, status, min_confidence, min_ev)


@app.get("/coupons/{date}", tags=["Coupons"])
async def get_coupons_by_date(
    date: str,
    league: str | None = Query(default=None),
    status: str | None = Query(default=None),
    min_confidence: float = Query(default=0.0, ge=0, le=1),
    min_ev: float = Query(default=0.0, description="Filtrer par EV minimum (ex: 0.05 pour +5%)"),
):
    """Récupère les coupons d'une date spécifique (ex: résultats d'hier)."""
    try:
        datetime.date.fromisoformat(date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Format de date invalide. Utilisez YYYY-MM-DD.")
    return _fetch_coupons(date, league, status, min_confidence, min_ev)


def _fetch_coupons(match_date: str, league: str | None, status: str | None, min_confidence: float, min_ev: float) -> dict:
    try:
        query = "SELECT * FROM predictions_history WHERE match_date = %s AND confidence_rate >= %s AND expected_value >= %s"
        params = [match_date, min_confidence, min_ev]

        if league:
            query += " AND league = %s"
            params.append(league)

        if status:
            query += " AND status = %s"
            params.append(status)

        query += " ORDER BY expected_value DESC, confidence_rate DESC"

        rows = execute(query, tuple(params), fetch=True)
        return {"date": match_date, "count": len(rows), "coupons": rows}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Erreur Neon : {exc}")


# ---------------------------------------------------------------------------
# Dépouillement (Settler)
# ---------------------------------------------------------------------------

def evaluate_prediction(pred_type: str, home_score: int, away_score: int) -> str:
    """Évalue si une prédiction est Gagnée ou Perdue selon le score final."""
    pred = pred_type.strip()
    total_goals = home_score + away_score
    btts = home_score > 0 and away_score > 0

    # Résultat 1N2
    if pred in ["Victoire Domicile", "1", "Home"] and home_score > away_score:
        return "Gagné"
    if pred in ["Match Nul", "X", "Draw"] and home_score == away_score:
        return "Gagné"
    if pred in ["Victoire Extérieur", "2", "Away"] and away_score > home_score:
        return "Gagné"

    # Double Chance
    if pred in ["Double Chance 1X", "1X"] and home_score >= away_score:
        return "Gagné"
    if pred in ["Double Chance X2", "2X", "X2"] and away_score >= home_score:
        return "Gagné"
    if pred in ["Double Chance 12", "12"] and home_score != away_score:
        return "Gagné"

    # Over / Under
    if pred in ["Over 2.5", "+2.5 Buts"] and total_goals > 2.5:
        return "Gagné"
    if pred in ["Under 2.5", "-2.5 Buts"] and total_goals < 2.5:
        return "Gagné"
    if pred in ["Over 1.5", "+1.5 Buts"] and total_goals > 1.5:
        return "Gagné"
    if pred in ["Under 1.5", "-1.5 Buts"] and total_goals < 1.5:
        return "Gagné"

    # BTTS
    if pred in ["BTTS", "Les deux équipes marquant", "BTTS Oui"] and btts:
        return "Gagné"
    if pred in ["BTTS Non", "Les deux équipes ne marquant pas"] and not btts:
        return "Gagné"

    return "Perdu"


# ---------------------------------------------------------------------------
# Jobs d'arrière-plan (Exécutés uniquement par Cron / GitHub Actions)
# ---------------------------------------------------------------------------

async def daily_prediction_job():
    """Génération des prédictions (exécuté par GitHub Actions)."""
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    print(f"\n[Cron] ▶ Lancement de la génération des prédictions + Calcul EV — {today}")

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
    MIN_CONFIDENCE = 0.50  # Probabilité minimum du modèle (50%)
    MIN_EV = 0.02          # EV minimum exigé (+2% de valeur)

    for match in upcoming_matches:
        try:
            # require_value_bet=True : calcule l'EV si les cotes sont présentes
            coupons = generate_coupons(match, require_value_bet=True)
            
            # --- FILTRAGE ET ARBITRAGE DOUBLE CHANCE PAR MATCH ---
            coupons = filter_best_double_chance(coupons)

            for c in coupons:
                confidence = float(c.get("confidence_rate", 0))
                odds = float(c.get("odds", 1.0))
                
                # Calcul dynamique de l'EV si pas déjà calculé dans predictor.py
                ev = float(c.get("ev", (confidence * odds) - 1.0)) if odds > 1.0 else 0.0

                # On applique le filtre de valeur (EV) & de confiance
                if confidence >= MIN_CONFIDENCE and ev >= MIN_EV:
                    c["event_id"] = match.get("event_id")
                    c["match_name"] = match.get("match_name") or f"{match.get('home_team')} - {match.get('away_team')}"
                    c["odds"] = odds
                    c["ev"] = round(ev, 4)
                    all_coupons.append(c)
        except Exception as exc:
            print(f"[Cron] ⚠️ Erreur prédiction {match.get('match_name')} : {exc}")

    if not all_coupons:
        print("[Cron] Aucun Value Bet (EV >= +2%) trouvé pour aujourd'hui.")
        return

    # Inscription BDD avec mise à jour des cotes et EV
    sql = """
        INSERT INTO predictions_history
            (match_date, match_name, league, home_team, away_team,
             match_time, prediction_type, confidence_rate, status, event_id, odds, expected_value)
        VALUES
            (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (match_date, match_name, prediction_type) DO UPDATE SET
            odds = EXCLUDED.odds,
            expected_value = EXCLUDED.expected_value,
            confidence_rate = EXCLUDED.confidence_rate;
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
            c.get("event_id"),
            c.get("odds", 1.0),
            c.get("ev", 0.0)
        ))

    try:
        inserted = execute_batch(sql, params_list)
        print(f"[Cron] ✅ {inserted} Value Bet(s) insérés/mis à jour avec succès.")
    except Exception as exc:
        print(f"[Cron] ❌ Échec transaction BDD : {exc}")
        raise exc


async def settle_finished_predictions():
    """Vérification et mise à jour directe du statut et du score des matchs terminés."""
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

    # Indexation par date pour les coupons qui N'ONT PAS d'event_id
    dates_without_id = set(str(p["match_date"]) for p in pending if not p.get("event_id") and p.get("match_date"))
    matches_by_date = {}
    
    for d in dates_without_id:
        try:
            matches_by_date[d] = fetch_all_matches(d)
        except Exception as err:
            print(f"[Settler] ⚠️ Erreur récap date {d}: {err}")

    for p in pending:
        event_id = p.get("event_id")
        m_date = str(p.get("match_date"))
        
        # 1. Tente de retrouver l'event_id manquant via le nom des équipes
        if not event_id and m_date in matches_by_date:
            p_name = p.get("match_name", "").strip().lower()
            p_home = p.get("home_team", "").strip().lower()
            p_away = p.get("away_team", "").strip().lower()
            
            matches_today = matches_by_date[m_date]
            
            found = next(
                (m for m in matches_today if m.get("match_name", "").strip().lower() == p_name),
                None
            )
            if not found and p_home and p_away:
                found = next(
                    (m for m in matches_today 
                     if p_home in m.get("home_team", "").lower() 
                     and p_away in m.get("away_team", "").lower()),
                    None
                )
            
            if found:
                event_id = found.get("event_id")
                execute("UPDATE predictions_history SET event_id = %s WHERE id = %s", (event_id, p["id"]))

        if not event_id:
            continue

        # 2. Interrogation de l'API Sofascore
        try:
            data = _get(f"{BASE_URL}/event/{event_id}")
        except Exception as err:
            print(f"[Settler] ⚠️ Erreur API pour l'événement #{event_id}: {err}")
            continue

        if not data or "event" not in data:
            continue

        ev = data["event"]
        status_type = ev.get("status", {}).get("type")

        # 3. Traitement si le match est terminé
        if status_type in ["finished", "ended"]:
            home_score = ev.get("homeScore", {}).get("current", 0) or 0
            away_score = ev.get("awayScore", {}).get("current", 0) or 0
            
            status_val = evaluate_prediction(p["prediction_type"], home_score, away_score)
            formatted_score = f"{home_score} - {away_score}"

            execute(
                "UPDATE predictions_history SET status = %s, score = %s, event_id = %s WHERE id = %s",
                (status_val, formatted_score, str(event_id), p["id"])
            )
            print(f"[Settler] ✅ Coupon #{p['id']} ({p.get('match_name')} - {p['prediction_type']}) -> Statut: {status_val}, Score: {formatted_score}")

        # 4. Traitement des matchs annulés ou reportés
        elif status_type in ["canceled", "postponed", "interrupted"]:
            execute(
                "UPDATE predictions_history SET status = 'Annulé' WHERE id = %s",
                (p["id"],)
            )
            print(f"[Settler] ⚠️ Coupon #{p['id']} ({p.get('match_name')}) -> Match Annulé/Reporté")


if __name__ == "__main__":
    async def run_cron():
        await daily_prediction_job()
        await settle_finished_predictions()

    asyncio.run(run_cron())