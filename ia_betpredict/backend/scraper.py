"""
scraper.py
----------
Récupère les matchs du jour sur les ligues cibles via l'API interne Sofascore + ScraperAPI.
"""

import datetime
import os
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote
from curl_cffi import requests

BASE_URL = "https://api.sofascore.com/api/v1"

LEAGUE_IDS = {
    "Veikkausliiga":         41,
    "Eliteserien":           20,
    "MLS":                   242,
    "Serie A Brasil":        325,
    "USL Championship":      13363,
    "USL League One":        13362,
    "USL League Two":        13546,
    "NPSL":                  13450,
    "NPSL Founders Cup":     13742,
    "Club Friendlies":       853,
    "Women Club Friendlies": 24932,
}

LEAGUE_ID_TO_NAME = {tid: name for name, tid in LEAGUE_IDS.items()}
SEASON_OVERRIDES: dict[int, int] = {}
FORM_WINDOW = 5

SCRAPER_API_KEY = os.getenv("SCRAPER_API_KEY", "").strip()
print(f"[DEBUG] SCRAPER_API_KEY présente ? {'OUI' if SCRAPER_API_KEY else 'NON (VIDE)'}")

SESSION = requests.Session(impersonate="chrome120")
SESSION.trust_env = False

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://www.sofascore.com/",
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
}

CACHE = {}


def _get(url: str, retries: int = 2) -> dict | None:
    if url in CACHE:
        return CACHE[url]

    if not SCRAPER_API_KEY:
        print("[scraper] ❌ Pas de SCRAPER_API_KEY configurée.")
        return None

    target_url = (
        f"http://api.scraperapi.com?"
        f"api_key={SCRAPER_API_KEY}"
        f"&url={quote(url)}"
        f"&keep_headers=true"
        f"&country_code=us"
    )

    for attempt in range(retries):
        try:
            resp = SESSION.get(target_url, headers=HEADERS, timeout=25)
            if resp.status_code == 200:
                try:
                    data = resp.json()
                except Exception:
                    CACHE[url] = None
                    return None
                CACHE[url] = data
                return data
            elif resp.status_code == 404:
                CACHE[url] = None
                return None
        except Exception as exc:
            print(f"[scraper] ⚠️ Exception {url} (tentative {attempt + 1}/{retries}): {exc!r}")
            time.sleep(0.5)

    return None


def _get_current_season_id(tournament_id: int) -> int | None:
    if tournament_id in SEASON_OVERRIDES:
        return SEASON_OVERRIDES[tournament_id]

    data = _get(f"{BASE_URL}/unique-tournament/{tournament_id}/seasons")
    if not data:
        return None
    seasons = data.get("seasons", [])
    return seasons[0]["id"] if seasons else None


def _event_unique_tournament_id(event: dict) -> int | None:
    tournament = event.get("tournament", {}) or {}
    unique = tournament.get("uniqueTournament", {}) or {}
    return unique.get("id") or tournament.get("id")


def _event_to_match(event: dict, league_name: str, live: bool = False) -> dict | None:
    try:
        ts = event.get("startTimestamp", 0)
        home = event["homeTeam"]
        away = event["awayTeam"]
        
        status_info = event.get("status", {}) or {}
        status_type = status_info.get("type", "").lower()
        is_live_match = live or status_type == "inprogress"
        
        match = {
            "league":       league_name,
            "match_name":   f"{home['name']} vs {away['name']}",
            "home_team_id": home["id"],
            "away_team_id": away["id"],
            "home_team":    home["name"],
            "away_team":    away["name"],
            "match_time":   datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).strftime("%H:%M"),
            "event_id":     event["id"],
            "status":       status_type,
            "is_live":      is_live_match
        }
        
        if is_live_match:
            match.update({
                "live_status": status_info.get("description") or status_info.get("type", "live"),
                "home_score": event.get("homeScore", {}).get("current"),
                "away_score": event.get("awayScore", {}).get("current"),
            })
            
        return match
    except (KeyError, TypeError):
        return None


def _fetch_scheduled_matches(date_str: str) -> list[dict] | None:
    data = _get(f"{BASE_URL}/sport/football/scheduled-events/{date_str}")
    if data is None:
        return None

    matches = []
    seen_event_ids = set()
    
    for event in data.get("events", []):
        tournament = event.get("tournament", {}) or {}
        unique_id = _event_unique_tournament_id(event)
        tournament_name = tournament.get("name", "").lower()

        league_name = LEAGUE_ID_TO_NAME.get(unique_id)

        if not league_name:
            if any(k in tournament_name for k in ["friendly", "amical", "club friendlies"]):
                league_name = "Club Friendlies"
            else:
                league_name = tournament.get("name", "Other League")

        match = _event_to_match(event, league_name)
        if not match or match["event_id"] in seen_event_ids:
            continue

        seen_event_ids.add(match["event_id"])
        matches.append(match)

    return matches


