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
    "Veikkausliiga":     41,    # Finlande
    "Eliteserien":       20,    # Norvège
    "MLS":               242,   # États-Unis
    "Serie A Brasil":    325,   # Brésil
    "Club Friendlies":   853,   # Matchs amicaux de clubs
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
# Calcul des features enrichies
# ---------------------------------------------------------------------------

def _get_team_features(team_id: int) -> dict:
    """Calcule toutes les features d'une équipe sur ses 5 derniers matchs."""
    data = _get(f"{BASE_URL}/team/{team_id}/events/last/0")
    events = (data.get("events", []) if data else [])[:FORM_WINDOW]

    if not events:
        return {
            "avg_scored": 1.2, "avg_conceded": 1.2,
            "form_pts": 4.5,   "win_rate": 0.4,
            "btts_rate": 0.45, "over25_rate": 0.50,
            "days_since_last": 7,
        }

    scored, conceded, pts, btts_l, over25_l, dates = [], [], [], [], [], []
    for ev in events:
        ht_id = ev.get("homeTeam", {}).get("id")
        hs  = ev.get("homeScore", {}).get("current", 0) or 0
        aws = ev.get("awayScore", {}).get("current", 0) or 0
        ts  = ev.get("startTimestamp", 0)
        gf, ga = (hs, aws) if ht_id == team_id else (aws, hs)
        scored.append(gf); conceded.append(ga)
        btts_l.append(1 if gf > 0 and ga > 0 else 0)
        over25_l.append(1 if gf + ga > 2.5 else 0)
        dates.append(ts)
        pts.append(3 if gf > ga else (1 if gf == ga else 0))

    n = len(scored)
    now_ts = int(datetime.datetime.now(datetime.UTC).timestamp())
    days_since = min((now_ts - max(dates)) // 86400, 60) if dates else 7

    return {
        "avg_scored":    round(sum(scored)    / n, 2),
        "avg_conceded":  round(sum(conceded)  / n, 2),
        "form_pts":      round(sum(pts)       / n, 2),
        "win_rate":      round(sum(1 for p in pts if p == 3) / n, 2),
        "btts_rate":     round(sum(btts_l)    / n, 2),
        "over25_rate":   round(sum(over25_l)  / n, 2),
        "days_since_last": int(days_since),
    }


def _get_h2h_features(home_id: int, away_id: int) -> dict:
    """Calcule les features H2H entre deux équipes."""
    data = _get(f"{BASE_URL}/event/0/h2h?homeTeamId={home_id}&awayTeamId={away_id}")
    default = {"h2h_over25_rate": 0.50, "h2h_btts_rate": 0.45}
    if not data:
        return default

    events = [
        e for e in data.get("teamDuel", {}).get("events", [])
        if e.get("status", {}).get("type") == "finished"
    ][:5]
    if not events:
        return default

    over25 = [1 if (e.get("homeScore",{}).get("current",0) or 0) +
                   (e.get("awayScore",{}).get("current",0) or 0) > 2.5 else 0 for e in events]
    btts   = [1 if (e.get("homeScore",{}).get("current",0) or 0) > 0 and
                   (e.get("awayScore",{}).get("current",0) or 0) > 0 else 0 for e in events]
    return {
        "h2h_over25_rate": round(sum(over25) / len(over25), 2),
        "h2h_btts_rate":   round(sum(btts)   / len(btts),   2),
    }


def compute_features(match: dict) -> dict:
    hf  = _get_team_features(match["home_team_id"])
    af  = _get_team_features(match["away_team_id"])
    h2h = _get_h2h_features(match["home_team_id"], match["away_team_id"])
    time.sleep(0.2)

    from predictor import COUNTRY_ENCODING
    is_neutral = 1 if match["league"] == "Club Friendlies" else 0

    return {
        # Features de base
        "home_goals_exp":    hf["avg_scored"],
        "away_goals_exp":    af["avg_scored"],
        "diff_goals_exp":    round(hf["avg_scored"] - af["avg_scored"], 2),
        "total_goals_exp":   round(hf["avg_scored"] + af["avg_scored"], 2),
        # Features enrichies
        "home_conceded_exp": hf["avg_conceded"],
        "away_conceded_exp": af["avg_conceded"],
        "home_form_pts":     hf["form_pts"],
        "away_form_pts":     af["form_pts"],
        "home_win_rate":     hf["win_rate"],
        "away_win_rate":     af["win_rate"],
        "home_btts_rate":    hf["btts_rate"],
        "away_btts_rate":    af["btts_rate"],
        "home_over25_rate":  hf["over25_rate"],
        "away_over25_rate":  af["over25_rate"],
        "days_since_last_h": hf["days_since_last"],
        "days_since_last_a": af["days_since_last"],
        "h2h_over25_rate":   h2h["h2h_over25_rate"],
        "h2h_btts_rate":     h2h["h2h_btts_rate"],
        "is_neutral_ground": is_neutral,
        # Legacy (anciens modèles)
        "Country_encoded":   COUNTRY_ENCODING.get(match["league"], 0),
    }


def fetch_matches_with_features(date_str: str | None = None) -> list[dict]:
    matches = fetch_all_matches(date_str)
    for match in matches:
        match["features"] = compute_features(match)
        time.sleep(0.3)
    return matches
