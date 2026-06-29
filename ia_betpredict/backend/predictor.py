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
# Chargement des modèles (une seule fois au démarrage)
# ---------------------------------------------------------------------------

_MODELS_DIR = os.path.join(os.path.dirname(__file__), "models")

def _load(filename: str):
    path = os.path.join(_MODELS_DIR, filename)
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Modèle introuvable : {path}\n"
            "Place tes fichiers .pkl dans backend/models/ "
            "(export depuis Colab avec joblib.dump)."
        )
    return joblib.load(path)


# Les modèles sont chargés une seule fois à l'import du module
try:
    MODEL_DC    = _load("model_winner.pkl")     # Double Chance (1X et X2)
    MODEL_OVER  = _load("model_goals.pkl") # 
    MODEL_BTTS  = _load("model_btts.pkl")   # Both Teams To Score
    
    _MODELS_LOADED = True
except FileNotFoundError as _e:
    print(f"[predictor]   {_e}")
    _MODELS_LOADED = False


# ---------------------------------------------------------------------------
# Features attendues par les modèles
# ---------------------------------------------------------------------------

FEATURE_COLUMNS = [
    "home_goals_exp",
    "away_goals_exp",
    "diff_goals_exp",
    "total_goals_exp",
]


def _features_to_df(features: dict) -> pd.DataFrame:
    row = {col: features.get(col, 0.0) for col in FEATURE_COLUMNS}
    return pd.DataFrame([row])


# ---------------------------------------------------------------------------
# Génération des coupons
# ---------------------------------------------------------------------------

def predict_match(features: dict) -> dict:
    """
    Retourne les probabilités brutes pour tous les marchés.
    Chaque modèle expose predict_proba ; on prend la classe positive (index 1).
    """
    if not _MODELS_LOADED:
        # Mode démo : probabilités aléatoires pour tester le frontend
        import random
        return {
            "Double Chance 1X": round(random.uniform(0.50, 0.90), 4),
            "Double Chance X2": round(random.uniform(0.50, 0.90), 4),
            "Over 2.5":         round(random.uniform(0.45, 0.85), 4),
            "BTTS":             round(random.uniform(0.45, 0.85), 4),
        }

    X = _features_to_df(features)

    # Double Chance : le modèle prédit 1X (classe 0) et X2 (classe 1)
    dc_proba = MODEL_DC.predict_proba(X)[0]

    return {
        "Double Chance 1X": round(float(dc_proba[0]), 4),
        "Double Chance X2": round(float(dc_proba[1]), 4),
        "Over 2.5":         round(float(MODEL_OVER.predict_proba(X)[0][1]), 4),
        "BTTS":             round(float(MODEL_BTTS.predict_proba(X)[0][1]), 4),
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
    probas     = predict_match(features)
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