def fetch_matches_for_league(league_name: str, tournament_id: int, date_str: str) -> list[dict]:
    season_id = _get_current_season_id(tournament_id)
    if not season_id:
        return []

    data = _get(f"{BASE_URL}/unique-tournament/{tournament_id}/season/{season_id}/events/next/0")
    if not data:
        return []

    matches = []
    for event in data.get("events", []):
        ts = event.get("startTimestamp", 0)
        event_date = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).strftime("%Y-%m-%d")

        if event_date != date_str:
            continue

        match = _event_to_match(event, league_name)
        if match:
            matches.append(match)
    return matches


def fetch_all_matches(date_str: str | None = None) -> list[dict]:
    if date_str is None:
        date_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")

    print(f"[scraper] Récupération des matchs pour le {date_str}…")

    scheduled_matches = _fetch_scheduled_matches(date_str)
    if scheduled_matches is not None:
        print(f"[scraper] ✅ {len(scheduled_matches)} match(s) total récupérés (Via Global Request)")
        return scheduled_matches

    print("[scraper] Fallback par ligue en cours…")
    all_matches = []
    for league_name, tid in LEAGUE_IDS.items():
        matches = fetch_matches_for_league(league_name, tid, date_str)
        if matches:
            all_matches.extend(matches)

    print(f"[scraper] ✅ {len(all_matches)} match(s) total pour le {date_str}")
    return all_matches


def fetch_inplay_matches() -> list[dict]:
    data = _get(f"{BASE_URL}/sport/football/events/live")
    if not data:
        return []

    matches = []
    seen_event_ids = set()
    for event in data.get("events", []):
        unique_id = _event_unique_tournament_id(event)
        league_name = LEAGUE_ID_TO_NAME.get(unique_id, event.get("tournament", {}).get("name", "Live Match"))
        
        match = _event_to_match(event, league_name, live=True)
        if not match or match["event_id"] in seen_event_ids:
            continue
        seen_event_ids.add(match["event_id"])
        matches.append(match)

    print(f"[scraper] ✅ {len(matches)} match(s) live ciblé(s)")
    return matches


def _get_team_features(team_id: int) -> dict:
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
    now_ts = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
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
    data = _get(f"{BASE_URL}/event/h2h/custom/{home_id}/{away_id}")
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
    hf = _get_team_features(match["home_team_id"])
    af = _get_team_features(match["away_team_id"])

    is_friendly = match["league"] in ["Club Friendlies", "Women Club Friendlies"]

    if not is_friendly:
        h2h = _get_h2h_features(match["home_team_id"], match["away_team_id"])
    else:
        h2h = {"h2h_over25_rate": 0.50, "h2h_btts_rate": 0.45}

    from predictor import COUNTRY_ENCODING
    is_neutral = 1 if is_friendly else 0

    return {
        "home_goals_exp":    hf["avg_scored"],
        "away_goals_exp":    af["avg_scored"],
        "diff_goals_exp":    round(hf["avg_scored"] - af["avg_scored"], 2),
        "total_goals_exp":   round(hf["avg_scored"] + af["avg_scored"], 2),
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
        "form_points_diff":  round(hf["form_pts"] - af["form_pts"], 2),
        "win_rate_diff":     round(hf["win_rate"] - af["win_rate"], 2),
        "btts_rate_diff":    round(hf["btts_rate"] - af["btts_rate"], 2),
        "over25_rate_diff":  round(hf["over25_rate"] - af["over25_rate"], 2),
        "Country_encoded":   COUNTRY_ENCODING.get(match["league"], 0),
    }


def fetch_matches_with_features(date_str: str | None = None) -> list[dict]:
    matches = fetch_all_matches(date_str)
    if not matches:
        return []

    upcoming_matches = [m for m in matches if m.get("status") == "notstarted"]
    print(f"[scraper] Calcul des features pour {len(upcoming_matches)} match(s) à venir (sur {len(matches)} scrapés)...")

    if not upcoming_matches:
        return []

    def _process_match(m):
        m["features"] = compute_features(m)
        return m

    with ThreadPoolExecutor(max_workers=8) as executor:
        processed_matches = list(executor.map(_process_match, upcoming_matches))

    return processed_matches