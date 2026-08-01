"""
scraper.py
----------
Récupère les matchs du jour sur les ligues cibles via l'API interne Sofascore.
Système de Scraping Hybride Résilient : ScrapingAnt (Prioritaire) -> ScraperAPI (Fallback 1) -> Direct (Fallback 2).
Incorpore l'extraction automatique des cotes via Sofascore avec Fallback vers Odds-API.io
pour le calcul de l'Expected Value (EV).
Supporte désormais les championnats Nordiques, Américains, Sud-Américains, Amicaux et du Top 5 Europe au complet (incluant Ligue 1).
"""

import datetime
import os
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote
from curl_cffi import requests
from difflib import SequenceMatcher

BASE_URL = "https://api.sofascore.com/api/v1"

LEAGUE_IDS = {
    # Ligues Nordiques & Américaines / Diverses
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

    # Championnats Top 5 Europe
    "Premier League":        17,
    "LaLiga":                8,
    "Bundesliga":            35,
    "Serie A":               23,
    "Ligue 1":               34,  # <-- Ajouté (France Ligue 1)
}

LEAGUE_ID_TO_NAME = {tid: name for name, tid in LEAGUE_IDS.items()}
SEASON_OVERRIDES: dict[int, int] = {}
FORM_WINDOW = 5

# Clés API d'environnement
SCRAPINGANT_KEY = os.getenv("SCRAPINGANT_KEY", "").strip()
SCRAPER_API_KEY = os.getenv("SCRAPER_API_KEY", "").strip()
ODDS_API_KEY = os.getenv("ODDS_API_KEY", "").strip()

print(f"[DEBUG] SCRAPINGANT_KEY présente ? {'OUI' if SCRAPINGANT_KEY else 'NON (VIDE)'}")
print(f"[DEBUG] SCRAPER_API_KEY présente ? {'OUI' if SCRAPER_API_KEY else 'NON (VIDE)'}")
print(f"[DEBUG] ODDS_API_KEY présente ? {'OUI' if ODDS_API_KEY else 'NON (VIDE)'}")

SESSION = requests.Session(impersonate="chrome120")
SESSION.trust_env = False

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://www.sofascore.com/",
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
}

CACHE = {}
ODDS_API_CACHE = {}


# ==========================================
# MODULES RESILIENTS DE SCRAPING (FAILOVER)
# ==========================================

def _fetch_via_scrapingant(url: str, timeout: int = 30) -> dict | None:
    """Tentative via ScrapingAnt (Prioritaire)."""
    if not SCRAPINGANT_KEY:
        return None
    
    encoded_url = quote(url, safe='')
    # Passage en browser=true pour franchir la sécurité Cloudflare de Sofascore
    ant_url = f"https://api.scrapingant.com/v2/general?x-api-key={SCRAPINGANT_KEY}&url={encoded_url}&browser=true"
    
    try:
        resp = SESSION.get(ant_url, timeout=timeout)
        if resp.status_code == 200:
            return resp.json()
        elif resp.status_code == 423:
            print("[scraper] ⚠️ ScrapingAnt bloqué/limite de concurrence atteinte (Code 423). Passage au fallback.")
            return None
        else:
            print(f"[scraper] ⚠️ ScrapingAnt Code Status: {resp.status_code}")
    except Exception as e:
        print(f"[scraper] ⚠️ ScrapingAnt Erreur: {e}")
        
    return None

def _fetch_via_scraperapi(url: str, timeout: int = 25) -> dict | None:
    """Tentative via ScraperAPI (Fallback 1)."""
    if not SCRAPER_API_KEY:
        return None
    target_url = (
        f"http://api.scraperapi.com?"
        f"api_key={SCRAPER_API_KEY}"
        f"&url={quote(url)}"
        f"&keep_headers=true"
        f"&country_code=us"
    )
    try:
        resp = SESSION.get(target_url, headers=HEADERS, timeout=timeout)
        if resp.status_code == 200:
            return resp.json()
        else:
            print(f"[scraper] ⚠️ ScraperAPI Code Status: {resp.status_code}")
    except Exception as e:
        print(f"[scraper] ⚠️ ScraperAPI Erreur: {e}")
    return None

