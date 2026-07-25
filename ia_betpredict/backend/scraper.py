"""
scraper.py
----------
Récupère les matchs du jour sur les ligues cibles et les tournois live Sofascore via l'API interne de Sofascore.
Endpoint utilisé : /unique-tournament/{id}/season/{season_id}/events/next/0
La saison active est récupérée dynamiquement au démarrage.
"""

import datetime
import time
import os
import re
import json
import random
import itertools
from urllib.parse import quote_plus
from curl_cffi import requests  # Utilisation de curl_cffi pour usurper l'empreinte TLS Chrome

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
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
    "Origin": "https://www.sofascore.com",
    "X-Requested-With": "XMLHttpRequest",
}

BASE_URL = "https://api.sofascore.com/api/v1"

LEAGUE_IDS = {
    # Core leagues (corrected IDs)
    "Veikkausliiga":       41,     # Finlande
    "Eliteserien":         20,     # Norvège
    "MLS":                 242,    # États-Unis
    "Serie A Brasil":      325,    # Brésil

    # US / additional competitions (added)
    "USL Championship":    13363,  # USA
    "USL League One":      13362,  # USA
    "USL League Two":      13546,  # USA
    "NPSL":                13450,  # USA (regional amateur league)
    "NPSL Founders Cup":   13742,  # USA (Founders Cup)

    # Friendlies / wide coverage
    "Club Friendlies":     853,    # Matchs amicaux de clubs (mondial)
    "Women Club Friendlies": 24932, # Matchs amicaux féminins (mondial)
}

FORM_WINDOW = 5

# Optional manual override: set known season ids to avoid relying on Sofascore API
SEASON_OVERRIDES: dict[int, int] = {
    # tournament_id: season_id,
    # e.g. 13363: 2026
}

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
            print(f"[scraper] Proxy file introuvable : {SOFASCORE_PROXY_FILE}")
    return proxies

PROXY_URLS = _load_proxies()
PROXY_CYCLE = itertools.cycle(PROXY_URLS) if PROXY_URLS else None
if PROXY_URLS:
    print(f"[scraper] Chargé {len(PROXY_URLS)} proxy(s) pour Sofascore")

# Session curl_cffi simulant un navigateur Chrome récent
SESSION = requests.Session(impersonate="chrome120")
SESSION.trust_env = False


def _build_proxy(proxy_url: str) -> dict[str, str] | None:
    if not proxy_url:
        return None
    return {"http": proxy_url, "https": proxy_url}


def _get_proxy() -> dict[str, str] | None:
    if not PROXY_CYCLE:
        return None
    proxy_url = next(PROXY_CYCLE)
    return _build_proxy(proxy_url)


# ---------------------------------------------------------------------------
# Helpers HTTP
# ---------------------------------------------------------------------------

def _get(url: str, retries: int = 3) -> dict | None:
    for attempt in range(retries):
        try:
            proxies = _get_proxy()
            if proxies:
                print(f"[scraper] Requête via proxy {proxies['https']}")
            resp = SESSION.get(url, headers=HEADERS, timeout=10, proxies=proxies)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 429:
                time.sleep(5 * (attempt + 1))
            elif resp.status_code == 403:
                print(f"[scraper] 403 Forbidden pour {url} (proxy={proxies})")
        except Exception as exc:
            print(f"[scraper] Erreur réseau ({attempt+1}/{retries}) : {exc}")
            time.sleep(2)
    return None


def _get_text(url: str, retries: int = 3) -> str | None:
    for attempt in range(retries):
        try:
            proxies = _get_proxy()
            if proxies:
                print(f"[scraper] Requête texte via proxy {proxies['https']}")
            resp = SESSION.get(url, headers=HEADERS, timeout=10, proxies=proxies)
            if resp.status_code == 200:
                return resp.text
            if resp.status_code == 429:
                time.sleep(5 * (attempt + 1))
            elif resp.status_code == 403:
                print(f"[scraper] 403 Forbidden pour {url} (proxy={proxies})")
        except Exception as exc:
            print(f"[scraper] Erreur réseau ({attempt+1}/{retries}) : {exc}")
            time.sleep(2)
    return None


# ---------------------------------------------------------------------------
# Saison active
# ---------------------------------------------------------------------------

def _get_current_season_id(tournament_id: int) -> int | None:
    """Retourne l'ID de la saison la plus récente pour un tournoi.

    Checks `SEASON_OVERRIDES` first to allow manual injection of season ids.
    """
    if tournament_id in SEASON_OVERRIDES:
        return SEASON_OVERRIDES[tournament_id]

    data = _get(f"{BASE_URL}/unique-tournament/{tournament_id}/seasons")
    if not data:
        return None
    seasons = data.get("seasons", [])
    return seasons[0]["id"] if seasons else None


