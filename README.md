# IA-BetPredict 🤖⚽

Plateforme de prédictions sportives par IA sur 4 ligues d'été.

## Stack

| Couche | Technologie |
|---|---|
| Backend & IA | Python 3.12 · FastAPI · XGBoost |
| Base de données | Neon (PostgreSQL serverless, gratuit illimité) |
| Scheduler | Vercel Cron (cron 00:00 UTC) |
| Frontend | HTML5 · CSS3 · JS Vanilla (PWA) |
| Déploiement API | Vercel (Serverless Python) |
| Déploiement Front | Vercel |

---

## Installation locale

### 1. Cloner le projet
```bash
git clone https://github.com/ton-username/ia-betpredict.git
cd ia-betpredict
```

### 2. Backend
```bash
cd ia_betpredict/backend
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Variables d'environnement
```bash
cp ia_betpredict/.env.example ia_betpredict/.env
# Édite .env avec ta DATABASE_URL Neon
```

Contenu du fichier `.env` (basé sur `.env.example`) :
```
DATABASE_URL=postgresql://<user>:<password>@<host>/<db>?sslmode=require
CRON_SECRET=une_cle_aleatoire_secrete
ALLOWED_ORIGINS=https://ia-betpredict.vercel.app
```

### 4. Modèles XGBoost
Exporte depuis Google Colab avec les noms exacts attendus :
```python
import joblib
joblib.dump(model_winner, "model_winner_global.pkl")   # Résultat 1N2 -> Double Chance
joblib.dump(model_goals,  "model_goals_global.pkl")    # Over 2.5
joblib.dump(model_btts,   "model_btts_global.pkl")     # Both Teams To Score
```
Place les `.pkl` dans `ia_betpredict/models/`.

### 5. Base de données Neon
- Crée un compte gratuit sur [neon.tech](https://neon.tech)
- Nouveau projet → copie la **Connection string** dans ton `.env`
- Dans **SQL Editor**, exécute `ia_betpredict/backend/schema.sql`

### 6. Lancer l'API
```bash
cd ia_betpredict/backend
uvicorn main:app --reload --port 8000
```

### 7. Lancer le frontend
Ouvre `index.html` dans ton navigateur,
ou utilise l'extension Live Server de VS Code.

---

## Endpoints API

| Méthode | Route | Description |
|---|---|---|
| GET | `/` | Healthcheck |
| GET | `/coupons` | Coupons du jour |
| GET | `/coupons/{date}` | Coupons d'une date (YYYY-MM-DD) |
| GET | `/matches/today` | Matchs ciblés du jour |
| GET | `/matches/inplay` | Matchs ciblés en cours |
| GET | `/predictions/live` | Prédictions sur matchs live ciblés |
| PATCH | `/coupons/{id}/status` | Mise à jour statut (Gagné/Perdu/En attente) |
| POST | `/run-daily-job` | Déclenche le job manuellement |

### Filtres disponibles
```
GET /coupons?league=MLS&min_confidence=0.70
GET /coupons/2025-07-10?league=Eliteserien&min_confidence=0.65
```

### Sécurisation du job cron
Le endpoint `/run-daily-job` et `PATCH /coupons/{id}/status` sont protégés
par le header `X-Cron-Secret`. Définis `CRON_SECRET` dans Vercel et utilise-le :
```bash
curl -X POST https://ton-api.vercel.app/run-daily-job \
  -H "X-Cron-Secret: ton_secret"
```

---

## Structure du projet

```
ia-betpredict/
├── ia_betpredict/
│   ├── backend/
│   │   ├── main.py          # FastAPI + routes + Vercel Cron job
│   │   ├── scraper.py       # Sofascore fetcher + ScrapingAnt fallback
│   │   ├── predictor.py     # Chargement .pkl + génération coupons
│   │   ├── db.py            # Connexion PostgreSQL (Neon, serverless-safe + retry)
│   │   ├── schema.sql       # Schéma à exécuter dans Neon
│   │   └── requirements.txt
│   ├── models/
│   │   ├── model_winner_{group}.pkl # Résultat 1N2 -> Double Chance
│   │   ├── model_goals_{group}.pkl  # Over 2.5
│   │   └── model_btts_{group}.pkl   # Both Teams To Score
│   └── .env                 # Variables d'environnement (ne pas commiter)
├── index.html               # Frontend PWA
├── style.css
├── app.js
├── manifest.json            # Manifest PWA
├── vercel.json              # Config Vercel (builds + cron)
└── .env.example             # Template des variables d'environnement
```

---

## Déploiement sur Vercel

### API + Frontend sur Vercel
```bash
vercel login
vercel
```
Ajoute la variable `DATABASE_URL` dans le dashboard Vercel :
> Settings → Environment Variables → `DATABASE_URL`

Le fichier `vercel.json` configure automatiquement :
- Le runtime Python pour le backend FastAPI
- Le Cron job quotidien à 00:00 UTC (`/run-daily-job`)

### Frontend — variable d'environnement API
Pour pointer le frontend vers l'API de production, ajoute dans `index.html`
**avant** le `<script src="app.js">` :
```html
<script>window.ENV_API_BASE = "https://ton-api.vercel.app";</script>
```

---

## Ligues cibles

| Pays | Ligue | ID Sofascore |
|---|---|---|
| 🇫🇮 Finlande | Veikkausliiga | 41 |
| 🇳🇴 Norvège | Eliteserien | 20 |
| 🇺🇸 États-Unis | MLS | 242 |
| 🇧🇷 Brésil | Série A | 325 |
| 🤝 International | Club Friendly Games | 853 |

---

## Seuils de confiance (règles métier)

| Marché | Modèle | Seuil minimum |
|---|---|---|
| Double Chance 1X / X2 | `model_winner_{group}.pkl` | ≥ 65% |
| Over 2.5 | `model_goals_{group}.pkl` | ≥ 60% |
| BTTS | `model_btts_{group}.pkl` | ≥ 60% |

> En phase de vérification des modèles, les seuils actifs peuvent être abaissés
> dans `ia_betpredict/backend/predictor.py`.

---

## Scraping Sofascore

Le scraper utilise d'abord l'endpoint global Sofascore
`/sport/football/scheduled-events/{YYYY-MM-DD}` puis filtre les ligues cibles.
Cela évite de dépendre d'IDs de saison figés. Si cet endpoint échoue, un fallback
par ligue reste disponible.

Variables ScrapingAnt recommandées :
```
SCRAPINGANT_KEY=<ta-cle>
SCRAPINGANT_BROWSER=false
SCRAPINGANT_PROXY_TYPE=datacenter
SCRAPINGANT_PROXY_COUNTRY=
SCRAPINGANT_TIMEOUT=25
```

Si Sofascore bloque encore les requêtes, teste :
```
SCRAPINGANT_PROXY_TYPE=residential
SCRAPINGANT_PROXY_COUNTRY=US
```

---

## Notes importantes

- **Scraping Sofascore** : L'API utilisée est interne (reverse-engineered). En production, les IPs de datacenter peuvent être bloquées. Si c'est le cas, utilise `SCRAPINGANT_PROXY_TYPE=residential`.
- **Mode démo** : Si les fichiers `.pkl` sont absents, l'API tourne en mode démo avec des probabilités aléatoires. Un warning `⚠️ MODE DÉMO` est affiché dans les logs.
- **Unicité des prédictions** : Le `schema.sql` inclut une contrainte `UNIQUE (match_date, match_name, prediction_type)` pour éviter les doublons en cas de re-run du job.
