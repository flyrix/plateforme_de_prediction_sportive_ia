"""
db.py
-----
Connexion PostgreSQL via Neon (ou tout PostgreSQL standard).
Utilise psycopg2 en mode synchrone + un pool de connexions léger.

Neon fournit une DATABASE_URL au format :
  postgresql://user:password@host.neon.tech/dbname?sslmode=require
"""

import os
import psycopg2
from psycopg2 import pool
from dotenv import load_dotenv

load_dotenv()

_pool: pool.SimpleConnectionPool | None = None


def _get_pool() -> pool.SimpleConnectionPool:
    global _pool
    if _pool is None:
        _pool = pool.SimpleConnectionPool(
            minconn=1,
            maxconn=5,
            dsn=os.environ["DATABASE_URL"],
        )
    return _pool


def get_conn():
    """Retourne une connexion depuis le pool."""
    return _get_pool().getconn()


def release_conn(conn):
    """Remet la connexion dans le pool."""
    _get_pool().putconn(conn)


def execute(query: str, params: tuple = (), fetch: bool = False):
    """
    Exécute une requête SQL.
    - fetch=False  → INSERT / UPDATE / DELETE (retourne le nombre de lignes)
    - fetch=True   → SELECT (retourne list[dict])
    """
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
        release_conn(conn)