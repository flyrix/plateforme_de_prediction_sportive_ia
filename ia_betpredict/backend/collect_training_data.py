"""
collect_training_data.py
------------------------
Collecte les données historiques depuis Sofascore pour entraîner
des modèles XGBoost spécialisés par groupe de ligues.

Usage :
    python collect_training_data.py

Produit : data/training_data.csv
"""

import time
import datetime
import requests
import pandas as pd
import os

# ---------------------------------------------------------------------------
# Config
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

# Groupes de ligues par culture footballistique
LEAGUE_GROUPS = {
    "nordique":      {"Veikkausliiga": 41, "Eliteserien": 20},
    "americain":     {"MLS": 242},
    "sud_americain": {"Serie A Brasil": 325},
    "amicaux":       {"Club Friendlies": 853},
}

# Nombre de saisons historiques à collecter par ligue
N_SEASONS = 5
# Nombre max de pages par saison (1 page = ~10 matchs)
MAX_PAGES_DEFAULT  = 50   # ~500 matchs par saison pour les ligues normales
MAX_PAGES_FRIENDLY = 15   # ~150 matchs suffisent pour les amicaux
# Nombre de matchs récents pour calculer la forme
FORM_WINDOW = 5

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "training_data.csv")


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

def _get(url: str, retries: int = 3) -> dict | None:
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:
                time.sleep(10 * (attempt + 1))
        except requests.RequestException as e:
            print(f"  [HTTP] Erreur ({attempt+1}/{retries}): {e}")
            time.sleep(3)
    return None


# ---------------------------------------------------------------------------
# Saisons
# ---------------------------------------------------------------------------

def get_seasons(tournament_id: int, n: int = N_SEASONS) -> list[dict]:
    data = _get(f"{BASE_URL}/unique-tournament/{tournament_id}/seasons")
    if not data:
        return []
    return data.get("seasons", [])[:n]


# ---------------------------------------------------------------------------
# Matchs d'une saison (avec pagination)
# ---------------------------------------------------------------------------

def get_season_events(tournament_id: int, season_id: int, league_name: str = "") -> list[dict]:
    """Récupère les matchs terminés d'une saison via pagination."""
    max_pages = MAX_PAGES_FRIENDLY if league_name == "Club Friendlies" else MAX_PAGES_DEFAULT
    all_events = []
    page = 0
    while page < max_pages:
        data = _get(
            f"{BASE_URL}/unique-tournament/{tournament_id}"
            f"/season/{season_id}/events/last/{page}"
        )
        if not data:
            break
        events = data.get("events", [])
        if not events:
            break
        finished = [
            e for e in events
            if e.get("status", {}).get("type") == "finished"
            and e.get("homeScore", {}).get("current") is not None
            and e.get("awayScore", {}).get("current") is not None
        ]
        all_events.extend(finished)
        if len(events) < 10:
            break
        page += 1
        time.sleep(0.2)
    return all_events


# ---------------------------------------------------------------------------
# Forme d'une équipe (N derniers matchs avant une date)
# ---------------------------------------------------------------------------

_team_cache: dict[int, list] = {}

def get_team_form(team_id: int) -> dict:
    """Calcule avg_scored et avg_conceded sur les N derniers matchs."""
    if team_id not in _team_cache:
        data = _get(f"{BASE_URL}/team/{team_id}/events/last/0")
        _team_cache[team_id] = data.get("events", []) if data else []
        time.sleep(0.2)

    events = _team_cache[team_id][:FORM_WINDOW]
    if not events:
        return {"avg_scored": 1.2, "avg_conceded": 1.2}

    scored, conceded = [], []
    for ev in events:
        ht_id = ev.get("homeTeam", {}).get("id")
        hs = ev.get("homeScore", {}).get("current", 0) or 0
        aws = ev.get("awayScore", {}).get("current", 0) or 0
        if ht_id == team_id:
            scored.append(hs); conceded.append(aws)
        else:
            scored.append(aws); conceded.append(hs)

    return {
        "avg_scored":   round(sum(scored)   / len(scored),   2),
        "avg_conceded": round(sum(conceded) / len(conceded), 2),
    }


# ---------------------------------------------------------------------------
# Construction du dataset
# ---------------------------------------------------------------------------

def build_row(event: dict, league_name: str, group: str) -> dict | None:
    """Construit une ligne du dataset à partir d'un event Sofascore."""
    try:
        home_id  = event["homeTeam"]["id"]
        away_id  = event["awayTeam"]["id"]
        home_g   = event["homeScore"]["current"]
        away_g   = event["awayScore"]["current"]
        ts       = event.get("startTimestamp", 0)
        date_str = datetime.datetime.fromtimestamp(ts, datetime.UTC).strftime("%Y-%m-%d")

        home_form = get_team_form(home_id)
        away_form = get_team_form(away_id)

        home_exp = home_form["avg_scored"]
        away_exp = away_form["avg_scored"]
        total    = home_g + away_g

        return {
            "date":            date_str,
            "league":          league_name,
            "group":           group,
            "home_team":       event["homeTeam"]["name"],
            "away_team":       event["awayTeam"]["name"],
            "home_goals":      home_g,
            "away_goals":      away_g,
            # Features
            "home_goals_exp":  home_exp,
            "away_goals_exp":  away_exp,
            "diff_goals_exp":  round(home_exp - away_exp, 2),
            "total_goals_exp": round(home_exp + away_exp, 2),
            # Targets
            "result":          0 if home_g > away_g else (1 if home_g == away_g else 2),  # 0=H, 1=D, 2=A
            "dc_1x":           1 if home_g >= away_g else 0,   # Double Chance 1X
            "dc_x2":           1 if away_g >= home_g else 0,   # Double Chance X2
            "over25":          1 if total > 2.5 else 0,
            "btts":            1 if home_g > 0 and away_g > 0 else 0,
        }
    except (KeyError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def collect():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    all_rows = []

    for group, leagues in LEAGUE_GROUPS.items():
        for league_name, tid in leagues.items():
            print(f"\n{'='*50}")
            print(f"[{group.upper()}] {league_name} (ID={tid})")
            seasons = get_seasons(tid)
            print(f"  {len(seasons)} saisons trouvées")

            for season in seasons:
                sid   = season["id"]
                sname = season["name"]
                print(f"  → Saison {sname} (id={sid})...", end=" ", flush=True)

                events = get_season_events(tid, sid, league_name)
                print(f"{len(events)} matchs terminés", end=" ", flush=True)

                rows_added = 0
                for ev in events:
                    row = build_row(ev, league_name, group)
                    if row:
                        all_rows.append(row)
                        rows_added += 1
                    time.sleep(0.05)

                print(f"→ {rows_added} lignes")
                time.sleep(1)

    df = pd.DataFrame(all_rows)
    df.to_csv(OUTPUT_FILE, index=False)
    print(f"\n✅ Dataset sauvegardé : {OUTPUT_FILE}")
    print(f"   {len(df)} matchs | {df['league'].value_counts().to_dict()}")
    return df


if __name__ == "__main__":
    collect()