def _fetch_direct(url: str, timeout: int = 15) -> dict | None:
    """Tentative directe curl_cffi (Fallback 2)."""
    try:
        resp = SESSION.get(url, headers=HEADERS, timeout=timeout)
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return None

def _get(url: str, retries: int = 2) -> dict | None:
    if url in CACHE:
        return CACHE[url]

    # Ordre des providers: ScrapingAnt -> ScraperAPI -> Direct
    providers = [
        ("ScrapingAnt", _fetch_via_scrapingant),
        ("ScraperAPI", _fetch_via_scraperapi),
        ("Direct", _fetch_direct)
    ]

    for attempt in range(retries):
        for name, provider_func in providers:
            try:
                data = provider_func(url)
                if data is not None:
                    CACHE[url] = data
                    return data
            except Exception as exc:
                print(f"[scraper] ⚠️ Erreur {name} pour {url} (Essai {attempt + 1}/{retries}) : {exc!r}")
        time.sleep(1.0)  # Pause pour réguler le taux de requêtes

    CACHE[url] = None
    return None


# ==========================================
# GESTION DU FALLBACK ODDS-API.IO
# ==========================================

def _similarity(a: str, b: str) -> float:
    """ Calcule le score de ressemblance entre deux noms d'équipes (0.0 à 1.0) """
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()

