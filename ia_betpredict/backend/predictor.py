"""
predictor.py
------------
Charge les modèles XGBoost / Scikit-Learn (.pkl) et expose la fonction unique
generate_coupons(match) qui retourne les paris éligibles selon les
seuils de confiance définis dans le cahier des charges.

Prend en charge les modèles spécialisés par groupe de ligues :
- Nordique
- Américain
- Sud-Américain
- Amicaux
- Top 5 Europe (Premier League, LaLiga, Bundesliga, Serie A)
- Global (Fallback)
"""

import os
import math
import logging
import random
from typing import Any

import joblib
import pandas as pd

# Configuration du logging
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)s] %(message)s")
logger = logging.getLogger("predictor")

# ---------------------------------------------------------------------------
# Seuils de confiance (règles métier du CDC)
# ---------------------------------------------------------------------------

THRESHOLDS: dict[str, float] = {
    "Double Chance 1X": 0.50,
    "Double Chance X2": 0.50,
    "Over 2.5":         0.50,
    "BTTS":             0.50,
}

# ---------------------------------------------------------------------------
# Encodage pays (compatibilité legacy scraper)
# ---------------------------------------------------------------------------
COUNTRY_ENCODING: dict[str, int] = {}

# ---------------------------------------------------------------------------
# Mapping ligue → groupe (pour charger le bon modèle spécialisé)
# ---------------------------------------------------------------------------

LEAGUE_TO_GROUP: dict[str, str] = {
    # Nordique
    "Veikkausliiga":         "nordique",
    "Eliteserien":           "nordique",

    # Américain
    "MLS":                   "americain",
    "USL Championship":      "americain",
    "USL League One":        "americain",
    "USL League Two":        "americain",
    "NPSL":                  "americain",

    # Sud-Américain
    "Serie A Brasil":        "sud_americain",

    # Amicaux
    "Club Friendlies":       "amicaux",
    "Women Club Friendlies": "amicaux",

    # Top 5 Europe
    "Premier League":        "europe_top5",
    "LaLiga":                "europe_top5",
    "Liga":                  "europe_top5",
    "Bundesliga":            "europe_top5",
    "Serie A":               "europe_top5",
}

# ---------------------------------------------------------------------------
# Chargement des modèles (une seule fois au démarrage)
# ---------------------------------------------------------------------------

_MODELS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "models")


def _load(filename: str) -> Any:
    """Charge un fichier .pkl en testant d'abord le dossier /models/, puis la racine."""
    path_in_models = os.path.normpath(os.path.join(_MODELS_DIR, filename))
    path_root = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), filename))
    
    if os.path.exists(path_in_models):
        target_path = path_in_models
    elif os.path.exists(path_root):
        target_path = path_root
    else:
        raise FileNotFoundError(f"Modèle introuvable: {filename}")

    try:
        return joblib.load(target_path)
    except ModuleNotFoundError as exc:
        if exc.name == "xgboost":
            logger.error("Le module xgboost n'est pas installé dans le virtuel environnement.")
            raise
        raise


def _load_group(group: str) -> dict[str, Any] | None:
    """Charge les 3 modèles spécialisés pour un groupe. Retourne None si absents."""
    try:
        return {
            "dc":   _load(f"model_winner_{group}.pkl"),
            "over": _load(f"model_goals_{group}.pkl"),
            "btts": _load(f"model_btts_{group}.pkl"),
        }
    except (FileNotFoundError, ModuleNotFoundError) as e:
        logger.debug(f"Impossible de charger le groupe '{group}': {e}")
        return None


# Chargement de tous les groupes spécialisés (y compris 'europe_top5' et 'global')
_SPECIALIZED: dict[str, dict[str, Any]] = {}
_LEGACY_WINNER_WARNING_EMITTED = False

GROUPS_TO_LOAD = ["europe_top5", "nordique", "americain", "sud_americain", "amicaux", "global"]

for _g in GROUPS_TO_LOAD:
    _m = _load_group(_g)
    if _m:
        _SPECIALIZED[_g] = _m
        logger.info(f"✅ Modèle spécialisé '{_g}' chargé.")

# Validation de la présence des modèles
if "global" in _SPECIALIZED or len(_SPECIALIZED) > 0:
    _MODELS_LOADED = True
    _GLOBAL = _SPECIALIZED.get("global")
else:
    logger.warning("⚠️ Aucun modèle trouvé dans le dossier /models/ ni à la racine. MODE DÉMO activé.")
    _GLOBAL = None
    _MODELS_LOADED = False


# ---------------------------------------------------------------------------
# Features attendues par les modèles
# ---------------------------------------------------------------------------

