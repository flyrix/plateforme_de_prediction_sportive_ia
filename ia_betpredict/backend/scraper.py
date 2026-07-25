"""
scraper.py
----------
Récupère les matchs du jour sur les ligues cibles et les tournois live Sofascore via l'API interne de Sofascore.
Utilise l'endpoint global `/scheduled-events/{date}` beaucoup plus robuste contre les blocages Cloudflare/Render.
"""

import datetime
import time
import os
import itertools
from curl_cffi import requests

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
    "Accept": "*/*",
    "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
    "Origin": "https://www.sofascore.com",
    "X-Requested-With": "XMLHttpRequest",
}

BASE_URL = "https://api.sofascore.com/api/v1"

# Mapping des IDs de tournois vers nos nom de ligues
TOURNAMENT_TO_LEAGUE = {
    41:    "Veikkausliiga",
    20:    "Eliteserien",
    242:   "MLS",
    325:   "Serie A Brasil",
    13363: "USL Championship",
    13362: "USL League One",
    13546: "USL League Two",
    13450: "NPSL",
    13742: "NPSL Founders Cup",
    853:   "Club Friendlies",
    24932: "Women Club Friendlies",
}

FORM_WINDOW = 5

# Gestion des proxies optionnels
SOFASCORE_PROXIES = os.getenv("SOFASCORE_PROXIES", "").strip()
SOFASCORE_PROXY_FILE = os.getenv("SOFASCORE_PROXY_FILE", "").strip()

def _load_proxies() -> list[str]:
    proxies: list[str] = []
    if SOFASCORE_PROXIES:
        proxies.extend([p.strip() for p in SOFASCORE_PROXIES.split(",") if p.strip()])
    if SOFASCORE_PROXY_FILE:
        try:
            with open(SOFASCORE_PROXY_FILE, "r", encoding="utf-8") as f:
                proxies.extend([
                    line.strip() for line in f
                    if line.strip() and not line.strip().startswith("#")
                ])
        except FileNotFoundError:
            pass
    return proxies

PROXY_URLS = _load_proxies()
PROXY_CYCLE = itertools.cycle(PROXY_URLS) if PROXY_URLS else None

SESSION = requests.Session(impersonate="chrome120")
SESSION.trust_env = False

def _get_proxy() -> dict[str, str] | None:
    if not PROXY_CYCLE:
        return None
    proxy_url = next(PROXY_CYCLE)
    return {"http": proxy_url, "https": proxy_url}

# ---------------------------------------------------------------------------
# Helpers HTTP
# ---------------------------------------------------------------------------

SCRAPER_API_KEY = os.getenv("SCRAPER_API_KEY", "")

SCRAPER_API_KEY = os.getenv("SCRAPER_API_KEY", "").strip()

def _get(url: str, retries: int = 3) -> dict | None:
    # 1. Utilisation de ScraperAPI si la clé est présente
    if SCRAPER_API_KEY:
        payload = {
            'api_key': SCRAPER_API_KEY,
            'url': url,
            'keep_headers': 'true',  # 👈 OBLIGATOIRE : Transmet nos headers (Referer, UA...) à Sofascore
        }
        for attempt in range(retries):
            try:
                # ScraperAPI gère les proxies tout seul, on utilise un GET standard avec nos HEADERS
                resp = SESSION.get('https://api.scraperapi.com', params=payload, headers=HEADERS, timeout=25)
                if resp.status_code == 200:
                    return resp.json()
                print(f"[scraper] ScraperAPI Status {resp.status_code} pour {url}")
            except Exception as exc:
                print(f"[scraper] Erreur ScraperAPI ({attempt+1}/{retries}) : {exc}")
                time.sleep(2)
        return None

    # 2. Fallback standard sans ScraperAPI
    for attempt in range(retries):
        try:
            resp = SESSION.get(url, headers=HEADERS, timeout=10)
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 403:
                print(f"[scraper] 403 Forbidden pour {url}")
        except Exception as exc:
            print(f"[scraper] Erreur réseau ({attempt+1}/{retries}) : {exc}")
            time.sleep(2)
    return None
# ---------------------------------------------------------------------------
# Récupération des matchs du jour (Endpoint Global)
# ---------------------------------------------------------------------------

def fetch_all_matches(date_str: str | None = None) -> list[dict]:
    if date_str is None:
        date_str = datetime.date.today().isoformat()

    print(f"[scraper] Récupération des matchs pour le {date_str} via scheduled-events…")
    
    url = f"{BASE_URL}/scheduled-events/{date_str}"
    data = _get(url)

    if not data or "events" not in data:
        print(f"[scraper] ⚠️ Impossible de récupérer les événements pour {date_str}")
        return []

    all_matches = []
    events = data.get("events", [])

    for event in events:
        ut = event.get("tournament", {}).get("uniqueTournament", {})
        tid = ut.get("id")

        # Vérifier si l'événement fait partie de nos ligues cibles
        if tid in TOURNAMENT_TO_LEAGUE:
            league_name = TOURNAMENT_TO_LEAGUE[tid]
            ts = event.get("startTimestamp", 0)
            
            all_matches.append({
                "league":       league_name,
                "match_name":   f"{event['homeTeam']['name']} vs {event['awayTeam']['name']}",
                "home_team_id": event["homeTeam"]["id"],
                "away_team_id": event["awayTeam"]["id"],
                "home_team":    event["homeTeam"]["name"],
                "away_team":    event["awayTeam"]["name"],
                "match_time":   datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).strftime("%H:%M"),
                "event_id":     event["id"],
            })

    print(f"[scraper] ✅ {len(all_matches)} match(s) ciblé(s) trouvé(s) sur {len(events)} évènements aujourd'hui.")
    return all_matches


def fetch_inplay_matches() -> list[dict]:
    """Récupère les matchs en direct."""
    date_str = datetime.date.today().isoformat()
    data = _get(f"{BASE_URL}/scheduled-events/{date_str}")
    if not data:
        return []

    inplay_matches = []
    for event in data.get("events", []):
        ut = event.get("tournament", {}).get("uniqueTournament", {})
        tid = ut.get("id")
        
        if tid in TOURNAMENT_TO_LEAGUE:
            st = event.get("status", {}).get("type", "").lower()
            if st and st not in ("finished", "canceled", "postponed", "notstarted"):
                ts = event.get("startTimestamp", 0)
                inplay_matches.append({
                    "league": TOURNAMENT_TO_LEAGUE[tid],
                    "match_name": f"{event['homeTeam']['name']} vs {event['awayTeam']['name']}",
                    "home_team_id": event["homeTeam"]["id"],
                    "away_team_id": event["awayTeam"]["id"],
                    "home_team": event["homeTeam"]["name"],
                    "away_team": event["awayTeam"]["name"],
                    "match_time": datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).strftime("%H:%M") if ts else "",
                    "event_id": event.get("id"),
                    "status": event.get("status", {}),
                })
    return inplay_matches

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
        "Country_encoded":   COUNTRY_ENCODING.get(match["league"], 0),
    }


def fetch_matches_with_features(date_str: str | None = None) -> list[dict]:
    matches = fetch_all_matches(date_str)
    for match in matches:
        match["features"] = compute_features(match)
        time.sleep(0.3)
    return matches