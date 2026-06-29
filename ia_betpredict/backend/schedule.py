"""
scheduler.py
------------
Tâche planifiée : chaque jour à 00:00 UTC, scrape les matchs du jour,
génère les coupons et les persiste dans Neon (PostgreSQL).

Peut aussi être déclenché manuellement via POST /run-daily-job.
"""

import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from scraper import fetch_matches_with_features
from predictor import generate_coupons
from db import execute


# ---------------------------------------------------------------------------
# Job principal
# ---------------------------------------------------------------------------

async def daily_prediction_job():
    """
    1. Récupère les matchs du jour avec leurs features
    2. Génère les coupons pour chaque match
    3. Insère les coupons éligibles dans Neon
    """
    today = datetime.date.today().isoformat()
    print(f"\n[scheduler] ▶ Lancement du job quotidien — {today}")

    # -- Scraping --
    try:
        matches = fetch_matches_with_features(today)
    except Exception as exc:
        print(f"[scheduler] ❌ Erreur scraping : {exc}")
        return

    if not matches:
        print("[scheduler] Aucun match trouvé pour aujourd'hui.")
        return

    # -- Prédiction & filtrage --
    all_coupons = []
    for match in matches:
        try:
            coupons = generate_coupons(match)
            all_coupons.extend(coupons)
        except Exception as exc:
            print(f"[scheduler] ⚠️  Erreur prédiction {match['match_name']} : {exc}")

    print(f"[scheduler] {len(all_coupons)} coupon(s) éligible(s) généré(s)")

    if not all_coupons:
        return

    # -- Persistance Neon --
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
            print(f"[scheduler] ⚠️  Erreur insertion {c['match_name']} : {exc}")

    print(f"[scheduler] ✅ {inserted} coupon(s) insérés dans Neon")


# ---------------------------------------------------------------------------
# Scheduler APScheduler
# ---------------------------------------------------------------------------

def create_scheduler() -> AsyncIOScheduler:
    """Crée et configure le scheduler (appelé depuis main.py)."""
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        daily_prediction_job,
        trigger=CronTrigger(hour=0, minute=0),
        id="daily_predictions",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    return scheduler