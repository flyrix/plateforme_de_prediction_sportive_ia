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
from typing import Any

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
    version="1.5.0",
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

def filter_best_double_chance(coupons: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Conserve uniquement la meilleure Double Chance par match (ex: garde 1X à 80% et rejette 2X à 77%).
    Les autres types de paris sont conservés sans modification.
    """
    dc_candidates: dict[str, dict[str, Any]] = {}
    filtered_coupons: list[dict[str, Any]] = []

    for c in coupons:
        pred_type = str(c.get("prediction_type", "")).strip().upper()

        # Détection des paris de type Double Chance
        is_dc = any(dc in pred_type for dc in ["DOUBLE CHANCE", "1X", "X2", "2X", "12"])

        if is_dc:
            match_key = str(c.get("event_id") or c.get("match_name"))
            confidence = float(c.get("confidence_rate", 0))

            # Si pas encore enregistré ou si la confiance est supérieure, on remplace
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
    league: str | None = Query(default=None, description="Filtrer par ligue"),
    status: str | None = Query(default=None, description="Statut : 'En attente', 'Gagné', 'Perdu'"),
    min_confidence: float = Query(default=0.50, ge=0.0, le=1.0, description="Confiance min (ex: 0.50)"),
    min_ev: float | None = Query(default=None, description="EV min (ex: 0.02 pour +2%)"),
):
    """
    Renvoie les coupons du jour. 
    Si la BDD ne contient aucun coupon aujourd'hui, renvoie automatiquement
    ceux de la journée la plus récente disponible.
    """
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    return _fetch_coupons(
        target_date=today,
        league=league,
        status=status,
        min_confidence=min_confidence,
        min_ev=min_ev
    )


@app.get("/coupons/{date}", tags=["Coupons"])
async def get_coupons_by_date(
    date: str,
    league: str | None = Query(default=None),
    status: str | None = Query(default=None),
    min_confidence: float = Query(default=0.0, ge=0.0, le=1.0),
    min_ev: float | None = Query(default=None, description="Filtrer par EV minimum"),
):
    """Récupère les coupons d'une date spécifique (ex: résultats d'hier)."""
    try:
        datetime.date.fromisoformat(date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Format de date invalide. Utilisez YYYY-MM-DD.")
    return _fetch_coupons(
        target_date=date,
        league=league,
        status=status,
        min_confidence=min_confidence,
        min_ev=min_ev,
        allow_fallback=False
    )


def _fetch_coupons(
    target_date: str, 
    league: str | None = None, 
    status: str | None = None, 
    min_confidence: float = 0.50, 
    min_ev: float | None = None,
    allow_fallback: bool = True
) -> dict[str, Any]:
    """
    Récupère les coupons en BDD.
    Si aucun coupon n'est trouvé pour 'target_date' et que allow_fallback=True,
    bascule automatiquement sur les coupons de la dernière date disponible.
    """
    try:
        base_conditions = ["confidence_rate >= %s"]
        params: list[Any] = [min_confidence]

        if min_ev is not None:
            base_conditions.append("expected_value >= %s")
            params.append(min_ev)
        if league:
            base_conditions.append("league ILIKE %s")
            params.append(f"%{league}%")
        if status:
            base_conditions.append("status = %s")
            params.append(status)

        where_clause = " AND ".join(base_conditions)

        # 1. Tentative pour la date cible
        query_date = f"""
            SELECT * FROM predictions_history 
            WHERE match_date = %s AND {where_clause}
            ORDER BY expected_value DESC, confidence_rate DESC
        """
        rows = execute(query_date, tuple([target_date] + params), fetch=True) or []

        is_fallback = False
        effective_date = target_date

        # 2. Fallback si aucun coupon aujourd'hui
        if not rows and allow_fallback:
            latest_date_query = f"""
                SELECT match_date FROM predictions_history 
                WHERE {where_clause}
                ORDER BY match_date DESC 
                LIMIT 1
            """
            latest_date_res = execute(latest_date_query, tuple(params), fetch=True)

            if latest_date_res:
                raw_date = latest_date_res[0]["match_date"]
                effective_date = raw_date.strftime("%Y-%m-%d") if isinstance(raw_date, (datetime.date, datetime.datetime)) else str(raw_date)
                is_fallback = True
                rows = execute(query_date, tuple([effective_date] + params), fetch=True) or []

        return {
            "requested_date": target_date,
            "effective_date": effective_date,
            "is_fallback": is_fallback,
            "count": len(rows),
            "coupons": rows
        }
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

    for match in upcoming_matches:
        try:
            # require_value_bet=False : génère le coupon MÊME SI la cote est manquante
            coupons = generate_coupons(match, require_value_bet=False)
            
            # --- FILTRAGE ET ARBITRAGE DOUBLE CHANCE PAR MATCH ---
            coupons = filter_best_double_chance(coupons)

            for c in coupons:
                confidence = float(c.get("confidence_rate", 0))
                odds = float(c.get("odds", 1.0))
                
                # Calcul de l'EV (sera 0.0 si cote manquante ou = 1.0)
                ev = float(c.get("ev", (confidence * odds) - 1.0)) if odds > 1.0 else 0.0

                # On insère dès que la confiance est >= 50%
                if confidence >= MIN_CONFIDENCE:
                    c["event_id"] = match.get("event_id")
                    c["match_name"] = match.get("match_name") or f"{match.get('home_team')} - {match.get('away_team')}"
                    c["odds"] = odds
                    c["ev"] = round(ev, 4)
                    all_coupons.append(c)
        except Exception as exc:
            print(f"[Cron] ⚠️ Erreur prédiction {match.get('match_name')} : {exc}")

    if not all_coupons:
        print("[Cron] Aucun coupon généré pour aujourd'hui.")
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
            "En attente",  # ✅ Force la valeur autorisée par la contrainte PostgreSQL
            c.get("event_id"),
            c.get("odds", 1.0),
            c.get("ev", 0.0)
        ))
        
    try:
        inserted = execute_batch(sql, params_list)
        print(f"[Cron] ✅ {inserted} Coupon(s) insérés/mis à jour avec succès.")
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
    ) or []
    
    if not pending:
        print("[Settler] Aucun coupon en attente.")
        return

    from scraper import fetch_all_matches

    # Indexation par date pour les coupons qui N'ONT PAS d'event_id
    dates_without_id = {
        str(p["match_date"]) for p in pending 
        if not p.get("event_id") and p.get("match_date")
    }
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