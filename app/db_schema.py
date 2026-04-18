INIT_DB_SQL = """
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
    id            TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
    tenant_id     TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    agent_id      TEXT REFERENCES tenant_agents(id) ON DELETE SET NULL,
    machine_id    TEXT NOT NULL DEFAULT '',
    hostname      TEXT NOT NULL DEFAULT '',
    severity      TEXT NOT NULL DEFAULT 'INFO',
    event_type    TEXT NOT NULL DEFAULT '',
    message       TEXT NOT NULL DEFAULT '',
    file_path     TEXT NOT NULL DEFAULT '',
    acknowledged  BOOLEAN NOT NULL DEFAULT FALSE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS tenant_config (
    tenant_id       TEXT PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
    webhook_url     TEXT NOT NULL DEFAULT '',
    alert_email     TEXT NOT NULL DEFAULT '',
    verify_interval INTEGER NOT NULL DEFAULT 60,
    max_vault_mb    INTEGER NOT NULL DEFAULT 10,
    allowed_exts    TEXT NOT NULL DEFAULT '.txt,.json,.py,.html,.js,.css'
);

CREATE TABLE IF NOT EXISTS website_inquiries (
    id            TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
    kind          TEXT NOT NULL DEFAULT 'contact',
    page_source   TEXT NOT NULL DEFAULT '/contact',
    company       TEXT NOT NULL DEFAULT '',
    contact_name  TEXT NOT NULL DEFAULT '',
    email         TEXT NOT NULL DEFAULT '',
    phone         TEXT NOT NULL DEFAULT '',
    seats         TEXT NOT NULL DEFAULT '',
    subject       TEXT NOT NULL DEFAULT '',
    message       TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'new',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tenant_agents_tenant
    ON tenant_agents(tenant_id);
CREATE INDEX IF NOT EXISTS idx_tenant_alerts_tenant_sev
    ON tenant_alerts(tenant_id, severity, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_website_inquiries_created
    ON website_inquiries(created_at DESC);

DO $$ BEGIN
  IF NOT EXISTS (
      SELECT 1 FROM information_schema.columns
      WHERE table_name='licenses' AND column_name='machine_id'
  ) THEN
      ALTER TABLE licenses ADD COLUMN machine_id TEXT DEFAULT NULL;
  END IF;
END $$;
"""

TENANT_MIGRATION_SQL = """
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
CREATE TABLE IF NOT EXISTS website_inquiries (
    id            TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
    kind          TEXT NOT NULL DEFAULT 'contact',
    page_source   TEXT NOT NULL DEFAULT '/contact',
    company       TEXT NOT NULL DEFAULT '',
    contact_name  TEXT NOT NULL DEFAULT '',
    email         TEXT NOT NULL DEFAULT '',
    phone         TEXT NOT NULL DEFAULT '',
    seats         TEXT NOT NULL DEFAULT '',
    subject       TEXT NOT NULL DEFAULT '',
    message       TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'new',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_tenant_agents_tenant
    ON tenant_agents(tenant_id);
CREATE INDEX IF NOT EXISTS idx_tenant_alerts_tenant_sev
    ON tenant_alerts(tenant_id, severity, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_website_inquiries_created
    ON website_inquiries(created_at DESC);
"""
