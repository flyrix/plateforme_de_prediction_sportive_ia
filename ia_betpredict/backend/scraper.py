"""
scraper.py
----------
Récupère les matchs du jour sur les ligues cibles via l'API interne Sofascore + ScrapingAnt.
"""

import datetime
import html
import json
import os
import re
import time
from curl_cffi import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_URL = "https://api.sofascore.com/api/v1"

LEAGUE_IDS = {
    "Veikkausliiga":         41,     # Finlande
    "Eliteserien":           20,     # Norvège
    "MLS":                   242,    # États-Unis
    "Serie A Brasil":        325,    # Brésil
    "USL Championship":      13363,  # USA
    "USL League One":        13362,  # USA
    "USL League Two":        13546,  # USA
    "NPSL":                  13450,  # USA
    "NPSL Founders Cup":     13742,  # USA
    "Club Friendlies":       853,    # Matchs amicaux
    
}
LEAGUE_ID_TO_NAME = {tid: name for name, tid in LEAGUE_IDS.items()}

FORM_WINDOW = 5


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        print(f"[scraper] {name} invalide, fallback={default}")
        return default


# Récupération de la clé API ScrapingAnt depuis les variables d'environnement
SCRAPINGANT_KEY = os.getenv("SCRAPINGANT_KEY", "").strip()
SCRAPINGANT_BROWSER = os.getenv("SCRAPINGANT_BROWSER", "false").lower() in {"1", "true", "yes", "on"}
SCRAPINGANT_PROXY_TYPE = os.getenv("SCRAPINGANT_PROXY_TYPE", "datacenter").strip()
SCRAPINGANT_PROXY_COUNTRY = os.getenv("SCRAPINGANT_PROXY_COUNTRY", "").strip().upper()
SCRAPINGANT_TIMEOUT = _env_int("SCRAPINGANT_TIMEOUT", 25)
SCRAPINGANT_RETURN_PAGE_SOURCE = os.getenv("SCRAPINGANT_RETURN_PAGE_SOURCE", "true").lower() in {
    "1", "true", "yes", "on"
}

SESSION = requests.Session(impersonate="chrome120")
SESSION.trust_env = False

# Headers imitant un vrai navigateur
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://www.sofascore.com/",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
}

# ---------------------------------------------------------------------------
# Configuration & Overrides
# ---------------------------------------------------------------------------

# Dictionnaire des ID de saisons pour éviter d'appeler l'endpoint /seasons
SEASON_OVERRIDES: dict[int, int] = {
    41: 61858,     # Veikkausliiga
    20: 61582,     # Eliteserien
    242: 67388,    # MLS
    325: 67345,    # Serie A Brasil
    13363: 67400,  # USL Championship
    13362: 67401,  # USL League One
    13546: 67402,  # USL League Two
    853: 68000,    # Club Friendlies
}

# ---------------------------------------------------------------------------
# Helpers HTTP avec ScrapingAnt
# ---------------------------------------------------------------------------

def _target_headers_for_scrapingant() -> dict:
    # ScrapingAnt ne transmet les headers cible que s'ils sont préfixés par Ant-.
    return {f"Ant-{name}": value for name, value in HEADERS.items()}


def _parse_json_response(resp, source_url: str) -> dict | None:
    target_status = resp.headers.get("ant-page-status-code")
    if target_status and target_status.isdigit() and int(target_status) >= 400:
        print(f"[scraper] Cible HTTP {target_status} pour {source_url}")
        return None

    try:
        return resp.json()
    except Exception:
        text = (resp.text or "").strip()
        if not text:
            return None

        # En mode browser, les réponses JSON peuvent revenir dans un <pre>.
        pre = re.search(r"<pre[^>]*>(.*?)</pre>", text, flags=re.IGNORECASE | re.DOTALL)
        if pre:
            text = html.unescape(pre.group(1)).strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            snippet = text[:160].replace("\n", " ")
            print(f"[scraper] Réponse non-JSON pour {source_url}: {snippet}")
            return None


def _get_via_scrapingant(url: str, retries: int) -> dict | None:
    params = {
        "x-api-key": SCRAPINGANT_KEY,
        "url": url,
        "browser": str(SCRAPINGANT_BROWSER).lower(),
        "timeout": str(SCRAPINGANT_TIMEOUT),
    }
    if SCRAPINGANT_BROWSER and SCRAPINGANT_RETURN_PAGE_SOURCE:
        params["return_page_source"] = "true"
    if SCRAPINGANT_PROXY_TYPE:
        params["proxy_type"] = SCRAPINGANT_PROXY_TYPE
    if SCRAPINGANT_PROXY_COUNTRY:
        params["proxy_country"] = SCRAPINGANT_PROXY_COUNTRY

    for attempt in range(retries):
        try:
            resp = SESSION.get(
                "https://api.scrapingant.com/v2/general",
                params=params,
                headers=_target_headers_for_scrapingant(),
                timeout=SCRAPINGANT_TIMEOUT + 5,
            )
            if resp.status_code == 200:
                data = _parse_json_response(resp, url)
                if data and "detail" not in data:
                    return data
                if data and "detail" in data:
                    print(f"[scraper] ScrapingAnt detail: {data['detail']}")
            else:
                print(f"[scraper] ScrapingAnt HTTP {resp.status_code} pour {url}: {resp.text[:120]}")
        except Exception as exc:
            print(f"[scraper] Erreur ScrapingAnt ({attempt + 1}/{retries}) : {exc}")
        time.sleep(2 * (attempt + 1))
    return None


