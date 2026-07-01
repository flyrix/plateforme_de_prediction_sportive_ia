"""
predictor.py
------------
Charge les 4 modèles XGBoost (.pkl) et expose une fonction unique
generate_coupons(match) qui retourne les paris éligibles selon les
seuils de confiance définis dans le cahier des charges.
"""

import os
import joblib
import pandas as pd

# ---------------------------------------------------------------------------
# Seuils de confiance (règles métier du CDC)
# ---------------------------------------------------------------------------

THRESHOLDS = {
    "Double Chance 1X": 0.65,
    "Double Chance X2": 0.65,
    "Over 2.5":         0.60,
    "BTTS":             0.60,
}

# ---------------------------------------------------------------------------
# Mapping ligue → groupe (pour charger le bon modèle spécialisé)
# ---------------------------------------------------------------------------

LEAGUE_TO_GROUP = {
    "Veikkausliiga":   "nordique",
    "Eliteserien":     "nordique",
    "MLS":             "americain",
    "Serie A Brasil":  "sud_americain",
    "Club Friendlies": "amicaux",
}

# ---------------------------------------------------------------------------
# Chargement des modèles (une seule fois au démarrage)
# ---------------------------------------------------------------------------

_MODELS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "models")

def _load(filename: str):
    path = os.path.normpath(os.path.join(_MODELS_DIR, filename))
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    return joblib.load(path)

def _load_group(group: str) -> dict | None:
    """Charge les 3 modèles spécialisés pour un groupe. Retourne None si absents."""
    try:
        return {
            "dc":   _load(f"model_winner_{group}.pkl"),
            "over": _load(f"model_goals_{group}.pkl"),
            "btts": _load(f"model_btts_{group}.pkl"),
        }
    except FileNotFoundError:
        return None

# Modèles globaux (fallback)
try:
    _GLOBAL = {
        "dc":   _load("model_winner.pkl"),
        "over": _load("model_goals.pkl"),
        "btts": _load("model_btts.pkl"),
    }
    _MODELS_LOADED = True
    print("[predictor] ✅ Modèles globaux chargés.")
except FileNotFoundError as _e:
    print(f"[predictor] ⚠️  Modèles introuvables : {_e}")
    print("[predictor] ⚠️  MODE DÉMO activé.")
    _GLOBAL = None
    _MODELS_LOADED = False

# Modèles spécialisés (optionnels, prioritaires si présents)
_SPECIALIZED: dict[str, dict] = {}
for _g in ["nordique", "americain", "sud_americain", "amicaux", "global"]:
    _m = _load_group(_g)
    if _m:
        _SPECIALIZED[_g] = _m
        print(f"[predictor] ✅ Modèle spécialisé '{_g}' chargé.")


# ---------------------------------------------------------------------------
# Features attendues par les modèles
# ---------------------------------------------------------------------------

FEATURE_COLUMNS = [
    "home_goals_exp",
    "away_goals_exp",
    "diff_goals_exp",
    "total_goals_exp",
]

# Country_encoded gardé pour compatibilité avec les anciens modèles globaux
COUNTRY_ENCODING = {
    "Veikkausliiga":   0,
    "Eliteserien":     1,
    "MLS":             2,
    "Serie A Brasil":  3,
    "Club Friendlies": 4,
}

FEATURE_COLUMNS_LEGACY = FEATURE_COLUMNS + ["Country_encoded"]


def _features_to_df(features: dict, legacy: bool = False) -> pd.DataFrame:
    cols = FEATURE_COLUMNS_LEGACY if legacy else FEATURE_COLUMNS
    row = {col: features.get(col, 0.0) for col in cols}
    return pd.DataFrame([row])


# ---------------------------------------------------------------------------
# Génération des coupons
# ---------------------------------------------------------------------------

def predict_match(features: dict, league: str = "") -> dict:
    """
    Utilise le modèle spécialisé pour la ligue si disponible,
    sinon le modèle global (legacy avec Country_encoded).
    """
    if not _MODELS_LOADED and not _SPECIALIZED:
        import random
        print("[predictor] ⚠️  MODE DÉMO")
        return {
            "Double Chance 1X": round(random.uniform(0.50, 0.90), 4),
            "Double Chance X2": round(random.uniform(0.50, 0.90), 4),
            "Over 2.5":         round(random.uniform(0.45, 0.85), 4),
            "BTTS":             round(random.uniform(0.45, 0.85), 4),
        }

    # Choisir le bon modèle : spécialisé > global spécialisé > legacy
    group = LEAGUE_TO_GROUP.get(league, "")
    models = (
        _SPECIALIZED.get(group)
        or _SPECIALIZED.get("global")
        or _GLOBAL
    )

    # Déterminer si on utilise les features legacy (avec Country_encoded)
    is_legacy = (models is _GLOBAL)
    X = _features_to_df(features, legacy=is_legacy)

    source = f"spécialisé '{group}'" if not is_legacy else "global (legacy)"
    print(f"[predictor] Modèle utilisé : {source} pour {league}")

    dc_proba   = models["dc"].predict_proba(X)[0]
    btts_proba = models["btts"].predict_proba(X)[0][1]

    # Over 2.5 : classifieur si nouveau modèle, régresseur si legacy
    if hasattr(models["over"], "predict_proba"):
        over_proba = models["over"].predict_proba(X)[0][1]
    else:
        import math
        goals_pred = float(models["over"].predict(X)[0])
        over_proba = round(1 / (1 + math.exp(-(goals_pred - 2.5))), 4)

    return {
        "Double Chance 1X": round(float(dc_proba[0]), 4),
        "Double Chance X2": round(float(dc_proba[1]), 4),
        "Over 2.5":         round(float(over_proba), 4),
        "BTTS":             round(float(btts_proba), 4),
    }


def generate_coupons(match: dict) -> list[dict]:
    """
    Applique les seuils métier et retourne uniquement les paris
    dont la confiance dépasse le seuil requis.

    Retourne une liste de dicts prêts à être insérés dans Supabase :
    {
        match_name, league, prediction_type,
        confidence_rate, status,
        home_team, away_team, match_time
    }
    """
    features   = match.get("features", {})
    probas     = predict_match(features, league=match.get("league", ""))
    coupons    = []

    for market, confidence in probas.items():
        threshold = THRESHOLDS.get(market, 1.0)
        if confidence >= threshold:
            coupons.append({
                "match_name":      match["match_name"],
                "league":          match["league"],
                "home_team":       match["home_team"],
                "away_team":       match["away_team"],
                "match_time":      match.get("match_time", ""),
                "prediction_type": market,
                "confidence_rate": confidence,
                "status":          "En attente",
            })

    return coupons