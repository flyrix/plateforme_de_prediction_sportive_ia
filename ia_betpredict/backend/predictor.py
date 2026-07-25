"""
predictor.py
------------
Charge les modèles XGBoost (.pkl) et expose une fonction unique
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
    try:
        return joblib.load(path)
    except ModuleNotFoundError as exc:
        if exc.name == "xgboost":
            raise
        raise

def _load_group(group: str) -> dict | None:
    """Charge les 3 modèles spécialisés pour un groupe. Retourne None si absents."""
    try:
        return {
            "dc":   _load(f"model_winner_{group}.pkl"),
            "over": _load(f"model_goals_{group}.pkl"),
            "btts": _load(f"model_btts_{group}.pkl"),
        }
    except (FileNotFoundError, ModuleNotFoundError):
        return None

# Chargement de tous les groupes spécialisés (y compris 'global')
_SPECIALIZED: dict[str, dict] = {}

for _g in ["nordique", "americain", "sud_americain", "amicaux", "global"]:
    _m = _load_group(_g)
    if _m:
        _SPECIALIZED[_g] = _m
        print(f"[predictor] ✅ Modèle spécialisé '{_g}' chargé.")

# Validation de la présence des modèles
if "global" in _SPECIALIZED or len(_SPECIALIZED) > 0:
    _MODELS_LOADED = True
    _GLOBAL = _SPECIALIZED.get("global")  # Utilise 'global' comme fallback
else:
    print("[predictor] ⚠️ Aucun modèle trouvé dans le dossier /models/.")
    print("[predictor] ⚠️ MODE DÉMO activé.")
    _GLOBAL = None
    _MODELS_LOADED = False


# ---------------------------------------------------------------------------
# Features attendues par les modèles
# ---------------------------------------------------------------------------

FEATURE_COLUMNS = [
    "home_goals_exp",    "away_goals_exp",
    "diff_goals_exp",    "total_goals_exp",
    "home_conceded_exp", "away_conceded_exp",
    "home_form_pts",     "away_form_pts",
    "home_win_rate",     "away_win_rate",
    "home_btts_rate",    "away_btts_rate",
    "home_over25_rate",  "away_over25_rate",
    "days_since_last_h", "days_since_last_a",
    "h2h_over25_rate",   "h2h_btts_rate",
    "is_neutral_ground",
]

# Encodage si besoin de compatibilité
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

def _predict_proba_dict(model, X):
    """Retourne un dict classe -> probabilité pour un modèle binaire."""
    probs = model.predict_proba(X)[0]
    classes = list(model.classes_)
    if len(classes) != len(probs):
        raise ValueError("Incohérence entre classes et probabilités du modèle")
    return {classes[i]: float(probs[i]) for i in range(len(classes))}


def predict_match(features: dict, league: str = "") -> dict:
    """
    Utilise le modèle spécialisé pour la ligue si disponible,
    sinon le modèle 'global' spécialisé.
    """
    if not _MODELS_LOADED and not _SPECIALIZED:
        import random
        print("[predictor] ⚠️ MODE DÉMO")
        return {
            "Double Chance 1X": round(random.uniform(0.50, 0.90), 4),
            "Double Chance X2": round(random.uniform(0.50, 0.90), 4),
            "Over 2.5":         round(random.uniform(0.45, 0.85), 4),
            "BTTS":             round(random.uniform(0.45, 0.85), 4),
        }

    # Sélection du modèle : spécialisé > global
    group = LEAGUE_TO_GROUP.get(league, "")
    models = (
        _SPECIALIZED.get(group)
        or _SPECIALIZED.get("global")
        or _GLOBAL
    )

    if models is None:
        raise RuntimeError(f"Aucun modèle disponible pour la ligue '{league}'")

    X = _features_to_df(features, legacy=False)

    source = f"spécialisé '{group}'" if group in _SPECIALIZED else "global"
    print(f"[predictor] Modèle utilisé : {source} pour {league}")

    dc_probs = _predict_proba_dict(models["dc"], X)
    prob_1x = dc_probs.get(1, dc_probs.get("1", 0.0))
    prob_x2 = dc_probs.get(0, dc_probs.get("0", 0.0))

    btts_probs = _predict_proba_dict(models["btts"], X)
    btts_proba = btts_probs.get(1, btts_probs.get("1", 0.0))

    # Over 2.5 : classifieur si présent, sinon régresseur
    if hasattr(models["over"], "predict_proba"):
        over_proba = models["over"].predict_proba(X)[0][1]
    else:
        import math
        goals_pred = float(models["over"].predict(X)[0])
        over_proba = round(1 / (1 + math.exp(-(goals_pred - 2.5))), 4)

    return {
        "Double Chance 1X": round(float(prob_1x), 4),
        "Double Chance X2": round(float(prob_x2), 4),
        "Over 2.5":         round(float(over_proba), 4),
        "BTTS":             round(float(btts_proba), 4),
    }


def generate_coupons(match: dict) -> list[dict]:
    """
    Applique les seuils métier et retourne uniquement les paris
    dont la confiance dépasse le seuil requis.
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