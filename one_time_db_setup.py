import os
import sys
import psycopg2
from psycopg2.extras import RealDictCursor

from app.db_schema import INIT_DB_SQL

DATABASE_URL = os.getenv("DATABASE_URL", "")
APP_BASE_URL = os.getenv("APP_BASE_URL", "http://localhost:8000")


def main() -> int:
    if not DATABASE_URL:
        print("ERROR: DATABASE_URL is not set.")
        print("Set Railway/Postgres env vars first, then rerun this script.")
        return 1

    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    cur = conn.cursor()
    try:
        print("[DB] Enabling pgcrypto extension if needed…")
        cur.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")
        print("[DB] Creating/verifying core tables…")
        cur.execute(INIT_DB_SQL)
        cur.execute("SELECT COUNT(*) FROM versions")
        row = cur.fetchone()
        count = row["count"] if isinstance(row, dict) else row[0]
        if count == 0:
            cur.execute(
                """
                INSERT INTO versions (version, release_notes, download_url, changelog_url, is_current)
                VALUES (%s, %s, %s, %s, TRUE)
                """,
                (
                    "2.5.0",
                    "Initial release",
                    f"{APP_BASE_URL}/download",
                    f"{APP_BASE_URL}/changelog",
                ),
            )
            print("[DB] Seeded initial release row.")
        conn.commit()
        print("[DB] All tables are ready.")
        return 0
    except Exception as exc:
        conn.rollback()
        print(f"[DB] Setup failed: {exc}")
        return 2
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
