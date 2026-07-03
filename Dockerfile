FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Installer dépendances système nécessaires pour psycopg2 / compilation native
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    gcc \
    g++ \
    wget \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copier les requirements et installer les dépendances Python
COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip setuptools wheel \
    && pip install --no-cache-dir -r requirements.txt

# Copier le code de l'application
COPY . /app

# Exposer le port de l'API
EXPOSE 8000

# Variables d'environnement par défaut (à surcharger en prod)
ENV DATABASE_URL="" \
    CRON_SECRET=""

# Commande par défaut : démarre l'API FastAPI via Uvicorn
CMD ["uvicorn", "ia_betpredict.backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
