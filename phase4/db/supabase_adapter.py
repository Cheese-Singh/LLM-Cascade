from __future__ import annotations

import logging
from typing import Any, Dict, Optional
from phase4.config import SUPABASE_DB_URL, SQLITE_DB_PATH
from phase4.db.sqlite_fixture import get_sqlite_connection, init_sqlite_db

logger = logging.getLogger(__name__)


class DatabaseAdapter:
    """Zero-secret database adapter supporting Supabase/PostgreSQL with automatic SQLite fallback."""

    def __init__(self, db_url: Optional[str] = SUPABASE_DB_URL):
        self.db_url = db_url
        self.is_supabase = bool(db_url and ("supabase" in db_url or "postgresql" in db_url))
        self._sqlite_conn = None

    def get_connection(self):
        if self.is_supabase:
            try:
                import psycopg2
                return psycopg2.connect(self.db_url)
            except Exception as e:
                logger.warning("Failed to connect to Supabase/PostgreSQL (%s). Falling back to local SQLite fixture.", e)
                self.is_supabase = False
        
        if self._sqlite_conn is None:
            self._sqlite_conn = get_sqlite_connection(SQLITE_DB_PATH)
            init_sqlite_db(self._sqlite_conn)
        return self._sqlite_conn

    def ping(self) -> Dict[str, Any]:
        """Test database connectivity."""
        try:
            conn = self.get_connection()
            if self.is_supabase:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1;")
                    return {"status": "connected", "type": "supabase_postgresql"}
            else:
                cur = conn.cursor()
                cur.execute("SELECT 1;")
                return {"status": "connected", "type": "sqlite_fixture", "path": str(SQLITE_DB_PATH)}
        except Exception as e:
            return {"status": "error", "error": str(e)}
