"""
collect_training_data.py
------------------------
Collecte les données historiques depuis Sofascore avec features enrichies.

Features collectées :
  - home/away_goals_exp     : moyenne buts marqués (5 derniers matchs)
  - home/away_conceded_exp  : moyenne buts encaissés
  - diff/total_goals_exp    : différence et somme des attaques
  - home/away_form_pts      : points sur les 5 derniers matchs (W=3, D=1, L=0)
  - home/away_win_rate      : % victoires sur les 5 derniers matchs
  - home/away_btts_rate     : % matchs avec les deux équipes qui marquent
  - home/away_over25_rate   : % matchs Over 2.5
  - h2h_over25_rate         : taux Over 2.5 dans les confrontations directes
  - h2h_btts_rate           : taux BTTS dans les confrontations directes
  - days_since_last_match_h : jours depuis le dernier match (domicile)
  - days_since_last_match_a : jours depuis le dernier match (extérieur)
  - is_neutral_ground       : terrain neutre (amicaux souvent sur terrain neutre)

Usage : python collect_training_data.py
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

LEAGUE_GROUPS = {
    "nordique":      {"Veikkausliiga": 41, "Eliteserien": 20},
    "americain":     {"MLS": 242},
    "sud_americain": {"Serie A Brasil": 325},
    "amicaux":       {"Club Friendlies": 853},
}

N_SEASONS          = 5
MAX_PAGES_DEFAULT  = 50
MAX_PAGES_FRIENDLY = 15
FORM_WINDOW        = 5
H2H_WINDOW         = 5

OUTPUT_DIR  = os.path.join(os.path.dirname(__file__), "..", "..", "data")
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
            if r.status_code == 403:
                print(f"  [HTTP] 403 Bloqué — pause 60s")
                time.sleep(60)
            elif r.status_code == 429:
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
    return data.get("seasons", [])[:n] if data else []


# ---------------------------------------------------------------------------
# Matchs d'une saison
# ---------------------------------------------------------------------------

def get_season_events(tournament_id: int, season_id: int, league_name: str = "") -> list[dict]:
    max_pages = MAX_PAGES_FRIENDLY if league_name == "Club Friendlies" else MAX_PAGES_DEFAULT
    all_events = []
    for page in range(max_pages):
        data = _get(f"{BASE_URL}/unique-tournament/{tournament_id}/season/{season_id}/events/last/{page}")
        if not data:
            break
        events = data.get("events", [])
        if not events:
            break
        all_events.extend([
            e for e in events
            if e.get("status", {}).get("type") == "finished"
            and e.get("homeScore", {}).get("current") is not None
            and e.get("awayScore", {}).get("current") is not None
        ])
        if len(events) < 10:
            break
        time.sleep(0.2)
    return all_events


# ---------------------------------------------------------------------------
# Cache équipes
# ---------------------------------------------------------------------------

_team_events_cache: dict[int, list] = {}

def _get_team_events(team_id: int) -> list:
    if team_id not in _team_events_cache:
        data = _get(f"{BASE_URL}/team/{team_id}/events/last/0")
        _team_events_cache[team_id] = data.get("events", []) if data else []
        time.sleep(0.15)
    return _team_events_cache[team_id]


# ---------------------------------------------------------------------------
# Features équipe
# ---------------------------------------------------------------------------

def get_team_features(team_id: int, before_ts: int = 0) -> dict:
    """
    Calcule toutes les features d'une équipe sur ses N derniers matchs
    avant le timestamp du match courant.
    """
    all_events = _get_team_events(team_id)

    # Filtrer les matchs avant la date du match courant
    events = [
        e for e in all_events
        if before_ts == 0 or e.get("startTimestamp", 0) < before_ts
    ][:FORM_WINDOW]

    if not events:
        return {
            "avg_scored": 1.2, "avg_conceded": 1.2,
            "form_pts": 4.5, "win_rate": 0.4,
            "btts_rate": 0.45, "over25_rate": 0.50,
            "days_since_last": 7,
        }

    scored, conceded, pts, btts_list, over25_list, dates = [], [], [], [], [], []

    for ev in events:
        ht_id = ev.get("homeTeam", {}).get("id")
        hs  = ev.get("homeScore", {}).get("current", 0) or 0
        aws = ev.get("awayScore", {}).get("current", 0) or 0
        ts  = ev.get("startTimestamp", 0)

        if ht_id == team_id:
            gf, ga = hs, aws
        else:
            gf, ga = aws, hs

        scored.append(gf)
        conceded.append(ga)
        btts_list.append(1 if gf > 0 and ga > 0 else 0)
        over25_list.append(1 if gf + ga > 2.5 else 0)
        dates.append(ts)

        if gf > ga:   pts.append(3)
        elif gf == ga: pts.append(1)
        else:          pts.append(0)

    n = len(scored)
    last_ts = max(dates) if dates else 0
    days_since = 0
    if last_ts and before_ts:
        days_since = max(0, (before_ts - last_ts) // 86400)

    return {
        "avg_scored":    round(sum(scored)    / n, 2),
        "avg_conceded":  round(sum(conceded)  / n, 2),
        "form_pts":      round(sum(pts)       / n, 2),
        "win_rate":      round(sum(1 for p in pts if p == 3) / n, 2),
        "btts_rate":     round(sum(btts_list) / n, 2),
        "over25_rate":   round(sum(over25_list) / n, 2),
        "days_since_last": min(days_since, 60),  # cap à 60 jours
    }


# ---------------------------------------------------------------------------
# Features H2H (confrontations directes)
# ---------------------------------------------------------------------------

_h2h_cache: dict[str, dict] = {}

def get_h2h_features(home_id: int, away_id: int) -> dict:
    key = f"{min(home_id, away_id)}_{max(home_id, away_id)}"
    if key in _h2h_cache:
        return _h2h_cache[key]

    data = _get(f"{BASE_URL}/event/0/h2h?homeTeamId={home_id}&awayTeamId={away_id}")
    default = {"h2h_over25_rate": 0.50, "h2h_btts_rate": 0.45}

    if not data:
        _h2h_cache[key] = default
        return default

    events = [
        e for e in data.get("teamDuel", {}).get("events", [])
        if e.get("status", {}).get("type") == "finished"
    ][:H2H_WINDOW]

    if not events:
        _h2h_cache[key] = default
        return default

    over25 = [1 if (e.get("homeScore",{}).get("current",0) or 0) +
                   (e.get("awayScore",{}).get("current",0) or 0) > 2.5 else 0
              for e in events]
    btts   = [1 if (e.get("homeScore",{}).get("current",0) or 0) > 0 and
                   (e.get("awayScore",{}).get("current",0) or 0) > 0 else 0
              for e in events]

    result = {
        "h2h_over25_rate": round(sum(over25) / len(over25), 2),
        "h2h_btts_rate":   round(sum(btts)   / len(btts),   2),
    }
    _h2h_cache[key] = result
    time.sleep(0.1)
    return result


# ---------------------------------------------------------------------------
# Construction d'une ligne du dataset
# ---------------------------------------------------------------------------

def build_row(event: dict, league_name: str, group: str) -> dict | None:
    try:
        home_id = event["homeTeam"]["id"]
        away_id = event["awayTeam"]["id"]
        home_g  = event["homeScore"]["current"]
        away_g  = event["awayScore"]["current"]
        ts      = event.get("startTimestamp", 0)
        date_str = datetime.datetime.fromtimestamp(ts, datetime.UTC).strftime("%Y-%m-%d")

        hf = get_team_features(home_id, before_ts=ts)
        af = get_team_features(away_id, before_ts=ts)
        h2h = get_h2h_features(home_id, away_id)

        # Terrain neutre : si le match est un amical et que le nom du stade
        # ne correspond pas à l'équipe domicile (heuristique simple)
        is_neutral = 1 if group == "amicaux" else 0

        total = home_g + away_g

        return {
            "date":   date_str,
            "league": league_name,
            "group":  group,
            "home_team": event["homeTeam"]["name"],
            "away_team": event["awayTeam"]["name"],
            "home_goals": home_g,
            "away_goals": away_g,
            # ── Features ──────────────────────────────────────────────────
            "home_goals_exp":        hf["avg_scored"],
            "away_goals_exp":        af["avg_scored"],
            "diff_goals_exp":        round(hf["avg_scored"] - af["avg_scored"], 2),
            "total_goals_exp":       round(hf["avg_scored"] + af["avg_scored"], 2),
            "home_conceded_exp":     hf["avg_conceded"],
            "away_conceded_exp":     af["avg_conceded"],
            "home_form_pts":         hf["form_pts"],
            "away_form_pts":         af["form_pts"],
            "home_win_rate":         hf["win_rate"],
            "away_win_rate":         af["win_rate"],
            "home_btts_rate":        hf["btts_rate"],
            "away_btts_rate":        af["btts_rate"],
            "home_over25_rate":      hf["over25_rate"],
            "away_over25_rate":      af["over25_rate"],
            "days_since_last_h":     hf["days_since_last"],
            "days_since_last_a":     af["days_since_last"],
            "h2h_over25_rate":       h2h["h2h_over25_rate"],
            "h2h_btts_rate":         h2h["h2h_btts_rate"],
            "is_neutral_ground":     is_neutral,
            # ── Targets ───────────────────────────────────────────────────
            "result":  0 if home_g > away_g else (1 if home_g == away_g else 2),
            "dc_1x":   1 if home_g >= away_g else 0,
            "dc_x2":   1 if away_g >= home_g else 0,
            "over25":  1 if total > 2.5 else 0,
            "btts":    1 if home_g > 0 and away_g > 0 else 0,
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
                print(f"  → {sname}...", end=" ", flush=True)

                events = get_season_events(tid, sid, league_name)
                print(f"{len(events)} matchs", end=" ", flush=True)

                rows_added = 0
                for ev in events:
                    row = build_row(ev, league_name, group)
                    if row:
                        all_rows.append(row)
                        rows_added += 1
                    time.sleep(0.05)

                print(f"→ {rows_added} lignes")
                time.sleep(0.5)

    df = pd.DataFrame(all_rows)
    df.to_csv(OUTPUT_FILE, index=False)
    print(f"\n✅ Dataset sauvegardé : {OUTPUT_FILE}")
    print(f"   {len(df)} matchs | {df['group'].value_counts().to_dict()}")
    return df


if __name__ == "__main__":
    collect()
