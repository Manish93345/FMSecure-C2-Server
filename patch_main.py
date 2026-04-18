from pathlib import Path
import ast
import textwrap

ROOT = Path('/home/user/fmsecure_refresh')
MAIN = ROOT / 'main.py'

text = MAIN.read_text(encoding='utf-8').replace('\r\n', '\n')

# ---------------------------------------------------------------------------
# Simple string patches
# ---------------------------------------------------------------------------
text = text.replace(
    'from fastapi.staticfiles import StaticFiles\n',
    'from fastapi.staticfiles import StaticFiles\nfrom fastapi.templating import Jinja2Templates\n',
    1,
)
text = text.replace('"tagline": "Enterprise EDR for Windows"', '"tagline": "The Core of Endpoint Security"')
text = text.replace('"logo_ico": "/static/app_icon.ico"', '"logo_ico": "/static/logo-mark.svg"')
text = text.replace('"logo_png": "/static/app_icon.png"', '"logo_png": "/static/logo-mark.svg"')

mount_block = textwrap.dedent('''
try:
    app.mount("/static", StaticFiles(directory="static"), name="static")
except Exception:
    pass
''')

helper_block = textwrap.dedent('''
try:
    app.mount("/static", StaticFiles(directory="static"), name="static")
except Exception:
    pass

templates = Jinja2Templates(directory="templates")

PUBLIC_NAV = [
    {"href": "/", "label": "Home"},
    {"href": "/features", "label": "Features"},
    {"href": "/pricing", "label": "Pricing"},
    {"href": "/docs", "label": "Docs"},
    {"href": "/status", "label": "Status"},
    {"href": "/contact", "label": "Contact"},
]

PRODUCT_HIGHLIGHTS = [
    {
        "icon": "01",
        "title": "Real-time file integrity monitoring",
        "text": "Event-driven monitoring catches create, modify, rename, and delete activity across protected folders without relying on slow polling loops.",
    },
    {
        "icon": "02",
        "title": "Ransomware killswitch",
        "text": "Burst detection, honeypot tripwires, and automated isolation workflows can lock down affected endpoints before encryption spreads.",
    },
    {
        "icon": "03",
        "title": "Auto-healing vault",
        "text": "Protected files can be restored from a hidden encrypted vault when tampering or deletion is detected.",
    },
    {
        "icon": "04",
        "title": "Cloud C2 and tenant operations",
        "text": "A Railway-hosted FastAPI control plane gives you live heartbeats, alert feeds, tenant portals, and remote response actions.",
    },
    {
        "icon": "05",
        "title": "Forensics and threat intelligence",
        "text": "Snapshots, process attribution, LOLBin detection, and MalwareBazaar lookups help responders understand what changed and why.",
    },
    {
        "icon": "06",
        "title": "Licensing and recovery built in",
        "text": "Server-validated licensing, offline grace handling, cloud disaster recovery, and device transfer flows are built into the platform.",
    },
]

FEATURE_GROUPS = [
    {
        "title": "Endpoint defense",
        "summary": "Protection primitives focused on ransomware, tamper detection, and high-fidelity change tracking.",
        "items": [
            "Real-time folder monitoring with watchdog-based event handling",
            "Auto-healing vault for modified and deleted files",
            "Ransomware burst detection and OS-level lockdown",
            "Honeypot files that trigger immediate critical response",
            "USB write-protection and policy enforcement",
        ],
    },
    {
        "title": "Investigation and intelligence",
        "summary": "Built for incident response, not just notification spam.",
        "items": [
            "Process attribution and suspicious LOLBin identification",
            "Encrypted forensic snapshots and tamper-evident logs",
            "MalwareBazaar hash lookups for known families",
            "Registry persistence and startup-path monitoring",
            "Severity scoring that maps events to action",
        ],
    },
    {
        "title": "Cloud operations",
        "summary": "Designed so a central team can manage multiple organisations cleanly.",
        "items": [
            "Global C2 dashboard for live endpoints and version rollouts",
            "Isolated multi-tenant portals for customer organisations",
            "Tenant API keys, seat limits, and alert acknowledgment flows",
            "Remote commands such as LOCKDOWN, VERIFY, and SAFE_MODE",
            "Server-validated licensing with device activation and transfer",
        ],
    },
    {
        "title": "Recovery and business continuity",
        "summary": "Security controls are paired with restoration options so operations can resume quickly.",
        "items": [
            "Google Drive disaster recovery integration",
            "Consent-based restore workflows",
            "Folder structure backup and selective restoration",
            "Offline cache for license validation",
            "Email-driven recovery and admin onboarding flows",
        ],
    },
]

SECURITY_PILLARS = [
    {
        "eyebrow": "Contain",
        "title": "Stop destructive activity early",
        "text": "FMSecure combines burst detection, honeypots, and host isolation workflows to reduce the blast radius of active attacks.",
    },
    {
        "eyebrow": "Restore",
        "title": "Recover protected assets fast",
        "text": "When a protected file changes unexpectedly, the encrypted vault and cloud recovery flows provide structured restoration paths.",
    },
    {
        "eyebrow": "Explain",
        "title": "Turn alerts into evidence",
        "text": "Event severity, process attribution, snapshots, and threat intelligence make the console useful to responders, not just observers.",
    },
    {
        "eyebrow": "Operate",
        "title": "Run the platform as a service",
        "text": "Multi-tenant controls, organisation dashboards, licensing, and version publishing support a professional managed-product experience.",
    },
]

PLATFORM_PILLARS = [
    {
        "title": "Desktop agent",
        "text": "Windows endpoint engine responsible for monitoring, alerting, vaulting, and active defense actions.",
    },
    {
        "title": "Cloud C2",
        "text": "FastAPI + PostgreSQL backend for fleet heartbeat tracking, commands, licensing, and version updates.",
    },
    {
        "title": "Tenant workspace",
        "text": "Organisation-specific portal for viewing agents, alerts, and configuration without cross-tenant leakage.",
    },
    {
        "title": "Recovery layer",
        "text": "Local encrypted vault plus cloud backup and consent-based restore flows for continuity after an incident.",
    },
]

FAQ_ITEMS = [
    {
        "question": "What makes FMSecure different from a basic file monitor?",
        "answer": "It is designed as an active defense and recovery platform: killswitch logic, auto-healing, cloud C2, licensing, tenant portals, and forensics go well beyond change logging.",
    },
    {
        "question": "Can I operate it for multiple customer organisations?",
        "answer": "Yes. The multi-tenant backend provides tenant creation, per-organisation admin users, alert isolation, seat limits, and tenant-specific endpoint dashboards.",
    },
    {
        "question": "Does it support cloud deployment on Railway?",
        "answer": "Yes. This build is structured to keep main.py in the project root while separating templates and static assets so Railway can deploy directly from GitHub.",
    },
    {
        "question": "How are licenses handled?",
        "answer": "Licenses are server-validated, device-bindable, transferable, and compatible with offline grace periods for endpoint continuity.",
    },
    {
        "question": "What can tenant admins do?",
        "answer": "Tenant admins can monitor agent health, review alerts, acknowledge incidents, tune configuration, and queue response commands such as host lockdown.",
    },
]

DOC_ARCHITECTURE = [
    {
        "title": "Control plane",
        "text": "FastAPI application served from Railway with PostgreSQL backing licensing, tenant operations, versions, inquiries, and alert telemetry.",
    },
    {
        "title": "Agent telemetry",
        "text": "Endpoints send heartbeat data and alerts to the C2 using tenant API keys or a global API key depending on mode.",
    },
    {
        "title": "Version channel",
        "text": "Admins publish a current version and release notes so running agents and download pages can surface upgrades immediately.",
    },
    {
        "title": "Recovery plane",
        "text": "The desktop product handles local vault restore and optional cloud recovery through Google Drive integration.",
    },
]

DOC_ENV_VARS = [
    ("DATABASE_URL", "Railway PostgreSQL connection string."),
    ("ADMIN_USERNAME", "Primary super-admin login name."),
    ("ADMIN_PASSWORD", "Primary super-admin password."),
    ("API_KEY", "Legacy/global desktop agent API key."),
    ("RAZORPAY_KEY_ID", "Razorpay public key for checkout."),
    ("RAZORPAY_KEY_SECRET", "Razorpay secret for payment verification."),
    ("LICENSE_HMAC_SECRET", "Secret used for deterministic license generation."),
    ("ADMIN_API_KEY", "Secret for API-style admin endpoints that do not rely on the session cookie."),
    ("APP_BASE_URL", "Public Railway URL for this deployment."),
    ("SENDGRID_API_KEY", "SendGrid API key for license, onboarding, and reset emails."),
    ("SENDER_EMAIL", "Verified from-address for outbound email."),
    ("DRIVE_FILE_ID", "Google Drive file id used for default product download."),
]

DOC_DEPLOYMENT_STEPS = [
    "Push the project root to GitHub with main.py, templates/, static/, and the DB setup script present in the repository root.",
    "On Railway, connect the GitHub repo and add a PostgreSQL service so DATABASE_URL is injected automatically.",
    "Configure the required environment variables in Railway, especially admin credentials, license secret, payment keys, and email settings.",
    "Run the one_time_db_setup.py script once if you want to pre-create every table before first traffic; startup also uses CREATE TABLE IF NOT EXISTS for safety.",
    "Deploy, verify /status and /docs, then sign in to /login and /tenant/login to validate both admin surfaces.",
]

COMPARE_ROWS = [
    ("Detection approach", "Real-time event monitoring", "Mostly periodic scans or hash checks"),
    ("Response", "Lockdown, isolate, auto-heal, recover", "Alert only"),
    ("Operations", "Multi-tenant dashboards and versioning", "Single-system or lab utility"),
    ("Recovery", "Local vault plus cloud restore patterns", "Usually manual restore steps"),
]


def _fmt_dt(value, fmt="%Y-%m-%d %H:%M"):
    if value in (None, ""):
        return "—"
    try:
        return value.strftime(fmt)
    except Exception:
        return str(value)


def _nl2br(value):
    if not value:
        return ""
    return str(value).replace("\\n", "<br>")


templates.env.filters["dt"] = _fmt_dt
templates.env.filters["nl2br"] = _nl2br
templates.env.globals["len"] = len
templates.env.globals["enumerate"] = enumerate


def render_page(request: Request, template_name: str, **context):
    shared = {
        "request": request,
        "brand": BRAND,
        "public_nav": PUBLIC_NAV,
        "pricing_display": PRICING_DISPLAY,
        "product_highlights": PRODUCT_HIGHLIGHTS,
        "feature_groups": FEATURE_GROUPS,
        "security_pillars": SECURITY_PILLARS,
        "platform_pillars": PLATFORM_PILLARS,
        "faq_items": FAQ_ITEMS,
        "docs_architecture": DOC_ARCHITECTURE,
        "docs_env_vars": DOC_ENV_VARS,
        "docs_steps": DOC_DEPLOYMENT_STEPS,
        "compare_rows": COMPARE_ROWS,
        "app_base_url": APP_BASE_URL,
        "download_url": DOWNLOAD_URL,
        "year": BRAND["copyright_year"],
    }
    shared.update(context)
    return templates.TemplateResponse(template_name, shared)


def _get_current_release():
    fallback = {
        "version": "2.5.0",
        "release_notes": "",
        "download_url": DOWNLOAD_URL,
        "changelog_url": f"{APP_BASE_URL}/changelog",
        "published_at": None,
    }
    if not DATABASE_URL:
        return fallback
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute(
            "SELECT version, release_notes, download_url, changelog_url, published_at "
            "FROM versions WHERE is_current=TRUE ORDER BY published_at DESC LIMIT 1"
        )
        row = cur.fetchone()
        cur.close(); conn.close()
        if not row:
            return fallback
        payload = dict(row)
        payload["download_url"] = payload.get("download_url") or DOWNLOAD_URL
        payload["changelog_url"] = payload.get("changelog_url") or f"{APP_BASE_URL}/changelog"
        return payload
    except Exception:
        return fallback


def _get_release_history(limit: int = 10):
    if not DATABASE_URL:
        return []
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute(
            "SELECT version, release_notes, download_url, changelog_url, published_at, is_current "
            "FROM versions ORDER BY published_at DESC LIMIT %s",
            (limit,),
        )
        rows = [dict(r) for r in cur.fetchall()]
        cur.close(); conn.close()
        return rows
    except Exception:
        return []


def _store_website_inquiry(kind: str, page_source: str, name: str, email: str, company: str = "", subject: str = "", message: str = "", seats: str = "", phone: str = ""):
    if not DATABASE_URL:
        return False
    conn = get_db(); cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO website_inquiries
            (kind, page_source, company, contact_name, email, phone, seats, subject, message)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (kind, page_source, company.strip(), name.strip(), email.strip().lower(), phone.strip(), seats.strip(), subject.strip(), message.strip()),
    )
    conn.commit(); cur.close(); conn.close()
    return True


def _get_recent_inquiries(limit: int = 8):
    if not DATABASE_URL:
        return []
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute(
            "SELECT id, kind, page_source, company, contact_name, email, phone, seats, subject, message, status, created_at "
            "FROM website_inquiries ORDER BY created_at DESC LIMIT %s",
            (limit,),
        )
        rows = [dict(r) for r in cur.fetchall()]
        cur.close(); conn.close()
        return rows
    except Exception:
        return []


def _public_status_snapshot():
    release = _get_current_release()
    snapshot = {
        "summary": "All core services operational" if DATABASE_URL else "Deployment missing database configuration",
        "components": [
            {
                "name": "Control plane",
                "status": "operational" if DATABASE_URL else "degraded",
                "detail": "FastAPI application and database connectivity" if DATABASE_URL else "DATABASE_URL is not configured",
            },
            {
                "name": "Licensing",
                "status": "operational" if LICENSE_SECRET and LICENSE_SECRET != "change-me" else "degraded",
                "detail": "License generation and validation service",
            },
            {
                "name": "Payments",
                "status": "operational" if RZP_KEY_ID and RZP_KEY_SECRET else "degraded",
                "detail": "Razorpay checkout and payment verification",
            },
            {
                "name": "Outbound email",
                "status": "operational" if SENDGRID_API_KEY else "degraded",
                "detail": "SendGrid-driven onboarding, reset, and license mail",
            },
        ],
        "release": release,
        "online_agents": 0,
        "active_tenants": 0,
    }
    if DATABASE_URL:
        try:
            conn = get_db(); cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM tenant_agents WHERE status='online'")
            snapshot["online_agents"] = cur.fetchone()["count"]
            cur.execute("SELECT COUNT(*) FROM tenants WHERE active=TRUE")
            snapshot["active_tenants"] = cur.fetchone()["count"]
            cur.close(); conn.close()
        except Exception:
            pass
    return snapshot
''')