def _extract_next_data_from_html(html: str) -> dict | None:
    """Extract JSON from Next.js __NEXT_DATA__ script in Sofascore pages."""
    m = re.search(r"<script id=\"__NEXT_DATA__\"[^>]*>(.*?)</script>", html, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except Exception:
        return None


def _find_season_id_in_json(obj: object, tournament_id: int) -> int | None:
    """Recursively search JSON for a seasons list matching tournament_id."""
    if isinstance(obj, dict):
        if obj.get("id") == tournament_id and "seasons" in obj:
            seasons = obj.get("seasons") or []
            if seasons:
                return seasons[0].get("id")
        ut = obj.get("uniqueTournament")
        if isinstance(ut, dict) and ut.get("id") == tournament_id:
            seasons = ut.get("seasons") or []
            if seasons:
                return seasons[0].get("id")
        for v in obj.values():
            found = _find_season_id_in_json(v, tournament_id)
            if found:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _find_season_id_in_json(item, tournament_id)
            if found:
                return found
    return None


def _get_season_id_via_search(tournament_id: int, league_name: str | None = None) -> int | None:
    """Fallback: query the public search page and parse embedded JSON to find seasons."""
    query = league_name or str(tournament_id)
    url = f"https://www.sofascore.com/search?query={quote_plus(query)}"
    html = _get_text(url)
    if not html:
        return None
    nd = _extract_next_data_from_html(html)
    if not nd:
        return None
    season_id = _find_season_id_in_json(nd, tournament_id)
    if season_id:
        return season_id

    def _find_urls(obj):
        urls = []
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k == "url" and isinstance(v, str) and v.rstrip('/').endswith(f"/{tournament_id}"):
                    urls.append(v)
                else:
                    urls.extend(_find_urls(v))
        elif isinstance(obj, list):
            for item in obj:
                urls.extend(_find_urls(item))
        return urls

    urls = _find_urls(nd)
    for u in urls:
        turl = f"https://www.sofascore.com{u}"
        html = _get_text(turl)
        if not html:
            continue
        nd2 = _extract_next_data_from_html(html)
        if not nd2:
            continue
        season_id = _find_season_id_in_json(nd2, tournament_id)
        if season_id:
            return season_id

    base_slug = (league_name or str(tournament_id)).lower().replace(" ", "-")
    candidates = [base_slug, f"{base_slug}", f"{base_slug.replace('usl-','usl-')}"]
    for cand in candidates:
        turl = f"https://www.sofascore.com/football/tournament/usa/{cand}"
        html = _get_text(turl)
        if not html:
            continue
        nd = _extract_next_data_from_html(html)
        if not nd:
            continue
        season_id = _find_season_id_in_json(nd, tournament_id)
        if season_id:
            return season_id

    return None


# ---------------------------------------------------------------------------
# Récupération des matchs du jour
# ---------------------------------------------------------------------------

def fetch_matches_for_league(league_name: str, tournament_id: int, date_str: str) -> list[dict]:
    season_id = _get_current_season_id(tournament_id)
    if not season_id:
        season_id = _get_season_id_via_search(tournament_id, league_name)
    if not season_id:
        print(f"[scraper] ⚠️  Saison introuvable pour {league_name}")
        return []

    data = _get(f"{BASE_URL}/unique-tournament/{tournament_id}/season/{season_id}/events/next/0")
    if not data:
        print(f"[scraper] ⚠️  Aucune réponse pour {league_name}")
        return []

    matches = []
    for event in data.get("events", []):
        ts = event.get("startTimestamp", 0)
        event_date = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).strftime("%Y-%m-%d")
        if event_date != date_str:
            continue
        matches.append({
            "league":       league_name,
            "match_name":   f"{event['homeTeam']['name']} vs {event['awayTeam']['name']}",
            "home_team_id": event["homeTeam"]["id"],
            "away_team_id": event["awayTeam"]["id"],
            "home_team":    event["homeTeam"]["name"],
            "away_team":    event["awayTeam"]["name"],
            "match_time":   datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).strftime("%H:%M"),
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


def fetch_inplay_matches() -> list[dict]:
    """Tente de récupérer les matchs actuellement en cours (in-play)."""
    all_matches = []
    for league_name, tid in LEAGUE_IDS.items():
        season_id = _get_current_season_id(tid)
        if not season_id:
            continue
        data = _get(f"{BASE_URL}/unique-tournament/{tid}/season/{season_id}/events/next/0")
        if not data:
            continue
        for event in data.get("events", []):
            st = event.get("status", {}).get("type", "").lower()
            if st and st not in ("finished", "canceled", "postponed"):
                ts = event.get("startTimestamp", 0)
                all_matches.append({
                    "league": league_name,
                    "match_name": f"{event['homeTeam']['name']} vs {event['awayTeam']['name']}",
                    "home_team_id": event["homeTeam"]["id"],
                    "away_team_id": event["awayTeam"]["id"],
                    "home_team": event["homeTeam"]["name"],
                    "away_team": event["awayTeam"]["name"],
                    "match_time": datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).strftime("%H:%M") if ts else "",
                    "event_id": event.get("id"),
                    "status": event.get("status", {}),
                })
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