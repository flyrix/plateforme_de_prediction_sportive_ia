"""
scraper.py
----------
Récupère les matchs du jour sur les 4 ligues cibles via l'API interne de Sofascore.
Endpoint utilisé : /unique-tournament/{id}/season/{season_id}/events/next/0
La saison active est récupérée dynamiquement au démarrage.
"""

import datetime
import time
import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.sofascore.com/",
    "Accept": "application/json",
}

BASE_URL = "https://api.sofascore.com/api/v1"

LEAGUE_IDS = {
    "Veikkausliiga":  238,
    "Eliteserien":    36,
    "MLS":            242,
    "Serie A Brasil": 325,
}

FORM_WINDOW = 5


# ---------------------------------------------------------------------------
# Helpers HTTP
# ---------------------------------------------------------------------------

def _get(url: str, retries: int = 3) -> dict | None:
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=10)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 429:
                time.sleep(5 * (attempt + 1))
        except requests.RequestException as exc:
            print(f"[scraper] Erreur réseau ({attempt+1}/{retries}) : {exc}")
            time.sleep(2)
    return None


# ---------------------------------------------------------------------------
# Saison active
# ---------------------------------------------------------------------------

def _get_current_season_id(tournament_id: int) -> int | None:
    """Retourne l'ID de la saison la plus récente pour un tournoi."""
    data = _get(f"{BASE_URL}/unique-tournament/{tournament_id}/seasons")
    if not data:
        return None
    seasons = data.get("seasons", [])
    return seasons[0]["id"] if seasons else None


# ---------------------------------------------------------------------------
# Récupération des matchs du jour
# ---------------------------------------------------------------------------

def fetch_matches_for_league(league_name: str, tournament_id: int, date_str: str) -> list[dict]:
    season_id = _get_current_season_id(tournament_id)
    if not season_id:
        print(f"[scraper] ⚠️  Saison introuvable pour {league_name}")
        return []

    # Récupère les prochains matchs (page 0 = les plus proches)
    data = _get(f"{BASE_URL}/unique-tournament/{tournament_id}/season/{season_id}/events/next/0")
    if not data:
        print(f"[scraper] ⚠️  Aucune réponse pour {league_name}")
        return []

    matches = []
    for event in data.get("events", []):
        ts = event.get("startTimestamp", 0)
        event_date = datetime.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")
        if event_date != date_str:
            continue
        matches.append({
            "league":       league_name,
            "match_name":   f"{event['homeTeam']['name']} vs {event['awayTeam']['name']}",
            "home_team_id": event["homeTeam"]["id"],
            "away_team_id": event["awayTeam"]["id"],
            "home_team":    event["homeTeam"]["name"],
            "away_team":    event["awayTeam"]["name"],
            "match_time":   datetime.datetime.utcfromtimestamp(ts).strftime("%H:%M"),
            "event_id":     event["id"],
        })
    return matches


def fetch_all_matches(date_str: str | None = None) -> list[dict]:
    if date_str is None:
        date_str = datetime.date.today().isoformat()

    print(f"[scraper] Récupération des matchs pour le {date_str}…")
    all_matches = []
    for league_name, tid in LEAGUE_IDS.items():
        matches = fetch_matches_for_league(league_name, tid, date_str)
        print(f"[scraper]   {league_name}: {len(matches)} match(s)")
        all_matches.extend(matches)
        time.sleep(0.5)

    print(f"[scraper] ✅ {len(all_matches)} match(s) total pour le {date_str}")
    return all_matches


# ---------------------------------------------------------------------------
# Calcul des features (moyennes glissantes)
# ---------------------------------------------------------------------------

def _get_team_last_n_goals(team_id: int, n: int = FORM_WINDOW) -> dict:
    data = _get(f"{BASE_URL}/team/{team_id}/events/last/0")
    if not data:
        return {"avg_scored": 1.2, "avg_conceded": 1.2}

    events = data.get("events", [])[:n]
    if not events:
        return {"avg_scored": 1.2, "avg_conceded": 1.2}

    scored, conceded = [], []
    for ev in events:
        ht_id = ev.get("homeTeam", {}).get("id")
        hs = ev.get("homeScore", {}).get("current", 0)
        aws = ev.get("awayScore", {}).get("current", 0)
        if ht_id == team_id:
            scored.append(hs); conceded.append(aws)
        else:
            scored.append(aws); conceded.append(hs)

    return {
        "avg_scored":   round(sum(scored)   / len(scored),   2),
        "avg_conceded": round(sum(conceded) / len(conceded), 2),
    }


def compute_features(match: dict) -> dict:
    home_stats = _get_team_last_n_goals(match["home_team_id"])
    away_stats = _get_team_last_n_goals(match["away_team_id"])
    home_exp = home_stats["avg_scored"]
    away_exp = away_stats["avg_scored"]
    return {
        "home_goals_exp":  home_exp,
        "away_goals_exp":  away_exp,
        "diff_goals_exp":  round(home_exp - away_exp, 2),
        "total_goals_exp": round(home_exp + away_exp, 2),
    }


def fetch_matches_with_features(date_str: str | None = None) -> list[dict]:
    matches = fetch_all_matches(date_str)
    for match in matches:
        match["features"] = compute_features(match)
        time.sleep(0.3)
    return matches