if mount_block not in text:
    raise RuntimeError('Expected mount block not found')
text = text.replace(mount_block, helper_block, 1)

# ---------------------------------------------------------------------------
# AST-based function replacement
# ---------------------------------------------------------------------------
REPLACEMENTS = {
    'init_db': textwrap.dedent('''
        def init_db():
            conn = get_db()
            cur = conn.cursor()

            try:
                cur.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")
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
                """)

                cur.execute("SELECT COUNT(*) FROM versions")
                row = cur.fetchone()
                row_count = row["count"] if isinstance(row, dict) else row[0]
                if row_count == 0:
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

                conn.commit()
                print("[DB] Tables ready.")
            except Exception as e:
                conn.rollback()
                print(f"[DB] Error initializing database: {e}")
                raise e
            finally:
                cur.close()
                conn.close()
    '''),
    'login_page': textwrap.dedent('''
        @app.get("/login", response_class=HTMLResponse)
        async def login_page(request: Request, error: str = ""):
            return render_page(
                request,
                "auth/admin_login.html",
                page_title="Admin Login",
                error=error,
            )
    '''),
    'dashboard': textwrap.dedent('''
        @app.get("/dashboard", response_class=HTMLResponse)
        async def dashboard(request: Request, _: bool = Depends(verify_session)):
            now = time.time()
            agent_records = []
            for machine_id, info in agents.items():
                is_online = (now - info["last_seen"]) < 30
                agent_records.append({
                    "machine_id": machine_id,
                    "hostname": info.get("hostname", "Unknown host"),
                    "username": info.get("username", "—"),
                    "ip": info.get("ip", "—"),
                    "tier": (info.get("tier") or "free").upper(),
                    "armed": bool(info.get("is_armed")),
                    "online": is_online,
                    "last_seen_epoch": int(info.get("last_seen", 0)),
                })
            agent_records.sort(key=lambda item: (not item["online"], item["hostname"].lower()))
            stats = {
                "total_agents": len(agent_records),
                "online_agents": sum(1 for row in agent_records if row["online"]),
                "armed_agents": sum(1 for row in agent_records if row["armed"]),
                "queued_commands": len(commands),
            }
            return render_page(
                request,
                "admin/dashboard.html",
                page_title="Global C2 Dashboard",
                portal_nav=[
                    {"href": "/dashboard", "label": "Global C2"},
                    {"href": "/licenses", "label": "Licenses"},
                    {"href": "/super/dashboard", "label": "Tenants"},
                    {"href": "/super/inquiries", "label": "Inquiries"},
                ],
                page_heading="Global command & control",
                page_subheading="Monitor connected endpoints, publish client releases, and manage operations from one surface.",
                stats=stats,
                agent_records=agent_records,
                current_release=_get_current_release(),
                recent_releases=_get_release_history(5),
            )
    '''),
    'super_dashboard': textwrap.dedent('''
        @app.get("/super/dashboard", response_class=HTMLResponse)
        async def super_dashboard(request: Request, _: bool = Depends(verify_session)):
            if not DATABASE_URL:
                return render_page(request, "admin/super_dashboard.html", page_title="Tenant Operations", db_missing=True, tenants=[], stats={}, recent_inquiries=[])

            conn = get_db(); cur = conn.cursor()
            cur.execute(
                """
                SELECT t.*,
                  (SELECT COUNT(*) FROM tenant_agents a WHERE a.tenant_id=t.id) AS agent_count,
                  (SELECT COUNT(*) FROM tenant_agents a WHERE a.tenant_id=t.id AND a.status='online') AS online_count,
                  (SELECT COUNT(*) FROM tenant_alerts al WHERE al.tenant_id=t.id AND al.acknowledged=FALSE) AS unacked_alerts
                FROM tenants t
                ORDER BY t.created_at DESC
                """
            )
            tenants = [dict(row) for row in cur.fetchall()]
            cur.execute("SELECT COUNT(*) FROM tenants WHERE active=TRUE")
            total_tenants = cur.fetchone()["count"]
            cur.execute("SELECT COUNT(*) FROM tenant_agents WHERE status='online'")
            total_online = cur.fetchone()["count"]
            cur.execute("SELECT COUNT(*) FROM tenant_alerts WHERE acknowledged=FALSE AND severity='CRITICAL'")
            total_critical = cur.fetchone()["count"]
            cur.execute("SELECT COUNT(*) FROM website_inquiries WHERE status='new'")
            new_inquiries = cur.fetchone()["count"]
            cur.close(); conn.close()

            for tenant in tenants:
                agent_count = tenant.get("agent_count") or 0
                online_count = tenant.get("online_count") or 0
                max_agents = max(tenant.get("max_agents") or 1, 1)
                tenant["usage_percent"] = int((agent_count / max_agents) * 100)
                tenant["is_trial"] = tenant.get("plan") == "trial"

            stats = {
                "total_tenants": total_tenants,
                "total_online": total_online,
                "total_critical": total_critical,
                "new_inquiries": new_inquiries,
            }
            return render_page(
                request,
                "admin/super_dashboard.html",
                page_title="Tenant Operations",
                portal_nav=[
                    {"href": "/dashboard", "label": "Global C2"},
                    {"href": "/licenses", "label": "Licenses"},
                    {"href": "/super/dashboard", "label": "Tenants"},
                    {"href": "/super/inquiries", "label": "Inquiries"},
                ],
                page_heading="Tenant operations workspace",
                page_subheading="Provision customer organisations, review fleet health, and track inbound demand from one admin console.",
                tenants=tenants,
                stats=stats,
                recent_inquiries=_get_recent_inquiries(6),
                db_missing=False,
            )
    '''),
    'super_tenant_detail': textwrap.dedent('''
        @app.get("/super/tenant-detail", response_class=HTMLResponse)
        async def super_tenant_detail(request: Request, id: str = "", new_key: str = "", msg: str = "", _: bool = Depends(verify_session)):
            if not id or not DATABASE_URL:
                return RedirectResponse("/super/dashboard")

            conn = get_db(); cur = conn.cursor()
            cur.execute("SELECT * FROM tenants WHERE id=%s", (id,))
            tenant = cur.fetchone()
            if not tenant:
                cur.close(); conn.close()
                return RedirectResponse("/super/dashboard")

            cur.execute(
                "SELECT machine_id, hostname, ip_address, username, tier, is_armed, status, last_seen, agent_version "
                "FROM tenant_agents WHERE tenant_id=%s ORDER BY last_seen DESC",
                (id,),
            )
            agents_list = [dict(row) for row in cur.fetchall()]
            cur.execute(
                "SELECT severity, event_type, message, hostname, created_at, acknowledged "
                "FROM tenant_alerts WHERE tenant_id=%s ORDER BY created_at DESC LIMIT 100",
                (id,),
            )
            alert_list = [dict(row) for row in cur.fetchall()]
            cur.execute("SELECT email, role, created_at FROM tenant_users WHERE tenant_id=%s ORDER BY created_at DESC", (id,))
            user_list = [dict(row) for row in cur.fetchall()]
            cur.close(); conn.close()

            tenant = dict(tenant)
            tenant["online_count"] = sum(1 for row in agents_list if row.get("status") == "online")
            tenant["alert_count"] = len(alert_list)
            tenant["created_label"] = _fmt_dt(tenant.get("created_at"), "%Y-%m-%d %H:%M")

            return render_page(
                request,
                "admin/super_tenant_detail.html",
                page_title=f"{tenant['name']} Tenant",
                portal_nav=[
                    {"href": "/dashboard", "label": "Global C2"},
                    {"href": "/super/dashboard", "label": "Tenants"},
                    {"href": "/super/inquiries", "label": "Inquiries"},
                ],
                page_heading=tenant["name"],
                page_subheading=f"{tenant['slug']} · {tenant['plan'].upper()} · {tenant['contact_email']}",
                tenant=tenant,
                new_key=new_key,
                msg=msg,
                agent_records=agents_list,
                alert_records=alert_list,
                user_records=user_list,
            )
    '''),
    'tenant_login_page': textwrap.dedent('''
        @app.get("/tenant/login", response_class=HTMLResponse)
        async def tenant_login_page(request: Request, error: str = ""):
            return render_page(
                request,
                "auth/tenant_login.html",
                page_title="Tenant Login",
                error=error,
            )
    '''),
    'tenant_forgot_password_page': textwrap.dedent('''
        @app.get("/tenant/forgot-password", response_class=HTMLResponse)
        async def tenant_forgot_password_page(request: Request, error: str = "", success: str = ""):
            return render_page(
                request,
                "auth/tenant_forgot_password.html",
                page_title="Forgot Password",
                error=error,
                success=success,
            )
    '''),
    'tenant_reset_password_page': textwrap.dedent('''
        @app.get("/tenant/reset-password", response_class=HTMLResponse)
        async def tenant_reset_password_page(request: Request, email: str = "", error: str = ""):
            return render_page(
                request,
                "auth/tenant_reset_password.html",
                page_title="Reset Password",
                error=error,
                email=email,
            )
    '''),
    'tenant_dashboard': textwrap.dedent('''
        @app.get("/tenant/dashboard", response_class=HTMLResponse)
        async def tenant_dashboard(request: Request):
            session = _get_tenant_session(request)
            if not session:
                return RedirectResponse("/tenant/login", status_code=302)

            tenant_id = session["tenant_id"]
            if not DATABASE_URL:
                return render_page(request, "tenant/dashboard.html", page_title="Tenant Dashboard", tenant=None, session=session, stats={}, agent_records=[], alert_records=[], config={}, db_missing=True)

            conn = get_db(); cur = conn.cursor()
            cur.execute("SELECT * FROM tenants WHERE id=%s", (tenant_id,))
            tenant = cur.fetchone()
            if not tenant:
                cur.close(); conn.close()
                return RedirectResponse("/tenant/login", status_code=302)

            cur.execute(
                "UPDATE tenant_agents SET status='offline' WHERE tenant_id=%s AND last_seen < NOW() - INTERVAL '35 seconds'",
                (tenant_id,),
            )
            conn.commit()

            cur.execute(
                "SELECT machine_id, hostname, ip_address, username, tier, is_armed, status, last_seen, agent_version "
                "FROM tenant_agents WHERE tenant_id=%s ORDER BY status DESC, last_seen DESC",
                (tenant_id,),
            )
            agents_list = [dict(row) for row in cur.fetchall()]
            cur.execute(
                "SELECT severity, event_type, message, hostname, file_path, created_at, acknowledged, id "
                "FROM tenant_alerts WHERE tenant_id=%s ORDER BY acknowledged ASC, created_at DESC LIMIT 50",
                (tenant_id,),
            )
            alert_list = [dict(row) for row in cur.fetchall()]
            cur.execute("SELECT * FROM tenant_config WHERE tenant_id=%s", (tenant_id,))
            config = dict(cur.fetchone() or {})
            cur.close(); conn.close()

            stats = _get_tenant_stats(tenant_id)
            stats["armed_agents"] = sum(1 for row in agents_list if row.get("is_armed"))
            stats["online_now"] = sum(1 for row in agents_list if row.get("status") == "online")

            return render_page(
                request,
                "tenant/dashboard.html",
                page_title=f"{tenant['name']} Dashboard",
                portal_nav=[
                    {"href": "/tenant/dashboard", "label": "Overview"},
                    {"href": "/download", "label": "Downloads"},
                    {"href": "/docs", "label": "Docs"},
                ],
                page_heading=tenant["name"],
                page_subheading=f"Signed in as {session['email']}",
                tenant=dict(tenant),
                session=session,
                stats=stats,
                agent_records=agents_list,
                alert_records=alert_list,
                config=config,
                db_missing=False,
            )
    '''),
    'landing_page_root': textwrap.dedent('''
        @app.get("/", response_class=HTMLResponse)
        async def landing_page_root(request: Request):
            return await landing_page(request)
    '''),
    'download_page': textwrap.dedent('''
        @app.get("/download", response_class=HTMLResponse)
        async def download_page(request: Request):
            current_release = _get_current_release()
            direct_url = DOWNLOAD_URL if DRIVE_FILE_ID else (current_release.get("download_url") or "#")
            return render_page(
                request,
                "public/download.html",
                page_title=f"Download v{current_release.get('version', 'Latest')}",
                current_release=current_release,
                direct_url=direct_url,
            )
    '''),
    'changelog_page': textwrap.dedent('''
        @app.get("/changelog", response_class=HTMLResponse)
        async def changelog_page(request: Request):
            releases = _get_release_history(20)
            return render_page(
                request,
                "public/changelog.html",
                page_title="Changelog",
                releases=releases,
            )
    '''),
    'landing_page': textwrap.dedent('''
        @app.get("/home", response_class=HTMLResponse)
        async def landing_page(request: Request):
            return render_page(
                request,
                "public/home.html",
                page_title="Home",
                current_release=_get_current_release(),
                live_status=_public_status_snapshot(),
            )
    '''),
    'pricing_page': textwrap.dedent('''
        @app.get("/pricing", response_class=HTMLResponse)
        async def pricing_page(request: Request):
            return render_page(
                request,
                "public/pricing.html",
                page_title="Pricing",
                rzp_key=RZP_KEY_ID,
                base_url=APP_BASE_URL,
            )
    '''),
    'enterprise_sales_page': textwrap.dedent('''
        @app.get("/enterprise", response_class=HTMLResponse)
        async def enterprise_sales_page(request: Request, error: str = "", success: bool = False):
            return render_page(
                request,
                "public/enterprise.html",
                page_title="Enterprise",
                error=error,
                success=success,
            )
    '''),
    'enterprise_sales_submit': textwrap.dedent('''
        @app.post("/enterprise")
        async def enterprise_sales_submit(
            company: str = Form(...),
            name: str = Form(...),
            email: str = Form(...),
            seats: str = Form("10"),
            message: str = Form(""),
        ):
            try:
                _store_website_inquiry(
                    kind="enterprise",
                    page_source="/enterprise",
                    company=company,
                    name=name,
                    email=email,
                    subject="Enterprise request",
                    message=message,
                    seats=seats,
                )
            except Exception as e:
                print(f"[INQUIRY] Enterprise save failed: {e}")
            threading.Thread(target=_notify_super_admin_of_lead, args=(company, name, email, seats, message), daemon=True).start()
            threading.Thread(target=_send_sales_acknowledgment, args=(email, name, company), daemon=True).start()
            return RedirectResponse("/enterprise?success=1", 302)
    '''),
    'payment_success': textwrap.dedent('''
        @app.get("/payment/success", response_class=HTMLResponse)
        async def payment_success(request: Request, key: str = "", email: str = "", tier: str = ""):
            tier_label = PLANS.get(tier, {}).get("label", "PRO")
            return render_page(
                request,
                "public/payment_success.html",
                page_title="Payment Successful",
                key=key,
                email=email,
                tier=tier,
                tier_label=tier_label,
            )
    '''),
    'licenses_page': textwrap.dedent('''
        @app.get("/licenses", response_class=HTMLResponse)
        async def licenses_page(request: Request, _: bool = Depends(verify_session)):
            conn = get_db(); cur = conn.cursor()
            cur.execute("SELECT * FROM licenses ORDER BY created_at DESC LIMIT 500")
            rows = [dict(r) for r in cur.fetchall()]
            cur.close(); conn.close()
            for row in rows:
                row["expired"] = _is_expired(row.get("expires_at"))
                row["status_label"] = "Active" if (not row["expired"] and row.get("active")) else "Expired"
            stats = {
                "total": len(rows),
                "active": sum(1 for row in rows if row["status_label"] == "Active"),
                "bound": sum(1 for row in rows if row.get("machine_id")),
                "annual": sum(1 for row in rows if row.get("tier") == "pro_annual"),
            }
            return render_page(
                request,
                "admin/licenses.html",
                page_title="Licenses",
                portal_nav=[
                    {"href": "/dashboard", "label": "Global C2"},
                    {"href": "/licenses", "label": "Licenses"},
                    {"href": "/super/dashboard", "label": "Tenants"},
                ],
                page_heading="License operations",
                page_subheading="Review issued keys, expiry state, and device bindings.",
                rows=rows,
                stats=stats,
            )
    '''),
}

