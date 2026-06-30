"""
db.py (Optimisé pour Vercel Serverless)
"""
import os
import time
import psycopg2
from dotenv import load_dotenv

load_dotenv()

_DB_URL = os.environ.get("DATABASE_URL", "")


def get_conn(retries: int = 3, delay: float = 1.0):
    """
    Ouvre une connexion Neon avec retry automatique.
    Neon peut mettre quelques ms à sortir du mode "sleep" (cold start).
    """
    if not _DB_URL:
        raise RuntimeError(
            "DATABASE_URL non définie. "
            "Vérifie ton fichier .env ou les variables Vercel."
        )
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            return psycopg2.connect(_DB_URL)
        except psycopg2.OperationalError as exc:
            last_exc = exc
            print(f"[db] Tentative {attempt}/{retries} échouée : {exc}")
            if attempt < retries:
                time.sleep(delay * attempt)
    raise last_exc


def execute(query: str, params: tuple = (), fetch: bool = False):
    """Exécute une requête et ferme immédiatement la connexion."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(query, params)
            if fetch:
                cols = [desc[0] for desc in cur.description]
                rows = cur.fetchall()
                return [dict(zip(cols, row)) for row in rows]
            conn.commit()
            return cur.rowcount
    except Exception:
        conn.rollback()
        raise
    finally:
        # Crucial sur Vercel : On ferme TOUJOURS la connexion immédiatement
        conn.close()