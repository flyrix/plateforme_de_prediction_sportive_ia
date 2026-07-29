"""
db.py (Optimisé pour Vercel Serverless & Neon)
"""
import os
import time
import psycopg2
from dotenv import load_dotenv

load_dotenv()

_DB_URL = os.environ.get("DATABASE_URL", "")


def get_conn(retries: int = 3, delay: float = 1.0):
    if not _DB_URL:
        print("[db] ❌ DATABASE_URL non définie. Vérifie ton fichier .env ou les secrets GitHub/Vercel.")
        raise RuntimeError(
            "DATABASE_URL non définie. Vérifie ton fichier .env ou les secrets GitHub/Vercel."
        )
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            return psycopg2.connect(_DB_URL)
        except psycopg2.OperationalError as exc:
            last_exc = exc
            print(f"[db] ⚠️ Connexion Neon (tentative {attempt}/{retries}) échouée : {exc}")
            if attempt < retries:
                time.sleep(delay * attempt)
    raise last_exc


def execute(query: str, params: tuple = (), fetch: bool = False):
    """Exécute une requête unique et ferme la connexion."""
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
    except Exception as exc:
        conn.rollback()
        print(f"[db] ❌ Erreur lors de l'exécution de la requête : {exc}")
        raise
    finally:
        conn.close()


def execute_batch(query: str, params_list: list[tuple]):
    """Exécute un lot de requêtes en UNE seule transaction."""
    if not params_list:
        print("[db] ⚠️ Liste de paramètres vide pour execute_batch.")
        return 0
    conn = get_conn()
    inserted_count = 0
    try:
        with conn.cursor() as cur:
            for params in params_list:
                cur.execute(query, params)
                inserted_count += cur.rowcount
            conn.commit()
            print(f"[db] ✅ Transaction réussie : {inserted_count} ligne(s) affectée(s).")
            return inserted_count
    except Exception as exc:
        conn.rollback()
        print(f"[db] ❌ Rollback effectué suite à une erreur batch : {exc}")
        raise
    finally:
        conn.close()