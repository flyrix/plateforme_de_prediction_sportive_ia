# plateforme_de_prediction_sportive_ia
# IA-BetPredict 🤖⚽

Plateforme de prédictions sportives par IA sur 4 ligues d'été.

## Stack

| Couche | Technologie |
|---|---|
| Backend & IA | Python 3.12 · FastAPI · XGBoost |
| Base de données | Neon (PostgreSQL serverless, gratuit illimité) |
| Scheduler | APScheduler (cron 00:00 UTC) |
| Frontend | HTML5 · CSS3 · JS Vanilla (PWA) |
| Déploiement API | Railway ou Render |
| Déploiement Front | Vercel ou Netlify |

---

## Installation locale

### 1. Cloner le projet
```bash
git clone https://github.com/ton-username/ia-betpredict.git
cd ia-betpredict
```

### 2. Backend
```bash
cd backend
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Variables d'environnement
```bash
cp ../.env.example ../.env
# Édite .env avec ta DATABASE_URL Neon
```

### 4. Modèles XGBoost
Exporte depuis Google Colab :
```python
import joblib
joblib.dump(model_dc,     "model_dc.pkl")
joblib.dump(model_over25, "model_over25.pkl")
joblib.dump(model_btts,   "model_btts.pkl")
joblib.dump(model_1n2,    "model_1n2.pkl")
```
Place les `.pkl` dans `backend/models/`.

### 5. Base de données Neon
- Crée un compte gratuit sur [neon.tech](https://neon.tech)
- Nouveau projet → copie la **Connection string** dans ton `.env`
- Dans **SQL Editor**, exécute `backend/schema.sql`

### 6. Lancer l'API
```bash
cd backend
uvicorn main:app --reload --port 8000
```

### 7. Lancer le frontend
Ouvre `frontend/index.html` dans ton navigateur,
ou utilise l'extension Live Server de VS Code.

---

## Endpoints API

| Méthode | Route | Description |
|---|---|---|
| GET | `/` | Healthcheck |
| GET | `/coupons` | Coupons du jour |
| GET | `/coupons/{date}` | Coupons d'une date (YYYY-MM-DD) |
| POST | `/run-daily-job` | Déclenche le job manuellement |

### Filtres disponibles
```
GET /coupons?league=MLS&min_confidence=0.70
```

---

## Structure du projet

```
ia-betpredict/
├── backend/
│   ├── main.py          # FastAPI + routes
│   ├── scraper.py       # Sofascore data fetcher
│   ├── predictor.py     # Chargement .pkl + génération coupons
│   ├── scheduler.py     # Cron job 00:00 UTC
│   ├── db.py            # Connexion PostgreSQL (Neon)
│   ├── schema.sql       # Schéma à exécuter dans Neon
│   ├── models/          # Tes fichiers .pkl ici
│   └── requirements.txt
├── frontend/
│   ├── index.html
│   ├── style.css
│   ├── app.js
│   └── manifest.json
└── .env.example
```

---

## Déploiement (Sprint 4)

### API sur Railway
```bash
cd backend
railway login
railway init
railway up
```
Ajoute la variable `DATABASE_URL` dans le dashboard Railway.

### Frontend sur Vercel
```bash
vercel --cwd frontend
```

---

## Ligues cibles

| Pays | Ligue | ID Sofascore |
|---|---|---|
| 🇫🇮 Finlande | Veikkausliiga | 238 |
| 🇳🇴 Norvège | Eliteserien | 36 |
| 🇺🇸 États-Unis | MLS | 242 |
| 🇧🇷 Brésil | Série A | 325 |

---

## Seuils de confiance (règles métier)

| Marché | Seuil minimum |
|---|---|
| Double Chance 1X / X2 | ≥ 65% |
| Over 2.5 | ≥ 60% |
| BTTS | ≥ 60% |