def _get(url: str, retries: int = 3) -> dict | None:
    if SCRAPINGANT_KEY:
        data = _get_via_scrapingant(url, retries)
        if data is not None:
            return data

    # Fallback Direct (curl_cffi passe le Cloudflare de Sofascore sans problème)
    for attempt in range(retries):
        try:
            resp = SESSION.get(url, headers=HEADERS, timeout=12)
            if resp.status_code == 200:
                return _parse_json_response(resp, url)
            elif resp.status_code == 404:
                return None
            elif resp.status_code in {403, 429}:
                print(f"[scraper] Sofascore HTTP {resp.status_code} pour {url}")
        except Exception as exc:
            print(f"[scraper] Erreur Sofascore directe ({attempt + 1}/{retries}) : {exc}")
            time.sleep(1)
            
    return None
# ---------------------------------------------------------------------------
# Extraction des saisons et matchs
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Extraction des saisons
# ---------------------------------------------------------------------------

def _get_current_season_id(tournament_id: int) -> int | None:
    # 👈 On vérifie d'abord si l'ID est court-circuité par SEASON_OVERRIDES
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
        match = {
            "league":       league_name,
            "match_name":   f"{home['name']} vs {away['name']}",
            "home_team_id": home["id"],
            "away_team_id": away["id"],
            "home_team":    home["name"],
            "away_team":    away["name"],
            "match_time":   datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).strftime("%H:%M"),
            "event_id":     event["id"],
        }
        if live:
            status = event.get("status", {}) or {}
            match.update({
                "is_live": True,
                "live_status": status.get("description") or status.get("type", "live"),
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
        league_name = LEAGUE_ID_TO_NAME.get(_event_unique_tournament_id(event))
        if not league_name:
            continue
        match = _event_to_match(event, league_name)
        if not match or match["event_id"] in seen_event_ids:
            continue
        seen_event_ids.add(match["event_id"])
        matches.append(match)
    return matches


def fetch_matches_for_league(league_name: str, tournament_id: int, date_str: str) -> list[dict]:
    season_id = _get_current_season_id(tournament_id)
    if not season_id:
        print(f"[scraper] ⚠️ Saison introuvable pour {league_name}")
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
        date_str = datetime.date.today().isoformat()

    print(f"[scraper] Récupération des matchs pour le {date_str}…")

    scheduled_matches = _fetch_scheduled_matches(date_str)
    if scheduled_matches is not None:
        for league_name in LEAGUE_IDS:
            count = sum(1 for match in scheduled_matches if match["league"] == league_name)
            if count:
                print(f"[scraper]   {league_name}: {count} match(s)")
        print(f"[scraper] ✅ {len(scheduled_matches)} match(s) total pour le {date_str}")
        return scheduled_matches

    print("[scraper] Fallback par ligue : scheduled-events indisponible.")
    all_matches = []
    for league_name, tid in LEAGUE_IDS.items():
        matches = fetch_matches_for_league(league_name, tid, date_str)
        print(f"[scraper]   {league_name}: {len(matches)} match(s)")
        all_matches.extend(matches)
        time.sleep(0.5)

    print(f"[scraper] ✅ {len(all_matches)} match(s) total pour le {date_str}")
    return all_matches


def fetch_inplay_matches() -> list[dict]:
    data = _get(f"{BASE_URL}/sport/football/events/live")
    if not data:
        return []

    matches = []
    seen_event_ids = set()
    for event in data.get("events", []):
        league_name = LEAGUE_ID_TO_NAME.get(_event_unique_tournament_id(event))
        if not league_name:
            continue
        match = _event_to_match(event, league_name, live=True)
        if not match or match["event_id"] in seen_event_ids:
            continue
        seen_event_ids.add(match["event_id"])
        matches.append(match)
    print(f"[scraper] ✅ {len(matches)} match(s) live ciblé(s)")
    return matches

# ---------------------------------------------------------------------------
# Calcul des features pour XGBoost
# ---------------------------------------------------------------------------

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
    # 👈 URL Sofascore correcte pour le H2H
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
    hf  = _get_team_features(match["home_team_id"])
    af  = _get_team_features(match["away_team_id"])
    h2h = _get_h2h_features(match["home_team_id"], match["away_team_id"])

    from predictor import COUNTRY_ENCODING
    is_neutral = 1 if match["league"] in ["Club Friendlies", "Women Club Friendlies"] else 0

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
    return matches