mod = ast.parse(text)
func_positions = {}
for node in mod.body:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        if node.name in REPLACEMENTS:
            start = min([d.lineno for d in node.decorator_list], default=node.lineno) - 1
            end = node.end_lineno
            func_positions[node.name] = (start, end)

missing = [name for name in REPLACEMENTS if name not in func_positions]
if missing:
    raise RuntimeError(f'Missing functions for replacement: {missing}')

lines = text.split('\n')
for name, (start, end) in sorted(func_positions.items(), key=lambda item: item[1][0], reverse=True):
    lines[start:end] = REPLACEMENTS[name].strip('\n').split('\n')
text = '\n'.join(lines)

# ---------------------------------------------------------------------------
# Insert new public and super-admin routes
# ---------------------------------------------------------------------------
extra_routes = textwrap.dedent('''

@app.get("/features", response_class=HTMLResponse)
async def features_page(request: Request):
    return render_page(request, "public/features.html", page_title="Features")


@app.get("/docs", response_class=HTMLResponse)
async def docs_page(request: Request):
    return render_page(request, "public/docs.html", page_title="Documentation")


@app.get("/privacy", response_class=HTMLResponse)
async def privacy_page(request: Request):
    return render_page(request, "public/privacy.html", page_title="Privacy Policy")


@app.get("/terms", response_class=HTMLResponse)
async def terms_page(request: Request):
    return render_page(request, "public/terms.html", page_title="Terms of Service")


@app.get("/status", response_class=HTMLResponse)
async def status_page(request: Request):
    return render_page(request, "public/status.html", page_title="System Status", status_snapshot=_public_status_snapshot())


@app.get("/contact", response_class=HTMLResponse)
async def contact_page(request: Request, success: bool = False, error: str = ""):
    return render_page(request, "public/contact.html", page_title="Contact", success=success, error=error)


@app.post("/contact")
async def contact_submit(
    name: str = Form(...),
    email: str = Form(...),
    company: str = Form(""),
    phone: str = Form(""),
    subject: str = Form("General enquiry"),
    message: str = Form(...),
):
    try:
        saved = _store_website_inquiry(
            kind="contact",
            page_source="/contact",
            company=company,
            name=name,
            email=email,
            phone=phone,
            subject=subject,
            message=message,
        )
        if not saved:
            return RedirectResponse("/contact?error=Database+not+configured", status_code=302)
        return RedirectResponse("/contact?success=1", status_code=302)
    except Exception as e:
        print(f"[INQUIRY] Contact save failed: {e}")
        return RedirectResponse("/contact?error=Could+not+store+your+message", status_code=302)


@app.get("/super/inquiries", response_class=HTMLResponse)
async def super_inquiries(request: Request, _: bool = Depends(verify_session)):
    return render_page(
        request,
        "admin/inquiries.html",
        page_title="Inbound Inquiries",
        portal_nav=[
            {"href": "/dashboard", "label": "Global C2"},
            {"href": "/licenses", "label": "Licenses"},
            {"href": "/super/dashboard", "label": "Tenants"},
            {"href": "/super/inquiries", "label": "Inquiries"},
        ],
        page_heading="Inbound inquiries",
        page_subheading="Contact form submissions and enterprise requests stored in PostgreSQL.",
        inquiries=_get_recent_inquiries(200),
    )
''')

marker = '\n# ══════════════════════════════════════════════════════════════════════════════\n# PAYMENT ENDPOINTS\n'
if marker not in text:
    raise RuntimeError('Could not find payment marker for route insertion')
text = text.replace(marker, extra_routes + marker, 1)

MAIN.write_text(text, encoding='utf-8')
print('main.py patched successfully')