def fetch_odds_from_odds_api() -> list[dict]:
    """ Récupère la liste globale des cotes sur Odds-API.io avec mise en cache """
    if "global_odds" in ODDS_API_CACHE:
        return ODDS_API_CACHE["global_odds"]

    if not ODDS_API_KEY:
        return []

    url = f"https://api.odds-api.io/v1/odds?apiKey={ODDS_API_KEY}&sport=soccer"
    try:
        resp = requests.get(url, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            ODDS_API_CACHE["global_odds"] = data
            return data
    except Exception as e:
        print(f"[scraper] ⚠️ Erreur lors de la requête Odds-API.io : {e}")

    return []

def fallback_odds_from_api(home_team: str, away_team: str) -> dict:
    """ Cherche un match équivalent sur Odds-API.io si Sofascore est vide """
    all_odds_data = fetch_odds_from_odds_api()
    if not all_odds_data:
        return {}

    best_match = None
    highest_score = 0.0

    for match in all_odds_data:
        api_home = match.get("home_team", "")
        api_away = match.get("away_team", "")

        score_h = _similarity(home_team, api_home)
        score_a = _similarity(away_team, api_away)
        total_score = (score_h + score_a) / 2

        if total_score > highest_score and total_score >= 0.65:
            highest_score = total_score
            best_match = match

    parsed_odds = {}
    if best_match and "sites" in best_match:
        for site in best_match.get("sites", []):
            odds = site.get("odds", {})
            
            # Cotes 1N2
            h2h = odds.get("h2h", [])
            if len(h2h) >= 3 and "1N2_H" not in parsed_odds:
                parsed_odds["1N2_H"] = float(h2h[0])
                parsed_odds["1N2_D"] = float(h2h[1])
                parsed_odds["1N2_A"] = float(h2h[2])

            # Cotes Over/Under 2.5
            totals = odds.get("totals", {})
            if "Over_2.5" not in parsed_odds and totals:
                points = totals.get("points", [])
                odds_val = totals.get("odds", [])
                for p, o in zip(points, odds_val):
                    if p == 2.5:
                        parsed_odds["Over_2.5"] = float(o[0])
                        parsed_odds["Under_2.5"] = float(o[1])

            if "1N2_H" in parsed_odds:
                break

    return parsed_odds


# ==========================================
# SCRAPING DES COTES SOFASCORE + HYBRIDE
# ==========================================

def fetch_match_odds(event_id: int, home_team: str = "", away_team: str = "") -> dict:
    """
    Récupère les cotes du bookmaker depuis l'API Sofascore.
    Si Sofascore ne renvoie aucune cote, bascule automatiquement sur Odds-API.io.
    """
    data = _get(f"{BASE_URL}/event/{event_id}/odds/1/all")
    parsed_odds = {}

    if data and "markets" in data:
        for market in data.get("markets", []):
            market_name = market.get("marketName", "").lower()
            
            # Cotes 1N2 (Full Time)
            if market_name in ["full time", "1x2", "match result"]:
                for choice in market.get("choices", []):
                    name = choice.get("name", "").upper()
                    decimal_odd = choice.get("decimalValue")
                    
                    if not decimal_odd and choice.get("change"):
                        decimal_odd = choice.get("current")

                    if decimal_odd:
                        val = float(decimal_odd)
                        if name in ["1", "HOME"]:
                            parsed_odds["1N2_H"] = val
                        elif name in ["X", "DRAW"]:
                            parsed_odds["1N2_D"] = val
                        elif name in ["2", "AWAY"]:
                            parsed_odds["1N2_A"] = val

            # Cotes Over/Under 2.5
            elif "total" in market_name or "goals" in market_name:
                for choice in market.get("choices", []):
                    choice_name = choice.get("name", "").lower()
                    choice_line = choice.get("choiceGroup") or str(choice.get("line", ""))
                    
                    if "2.5" in choice_line or choice.get("line") == 2.5:
                        if "over" in choice_name and choice.get("decimalValue"):
                            parsed_odds["Over_2.5"] = float(choice["decimalValue"])
                        elif "under" in choice_name and choice.get("decimalValue"):
                            parsed_odds["Under_2.5"] = float(choice["decimalValue"])

            # Cotes Both Teams to Score (BTTS)
            elif "both teams to score" in market_name or "btts" in market_name:
                for choice in market.get("choices", []):
                    choice_name = choice.get("name", "").lower()
                    if choice_name in ["yes", "oui"] and choice.get("decimalValue"):
                        parsed_odds["BTTS_Yes"] = float(choice["decimalValue"])
                    elif choice_name in ["no", "non"] and choice.get("decimalValue"):
                        parsed_odds["BTTS_No"] = float(choice["decimalValue"])

    # Fallback si Sofascore n'a rien renvoyé
    if not parsed_odds and home_team and away_team:
        print(f"[scraper] ⚠️ Cotes manquantes Sofascore pour {home_team} vs {away_team}. Tentative sur Odds-API.io...")
        parsed_odds = fallback_odds_from_api(home_team, away_team)

    return parsed_odds


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
            "is_live":      is_live_match,
            "odds":         {}
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
            elif "premier league" in tournament_name:
                league_name = "Premier League"
            elif "laliga" in tournament_name or "la liga" in tournament_name:
                league_name = "LaLiga"
            elif "bundesliga" in tournament_name:
                league_name = "Bundesliga"
            elif "serie a" in tournament_name:
                league_name = "Serie A"
            elif "ligue 1" in tournament_name:
                league_name = "Ligue 1"  # <-- Ajouté (Match en String)
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
    print(f"[scraper] Calcul des features et cotes pour {len(upcoming_matches)} match(s) à venir (sur {len(matches)} scrapés)...")

    if not upcoming_matches:
        return []

    def _process_match(m):
        m["features"] = compute_features(m)
        m["odds"]     = fetch_match_odds(m["event_id"], m["home_team"], m["away_team"])
        return m

    # max_workers réduit à 2 pour ne pas surcharger les limites de requêtes simultanées des scrapers
    with ThreadPoolExecutor(max_workers=2) as executor:
        processed_matches = list(executor.map(_process_match, upcoming_matches))

    return processed_matches