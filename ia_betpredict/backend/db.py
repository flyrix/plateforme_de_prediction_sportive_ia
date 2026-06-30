"""
db.py (Optimisé pour Vercel Serverless)
"""
import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

def get_conn():
    """Ouvre une connexion unique pour la requête Serverless actuelle."""
    # Neon utilise le SSL obligatoire (?sslmode=require)
    return psycopg2.connect(os.environ["DATABASE_URL"])


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