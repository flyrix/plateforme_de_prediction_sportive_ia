"""
scraper.py
----------
Récupère les matchs du jour sur les 4 ligues cibles via l'API interne de Sofascore,
puis calcule les features glissantes pour chaque match.

Sofascore n'a pas d'API publique officielle. On utilise les endpoints
internes (reverse-engineered) que le site web appelle lui-même.
En production, si Sofascore bloque les requêtes, passe par un proxy rotatif.
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

# IDs des tournois Sofascore pour les 4 ligues cibles
LEAGUE_IDS = {
    "Veikkausliiga":  238,   # Finlande
    "Eliteserien":    36,    # Norvège
    "MLS":            242,   # États-Unis
    "Serie A Brasil": 325,   # Brésil
}

# Nombre de matchs récents à utiliser pour les moyennes glissantes
FORM_WINDOW = 5


# ---------------------------------------------------------------------------
# Helpers HTTP
# ---------------------------------------------------------------------------

def _get(url: str, retries: int = 3) -> dict | None:
    """GET avec retry simple."""
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=10)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 429:
                # Rate limit : attendre avant de réessayer
                time.sleep(5 * (attempt + 1))
        except requests.RequestException as exc:
            print(f"[scraper] Erreur réseau ({attempt+1}/{retries}) : {exc}")
            time.sleep(2)
    return None


# ---------------------------------------------------------------------------
# Récupération des matchs du jour
# ---------------------------------------------------------------------------

def fetch_matches_for_league(league_name: str, tournament_id: int, date_str: str) -> list[dict]:
    """
    Retourne la liste des matchs pour une ligue et une date (YYYY-MM-DD).
    Utilise l'endpoint /scheduled/inverse de Sofascore qui filtre directement
    par date, évitant ainsi de rater des matchs hors de la première page next/0.
    """
    # Endpoint filtré par date (plus fiable que next/0)
    url = f"{BASE_URL}/sport/football/scheduled-events/{date_str}"
    data = _get(url)
    if not data:
        return []

    matches = []
    for event in data.get("events", []):
        # On filtre uniquement les matchs appartenant au tournoi cible
        tournament = event.get("tournament", {})
        unique_tournament = tournament.get("uniqueTournament", {})
        if unique_tournament.get("id") != tournament_id:
            continue

        ts = event.get("startTimestamp", 0)
        matches.append({
            "league":        league_name,
            "match_name":    f"{event['homeTeam']['name']} vs {event['awayTeam']['name']}",
            "home_team_id":  event["homeTeam"]["id"],
            "away_team_id":  event["awayTeam"]["id"],
            "home_team":     event["homeTeam"]["name"],
            "away_team":     event["awayTeam"]["name"],
            "match_time":    datetime.datetime.utcfromtimestamp(ts).strftime("%H:%M"),
            "event_id":      event["id"],
        })
    return matches


def fetch_all_matches(date_str: str | None = None) -> list[dict]:
    """
    Agrège les matchs du jour pour toutes les ligues cibles.
    Un seul appel HTTP vers Sofascore (endpoint par date), puis
    filtrage local par tournament_id — au lieu de 4 appels séparés.
    """
    if date_str is None:
        date_str = datetime.date.today().isoformat()

    print(f"[scraper] Récupération de tous les matchs ({date_str})…")
    url = f"{BASE_URL}/sport/football/scheduled-events/{date_str}"
    data = _get(url)
    if not data:
        print(f"[scraper] ❌ Aucune réponse de Sofascore pour le {date_str}")
        return []

    # Index inverse : tournament_id → league_name pour filtrage O(1)
    id_to_league = {v: k for k, v in LEAGUE_IDS.items()}

    all_matches = []
    for event in data.get("events", []):
        unique_tournament = event.get("tournament", {}).get("uniqueTournament", {})
        tid = unique_tournament.get("id")
        league_name = id_to_league.get(tid)
        if league_name is None:
            continue

        ts = event.get("startTimestamp", 0)
        all_matches.append({
            "league":        league_name,
            "match_name":    f"{event['homeTeam']['name']} vs {event['awayTeam']['name']}",
            "home_team_id":  event["homeTeam"]["id"],
            "away_team_id":  event["awayTeam"]["id"],
            "home_team":     event["homeTeam"]["name"],
            "away_team":     event["awayTeam"]["name"],
            "match_time":    datetime.datetime.utcfromtimestamp(ts).strftime("%H:%M"),
            "event_id":      event["id"],
        })

    print(f"[scraper] ✅ {len(all_matches)} match(s) trouvé(s) pour le {date_str}")
    return all_matches


# ---------------------------------------------------------------------------
# Calcul des features (moyennes glissantes)
# ---------------------------------------------------------------------------

def _get_team_last_n_goals(team_id: int, n: int = FORM_WINDOW) -> dict:
    """
    Récupère les N derniers matchs d'une équipe et calcule :
    - avg_scored  : moyenne de buts marqués
    - avg_conceded: moyenne de buts encaissés
    """
    url = f"{BASE_URL}/team/{team_id}/events/last/0"
    data = _get(url)
    if not data:
        return {"avg_scored": 1.2, "avg_conceded": 1.2}  # valeurs par défaut neutres

    events = data.get("events", [])[:n]
    if not events:
        return {"avg_scored": 1.2, "avg_conceded": 1.2}

    scored = []
    conceded = []
    for ev in events:
        ht_id = ev.get("homeTeam", {}).get("id")
        hs = ev.get("homeScore", {}).get("current", 0)
        aws = ev.get("awayScore", {}).get("current", 0)
        if ht_id == team_id:
            scored.append(hs)
            conceded.append(aws)
        else:
            scored.append(aws)
            conceded.append(hs)

    return {
        "avg_scored":   round(sum(scored)   / len(scored),   2),
        "avg_conceded": round(sum(conceded) / len(conceded), 2),
    }


def compute_features(match: dict) -> dict:
    """
    Calcule les 4 features attendues par les modèles XGBoost :
    - home_goals_exp  : buts marqués moyens à domicile
    - away_goals_exp  : buts marqués moyens à l'extérieur
    - diff_goals_exp  : différence home - away
    - total_goals_exp : somme des deux attaques
    """
    home_stats = _get_team_last_n_goals(match["home_team_id"])
    away_stats = _get_team_last_n_goals(match["away_team_id"])

    home_exp = home_stats["avg_scored"]
    away_exp = away_stats["avg_scored"]

    features = {
        "home_goals_exp":  home_exp,
        "away_goals_exp":  away_exp,
        "diff_goals_exp":  round(home_exp - away_exp, 2),
        "total_goals_exp": round(home_exp + away_exp, 2),
    }
    return features


def fetch_matches_with_features(date_str: str | None = None) -> list[dict]:
    """
    Point d'entrée principal : retourne les matchs du jour
    enrichis de leurs features prêtes à passer dans les modèles.
    """
    matches = fetch_all_matches(date_str)
    for match in matches:
        match["features"] = compute_features(match)
        time.sleep(0.5)
    return matches