FEATURE_COLUMNS: list[str] = [
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

FEATURE_COLUMNS_LEGACY: list[str] = FEATURE_COLUMNS + ["Country_encoded"]


def _model_feature_names(model: Any) -> list[str] | None:
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


def _features_to_df(features: dict[str, Any], model: Any = None, legacy: bool = False) -> pd.DataFrame:
    """Convertit le dictionnaire de caractéristiques en un DataFrame aligné sur le modèle."""
    cols = _model_feature_names(model) if model is not None else None
    if not cols:
        cols = FEATURE_COLUMNS_LEGACY if legacy else FEATURE_COLUMNS
        logger.debug(f"Colonnes du modèle non détectables, repli sur la liste par défaut ({len(cols)} features).")
    
    row = {col: features.get(col, 0.0) for col in cols}
    missing = [c for c in cols if c not in features]
    if missing:
        logger.debug(f"Features absentes du dictionnaire, valeur 0.0 utilisée: {missing}")
    
    return pd.DataFrame([row])


# ---------------------------------------------------------------------------
# Génération des prédictions
# ---------------------------------------------------------------------------

def _predict_proba_dict(model: Any, X: pd.DataFrame) -> dict[str, float]:
    """Retourne un dict classe -> probabilité pour un modèle binaire ou multiclasse."""
    probs = model.predict_proba(X)[0]
    classes = list(getattr(model, "classes_", range(len(probs))))
    if len(classes) != len(probs):
        raise ValueError("Incohérence entre classes et probabilités du modèle")
    return {str(classes[i]): float(probs[i]) for i in range(len(classes))}


def _class_probability(probas: dict[str, float], class_id: int | str) -> float:
    """Extrait la probabilité d'une classe sous forme de float."""
    return float(probas.get(str(class_id), 0.0))


def _double_chance_predictions(model: Any, X: pd.DataFrame) -> dict[str, float]:
    """
    Calcule 1X/X2 depuis un modèle résultat 3 classes :
      0 = domicile, 1 = nul, 2 = extérieur.
    Retient uniquement la Double Chance la plus probable pour le match.
    """
    global _LEGACY_WINNER_WARNING_EMITTED

    probs = _predict_proba_dict(model, X)
    class_labels = set(probs.keys())

    if {"0", "1", "2"}.issubset(class_labels):
        home = _class_probability(probs, 0)
        draw = _class_probability(probs, 1)
        away = _class_probability(probs, 2)
        
        prob_1x = round(home + draw, 4)
        prob_x2 = round(draw + away, 4)

        # Sélection de la meilleure Double Chance uniquement
        if prob_1x >= prob_x2:
            return {"Double Chance 1X": prob_1x}
        else:
            return {"Double Chance X2": prob_x2}

    if {"0", "1"}.issubset(class_labels):
        if not _LEGACY_WINNER_WARNING_EMITTED:
            logger.warning("Modèle winner legacy binaire : X2 ignoré jusqu'au réentraînement.")
            _LEGACY_WINNER_WARNING_EMITTED = True
        return {"Double Chance 1X": round(_class_probability(probs, 1), 4)}

    raise ValueError(f"Classes inattendues pour model_winner: {sorted(class_labels)}")


def predict_match(features: dict[str, Any], league: str = "") -> dict[str, float]:
    """
    Utilise le modèle spécialisé pour la ligue si disponible, sinon le modèle 'global'.
    """
    if not _MODELS_LOADED and not _SPECIALIZED:
        logger.warning("MODE DÉMO activé - Génération de probabilités aléatoires.")
        
        # En mode Démo, on tire au sort 1X ou X2 pour ne garder qu'une seule Double Chance
        dc_choice = "Double Chance 1X" if random.random() > 0.5 else "Double Chance X2"
        return {
            dc_choice:          round(random.uniform(0.50, 0.90), 4),
            "Over 2.5":         round(random.uniform(0.45, 0.85), 4),
            "BTTS":             round(random.uniform(0.45, 0.85), 4),
        }

    # Détermination du groupe ciblé (Ex: Premier League -> europe_top5)
    group = LEAGUE_TO_GROUP.get(league, "")
    
    # Fallback dynamique au cas où le nom du championnat diffère légèrement
    if not group and league:
        for l_name, g_name in LEAGUE_TO_GROUP.items():
            if l_name.lower() in league.lower():
                group = g_name
                break

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


def generate_coupons(match: dict[str, Any], require_value_bet: bool = True) -> list[dict[str, Any]]:
    """
    Applique les seuils métier et calcule l'EV si la cote est présente.
    Structure de clés 100% alignée avec main.py et Neon.
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
                        "odds":            bookmaker_odd,          # Alignement avec main.py
                        "ev":              round(ev, 4),           # Alignement avec main.py
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
                    "odds":            1.0,
                    "ev":              0.0,
                    "expected_value":  0.0,
                    "is_value_bet":    False,
                    "status":          match.get("status", "Cote manquante"),
                    "event_id":        match.get("event_id"),
                })

    return coupons