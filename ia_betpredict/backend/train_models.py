"""
train_models.py
---------------
Entraîne des modèles XGBoost spécialisés par groupe de ligues.

Groupes :
  - nordique      : Veikkausliiga + Eliteserien
  - americain     : MLS
  - sud_americain : Serie A Brasil
  - amicaux       : Club Friendlies
  - global        : toutes ligues combinées (fallback)

Usage :
    python train_models.py

Produit dans models/ :
  model_winner_{group}.pkl
  model_goals_{group}.pkl
  model_btts_{group}.pkl
"""

import os
import warnings
import joblib
import pandas as pd
import numpy as np
from xgboost import XGBClassifier, XGBRegressor
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, mean_absolute_error
from sklearn.preprocessing import LabelEncoder

warnings.filterwarnings("ignore")

DATA_FILE  = os.path.join(os.path.dirname(__file__), "..", "..", "data", "training_data.csv")
MODELS_DIR = os.path.join(os.path.dirname(__file__), "..", "models")

FEATURE_COLS = [
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

# Hyperparamètres XGBoost
XGB_PARAMS = {
    "n_estimators":     200,
    "max_depth":        4,
    "learning_rate":    0.05,
    "subsample":        0.8,
    "colsample_bytree": 0.8,
    "random_state":     42,
    "eval_metric":      "logloss",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def train_group(df: pd.DataFrame, group_name: str, label: str):
    """Entraîne les 3 modèles pour un groupe et les sauvegarde."""
    print(f"\n{'─'*50}")
    print(f"Groupe : {label} ({len(df)} matchs)")

    if len(df) < 50:
        print(f"  ⚠️  Pas assez de données ({len(df)} < 50), groupe ignoré.")
        return

    X = df[FEATURE_COLS].copy()

    # ── model_winner (Double Chance) ──────────────────────────────────────
    # Cible : 0=1X (home win ou draw), 1=X2 (away win ou draw), 2=home win seul
    # On simplifie : 0 = home gagne ou nul, 1 = away gagne ou nul
    y_dc = df["dc_1x"]  # binaire : 1 si 1X, 0 sinon
    X_tr, X_te, y_tr, y_te = train_test_split(X, y_dc, test_size=0.2, random_state=42)
    model_dc = XGBClassifier(**XGB_PARAMS)
    model_dc.fit(X_tr, y_tr, eval_set=[(X_te, y_te)], verbose=False)
    acc = accuracy_score(y_te, model_dc.predict(X_te))
    print(f"  model_winner  → accuracy={acc:.3f} ({len(X_tr)} train / {len(X_te)} test)")
    joblib.dump(model_dc, os.path.join(MODELS_DIR, f"model_winner_{group_name}.pkl"))

    # ── model_goals (Over 2.5) ────────────────────────────────────────────
    y_goals = df["over25"]
    X_tr, X_te, y_tr, y_te = train_test_split(X, y_goals, test_size=0.2, random_state=42)
    model_goals = XGBClassifier(**XGB_PARAMS)
    model_goals.fit(X_tr, y_tr, eval_set=[(X_te, y_te)], verbose=False)
    acc = accuracy_score(y_te, model_goals.predict(X_te))
    print(f"  model_goals   → accuracy={acc:.3f}")
    joblib.dump(model_goals, os.path.join(MODELS_DIR, f"model_goals_{group_name}.pkl"))

    # ── model_btts (Both Teams To Score) ─────────────────────────────────
    y_btts = df["btts"]
    X_tr, X_te, y_tr, y_te = train_test_split(X, y_btts, test_size=0.2, random_state=42)
    model_btts = XGBClassifier(**XGB_PARAMS)
    model_btts.fit(X_tr, y_tr, eval_set=[(X_te, y_te)], verbose=False)
    acc = accuracy_score(y_te, model_btts.predict(X_te))
    print(f"  model_btts    → accuracy={acc:.3f}")
    joblib.dump(model_btts, os.path.join(MODELS_DIR, f"model_btts_{group_name}.pkl"))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def train():
    print(f"Chargement des données : {DATA_FILE}")
    df = pd.read_csv(DATA_FILE)
    print(f"  {len(df)} matchs chargés")
    print(f"  Distribution : {df['group'].value_counts().to_dict()}")

    os.makedirs(MODELS_DIR, exist_ok=True)

    # Entraîner un modèle par groupe
    groups = {
        "nordique":      ("Nordique (Veikkausliiga + Eliteserien)", df[df["group"] == "nordique"]),
        "americain":     ("Américain (MLS)",                        df[df["group"] == "americain"]),
        "sud_americain": ("Sud-Américain (Serie A Brasil)",          df[df["group"] == "sud_americain"]),
        "amicaux":       ("Amicaux (Club Friendlies)",               df[df["group"] == "amicaux"]),
        "global":        ("Global (toutes ligues)",                  df),
    }

    for group_name, (label, subset) in groups.items():
        train_group(subset, group_name, label)

    print(f"\n✅ Modèles sauvegardés dans {MODELS_DIR}/")
    print("   Fichiers générés :")
    for f in sorted(os.listdir(MODELS_DIR)):
        if f.endswith(".pkl"):
            size = os.path.getsize(os.path.join(MODELS_DIR, f)) / 1024
            print(f"   {f:45s} {size:6.1f} KB")


if __name__ == "__main__":
    train()
