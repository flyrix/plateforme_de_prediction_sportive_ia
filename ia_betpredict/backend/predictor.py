"""
predictor.py
------------
Charge les modèles XGBoost (.pkl) et expose une fonction unique
generate_coupons(match) qui retourne les paris éligibles selon les
seuils de confiance définis dans le cahier des charges.
"""

import os
import math
import logging
import joblib
import pandas as pd

# Configuration du logging
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)s] %(message)s")
logger = logging.getLogger("predictor")

# ---------------------------------------------------------------------------
# Seuils de confiance (règles métier du CDC)
# ---------------------------------------------------------------------------

THRESHOLDS = {
    "Double Chance 1X": 0.50,
    "Double Chance X2": 0.50,
    "Over 2.5":         0.50,
    "BTTS":             0.50,
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
_LEGACY_WINNER_WARNING_EMITTED = False

for _g in ["nordique", "americain", "sud_americain", "amicaux", "global"]:
    _m = _load_group(_g)
    if _m:
        _SPECIALIZED[_g] = _m
        logger.info(f"✅ Modèle spécialisé '{_g}' chargé.")

# Validation de la présence des modèles
if "global" in _SPECIALIZED or len(_SPECIALIZED) > 0:
    _MODELS_LOADED = True
    _GLOBAL = _SPECIALIZED.get("global")
else:
    logger.warning("⚠️ Aucun modèle trouvé dans le dossier /models/. MODE DÉMO activé.")
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
    "form_points_diff",  "win_rate_diff",
    "btts_rate_diff",    "over25_rate_diff",
]

FEATURE_COLUMNS_LEGACY = FEATURE_COLUMNS + ["Country_encoded"]


def _model_feature_names(model) -> list[str] | None:
    """Récupère la liste exacte des features attendues directement depuis le modèle."""
    names = getattr(model, "feature_names_in_", None)
    if names is not None:
        return list(names)
    try:
        booster = model.get_booster()
        if booster.feature_names:
            return list(booster.feature_names)
    except Exception:
        pass
    return None


def _features_to_df(features: dict, model=None, legacy: bool = False) -> pd.DataFrame:
    cols = _model_feature_names(model) if model is not None else None
    if not cols:
        cols = FEATURE_COLUMNS_LEGACY if legacy else FEATURE_COLUMNS
        logger.warning(f"Colonnes du modèle non détectables, repli sur la liste par défaut ({len(cols)} features).")
    
    row = {col: features.get(col, 0.0) for col in cols}
    missing = [c for c in cols if c not in features]
    if missing:
        logger.warning(f"Features absentes du dictionnaire, valeur 0.0 utilisée: {missing}")
    
    return pd.DataFrame([row])


# ---------------------------------------------------------------------------
# Génération des prédictions
# ---------------------------------------------------------------------------

def _predict_proba_dict(model, X) -> dict:
    """Retourne un dict classe -> probabilité pour un modèle binaire ou multiclasse."""
    probs = model.predict_proba(X)[0]
    classes = list(model.classes_)
    if len(classes) != len(probs):
        raise ValueError("Incohérence entre classes et probabilités du modèle")
    return {classes[i]: float(probs[i]) for i in range(len(classes))}


def _class_probability(probas: dict, class_id: int) -> float:
    return float(probas.get(class_id, probas.get(str(class_id), 0.0)))


def _double_chance_predictions(model, X) -> dict:
    """
    Calcule 1X/X2 depuis un modèle résultat 3 classes :
      0 = domicile, 1 = nul, 2 = extérieur.
    """
    global _LEGACY_WINNER_WARNING_EMITTED

    probs = _predict_proba_dict(model, X)
    class_labels = {str(label) for label in probs.keys()}

    if {"0", "1", "2"}.issubset(class_labels):
        home = _class_probability(probs, 0)
        draw = _class_probability(probs, 1)
        away = _class_probability(probs, 2)
        return {
            "Double Chance 1X": round(home + draw, 4),
            "Double Chance X2": round(draw + away, 4),
        }

    if {"0", "1"}.issubset(class_labels):
        if not _LEGACY_WINNER_WARNING_EMITTED:
            logger.warning("Modèle winner legacy binaire : X2 ignoré jusqu'au réentraînement.")
            _LEGACY_WINNER_WARNING_EMITTED = True
        return {"Double Chance 1X": round(_class_probability(probs, 1), 4)}

    raise ValueError(f"Classes inattendues pour model_winner: {sorted(class_labels)}")


def predict_match(features: dict, league: str = "") -> dict:
    """
    Utilise le modèle spécialisé pour la ligue si disponible, sinon le modèle 'global'.
    """
    if not _MODELS_LOADED and not _SPECIALIZED:
        import random
        logger.warning("MODE DÉMO activé")
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

    source = f"spécialisé '{group}'" if group in _SPECIALIZED else "global"
    logger.info(f"Modèle utilisé : {source} pour la ligue '{league}'")

    X_dc   = _features_to_df(features, model=models["dc"])
    X_over = _features_to_df(features, model=models["over"])
    X_btts = _features_to_df(features, model=models["btts"])

    winner_predictions = _double_chance_predictions(models["dc"], X_dc)

    btts_probs = _predict_proba_dict(models["btts"], X_btts)
    btts_proba = _class_probability(btts_probs, 1)

    # Over 2.5 : classifieur si présent, sinon régresseur avec sigmoïde
    if hasattr(models["over"], "predict_proba"):
        over_probs = _predict_proba_dict(models["over"], X_over)
        over_proba = _class_probability(over_probs, 1)
    else:
        goals_pred = float(models["over"].predict(X_over)[0])
        over_proba = round(1 / (1 + math.exp(-(goals_pred - 2.5))), 4)

    return {
        **winner_predictions,
        "Over 2.5": round(float(over_proba), 4),
        "BTTS":     round(float(btts_proba), 4),
    }


def generate_coupons(match: dict, require_value_bet: bool = True) -> list[dict]:
    """
    Applique les seuils métier et calcule l'EV si la cote est présente.
    """
    features   = match.get("features", {})
    odds       = match.get("odds", {})
    probas     = predict_match(features, league=match.get("league", ""))
    coupons    = []

    for market, confidence in probas.items():
        threshold     = THRESHOLDS.get(market, 1.0)
        bookmaker_odd = odds.get(market) if odds else None

        if confidence >= threshold:
            # Cas 1 : Cote disponible -> Calcul EV
            if bookmaker_odd is not None and bookmaker_odd > 1.0:
                ev = (confidence * bookmaker_odd) - 1.0

                if ev > 0:
                    coupons.append({
                        "league":          match.get("league"),
                        "home_team":       match.get("home_team"),
                        "away_team":       match.get("away_team"),
                        "match_time":      match.get("match_time"),
                        "prediction_type": market,
                        "confidence_rate": confidence,
                        "odd":             bookmaker_odd,
                        "expected_value":  round(ev, 4),
                        "is_value_bet":    True,
                        "status":          match.get("status", "En attente"),
                        "event_id":        match.get("event_id"),
                    })

            # Cas 2 : Cote absente mais autorisée par require_value_bet=False
            elif not require_value_bet:
                coupons.append({
                    "league":          match.get("league"),
                    "home_team":       match.get("home_team"),
                    "away_team":       match.get("away_team"),
                    "match_time":      match.get("match_time"),
                    "prediction_type": market,
                    "confidence_rate": confidence,
                    "odd":             None,
                    "expected_value":  None,
                    "is_value_bet":    False,
                    "status":          match.get("status", "Cote manquante"),
                    "event_id":        match.get("event_id"),
                })

    return coupons