"""
core/database.py — Database connection, schema initialisation, and background sweeper.
"""
import time
import threading
import psycopg2
from psycopg2.extras import RealDictCursor

from core.config import DATABASE_URL, APP_BASE_URL


def get_db():
    """Open and return a new psycopg2 connection with RealDictCursor."""
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


def init_db():
    """
    Create all tables (idempotent — safe to run on every startup).
    Also seeds the versions table with a starter row if it is empty.
    """
    conn = get_db()
    cur  = conn.cursor()

    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS licenses (
                license_key TEXT PRIMARY KEY,
                email       TEXT NOT NULL,
                tier        TEXT NOT NULL DEFAULT 'pro_monthly',
                payment_id  TEXT,
                order_id    TEXT,
                expires_at  TIMESTAMPTZ NOT NULL,
                active      BOOLEAN NOT NULL DEFAULT TRUE,
                machine_id  TEXT DEFAULT NULL,
                created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS pending_orders (
                order_id   TEXT PRIMARY KEY,
                email      TEXT NOT NULL,
                tier       TEXT NOT NULL,
                amount     INTEGER NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS versions (
                id            SERIAL PRIMARY KEY,
                version       TEXT NOT NULL,
                release_notes TEXT NOT NULL DEFAULT '',
                download_url  TEXT NOT NULL DEFAULT '',
                changelog_url TEXT NOT NULL DEFAULT '',
                published_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                is_current    BOOLEAN NOT NULL DEFAULT TRUE
            );

            CREATE TABLE IF NOT EXISTS tenants (
                id            TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
                name          TEXT NOT NULL,
                slug          TEXT UNIQUE NOT NULL,
                api_key       TEXT UNIQUE NOT NULL,
                plan          TEXT NOT NULL DEFAULT 'business',
                max_agents    INTEGER NOT NULL DEFAULT 10,
                contact_email TEXT NOT NULL DEFAULT '',
                notes         TEXT NOT NULL DEFAULT '',
                active        BOOLEAN NOT NULL DEFAULT TRUE,
                created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS tenant_agents (
                id            TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
                tenant_id     TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                machine_id    TEXT NOT NULL,
                hostname      TEXT NOT NULL DEFAULT '',
                ip_address    TEXT NOT NULL DEFAULT '',
                os_info       TEXT NOT NULL DEFAULT '',
                agent_version TEXT NOT NULL DEFAULT '2.5.0',
                username      TEXT NOT NULL DEFAULT '',
                tier          TEXT NOT NULL DEFAULT 'free',
                is_armed      BOOLEAN NOT NULL DEFAULT FALSE,
                status        TEXT NOT NULL DEFAULT 'offline',
                last_seen     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                registered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE(tenant_id, machine_id)
            );

            CREATE TABLE IF NOT EXISTS tenant_users (
                id            TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
                tenant_id     TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                email         TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                role          TEXT NOT NULL DEFAULT 'admin',
                created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE(tenant_id, email)
            );

            CREATE TABLE IF NOT EXISTS tenant_alerts (
                id           TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
                tenant_id    TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                agent_id     TEXT REFERENCES tenant_agents(id) ON DELETE SET NULL,
                machine_id   TEXT NOT NULL DEFAULT '',
                hostname     TEXT NOT NULL DEFAULT '',
                severity     TEXT NOT NULL DEFAULT 'INFO',
                event_type   TEXT NOT NULL DEFAULT '',
                message      TEXT NOT NULL DEFAULT '',
                file_path    TEXT NOT NULL DEFAULT '',
                acknowledged BOOLEAN NOT NULL DEFAULT FALSE,
                created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS tenant_config (
                tenant_id       TEXT PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
                webhook_url     TEXT NOT NULL DEFAULT '',
                alert_email     TEXT NOT NULL DEFAULT '',
                verify_interval INTEGER NOT NULL DEFAULT 60,
                max_vault_mb    INTEGER NOT NULL DEFAULT 10,
                allowed_exts    TEXT NOT NULL DEFAULT '.txt,.json,.py,.html,.js,.css'
            );

            -- Indexes for fast lookups
            CREATE INDEX IF NOT EXISTS idx_tenant_agents_tenant
                ON tenant_agents(tenant_id);

            CREATE INDEX IF NOT EXISTS idx_tenant_alerts_tenant_sev
                ON tenant_alerts(tenant_id, severity, created_at DESC);

            -- Migration: add machine_id to licenses if missing
            DO $$ BEGIN
              IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='licenses' AND column_name='machine_id'
              ) THEN
                ALTER TABLE licenses ADD COLUMN machine_id TEXT DEFAULT NULL;
              END IF;
            END $$;

            -- Migration: add payment_id / order_id if missing
            ALTER TABLE licenses ADD COLUMN IF NOT EXISTS payment_id TEXT;
            ALTER TABLE licenses ADD COLUMN IF NOT EXISTS order_id   TEXT;
        """)

        # Seed versions table if empty
        cur.execute("SELECT COUNT(*) FROM versions")
        row = cur.fetchone()
        row_count = row["count"] if isinstance(row, dict) else row[0]

        if row_count == 0:
            cur.execute("""
                INSERT INTO versions
                    (version, release_notes, download_url, changelog_url, is_current)
                VALUES (%s, %s, %s, %s, TRUE)
            """, (
                "2.5.0",
                "Initial release",
                f"{APP_BASE_URL}/download",
                f"{APP_BASE_URL}/changelog",
            ))

        conn.commit()
        print("[DB] Tables ready.")

    except Exception as e:
        conn.rollback()
        print(f"[DB] Error initialising database: {e}")
        raise

    finally:
        cur.close()
        conn.close()


def _start_offline_sweeper():
    """
    Background thread: marks agents as 'offline' when last_seen > 45 s ago.

    Industry pattern (CrowdStrike, SentinelOne):
      Agent heartbeat interval: 10 s
      Grace period before marking offline: 45 s (4.5× heartbeat)
      Sweep frequency: 30 s
    """
    def _sweep():
        while True:
            try:
                if DATABASE_URL:
                    conn = get_db(); cur = conn.cursor()
                    cur.execute(
                        "UPDATE tenant_agents SET status = 'offline' "
                        "WHERE status = 'online' "
                        "AND last_seen < NOW() - INTERVAL '45 seconds'"
                    )
                    affected = cur.rowcount
                    conn.commit(); cur.close(); conn.close()
                    if affected > 0:
                        print(f"[SWEEPER] Marked {affected} agent(s) offline.")
            except Exception as e:
                print(f"[SWEEPER] Error (non-critical): {e}")
            time.sleep(30)

    t = threading.Thread(target=_sweep, daemon=True, name="FMSecure-OfflineSweeper")
    t.start()
    print("[SWEEPER] Offline sweeper started (30 s interval, 45 s grace).")
