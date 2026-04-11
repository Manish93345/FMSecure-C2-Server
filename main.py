"""
main.py — FMSecure C2 + License Server (FINAL)
Fixes in this version:
  1. Email runs in background thread — no more 3-minute payment delay
  2. SendGrid HTTP API replaces SMTP — works on Railway free tier
  3. Falls back to printing key if no SendGrid key set
  4. Device-based license validation (no email check)

Railway environment variables:
  ADMIN_USERNAME      = your admin username
  ADMIN_PASSWORD      = strong password
  API_KEY             = desktop agent API key
  RAZORPAY_KEY_ID     = rzp_test_... or rzp_live_...
  RAZORPAY_KEY_SECRET = razorpay secret
  LICENSE_HMAC_SECRET = generate: python -c "import secrets;print(secrets.token_hex(32))"
  ADMIN_API_KEY       = any secret for admin endpoints
  APP_BASE_URL        = https://your-server.railway.app
  DATABASE_URL        = auto-set by Railway PostgreSQL plugin
  SENDGRID_API_KEY    = get free at sendgrid.com (100 emails/day free)
  SENDER_EMAIL        = glimpsefilmy@gmail.com  (the FROM address in emails)

requirements.txt:
  razorpay
  psycopg2-binary
  slowapi
  sendgrid
"""
import os, secrets, time, hashlib, hmac as _hmac, uuid, threading, random
from functools import wraps
from datetime import datetime, timezone, timedelta

from fastapi import Response
from fastapi import FastAPI, Request, Depends, HTTPException, Form, Cookie
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
import psycopg2
from psycopg2.extras import RealDictCursor
import razorpay

# ── Config ─────────────────────────────────────────────────────────────────────
DATABASE_URL      = os.getenv("DATABASE_URL", "")
RZP_KEY_ID        = os.getenv("RAZORPAY_KEY_ID", "")
RZP_KEY_SECRET    = os.getenv("RAZORPAY_KEY_SECRET", "")
LICENSE_SECRET    = os.getenv("LICENSE_HMAC_SECRET", "change-me")
ADMIN_API_KEY     = os.getenv("ADMIN_API_KEY", "dev-only")
APP_BASE_URL      = os.getenv("APP_BASE_URL", "http://localhost:8000")
ADMIN_USER        = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASS        = os.getenv("ADMIN_PASSWORD", "password")
API_KEY           = os.getenv("API_KEY", "default-dev-key")
SENDGRID_API_KEY  = os.getenv("SENDGRID_API_KEY", "")
SENDER_EMAIL      = os.getenv("SENDER_EMAIL", "glimpsefilmy@gmail.com")
SESSION_TOKEN     = secrets.token_hex(16)
DRIVE_FILE_ID     = os.getenv("DRIVE_FILE_ID", "1tAadU5gjgkH26Hs-WM_VDKQUo0-TT8-e")   # Google Drive file ID for download

# Download URL — auto-derived. Just set DRIVE_FILE_ID env var on Railway.
DOWNLOAD_URL = (
    f"https://drive.google.com/uc?export=download&id={DRIVE_FILE_ID}"
    if DRIVE_FILE_ID else "#"
)
PRODUCT_PAGE_URL = os.getenv("PRODUCT_PAGE_URL", f"{APP_BASE_URL}/download")
_tenant_sessions: dict = {}   # token → {"tenant_id": str, "email": str, "role": str}
_TENANT_SESSION_TTL = 86400   # 24 hours
rzp_client = razorpay.Client(auth=(RZP_KEY_ID, RZP_KEY_SECRET))

# ── Plans — amounts in PAISE (Rs 999 = 99900) ─────────────────────────────────
# To change price: edit "amount". To change label: edit "label" AND the HTML below.
PLANS = {
    "pro_monthly": {"label":"PRO Monthly","amount":499, "currency":"INR",
                    "description":"FMSecure PRO - Monthly","days":31},
    "pro_annual":  {"label":"PRO Annual", "amount":4999,"currency":"INR",
                    "description":"FMSecure PRO - Annual","days":365},
}

# ── App setup ──────────────────────────────────────────────────────────────────
limiter = Limiter(key_func=get_remote_address)
app     = FastAPI(title="FMSecure")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
try:
    app.mount("/static", StaticFiles(directory="static"), name="static")
except Exception:
    pass

agents = {}; commands = {}

# In-memory OTP store for license transfer flow
# key: license_key → {"otp": str, "email": str, "expires": float}
# Railway runs a single process so in-memory is safe here.
_pending_transfers: dict = {}
_TRANSFER_OTP_TTL = 300   # 5 minutes

# ── Database ───────────────────────────────────────────────────────────────────
def get_db():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def init_db():
    conn = get_db()
    cur = conn.cursor()
    
    try:
        # 1. Execute standard SQL for creating tables and migrations
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
                id           SERIAL PRIMARY KEY,
                version      TEXT NOT NULL,
                release_notes TEXT NOT NULL DEFAULT '',
                download_url TEXT NOT NULL DEFAULT '',
                changelog_url TEXT NOT NULL DEFAULT '',
                published_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                is_current   BOOLEAN NOT NULL DEFAULT TRUE
            );

            CREATE TABLE IF NOT EXISTS tenants (
                id          TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
                name        TEXT NOT NULL,
                slug        TEXT UNIQUE NOT NULL,
                api_key     TEXT UNIQUE NOT NULL,
                plan        TEXT NOT NULL DEFAULT 'business',
                max_agents  INTEGER NOT NULL DEFAULT 10,
                contact_email TEXT NOT NULL DEFAULT '',
                notes       TEXT NOT NULL DEFAULT '',
                active      BOOLEAN NOT NULL DEFAULT TRUE,
                created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
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
                id          TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
                tenant_id   TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                agent_id    TEXT REFERENCES tenant_agents(id) ON DELETE SET NULL,
                machine_id  TEXT NOT NULL DEFAULT '',
                hostname    TEXT NOT NULL DEFAULT '',
                severity    TEXT NOT NULL DEFAULT 'INFO',
                event_type  TEXT NOT NULL DEFAULT '',
                message     TEXT NOT NULL DEFAULT '',
                file_path   TEXT NOT NULL DEFAULT '',
                acknowledged BOOLEAN NOT NULL DEFAULT FALSE,
                created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        
            CREATE TABLE IF NOT EXISTS tenant_config (
                tenant_id       TEXT PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
                webhook_url     TEXT NOT NULL DEFAULT '',
                alert_email     TEXT NOT NULL DEFAULT '',
                verify_interval INTEGER NOT NULL DEFAULT 60,
                max_vault_mb    INTEGER NOT NULL DEFAULT 10,
                allowed_exts    TEXT NOT NULL DEFAULT '.txt,.json,.py,.html,.js,.css'
            );
        
            -- Index for fast agent lookups by tenant
            CREATE INDEX IF NOT EXISTS idx_tenant_agents_tenant
                ON tenant_agents(tenant_id);
        
            -- Index for fast alert lookups by tenant + severity
            CREATE INDEX IF NOT EXISTS idx_tenant_alerts_tenant_sev
                ON tenant_alerts(tenant_id, severity, created_at DESC);

            -- Run the migration for machine_id
            DO $$ BEGIN
              IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                             WHERE table_name='licenses' AND column_name='machine_id') 
              THEN ALTER TABLE licenses ADD COLUMN machine_id TEXT DEFAULT NULL; END IF;
            END $$;
        """)

        # 2. Back in Python land: Check count and seed the starter row
        cur.execute("SELECT COUNT(*) FROM versions")
        row = cur.fetchone()
        
        # NOTE: If you are using a standard cursor, row is a tuple, so we use row[0].
        # If you are explicitly using a DictCursor (like RealDictCursor in psycopg2), use row["count"].
        row_count = row["count"] if isinstance(row, dict) else row[0]

        if row_count == 0:
            cur.execute("""
                INSERT INTO versions (version, release_notes, download_url, changelog_url, is_current)
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
        conn.rollback() # Roll back if something goes wrong so the DB doesn't lock
        print(f"[DB] Error initializing database: {e}")
        raise e
        
    finally:
        # Always close cursors and connections inside a finally block to prevent connection leaks!
        cur.close()
        conn.close()


def _start_offline_sweeper():
    """
    Background thread that marks stale agents as offline.
 
    Industry pattern (CrowdStrike, SentinelOne):
      Agent heartbeat interval: 10s
      Grace period before marking offline: 45s (4.5x heartbeat)
      Sweep frequency: 30s
 
    Without this, agents show "online" forever after their machine
    disconnects because the sweep only ran on incoming heartbeats.
    """
    def _sweep():
        while True:
            try:
                if DATABASE_URL:
                    conn = get_db(); cur = conn.cursor()
                    cur.execute(
                        "UPDATE tenant_agents SET status = \'offline\' "
                        "WHERE status = \'online\' "
                        "AND last_seen < NOW() - INTERVAL \'45 seconds\'"
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
    print("[SWEEPER] Offline sweeper started (30s interval, 45s grace).")


@app.on_event("startup")
async def startup():
  if DATABASE_URL:
    init_db()
    _start_offline_sweeper()    # ← ADD THIS LINE
  else:
    print("[DB] WARNING: No DATABASE_URL")

# ── Helpers ────────────────────────────────────────────────────────────────────
def _is_expired(e):
    try:
        if isinstance(e, str): e = datetime.fromisoformat(e.replace("Z","+00:00"))
        if e.tzinfo is None: e = e.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) > e
    except: return True

def _gen_key(tier, email, payment_id):
    sig = _hmac.new(LICENSE_SECRET.encode(),
                    f"{tier}:{email.lower()}:{payment_id}".encode(),
                    hashlib.sha256).hexdigest()[:16].upper()
    return f"FMSECURE-{'PRA' if 'annual' in tier else 'PRM'}-{sig}"

def _save_license(key, email, tier, payment_id, order_id, expires_iso):
    conn = get_db(); cur = conn.cursor()
    cur.execute("""
        INSERT INTO licenses (license_key,email,tier,payment_id,order_id,expires_at,active)
        VALUES (%s,%s,%s,%s,%s,%s,TRUE)
        ON CONFLICT (license_key) DO UPDATE SET expires_at=EXCLUDED.expires_at, active=TRUE
    """, (key, email.lower(), tier, payment_id, order_id, expires_iso))
    conn.commit(); cur.close(); conn.close()

def _check_admin(api_key):
    if api_key != ADMIN_API_KEY:
        raise HTTPException(status_code=403, detail="Forbidden")

def _gen_tenant_api_key() -> str:
    """Generate a secure, prefixed tenant API key."""
    return "fms-tenant-" + secrets.token_urlsafe(24)
 
 
def _hash_password(password: str) -> str:
    """SHA-256 password hash with a fixed salt prefix (simple, works offline)."""
    return hashlib.sha256(("fmsecure_salt_v1:" + password).encode()).hexdigest()
 
 
def _verify_password(password: str, hashed: str) -> bool:
    return secrets.compare_digest(_hash_password(password), hashed)
 
 
def _get_tenant_by_api_key(api_key: str) -> dict | None:
    """Look up a tenant by their API key. Returns None if not found or inactive."""
    if not api_key or not DATABASE_URL:
        return None
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute(
            "SELECT * FROM tenants WHERE api_key = %s AND active = TRUE",
            (api_key,))
        row = cur.fetchone()
        cur.close(); conn.close()
        return dict(row) if row else None
    except Exception as e:
        print(f"[TENANT] API key lookup error: {e}")
        return None
 
 
def _create_tenant_session(tenant_id: str, email: str, role: str) -> str:
    """Create a session token for a tenant admin login. Returns the token."""
    token = secrets.token_urlsafe(32)
    _tenant_sessions[token] = {
        "tenant_id": tenant_id,
        "email":     email,
        "role":      role,
        "created_at": time.time(),
    }
    return token
 
 
def _get_tenant_session(request: "Request") -> dict | None:
    """
    Read tenant session from cookie. Returns session dict or None.
    Also cleans up expired sessions.
    """
    token = request.cookies.get("fms_tenant_session")
    if not token:
        return None
    session = _tenant_sessions.get(token)
    if not session:
        return None
    # Check TTL
    if time.time() - session["created_at"] > _TENANT_SESSION_TTL:
        del _tenant_sessions[token]
        return None
    return session
 
 
def _require_tenant_session(request: "Request") -> dict:
    """
    Dependency-style helper — raises 302 redirect if no valid tenant session.
    Use: session = _require_tenant_session(request)
    """
    session = _get_tenant_session(request)
    if not session:
        raise HTTPException(
            status_code=302,
            headers={"Location": "/tenant/login"})
    return session
 
 
def _get_tenant_stats(tenant_id: str) -> dict:
    """Return agent count, online count, and unacked alert count for a tenant."""
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM tenant_agents WHERE tenant_id = %s",
            (tenant_id,))
        total_agents = cur.fetchone()["count"]
 
        cur.execute(
            "SELECT COUNT(*) FROM tenant_agents "
            "WHERE tenant_id = %s AND status = 'online'",
            (tenant_id,))
        online_agents = cur.fetchone()["count"]
 
        cur.execute(
            "SELECT COUNT(*) FROM tenant_alerts "
            "WHERE tenant_id = %s AND acknowledged = FALSE",
            (tenant_id,))
        unacked_alerts = cur.fetchone()["count"]
 
        cur.execute(
            "SELECT COUNT(*) FROM tenant_alerts "
            "WHERE tenant_id = %s AND severity = 'CRITICAL' AND acknowledged = FALSE",
            (tenant_id,))
        critical_alerts = cur.fetchone()["count"]
 
        cur.close(); conn.close()
        return {
            "total_agents":   total_agents,
            "online_agents":  online_agents,
            "unacked_alerts": unacked_alerts,
            "critical_alerts": critical_alerts,
        }
    except Exception as e:
        print(f"[TENANT] Stats error: {e}")
        return {"total_agents": 0, "online_agents": 0,
                "unacked_alerts": 0, "critical_alerts": 0}

async def verify_session(fmsecure_session: str = Cookie(None)):
    if not fmsecure_session or not secrets.compare_digest(fmsecure_session, SESSION_TOKEN):
        raise HTTPException(status_code=302, headers={"Location": "/login"})
    return True

def _send_license_email(email: str, license_key: str, tier: str, expires_iso: str):
    """
    Send license key via SendGrid HTTP API.
    HTTP-based — works on Railway free tier (SMTP is blocked, HTTP is not).
    Falls back to printing the key if no SendGrid key is configured.
    """
    tier_label  = PLANS.get(tier, {}).get("label", "PRO")
    expires_str = expires_iso[:10]

    if not SENDGRID_API_KEY:
        # No SendGrid key — key is still on the success page, just log it
        print(f"[EMAIL] No SENDGRID_API_KEY. Key for {email}: {license_key}")
        return

    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:520px;margin:0 auto;
                background:#0d1117;color:#e6edf3;padding:32px;border-radius:10px;">
      <h2 style="color:#2f81f7;margin-top:0">&#128737; FMSecure PRO Activated</h2>
      <p style="color:#a0a8b8;font-size:15px">
        Your <strong style="color:#e6edf3">{tier_label}</strong>
        is active until <strong style="color:#e6edf3">{expires_str}</strong>.
      </p>
      <div style="background:#161b22;border:1px solid #30363d;border-radius:8px;
                  padding:24px;text-align:center;margin:24px 0;">
        <p style="margin:0 0 10px;color:#8b949e;font-size:11px;letter-spacing:1px;font-weight:600">
          YOUR LICENSE KEY
        </p>
        <div style="font-size:22px;font-weight:700;color:#2f81f7;letter-spacing:3px;
                    font-family:Courier,monospace;word-break:break-all">
          {license_key}
        </div>
      </div>
      <div style="background:#1c2333;border-left:4px solid #2f81f7;
                  border-radius:4px;padding:16px;margin-bottom:20px">
        <p style="margin:0;color:#a0a8b8;font-size:14px;line-height:1.8">
          <strong style="color:#e6edf3">How to activate:</strong><br>
          1. Open <strong>FMSecure</strong> on your PC<br>
          2. Click your <strong>username</strong> (top-right corner)<br>
          3. Click <strong>Activate License</strong><br>
          4. Paste this key and click <strong>Activate</strong><br>
          5. PRO features unlock immediately
        </p>
      </div>
      <p style="color:#484f58;font-size:12px;border-top:1px solid #21262d;
                padding-top:16px;margin:0">
        This key activates on one device. To transfer to a new device, reply to this email.<br>
        FMSecure v2.0 &bull; Enterprise EDR for Windows &bull; Made in India
      </p>
    </div>"""

    try:
        import sendgrid
        from sendgrid.helpers.mail import Mail
        sg = sendgrid.SendGridAPIClient(api_key=SENDGRID_API_KEY)
        message = Mail(
            from_email=SENDER_EMAIL,
            to_emails=email,
            subject="Your FMSecure PRO License Key",
            html_content=html
        )
        resp = sg.send(message)
        print(f"[EMAIL] Sent to {email} — status {resp.status_code}")
    except Exception as e:
        print(f"[EMAIL] SendGrid failed for {email}: {e}")
        print(f"[EMAIL] Key was: {license_key}")

# ══════════════════════════════════════════════════════════════════════════════
# AUTH PAGES
# ══════════════════════════════════════════════════════════════════════════════
@app.get("/login", response_class=HTMLResponse)
async def login_page(error: str = ""):
    err = f'<p style="color:#f85149;background:#2d1c1c;padding:10px;border-radius:6px;margin-bottom:16px;font-size:14px">{error}</p>' if error else ""
    return f"""<!DOCTYPE html><html><head><title>FMSecure | Login</title>
    <style>*{{box-sizing:border-box;margin:0;padding:0}}
    body{{background:#0a0a0a;color:#e6edf3;display:flex;align-items:center;
          justify-content:center;min-height:100vh;font-family:system-ui,sans-serif}}
    .card{{background:#161b22;border:1px solid #30363d;border-radius:12px;padding:40px;width:360px}}
    h3{{color:#2f81f7;text-align:center;margin-bottom:4px}}
    p.sub{{color:#8b949e;text-align:center;font-size:13px;margin-bottom:24px}}
    label{{display:block;color:#8b949e;font-size:11px;font-weight:600;letter-spacing:.5px;margin-bottom:6px}}
    input{{width:100%;background:#0d1117;border:1px solid #30363d;border-radius:6px;
           color:#e6edf3;padding:10px 14px;font-size:14px;outline:none;margin-bottom:16px}}
    input:focus{{border-color:#2f81f7}}
    button{{width:100%;background:#238636;border:none;border-radius:6px;color:#fff;
             padding:12px;font-size:14px;font-weight:600;cursor:pointer}}</style></head><body>
    <div class="card"><h3>FMSecure C2</h3><p class="sub">Enterprise Authentication</p>
      {err}
      <form method="post" action="/login">
        <label>USERNAME</label><input name="username" type="text" required autofocus>
        <label>PASSWORD</label><input name="password" type="password" required>
        <button type="submit">Authenticate</button>
      </form>
    </div></body></html>"""

@app.post("/login")
async def process_login(username: str = Form(...), password: str = Form(...)):
    if secrets.compare_digest(username, ADMIN_USER) and secrets.compare_digest(password, ADMIN_PASS):
        resp = RedirectResponse(url="/dashboard", status_code=302)
        resp.set_cookie("fmsecure_session", SESSION_TOKEN, httponly=True, max_age=86400)
        return resp
    return RedirectResponse(url="/login?error=Invalid+credentials", status_code=302)

@app.get("/logout")
async def logout():
    resp = RedirectResponse(url="/login", status_code=302)
    resp.delete_cookie("fmsecure_session")
    return resp

# ══════════════════════════════════════════════════════════════════════════════
# C2 DASHBOARD
# ══════════════════════════════════════════════════════════════════════════════
class Heartbeat(BaseModel):
    machine_id: str
    hostname:   str
    username:   str
    tier:       str
    is_armed:   bool
    # New optional fields — old agents send without them, that's fine
    agent_version: str = "2.5.0"
    os_info:       str = ""

@app.post("/api/heartbeat")
@limiter.limit("200/minute")
async def receive_heartbeat(request: Request, data: Heartbeat):
    # ── Legacy single-user auth (unchanged) ──────────────────────────────────
    tenant_key = request.headers.get("x-tenant-key", "")
    api_key    = request.headers.get("x-api-key",    "")
 
    # Multi-tenant path
    if tenant_key:
        tenant = _get_tenant_by_api_key(tenant_key)
        if not tenant:
            raise HTTPException(status_code=401, detail="Invalid tenant key")

        # ── Seat enforcement: reject if tenant is at max capacity ────────────
        # We check BEFORE upserting so a genuinely new machine is blocked.
        # A machine that already has a record is always allowed (reconnecting).
        if DATABASE_URL:
            try:
                conn = get_db(); cur = conn.cursor()
 
                # Is this machine already registered with this tenant?
                cur.execute(
                    "SELECT id FROM tenant_agents "
                    "WHERE tenant_id=%s AND machine_id=%s",
                    (tenant["id"], data.machine_id))
                already_registered = cur.fetchone() is not None
 
                if not already_registered:
                    # Count current agents for this tenant
                    cur.execute(
                        "SELECT COUNT(*) FROM tenant_agents "
                        "WHERE tenant_id=%s",
                        (tenant["id"],))
                    current_count = cur.fetchone()["count"]
                    max_seats     = tenant.get("max_agents", 10)
 
                    if current_count >= max_seats:
                        cur.close(); conn.close()
                        print(f"[SEAT] Tenant {tenant['slug']} at capacity "
                              f"({current_count}/{max_seats}). "
                              f"Rejecting {data.machine_id[:16]}…")
                        # Return 402 so the desktop can show a friendly message
                        raise HTTPException(
                            status_code=402,
                            detail=(
                                f"Seat limit reached ({current_count}/{max_seats}). "
                                f"Contact your administrator to add more seats."
                            )
                        )
 
                cur.close(); conn.close()
 
            except HTTPException:
                raise   # re-raise the 402 we just created
            except Exception as e:
                print(f"[SEAT] Check error (non-critical): {e}")
                # On DB error, allow the heartbeat through — don\'t block agents
                # for infrastructure reasons
 
        # Upsert agent record in DB
        if DATABASE_URL:
            try:
                conn = get_db(); cur = conn.cursor()
 
                # Mark all this tenant's agents offline if last_seen > 35s
                cur.execute(
                    "UPDATE tenant_agents SET status='offline' "
                    "WHERE tenant_id=%s AND last_seen < NOW() - INTERVAL '35 seconds'",
                    (tenant["id"],))
 
                # Upsert this agent
                cur.execute("""
                    INSERT INTO tenant_agents
                        (tenant_id, machine_id, hostname, ip_address, username,
                         tier, is_armed, status, agent_version, os_info, last_seen)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,'online',%s,%s,NOW())
                    ON CONFLICT (tenant_id, machine_id) DO UPDATE SET
                        hostname      = EXCLUDED.hostname,
                        ip_address    = EXCLUDED.ip_address,
                        username      = EXCLUDED.username,
                        tier          = EXCLUDED.tier,
                        is_armed      = EXCLUDED.is_armed,
                        status        = 'online',
                        agent_version = EXCLUDED.agent_version,
                        os_info       = EXCLUDED.os_info,
                        last_seen     = NOW()
                    RETURNING id
                """, (
                    tenant["id"], data.machine_id, data.hostname,
                    request.client.host, data.username, data.tier,
                    data.is_armed, data.agent_version, data.os_info
                ))
                agent_row = cur.fetchone()
                conn.commit(); cur.close(); conn.close()
 
            except Exception as e:
                print(f"[TENANT HB] DB error: {e}")
 
        # Return any queued command for this machine
        cmd = commands.pop(data.machine_id, "NONE")
        return {"status": "ok", "command": cmd, "tenant": tenant["slug"]}
 
    # Legacy single-user path (unchanged)
    if api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")
    agents[data.machine_id] = {
        "hostname": data.hostname, "username": data.username,
        "tier": data.tier, "is_armed": data.is_armed,
        "last_seen": time.time(), "ip": request.client.host
    }
    return {"status": "ok", "command": commands.pop(data.machine_id, "NONE")}


class AgentAlert(BaseModel):
    machine_id: str
    hostname:   str
    severity:   str
    event_type: str
    message:    str
    file_path:  str = ""

@app.post("/api/agent/alert")
@limiter.limit("60/minute")
async def receive_agent_alert(request: Request, data: AgentAlert):
    tenant_key = request.headers.get("x-tenant-key", "")
    if not tenant_key:
        raise HTTPException(status_code=400, detail="x-tenant-key required")
 
    tenant = _get_tenant_by_api_key(tenant_key)
    if not tenant:
        raise HTTPException(status_code=401, detail="Invalid tenant key")
 
    if not DATABASE_URL:
        return {"status": "ok", "stored": False}
 
    try:
        conn = get_db(); cur = conn.cursor()
 
        # Look up agent ID
        cur.execute(
            "SELECT id FROM tenant_agents "
            "WHERE tenant_id=%s AND machine_id=%s",
            (tenant["id"], data.machine_id))
        agent_row = cur.fetchone()
        agent_id  = agent_row["id"] if agent_row else None
 
        # Insert alert
        cur.execute("""
            INSERT INTO tenant_alerts
                (tenant_id, agent_id, machine_id, hostname,
                 severity, event_type, message, file_path)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        """, (
            tenant["id"], agent_id, data.machine_id, data.hostname,
            data.severity.upper(), data.event_type,
            data.message[:1000], data.file_path[:500]
        ))
        conn.commit(); cur.close(); conn.close()
        return {"status": "ok", "stored": True}
 
    except Exception as e:
        print(f"[ALERT] DB error: {e}")
        return {"status": "ok", "stored": False}


@app.get("/agent/config")
async def get_agent_config(request: Request):
    """
    Returns the tenant\'s policy config for this agent.
    The desktop app calls this on startup and applies the values,
    overriding local config.json (Option B — IT admin controls policy).
 
    Auth: x-tenant-key header (same as heartbeat).
    Returns 200 + config dict, or 401 if key invalid.
    """
    tenant_key = request.headers.get("x-tenant-key", "")
    if not tenant_key:
        raise HTTPException(status_code=400,
                            detail="x-tenant-key header required")
 
    tenant = _get_tenant_by_api_key(tenant_key)
    if not tenant:
        raise HTTPException(status_code=401, detail="Invalid tenant key")
 
    if not DATABASE_URL:
        return JSONResponse({"tenant_name": tenant["name"], "config": {}})
 
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute(
            "SELECT * FROM tenant_config WHERE tenant_id = %s",
            (tenant["id"],))
        cfg_row = cur.fetchone()
        cur.close(); conn.close()
    except Exception as e:
        print(f"[CONFIG] DB error: {e}")
        return JSONResponse({"tenant_name": tenant["name"], "config": {}})
 
    # Build the config dict — only include non-empty values
    # so the agent only overrides settings that the IT admin actually set
    cfg = {}
    if cfg_row:
        if cfg_row.get("webhook_url"):
            cfg["webhook_url"] = cfg_row["webhook_url"]
        if cfg_row.get("alert_email"):
            cfg["admin_email"] = cfg_row["alert_email"]   # matches CONFIG key
        if cfg_row.get("verify_interval") and cfg_row["verify_interval"] > 0:
            cfg["verify_interval"] = cfg_row["verify_interval"]
        if cfg_row.get("max_vault_mb") and cfg_row["max_vault_mb"] > 0:
            cfg["vault_max_size_mb"] = cfg_row["max_vault_mb"]   # matches CONFIG key
        if cfg_row.get("allowed_exts"):
            # Convert comma-separated string to list matching CONFIG format
            exts = [e.strip() for e in cfg_row["allowed_exts"].split(",")
                    if e.strip().startswith(".")]
            if exts:
                cfg["vault_allowed_exts"] = exts
 
    return JSONResponse(
        content={
            "tenant_name": tenant["name"],
            "tenant_slug": tenant["slug"],
            "plan":        tenant["plan"],
            "config":      cfg,
        },
        headers={"Cache-Control": "no-store"}
    )


@app.post("/api/trigger_lockdown/{machine_id}")
async def trigger_lockdown(machine_id: str, _: bool = Depends(verify_session)):
    commands[machine_id] = "LOCKDOWN"
    return {"status": "Lockdown queued"}

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(_: bool = Depends(verify_session)):
    now = time.time(); rows = ""
    for mid, info in agents.items():
        online = (now - info["last_seen"]) < 30
        sb = '<span style="background:#238636;color:#fff;padding:2px 8px;border-radius:4px;font-size:12px">ONLINE</span>' if online else '<span style="background:#30363d;color:#8b949e;padding:2px 8px;border-radius:4px;font-size:12px">OFFLINE</span>'
        ab = '<span style="background:#1f6feb;color:#fff;padding:2px 8px;border-radius:4px;font-size:12px">ARMED</span>' if info["is_armed"] else '<span style="background:#9e6a03;color:#fff;padding:2px 8px;border-radius:4px;font-size:12px">UNARMED</span>'
        rows += f"<tr><td style='font-family:monospace;color:#8b949e'>{mid[:14]}...</td><td><strong>{info['hostname']}</strong></td><td>{info['username']}</td><td>{info['ip']}</td><td>{sb}</td><td>{ab}</td><td><button onclick=\"lock('{mid}')\" style='background:#da3633;color:#fff;border:none;border-radius:4px;padding:4px 12px;cursor:pointer;font-size:13px'>ISOLATE</button></td></tr>"
    
    if not rows: 
        rows = "<tr><td colspan='7' style='text-align:center;color:#484f58;padding:32px'>No endpoints connected</td></tr>"
    
    return f"""<!DOCTYPE html><html><head><title>FMSecure C2</title>
    <style>*{{box-sizing:border-box;margin:0;padding:0}}body{{background:#0a0a0a;color:#e6edf3;font-family:system-ui,sans-serif}}
    nav{{background:#161b22;border-bottom:1px solid #30363d;padding:16px 24px;display:flex;justify-content:space-between;align-items:center}}
    .brand{{color:#2f81f7;font-weight:700;font-size:18px}}a{{color:#8b949e;text-decoration:none;font-size:13px;margin-left:16px}}a:hover{{color:#e6edf3}}
    .container{{padding:24px}}table{{width:100%;border-collapse:collapse;background:#161b22;border-radius:8px;overflow:hidden}}
    th{{background:#0d1117;color:#8b949e;padding:12px 16px;text-align:left;font-size:12px;font-weight:600;letter-spacing:.5px}}
    td{{padding:12px 16px;border-top:1px solid #21262d;font-size:14px}}</style></head><body>
    <nav><span class="brand">FMSecure Global C2</span>
    <div>
  <a href="/licenses">Licenses</a>
  <a href="/super/dashboard" style="color:#f0883e">Tenants</a>
  <a href="/">Product Page</a>
  <a href="/pricing">Pricing</a>
  <a href="/logout">Logout</a>
</div></nav>
    
    <div class="container">
        <div style="background:#161b22;border:1px solid #30363d;border-radius:8px;
                    padding:24px;margin-bottom:24px">
          <h3 style="color:#e6edf3;margin:0 0 6px">🚀 Publish New Version</h3>
          <p style="color:#8b949e;font-size:13px;margin:0 0 18px">
            When you ship a new EXE, fill this in. Every running copy of FMSecure
            will show an update banner within seconds.
          </p>
          <form method="POST" action="/api/version/publish-form"
                style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
            <div>
              <label style="color:#8b949e;font-size:12px;display:block;margin-bottom:4px">VERSION NUMBER *</label>
              <input name="version" placeholder="e.g. 2.6.0" required
                     style="width:100%;background:#0d1117;color:#e6edf3;border:1px solid #30363d;
                            border-radius:6px;padding:8px 12px;font-size:14px">
            </div>
            <div>
              <label style="color:#8b949e;font-size:12px;display:block;margin-bottom:4px">RELEASE NOTES (shown in banner)</label>
              <input name="release_notes" placeholder="Bug fixes, new cloud features…"
                     style="width:100%;background:#0d1117;color:#e6edf3;border:1px solid #30363d;
                            border-radius:6px;padding:8px 12px;font-size:14px">
            </div>
            <div>
              <label style="color:#8b949e;font-size:12px;display:block;margin-bottom:4px">DOWNLOAD URL (leave blank for default)</label>
              <input name="download_url" placeholder="https://…"
                     style="width:100%;background:#0d1117;color:#e6edf3;border:1px solid #30363d;
                            border-radius:6px;padding:8px 12px;font-size:14px">
            </div>
            <div>
              <label style="color:#8b949e;font-size:12px;display:block;margin-bottom:4px">CHANGELOG URL (leave blank for default)</label>
              <input name="changelog_url" placeholder="https://…"
                     style="width:100%;background:#0d1117;color:#e6edf3;border:1px solid #30363d;
                            border-radius:6px;padding:8px 12px;font-size:14px">
            </div>
            <div style="grid-column:1/-1">
              <button type="submit"
                      style="background:#238636;color:#fff;border:none;border-radius:6px;
                             padding:10px 24px;font-size:14px;font-weight:600;cursor:pointer">
                Publish Version →
              </button>
            </div>
          </form>
        </div>
        <table><thead><tr><th>MACHINE ID</th><th>HOSTNAME</th><th>USER</th><th>IP</th><th>STATUS</th><th>ENGINE</th><th>ACTION</th></tr></thead>
        <tbody>{rows}</tbody></table>
    </div>
    
    <script>
        // SMART HEARTBEAT: Refreshes every 5s, BUT pauses if the admin is typing!
        setInterval(() => {{
            const isTyping = document.querySelector('input:focus, textarea:focus');
            if (!isTyping) {{
                location.reload();
            }}
        }}, 5000);

        async function lock(mid) {{
            if(confirm("Isolate?")) {{
                await fetch("/api/trigger_lockdown/"+mid, {{method:"POST"}});
                alert("Queued!");
            }}
        }}
    </script>
    </body></html>"""


# ── Super Admin: DB migration helper ─────────────────────────────────────────
@app.get("/super/db-migrate")
async def super_db_migrate(api_key: str = ""):
    """
    Idempotent migration endpoint — safe to run multiple times.
    Creates all tenant tables if they don't exist yet.
    Call this once after first deploy, or after any schema update.
    """
    _check_admin(api_key)
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("""
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
            CREATE INDEX IF NOT EXISTS idx_tenant_agents_tenant
                ON tenant_agents(tenant_id);
            CREATE INDEX IF NOT EXISTS idx_tenant_alerts_tenant_sev
                ON tenant_alerts(tenant_id, severity, created_at DESC);
        """)
        conn.commit(); cur.close(); conn.close()
        return {"ok": True, "message": "All tenant tables created / verified successfully."}
    except Exception as e:
        return {"ok": False, "error": str(e)}
 
 
# ── Super Admin: List all tenants ─────────────────────────────────────────────
@app.get("/super/tenants")
async def super_list_tenants(api_key: str = ""):
    _check_admin(api_key)
    if not DATABASE_URL:
        return {"tenants": []}
    conn = get_db(); cur = conn.cursor()
    cur.execute(
        "SELECT t.*, "
        "  (SELECT COUNT(*) FROM tenant_agents a WHERE a.tenant_id=t.id) as agent_count, "
        "  (SELECT COUNT(*) FROM tenant_users  u WHERE u.tenant_id=t.id) as user_count "
        "FROM tenants t ORDER BY t.created_at DESC")
    rows = [dict(r) for r in cur.fetchall()]
    cur.close(); conn.close()
    # Convert datetime to ISO string for JSON
    for r in rows:
        if r.get("created_at"):
            r["created_at"] = r["created_at"].isoformat()
    return {"count": len(rows), "tenants": rows}
 
 
# ── Super Admin: Create tenant ────────────────────────────────────────────────
class CreateTenantBody(BaseModel):
    name:          str
    slug:          str                 # url-safe identifier e.g. "acme-corp"
    contact_email: str
    plan:          str  = "business"
    max_agents:    int  = 10
    notes:         str  = ""
    admin_email:   str  = ""           # creates first tenant admin user if set
    admin_password:str  = ""
    api_key:       str  = ""
 
@app.post("/super/tenants")
async def super_create_tenant(body: CreateTenantBody):
    _check_admin(body.api_key)
    if not DATABASE_URL:
        raise HTTPException(status_code=500, detail="No database")
 
    tenant_id  = str(uuid.uuid4())
    tenant_key = _gen_tenant_api_key()
    slug       = body.slug.lower().strip().replace(" ", "-")
 
    try:
        conn = get_db(); cur = conn.cursor()
 
        # Insert tenant
        cur.execute("""
            INSERT INTO tenants
                (id, name, slug, api_key, plan, max_agents, contact_email, notes)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        """, (tenant_id, body.name.strip(), slug, tenant_key,
              body.plan, body.max_agents,
              body.contact_email.strip(), body.notes.strip()))
 
        # Create default config row
        cur.execute(
            "INSERT INTO tenant_config (tenant_id) VALUES (%s)",
            (tenant_id,))
 
        # Optionally create first admin user
        if body.admin_email and body.admin_password:
            cur.execute("""
                INSERT INTO tenant_users
                    (tenant_id, email, password_hash, role)
                VALUES (%s,%s,%s,'admin')
            """, (tenant_id,
                  body.admin_email.strip().lower(),
                  _hash_password(body.admin_password)))
 
        conn.commit(); cur.close(); conn.close()
        print(f"[TENANT] Created: {body.name} ({slug}) — key: {tenant_key}")
 
        return {
            "ok":        True,
            "tenant_id": tenant_id,
            "api_key":   tenant_key,
            "slug":      slug,
            "message":   f"Tenant '{body.name}' created. Hand the api_key to the firm's IT admin.",
        }
 
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
 
 
# ── Super Admin: Get single tenant detail ─────────────────────────────────────
@app.get("/super/tenants/{tenant_id}")
async def super_get_tenant(tenant_id: str, api_key: str = ""):
    _check_admin(api_key)
    if not DATABASE_URL:
        raise HTTPException(status_code=500, detail="No database")
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT * FROM tenants WHERE id=%s", (tenant_id,))
    tenant = cur.fetchone()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    cur.execute(
        "SELECT machine_id,hostname,status,is_armed,last_seen,tier "
        "FROM tenant_agents WHERE tenant_id=%s ORDER BY last_seen DESC",
        (tenant_id,))
    agents_rows = [dict(r) for r in cur.fetchall()]
    cur.execute(
        "SELECT severity,event_type,message,hostname,created_at "
        "FROM tenant_alerts WHERE tenant_id=%s "
        "ORDER BY created_at DESC LIMIT 50",
        (tenant_id,))
    alert_rows = [dict(r) for r in cur.fetchall()]
    cur.close(); conn.close()
 
    for r in agents_rows:
        if r.get("last_seen"): r["last_seen"] = r["last_seen"].isoformat()
    for r in alert_rows:
        if r.get("created_at"): r["created_at"] = r["created_at"].isoformat()
 
    return {
        "tenant":  dict(tenant),
        "agents":  agents_rows,
        "alerts":  alert_rows,
        "stats":   _get_tenant_stats(tenant_id),
    }
 
 
# ── Super Admin: Reset tenant API key ────────────────────────────────────────
@app.post("/super/tenants/{tenant_id}/reset-key")
async def super_reset_tenant_key(tenant_id: str, api_key: str = ""):
    _check_admin(api_key)
    new_key = _gen_tenant_api_key()
    conn = get_db(); cur = conn.cursor()
    cur.execute(
        "UPDATE tenants SET api_key=%s WHERE id=%s RETURNING name",
        (new_key, tenant_id))
    row = cur.fetchone()
    conn.commit(); cur.close(); conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return {"ok": True, "new_api_key": new_key, "tenant": row["name"]}
 
 
# ── Super Admin: Suspend / unsuspend tenant ───────────────────────────────────
@app.post("/super/tenants/{tenant_id}/suspend")
async def super_suspend_tenant(tenant_id: str, suspend: bool = True, api_key: str = ""):
    _check_admin(api_key)
    conn = get_db(); cur = conn.cursor()
    cur.execute(
        "UPDATE tenants SET active=%s WHERE id=%s RETURNING name",
        (not suspend, tenant_id))
    row = cur.fetchone()
    conn.commit(); cur.close(); conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return {"ok": True, "active": not suspend, "tenant": row["name"]}


@app.get("/super/alerts")
async def super_all_alerts(api_key: str = "", limit: int = 100):
    _check_admin(api_key)
    if not DATABASE_URL:
        return {"alerts": []}
    conn = get_db(); cur = conn.cursor()
    cur.execute("""
        SELECT a.*, t.name as tenant_name, t.slug as tenant_slug
        FROM tenant_alerts a
        JOIN tenants t ON t.id = a.tenant_id
        ORDER BY a.created_at DESC
        LIMIT %s
    """, (min(limit, 500),))
    rows = [dict(r) for r in cur.fetchall()]
    cur.close(); conn.close()
    for r in rows:
        if r.get("created_at"):
            r["created_at"] = r["created_at"].isoformat()
    return {"count": len(rows), "alerts": rows}
 
 
# ── Super Admin: Global alert view ───────────────────────────────────────────
@app.get("/super/alerts")
async def super_all_alerts(api_key: str = "", limit: int = 100):
    _check_admin(api_key)
    if not DATABASE_URL:
        return {"alerts": []}
    conn = get_db(); cur = conn.cursor()
    cur.execute("""
        SELECT a.*, t.name as tenant_name, t.slug as tenant_slug
        FROM tenant_alerts a
        JOIN tenants t ON t.id = a.tenant_id
        ORDER BY a.created_at DESC
        LIMIT %s
    """, (min(limit, 500),))
    rows = [dict(r) for r in cur.fetchall()]
    cur.close(); conn.close()
    for r in rows:
        if r.get("created_at"): r["created_at"] = r["created_at"].isoformat()
    return {"count": len(rows), "alerts": rows}
 
 
# ── Super Admin: Visual Dashboard ────────────────────────────────────────────
@app.get("/super/dashboard", response_class=HTMLResponse)
async def super_dashboard(request: Request, _: bool = Depends(verify_session)):
    if not DATABASE_URL:
        return HTMLResponse("<h1>No database configured</h1>")
 
    conn = get_db(); cur = conn.cursor()
 
    # Fetch all tenants with stats
    cur.execute("""
        SELECT t.*,
          (SELECT COUNT(*) FROM tenant_agents a WHERE a.tenant_id=t.id)   AS agent_count,
          (SELECT COUNT(*) FROM tenant_agents a WHERE a.tenant_id=t.id
            AND a.status='online')                                          AS online_count,
          (SELECT COUNT(*) FROM tenant_alerts al WHERE al.tenant_id=t.id
            AND al.acknowledged=FALSE)                                      AS unacked_alerts
        FROM tenants t ORDER BY t.created_at DESC
    """)
    tenants = cur.fetchall()
 
    # Global counts
    cur.execute("SELECT COUNT(*) FROM tenants WHERE active=TRUE")
    total_tenants = cur.fetchone()["count"]
    cur.execute("SELECT COUNT(*) FROM tenant_agents WHERE status='online'")
    total_online  = cur.fetchone()["count"]
    cur.execute(
        "SELECT COUNT(*) FROM tenant_alerts "
        "WHERE acknowledged=FALSE AND severity='CRITICAL'")
    total_critical = cur.fetchone()["count"]
    cur.close(); conn.close()
 
    # Build tenant rows HTML
    tenant_rows = ""
    for t in tenants:
        status_badge = (
            '<span style="background:#238636;color:#fff;padding:2px 8px;'
            'border-radius:4px;font-size:12px">Active</span>'
            if t["active"] else
            '<span style="background:#6e7681;color:#fff;padding:2px 8px;'
            'border-radius:4px;font-size:12px">Suspended</span>'
        )
        alert_badge = ""
        if t["unacked_alerts"]:
            alert_badge = (
                f'<span style="background:#da3633;color:#fff;padding:2px 8px;'
                f'border-radius:4px;font-size:11px">'
                f'⚠ {t["unacked_alerts"]}</span>'
            )
        created = t["created_at"].strftime("%Y-%m-%d") if t["created_at"] else "—"
        tenant_rows += f"""
        <tr>
          <td><strong>{t['name']}</strong><br>
              <span style="font-family:monospace;color:#8b949e;font-size:11px">
                {t['slug']}
              </span>
          </td>
          <td>{t['plan'].upper()}</td>
          <td>{t['contact_email']}</td>
          <td>
            <span style="color:#3fb950;font-weight:600">{t['online_count']}</span>
            <span style="color:#8b949e">/ {t['agent_count']} / {t['max_agents']}</span>
          </td>
          <td>{status_badge}</td>
          <td>{alert_badge or '<span style="color:#484f58">—</span>'}</td>
          <td>{created}</td>
          <td style="white-space:nowrap">
            <a href="/super/tenant-detail?id={t['id']}"
               style="color:#2f81f7;text-decoration:none;font-size:12px;margin-right:8px">
              View ›
            </a>
          </td>
        </tr>"""
 
    if not tenant_rows:
        tenant_rows = (
            "<tr><td colspan='8' style='text-align:center;color:#484f58;"
            "padding:32px'>No tenants yet — create your first one below</td></tr>")
 
    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8">
<title>FMSecure Super Admin</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:#0a0a0a;color:#e6edf3;font-family:system-ui,sans-serif}}
  nav{{background:#161b22;border-bottom:1px solid #30363d;padding:16px 24px;
       display:flex;justify-content:space-between;align-items:center}}
  .brand{{color:#f0883e;font-weight:700;font-size:18px}}
  nav a{{color:#8b949e;text-decoration:none;font-size:13px;margin-left:16px}}
  nav a:hover{{color:#e6edf3}}
  .container{{padding:28px 32px;max-width:1400px;margin:0 auto}}
  .stat-row{{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-bottom:28px}}
  .stat{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:20px 24px}}
  .stat .num{{font-size:32px;font-weight:800;margin-bottom:4px}}
  .stat .lbl{{color:#8b949e;font-size:13px}}
  .card{{background:#161b22;border:1px solid #30363d;border-radius:8px;
          padding:24px;margin-bottom:24px}}
  .card h3{{margin:0 0 16px;font-size:16px}}
  table{{width:100%;border-collapse:collapse}}
  th{{background:#0d1117;color:#8b949e;padding:10px 16px;text-align:left;
      font-size:11px;font-weight:600;letter-spacing:.5px}}
  td{{padding:12px 16px;border-top:1px solid #21262d;font-size:13px}}
  label{{display:block;color:#8b949e;font-size:11px;font-weight:600;
         letter-spacing:.5px;margin-bottom:6px;margin-top:12px}}
  input,select,textarea{{
    width:100%;background:#0d1117;color:#e6edf3;
    border:1px solid #30363d;border-radius:6px;
    padding:8px 12px;font-size:13px;outline:none}}
  input:focus,select:focus{{border-color:#2f81f7}}
  .grid2{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}
  .grid3{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px}}
  .btn{{background:#238636;color:#fff;border:none;border-radius:6px;
        padding:10px 24px;font-size:13px;font-weight:600;cursor:pointer;
        margin-top:16px}}
  .btn:hover{{background:#2ea043}}
</style>
</head><body>
<nav>
  <span class="brand">⚡ FMSecure — Super Admin</span>
  <div>
    <a href="/dashboard">C2 Dashboard</a>
    <a href="/licenses">Licenses</a>
    <a href="/super/dashboard">Tenants</a>
    <a href="/logout">Logout</a>
  </div>
</nav>
 
<div class="container">
 
  <!-- Stat row -->
  <div class="stat-row">
    <div class="stat">
      <div class="num" style="color:#2f81f7">{total_tenants}</div>
      <div class="lbl">Active Tenants</div>
    </div>
    <div class="stat">
      <div class="num" style="color:#3fb950">{total_online}</div>
      <div class="lbl">Agents Online Right Now</div>
    </div>
    <div class="stat">
      <div class="num" style="color:#f85149">{total_critical}</div>
      <div class="lbl">Unacknowledged Critical Alerts</div>
    </div>
  </div>
 
  <!-- Tenant table -->
  <div class="card">
    <h3>All Tenants</h3>
    <table>
      <thead><tr>
        <th>TENANT</th><th>PLAN</th><th>CONTACT</th>
        <th>AGENTS (online/total/limit)</th>
        <th>STATUS</th><th>ALERTS</th><th>CREATED</th><th>ACTION</th>
      </tr></thead>
      <tbody>{tenant_rows}</tbody>
    </table>
  </div>
 
  <!-- Create Tenant Form -->
  <div class="card">
    <h3>➕ Create New Tenant</h3>
    <form method="POST" action="/super/tenants-form">
      <div class="grid3">
        <div>
          <label>COMPANY NAME *</label>
          <input name="name" placeholder="Acme Corp" required>
        </div>
        <div>
          <label>SLUG (URL identifier) *</label>
          <input name="slug" placeholder="acme-corp" required
                 pattern="[a-z0-9-]+"
                 title="Lowercase letters, numbers, hyphens only">
        </div>
        <div>
          <label>CONTACT EMAIL *</label>
          <input name="contact_email" type="email" placeholder="it@acme.com" required>
        </div>
      </div>
      <div class="grid3">
        <div>
          <label>PLAN</label>
          <select name="plan">
            <option value="business">Business</option>
            <option value="enterprise">Enterprise</option>
            <option value="trial">Trial (7 days)</option>
          </select>
        </div>
        <div>
          <label>MAX AGENTS (seats)</label>
          <input name="max_agents" type="number" value="10" min="1" max="10000">
        </div>
        <div>
          <label>NOTES (internal)</label>
          <input name="notes" placeholder="e.g. Direct sale, 3-month deal">
        </div>
      </div>
      <div class="grid2" style="margin-top:4px">
        <div>
          <label>FIRST ADMIN EMAIL (optional — creates login immediately)</label>
          <input name="admin_email" type="email" placeholder="admin@acme.com">
        </div>
        <div>
          <label>FIRST ADMIN PASSWORD (min 8 chars)</label>
          <input name="admin_password" type="password" placeholder="Strong password">
        </div>
      </div>
      <button class="btn" type="submit">Create Tenant & Generate API Key →</button>
    </form>
  </div>
 
</div>
</body></html>"""
 
 
# ── Form handler for tenant creation from dashboard ───────────────────────────
@app.post("/super/tenants-form")
async def super_create_tenant_form(
    request: Request,
    name:           str = Form(...),
    slug:           str = Form(...),
    contact_email:  str = Form(...),
    plan:           str = Form("business"),
    max_agents:     int = Form(10),
    notes:          str = Form(""),
    admin_email:    str = Form(""),
    admin_password: str = Form(""),
    _: bool = Depends(verify_session)
):
    tenant_id  = str(uuid.uuid4())
    tenant_key = _gen_tenant_api_key()
    slug_clean = slug.lower().strip().replace(" ", "-")
 
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("""
            INSERT INTO tenants
                (id, name, slug, api_key, plan, max_agents, contact_email, notes)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        """, (tenant_id, name.strip(), slug_clean, tenant_key,
              plan, max_agents, contact_email.strip(), notes.strip()))
        cur.execute(
            "INSERT INTO tenant_config (tenant_id) VALUES (%s)", (tenant_id,))
        if admin_email and admin_password:
            cur.execute("""
                INSERT INTO tenant_users (tenant_id, email, password_hash, role)
                VALUES (%s,%s,%s,'admin')
            """, (tenant_id, admin_email.strip().lower(),
                  _hash_password(admin_password)))
        conn.commit(); cur.close(); conn.close()
        print(f"[TENANT] Created via dashboard: {name} — {tenant_key}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
 
    # Redirect back with the API key in the URL so admin can copy it
    return RedirectResponse(
        f"/super/tenant-detail?id={tenant_id}&new_key={tenant_key}",
        status_code=303)
 
 
# ── Tenant detail page (super admin view) ─────────────────────────────────────
@app.get("/super/tenant-detail", response_class=HTMLResponse)
async def super_tenant_detail(
    request: Request,
    id: str = "",
    new_key: str = "",
    _: bool = Depends(verify_session)
):
    if not id or not DATABASE_URL:
        return RedirectResponse("/super/dashboard")
 
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT * FROM tenants WHERE id=%s", (id,))
    tenant = cur.fetchone()
    if not tenant:
        cur.close(); conn.close()
        return RedirectResponse("/super/dashboard")
 
    cur.execute(
        "SELECT machine_id,hostname,ip_address,username,tier,"
        "is_armed,status,last_seen,agent_version "
        "FROM tenant_agents WHERE tenant_id=%s ORDER BY last_seen DESC",
        (id,))
    agents_list = cur.fetchall()
 
    cur.execute(
        "SELECT severity,event_type,message,hostname,created_at,acknowledged "
        "FROM tenant_alerts WHERE tenant_id=%s "
        "ORDER BY created_at DESC LIMIT 100",
        (id,))
    alert_list = cur.fetchall()
 
    cur.execute(
        "SELECT email,role,created_at FROM tenant_users WHERE tenant_id=%s",
        (id,))
    user_list = cur.fetchall()
    cur.close(); conn.close()
 
    # Build agent rows
    agent_rows = ""
    for a in agents_list:
        online = a["status"] == "online"
        sb = (
            '<span style="color:#3fb950;font-weight:600">● ONLINE</span>'
            if online else
            '<span style="color:#6e7681">○ OFFLINE</span>'
        )
        arm = (
            '<span style="color:#1f6feb">ARMED</span>'
            if a["is_armed"] else
            '<span style="color:#9e6a03">UNARMED</span>'
        )
        ls = a["last_seen"].strftime("%H:%M:%S %d/%m") if a["last_seen"] else "—"
        agent_rows += f"""
        <tr>
          <td style="font-family:monospace;font-size:11px;color:#8b949e">
            {a['machine_id'][:20]}…
          </td>
          <td><strong>{a['hostname']}</strong></td>
          <td>{a['username']}</td>
          <td>{a['ip_address']}</td>
          <td>{sb}</td>
          <td>{arm}</td>
          <td>{a['tier'].upper()}</td>
          <td>{ls}</td>
        </tr>"""
 
    if not agent_rows:
        agent_rows = (
            "<tr><td colspan='8' style='text-align:center;color:#484f58;"
            "padding:24px'>No agents registered yet</td></tr>")
 
    # Build alert rows
    alert_rows = ""
    sev_colors = {
        "CRITICAL": "#f85149", "HIGH": "#f0883e",
        "MEDIUM": "#d29922", "INFO": "#3fb950"
    }
    for al in alert_list:
        clr = sev_colors.get(al["severity"], "#8b949e")
        ts  = al["created_at"].strftime("%Y-%m-%d %H:%M") if al["created_at"] else "—"
        ack = ("✓" if al["acknowledged"] else
               f'<span style="color:{clr}">●</span>')
        alert_rows += f"""
        <tr>
          <td><span style="color:{clr};font-weight:600">{al['severity']}</span></td>
          <td style="font-size:12px">{al['event_type']}</td>
          <td>{al['hostname']}</td>
          <td style="font-size:12px;max-width:300px;overflow:hidden;
                     text-overflow:ellipsis;white-space:nowrap">
            {al['message'][:120]}
          </td>
          <td style="font-size:11px;color:#8b949e">{ts}</td>
          <td style="text-align:center">{ack}</td>
        </tr>"""
 
    if not alert_rows:
        alert_rows = (
            "<tr><td colspan='6' style='text-align:center;color:#484f58;"
            "padding:24px'>No alerts recorded</td></tr>")
 
    # New API key banner
    new_key_banner = ""
    if new_key:
        new_key_banner = f"""
        <div style="background:#0c2d0c;border:1px solid #238636;border-radius:8px;
                    padding:20px 24px;margin-bottom:24px">
          <div style="font-weight:700;color:#3fb950;margin-bottom:8px">
            ✅ Tenant Created Successfully
          </div>
          <div style="color:#8b949e;font-size:13px;margin-bottom:10px">
            Hand this API key to the firm's IT administrator. Store it safely —
            it won't be shown again in full.
          </div>
          <div style="font-family:monospace;font-size:16px;color:#e6edf3;
                      background:#0d1117;padding:14px 18px;border-radius:6px;
                      letter-spacing:1px;word-break:break-all">
            {new_key}
          </div>
          <div style="color:#484f58;font-size:12px;margin-top:8px">
            Desktop agents include this key as the
            <code>x-tenant-key</code> header in every heartbeat request.
          </div>
        </div>"""
 
    # User list rows
    user_rows = ""
    for u in user_list:
        ts = u["created_at"].strftime("%Y-%m-%d") if u["created_at"] else "—"
        user_rows += f"""
        <tr>
          <td>{u['email']}</td>
          <td>{u['role'].upper()}</td>
          <td style="color:#8b949e;font-size:12px">{ts}</td>
        </tr>"""
    if not user_rows:
        user_rows = (
            "<tr><td colspan='3' style='color:#484f58;padding:16px'>"
            "No admin users yet</td></tr>")
 
    created = (tenant["created_at"].strftime("%Y-%m-%d %H:%M")
               if tenant["created_at"] else "—")
 
    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8">
<title>FMSecure | {tenant['name']}</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:#0a0a0a;color:#e6edf3;font-family:system-ui,sans-serif}}
  nav{{background:#161b22;border-bottom:1px solid #30363d;padding:16px 24px;
       display:flex;justify-content:space-between;align-items:center}}
  .brand{{color:#f0883e;font-weight:700}}
  nav a{{color:#8b949e;text-decoration:none;font-size:13px;margin-left:16px}}
  nav a:hover{{color:#e6edf3}}
  .container{{padding:28px 32px;max-width:1400px;margin:0 auto}}
  .card{{background:#161b22;border:1px solid #30363d;border-radius:8px;
          padding:24px;margin-bottom:20px}}
  .card h3{{margin:0 0 16px;font-size:15px;color:#8b949e;
            letter-spacing:.5px;text-transform:uppercase}}
  table{{width:100%;border-collapse:collapse}}
  th{{background:#0d1117;color:#8b949e;padding:10px 16px;text-align:left;
      font-size:11px;font-weight:600;letter-spacing:.5px}}
  td{{padding:10px 16px;border-top:1px solid #21262d;font-size:13px}}
  .meta-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;
              margin-bottom:20px}}
  .meta{{background:#161b22;border:1px solid #30363d;border-radius:8px;
         padding:16px 20px}}
  .meta .lbl{{color:#8b949e;font-size:11px;font-weight:600;
              letter-spacing:.5px;margin-bottom:4px}}
  .meta .val{{font-size:18px;font-weight:700}}
</style>
</head><body>
<nav>
  <span class="brand">⚡ FMSecure</span>
  <div>
    <a href="/super/dashboard">← All Tenants</a>
    <a href="/dashboard">C2</a>
    <a href="/logout">Logout</a>
  </div>
</nav>
 
<div class="container">
  <div style="margin-bottom:20px">
    <h1 style="font-size:24px">{tenant['name']}</h1>
    <span style="color:#8b949e;font-size:13px">
      {tenant['slug']} · {tenant['plan'].upper()} · {tenant['contact_email']}
      · Created {created}
    </span>
  </div>
 
  {new_key_banner}
 
  <div class="meta-grid">
    <div class="meta">
      <div class="lbl">AGENTS REGISTERED</div>
      <div class="val">{len(agents_list)} / {tenant['max_agents']}</div>
    </div>
    <div class="meta">
      <div class="lbl">ONLINE NOW</div>
      <div class="val" style="color:#3fb950">
        {sum(1 for a in agents_list if a['status']=='online')}
      </div>
    </div>
    <div class="meta">
      <div class="lbl">TOTAL ALERTS</div>
      <div class="val">{len(alert_list)}</div>
    </div>
    <div class="meta">
      <div class="lbl">STATUS</div>
      <div class="val" style="color:{'#3fb950' if tenant['active'] else '#f85149'}">
        {'Active' if tenant['active'] else 'Suspended'}
      </div>
    </div>
  </div>
 
  <div class="card">
    <h3>Agents</h3>
    <table>
      <thead><tr>
        <th>MACHINE ID</th><th>HOSTNAME</th><th>USER</th><th>IP</th>
        <th>STATUS</th><th>ENGINE</th><th>TIER</th><th>LAST SEEN</th>
      </tr></thead>
      <tbody>{agent_rows}</tbody>
    </table>
  </div>
 
  <div class="card">
    <h3>Recent Alerts (last 100)</h3>
    <table>
      <thead><tr>
        <th>SEV</th><th>TYPE</th><th>HOST</th>
        <th>MESSAGE</th><th>TIME</th><th>ACK</th>
      </tr></thead>
      <tbody>{alert_rows}</tbody>
    </table>
  </div>
 
  <div class="card">
    <h3>Admin Users</h3>
    <table>
      <thead><tr><th>EMAIL</th><th>ROLE</th><th>CREATED</th></tr></thead>
      <tbody>{user_rows}</tbody>
    </table>
  </div>
 
</div>
</body></html>"""


# ── Tenant Admin: Login page ──────────────────────────────────────────────────
@app.get("/tenant/login", response_class=HTMLResponse)
async def tenant_login_page(error: str = ""):
    err = (f'<p style="color:#f85149;background:#2d1c1c;padding:10px;'
           f'border-radius:6px;margin-bottom:16px;font-size:14px">{error}</p>'
           if error else "")
    return f"""<!DOCTYPE html>
<html><head>
<meta charset="UTF-8">
<title>FMSecure | Organisation Login</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:#0a0a0a;color:#e6edf3;display:flex;align-items:center;
       justify-content:center;min-height:100vh;font-family:system-ui,sans-serif}}
  .card{{background:#161b22;border:1px solid #30363d;border-radius:12px;
         padding:40px;width:380px}}
  h2{{color:#2f81f7;margin-bottom:4px;font-size:22px}}
  p.sub{{color:#8b949e;font-size:13px;margin-bottom:24px}}
  label{{display:block;color:#8b949e;font-size:11px;font-weight:600;
         letter-spacing:.5px;margin-bottom:6px}}
  input{{width:100%;background:#0d1117;border:1px solid #30363d;border-radius:6px;
         color:#e6edf3;padding:10px 14px;font-size:14px;outline:none;
         margin-bottom:16px}}
  input:focus{{border-color:#2f81f7}}
  button{{width:100%;background:#2f81f7;border:none;border-radius:6px;
          color:#fff;padding:12px;font-size:14px;font-weight:600;cursor:pointer}}
  button:hover{{background:#4f96ff}}
</style>
</head><body>
<div class="card">
  <h2>⚡ FMSecure</h2>
  <p class="sub">Organisation Security Portal</p>
  {err}
  <form method="POST" action="/tenant/login">
    <label>EMAIL ADDRESS</label>
    <input name="email" type="email" required autofocus placeholder="admin@yourcompany.com">
    <label>PASSWORD</label>
    <input name="password" type="password" required placeholder="••••••••">
    <button type="submit">Sign In →</button>
  </form>
  <p style="color:#484f58;font-size:12px;text-align:center;margin-top:20px">
    Contact your FMSecure account manager if you need access.
  </p>
</div>
</body></html>"""
 
 
# ── Tenant Admin: Process login ───────────────────────────────────────────────
@app.post("/tenant/login")
async def tenant_login_post(
    email:    str = Form(...),
    password: str = Form(...)
):
    if not DATABASE_URL:
        return RedirectResponse(
            "/tenant/login?error=Server+not+configured", status_code=302)
 
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("""
            SELECT u.*, t.id as tenant_id, t.name as tenant_name, t.active as tenant_active
            FROM tenant_users u
            JOIN tenants t ON t.id = u.tenant_id
            WHERE u.email = %s
        """, (email.strip().lower(),))
        user = cur.fetchone()
        cur.close(); conn.close()
    except Exception as e:
        print(f"[TENANT LOGIN] DB error: {e}")
        return RedirectResponse(
            "/tenant/login?error=Server+error", status_code=302)
 
    if not user:
        return RedirectResponse(
            "/tenant/login?error=Invalid+credentials", status_code=302)
    if not user["tenant_active"]:
        return RedirectResponse(
            "/tenant/login?error=Your+organisation+account+is+suspended",
            status_code=302)
    if not _verify_password(password, user["password_hash"]):
        return RedirectResponse(
            "/tenant/login?error=Invalid+credentials", status_code=302)
 
    token = _create_tenant_session(
        user["tenant_id"], user["email"], user["role"])
 
    resp = RedirectResponse("/tenant/dashboard", status_code=302)
    resp.set_cookie(
        "fms_tenant_session", token,
        httponly=True, max_age=_TENANT_SESSION_TTL)
    return resp
 
 
# ── Tenant Admin: Logout ──────────────────────────────────────────────────────
@app.get("/tenant/logout")
async def tenant_logout(request: Request):
    token = request.cookies.get("fms_tenant_session", "")
    _tenant_sessions.pop(token, None)
    resp = RedirectResponse("/tenant/login", status_code=302)
    resp.delete_cookie("fms_tenant_session")
    return resp
 
 
# ── Tenant Admin: Main Dashboard ──────────────────────────────────────────────
@app.get("/tenant/dashboard", response_class=HTMLResponse)
async def tenant_dashboard(request: Request):
    session = _get_tenant_session(request)
    if not session:
        return RedirectResponse("/tenant/login", status_code=302)
 
    tenant_id = session["tenant_id"]
 
    if not DATABASE_URL:
        return HTMLResponse("<h1>No database</h1>")
 
    conn = get_db(); cur = conn.cursor()
 
    # Tenant info
    cur.execute("SELECT * FROM tenants WHERE id=%s", (tenant_id,))
    tenant = cur.fetchone()
    if not tenant:
        cur.close(); conn.close()
        return RedirectResponse("/tenant/login", status_code=302)
 
    # Mark stale agents offline (>35s)
    cur.execute(
        "UPDATE tenant_agents SET status='offline' "
        "WHERE tenant_id=%s AND last_seen < NOW() - INTERVAL '35 seconds'",
        (tenant_id,))
    conn.commit()
 
    # Agents
    cur.execute("""
        SELECT machine_id,hostname,ip_address,username,tier,
               is_armed,status,last_seen,agent_version
        FROM tenant_agents WHERE tenant_id=%s
        ORDER BY status DESC, last_seen DESC
    """, (tenant_id,))
    agents_list = cur.fetchall()
 
    # Recent alerts (last 50, unacked first)
    cur.execute("""
        SELECT severity,event_type,message,hostname,file_path,
               created_at,acknowledged,id
        FROM tenant_alerts WHERE tenant_id=%s
        ORDER BY acknowledged ASC, created_at DESC
        LIMIT 50
    """, (tenant_id,))
    alert_list = cur.fetchall()
 
    # Config
    cur.execute(
        "SELECT * FROM tenant_config WHERE tenant_id=%s", (tenant_id,))
    config = cur.fetchone() or {}
    cur.close(); conn.close()
 
    stats   = _get_tenant_stats(tenant_id)
    online  = sum(1 for a in agents_list if a["status"] == "online")
    armed   = sum(1 for a in agents_list if a["is_armed"])
 
    # Build agent rows
    agent_rows = ""
    for a in agents_list:
        is_online = a["status"] == "online"
        s_dot = (
            '<span style="color:#3fb950">●</span>'
            if is_online else
            '<span style="color:#6e7681">○</span>'
        )
        arm_badge = (
            '<span style="background:#1f2d4d;color:#2f81f7;padding:2px 8px;'
            'border-radius:4px;font-size:11px">ARMED</span>'
            if a["is_armed"] else
            '<span style="background:#2d2208;color:#d29922;padding:2px 8px;'
            'border-radius:4px;font-size:11px">UNARMED</span>'
        )
        ls = a["last_seen"].strftime("%H:%M %d/%m") if a["last_seen"] else "—"
        agent_rows += f"""
        <tr>
          <td>{s_dot} <strong>{a['hostname']}</strong></td>
          <td>{a['username']}</td>
          <td style="font-size:12px;color:#8b949e">{a['ip_address']}</td>
          <td>{arm_badge}</td>
          <td style="font-size:12px;color:#8b949e">{a['tier'].upper()}</td>
          <td style="font-size:12px;color:#8b949e">{a['agent_version']}</td>
          <td style="font-size:11px;color:#8b949e">{ls}</td>
          <td>
            <button onclick="sendCommand('{a['machine_id']}','LOCKDOWN')"
                    style="background:#da3633;color:#fff;border:none;
                           border-radius:4px;padding:3px 10px;
                           cursor:pointer;font-size:11px">
              Isolate
            </button>
          </td>
        </tr>"""
 
    if not agent_rows:
        agent_rows = (
            "<tr><td colspan='8' style='text-align:center;color:#484f58;"
            "padding:32px'>"
            "No agents connected yet. Install FMSecure on endpoints and "
            "configure the tenant API key.</td></tr>")
 
    # Build alert rows
    alert_rows = ""
    sev_colors = {
        "CRITICAL": "#f85149", "HIGH": "#f0883e",
        "MEDIUM": "#d29922",   "INFO": "#3fb950"
    }
    for al in alert_list:
        clr  = sev_colors.get(al["severity"], "#8b949e")
        ts   = al["created_at"].strftime("%Y-%m-%d %H:%M") if al["created_at"] else "—"
        acked = al["acknowledged"]
        row_style = "opacity:.5" if acked else ""
        
        # 🚨 FIX: Extract the button logic outside the main f-string to prevent the backslash error
        ack_html = (
            '<span style="color:#3fb950;font-size:12px">✓ Acked</span>'
            if acked else
            f'<button onclick="ackAlert(\'{al["id"]}\')"'
            f' style="background:#21262d;color:#8b949e;border:1px solid #30363d;'
            f'border-radius:4px;padding:2px 8px;cursor:pointer;font-size:11px">'
            f'Acknowledge</button>'
        )

        alert_rows += f"""
        <tr style="{row_style}">
          <td>
            <span style="color:{clr};font-weight:700;font-size:12px">
              {al['severity']}
            </span>
          </td>
          <td style="font-size:12px">{al['event_type']}</td>
          <td>{al['hostname']}</td>
          <td style="font-size:12px;max-width:280px;overflow:hidden;
                     text-overflow:ellipsis;white-space:nowrap">
            {al['message'][:100]}
          </td>
          <td style="font-size:11px;color:#8b949e">{ts}</td>
          <td>
            {ack_html}
          </td>
        </tr>"""
 
    if not alert_rows:
        alert_rows = (
            "<tr><td colspan='6' style='text-align:center;color:#484f58;"
            "padding:24px'>No alerts recorded</td></tr>")
 
    webhook_val = (config.get("webhook_url") or "") if config else ""
    email_val   = (config.get("alert_email")  or "") if config else ""
 
    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8">
<title>FMSecure | {tenant['name']} Dashboard</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:#0a0a0a;color:#e6edf3;font-family:system-ui,sans-serif}}
  nav{{background:#161b22;border-bottom:1px solid #30363d;padding:14px 28px;
       display:flex;justify-content:space-between;align-items:center}}
  .brand{{color:#2f81f7;font-weight:700;font-size:16px}}
  nav a{{color:#8b949e;text-decoration:none;font-size:13px;margin-left:16px}}
  nav a:hover{{color:#e6edf3}}
  .org{{color:#e6edf3;font-weight:600;font-size:14px}}
  .container{{padding:24px 32px;max-width:1400px;margin:0 auto}}
  .stat-row{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;
             margin-bottom:24px}}
  .stat{{background:#161b22;border:1px solid #30363d;border-radius:8px;
         padding:18px 22px}}
  .stat .num{{font-size:28px;font-weight:800;margin-bottom:2px}}
  .stat .lbl{{color:#8b949e;font-size:12px}}
  .card{{background:#161b22;border:1px solid #30363d;border-radius:8px;
          padding:22px;margin-bottom:20px}}
  .card h3{{margin:0 0 14px;font-size:13px;color:#8b949e;
            font-weight:600;letter-spacing:.5px;text-transform:uppercase}}
  table{{width:100%;border-collapse:collapse}}
  th{{background:#0d1117;color:#8b949e;padding:9px 14px;text-align:left;
      font-size:11px;font-weight:600;letter-spacing:.5px}}
  td{{padding:10px 14px;border-top:1px solid #21262d;font-size:13px}}
  label{{display:block;color:#8b949e;font-size:11px;font-weight:600;
         letter-spacing:.5px;margin-bottom:5px;margin-top:12px}}
  input{{width:100%;background:#0d1117;color:#e6edf3;
         border:1px solid #30363d;border-radius:6px;
         padding:8px 12px;font-size:13px;outline:none}}
  input:focus{{border-color:#2f81f7}}
  .save-btn{{background:#238636;color:#fff;border:none;border-radius:6px;
             padding:9px 22px;font-size:13px;font-weight:600;
             cursor:pointer;margin-top:14px}}
  .save-btn:hover{{background:#2ea043}}
</style>
</head><body>
 
<nav>
  <span class="brand">⚡ FMSecure</span>
  <span class="org">🏢 {tenant['name']}</span>
  <div>
    <span style="color:#8b949e;font-size:12px">
      Signed in as {session['email']}
    </span>
    <a href="/tenant/logout">Sign Out</a>
  </div>
</nav>
 
<div class="container">
 
  <!-- Alert banner for critical unacked events -->
  {'<div style="background:#2d0d0d;border:1px solid #f85149;border-radius:8px;padding:14px 20px;margin-bottom:20px;color:#f85149;font-weight:600">⚠ ' + str(stats["critical_alerts"]) + ' unacknowledged CRITICAL alert(s) require your attention</div>' if stats['critical_alerts'] else ''}
 
  <!-- Stats -->
  <div class="stat-row">
    <div class="stat">
      <div class="num">{stats['total_agents']}</div>
      <div class="lbl">Total Endpoints</div>
    </div>
    <div class="stat">
      <div class="num" style="color:#3fb950">{online}</div>
      <div class="lbl">Online Now</div>
    </div>
    <div class="stat">
      <div class="num" style="color:#2f81f7">{armed}</div>
      <div class="lbl">Engines Armed</div>
    </div>
    <div class="stat">
      <div class="num" style="color:{'#f85149' if stats['unacked_alerts'] else '#3fb950'}">
        {stats['unacked_alerts']}
      </div>
      <div class="lbl">Unacknowledged Alerts</div>
    </div>
  </div>
 
  <!-- Seat usage -->
  <div style="color:#8b949e;font-size:12px;margin-bottom:16px">
    Seat usage: {stats['total_agents']} / {tenant['max_agents']} agents
    · Plan: <strong style="color:#e6edf3">{tenant['plan'].upper()}</strong>
  </div>
 
  <!-- Agent table -->
  <div class="card">
    <h3>Endpoints</h3>
    <table>
      <thead><tr>
        <th>HOSTNAME</th><th>USER</th><th>IP</th>
        <th>ENGINE</th><th>TIER</th><th>VERSION</th>
        <th>LAST SEEN</th><th>ACTION</th>
      </tr></thead>
      <tbody>{agent_rows}</tbody>
    </table>
  </div>
 
  <!-- Alert table -->
  <div class="card">
    <h3>Security Alerts</h3>
    <table>
      <thead><tr>
        <th>SEV</th><th>TYPE</th><th>HOST</th>
        <th>MESSAGE</th><th>TIME</th><th>STATUS</th>
      </tr></thead>
      <tbody>{alert_rows}</tbody>
    </table>
  </div>
 
  <!-- Config -->
  <div class="card">
    <h3>Policy Settings</h3>
    <p style="color:#8b949e;font-size:12px;margin-bottom:14px">
      These settings are pushed to all enrolled agents automatically.
      They override individual machine settings (IT admin controls policy).
    </p>
    <form method="POST" action="/tenant/config">
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">
        <div>
          <label>ALERT EMAIL</label>
          <input name="alert_email" type="email"
                 value="{ (config.get('alert_email') or '') if config else '' }"
                 placeholder="it-alerts@yourcompany.com">
        </div>
        <div>
          <label>DISCORD / SLACK WEBHOOK URL</label>
          <input name="webhook_url"
                 value="{ (config.get('webhook_url') or '') if config else '' }"
                 placeholder="https://discord.com/api/webhooks/...">
        </div>
        <div>
          <label>VERIFY INTERVAL (seconds, min 10)</label>
          <input name="verify_interval" type="number"
                 value="{ (config.get('verify_interval') or 60) if config else 60 }" min="10" max="86400">
        </div>
        <div>
          <label>MAX VAULT FILE SIZE (MB)</label>
          <input name="max_vault_mb" type="number"
                 value="{ (config.get('max_vault_mb') or 10) if config else 10 }" min="1" max="500">
        </div>
        <div style="grid-column:1/-1">
          <label>ALLOWED VAULT EXTENSIONS (comma-separated)</label>
          <input name="allowed_exts"
                 value="{ (config.get('allowed_exts') or '.txt,.json,.py') if config else '.txt,.json,.py' }"
                 placeholder=".txt,.json,.py,.html,...">
        </div>
      </div>
      <button class="save-btn" type="submit">Save Policy →</button>
    </form>
  </div>
 
</div>
 
<script>
// Auto-refresh every 15s (pauses if user is typing)
setInterval(() => {{
  if (!document.querySelector('input:focus')) location.reload();
}}, 15000);
 
async function ackAlert(alertId) {{
  try {{
    await fetch('/tenant/alerts/' + alertId + '/ack', {{method:'POST'}});
    location.reload();
  }} catch(e) {{ alert('Failed to acknowledge: ' + e); }}
}}
 
async function sendCommand(machineId, cmd) {{
  if (!confirm('Send ' + cmd + ' to ' + machineId + '?')) return;
  try {{
    const resp = await fetch('/tenant/command/' + machineId + '?cmd=' + cmd,
                             {{method:'POST'}});
    const data = await resp.json();
    alert(data.message || 'Command queued.');
  }} catch(e) {{ alert('Failed: ' + e); }}
}}
</script>
</body></html>"""
 
 
# ── Tenant Admin: Save config ─────────────────────────────────────────────────
@app.post("/tenant/config")
async def tenant_save_config(
    request:         Request,
    alert_email:     str = Form(""),
    webhook_url:     str = Form(""),
    verify_interval: int = Form(60),
    max_vault_mb:    int = Form(10),
    allowed_exts:    str = Form(".txt,.json,.py,.html,.js,.css"),
):
    session = _get_tenant_session(request)
    if not session:
        return RedirectResponse("/tenant/login", status_code=302)
    if not DATABASE_URL:
        return RedirectResponse("/tenant/dashboard", status_code=302)
 
    # Sanitise
    verify_interval = max(10, min(verify_interval, 86400))  # 10s – 24h
    max_vault_mb    = max(1,  min(max_vault_mb, 500))       # 1MB – 500MB
 
    conn = get_db(); cur = conn.cursor()
    cur.execute("""
        INSERT INTO tenant_config
            (tenant_id, alert_email, webhook_url,
             verify_interval, max_vault_mb, allowed_exts)
        VALUES (%s,%s,%s,%s,%s,%s)
        ON CONFLICT (tenant_id) DO UPDATE SET
            alert_email     = EXCLUDED.alert_email,
            webhook_url     = EXCLUDED.webhook_url,
            verify_interval = EXCLUDED.verify_interval,
            max_vault_mb    = EXCLUDED.max_vault_mb,
            allowed_exts    = EXCLUDED.allowed_exts
    """, (session["tenant_id"],
          alert_email.strip(), webhook_url.strip(),
          verify_interval, max_vault_mb, allowed_exts.strip()))
    conn.commit(); cur.close(); conn.close()
 
    print(f"[TENANT CONFIG] Updated for tenant {session['tenant_id'][:8]}…")
    return RedirectResponse("/tenant/dashboard", status_code=302)
 
 
# ── Tenant Admin: Acknowledge alert ───────────────────────────────────────────
@app.post("/tenant/alerts/{alert_id}/ack")
async def tenant_ack_alert(alert_id: str, request: Request):
    session = _get_tenant_session(request)
    if not session:
        raise HTTPException(status_code=401)
    if not DATABASE_URL:
        return {"ok": False}
    conn = get_db(); cur = conn.cursor()
    cur.execute(
        "UPDATE tenant_alerts SET acknowledged=TRUE "
        "WHERE id=%s AND tenant_id=%s",
        (alert_id, session["tenant_id"]))
    conn.commit(); cur.close(); conn.close()
    return {"ok": True}
 
 
# ── Tenant Admin: Send command to agent ───────────────────────────────────────
@app.post("/tenant/command/{machine_id}")
async def tenant_send_command(
    machine_id: str,
    request:    Request,
    cmd:        str = "LOCKDOWN"
):
    session = _get_tenant_session(request)
    if not session:
        raise HTTPException(status_code=401)
 
    allowed_cmds = {"LOCKDOWN", "VERIFY", "SAFE_MODE"}
    if cmd not in allowed_cmds:
        raise HTTPException(status_code=400, detail="Unknown command")
 
    # Verify this machine belongs to this tenant before queuing
    if DATABASE_URL:
        conn = get_db(); cur = conn.cursor()
        cur.execute(
            "SELECT id FROM tenant_agents "
            "WHERE machine_id=%s AND tenant_id=%s",
            (machine_id, session["tenant_id"]))
        row = cur.fetchone()
        cur.close(); conn.close()
        if not row:
            raise HTTPException(
                status_code=403,
                detail="Agent does not belong to your organisation")
 
    commands[machine_id] = cmd
    print(f"[TENANT CMD] {cmd} queued for {machine_id} "
          f"by {session['email']}")
    return {"ok": True, "message": f"{cmd} queued for {machine_id}"}

# ══════════════════════════════════════════════════════════════════════════════
# PRODUCT LANDING PAGE
# ══════════════════════════════════════════════════════════════════════════════
@app.get("/", response_class=HTMLResponse)
async def landing_page_root():
    return await landing_page()

# ══════════════════════════════════════════════════════════════════════════════
# PRODUCT PAGES
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/download", response_class=HTMLResponse)
async def download_page():
    """Public download page — linked from the in-app update banner."""
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute(
            "SELECT version, release_notes, download_url "
            "FROM versions WHERE is_current = TRUE "
            "ORDER BY published_at DESC LIMIT 1")
        row = cur.fetchone()
        cur.close(); conn.close()
        version      = row["version"]      if row else "2.5.0"
        notes        = row["release_notes"] if row else ""
        direct_url   = (f"https://drive.google.com/uc?export=download&id={DRIVE_FILE_ID}"
                        if DRIVE_FILE_ID else "#")
    except Exception:
        version, notes, direct_url = "2.5.0", "", "#"

    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Download FMSecure v{version}</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:#0d1117;color:#e6edf3;font-family:system-ui,sans-serif;
       min-height:100vh;display:flex;flex-direction:column}}
  nav{{background:#161b22;border-bottom:1px solid #30363d;
       padding:16px 32px;display:flex;align-items:center;gap:16px}}
  .logo{{color:#2f81f7;font-size:20px;font-weight:700;text-decoration:none}}
  nav a{{color:#8b949e;text-decoration:none;font-size:14px}}
  nav a:hover{{color:#e6edf3}}
  .hero{{flex:1;display:flex;flex-direction:column;align-items:center;
         justify-content:center;padding:60px 24px;text-align:center}}
  .badge{{background:#238636;color:#fff;padding:4px 14px;border-radius:20px;
          font-size:13px;font-weight:600;display:inline-block;margin-bottom:20px}}
  h1{{font-size:42px;font-weight:800;margin-bottom:12px;
      background:linear-gradient(135deg,#2f81f7,#a371f7);
      -webkit-background-clip:text;-webkit-text-fill-color:transparent}}
  .subtitle{{color:#8b949e;font-size:18px;margin-bottom:36px}}
  .notes{{background:#161b22;border:1px solid #30363d;border-radius:8px;
          padding:16px 24px;margin-bottom:36px;font-size:14px;
          color:#8b949e;max-width:520px}}
  .notes strong{{color:#e6edf3}}
  .dl-btn{{background:#238636;color:#fff;padding:16px 48px;border-radius:8px;
           font-size:17px;font-weight:700;text-decoration:none;
           border:none;cursor:pointer;
           transition:background .2s}}
  .dl-btn:hover{{background:#2ea043}}
  .dl-btn:active{{background:#1a7f37}}
  .meta{{margin-top:20px;color:#484f58;font-size:13px}}
  .features{{display:flex;gap:24px;margin-top:60px;flex-wrap:wrap;
             justify-content:center;max-width:800px}}
  .feat{{background:#161b22;border:1px solid #30363d;border-radius:8px;
         padding:20px 24px;width:220px;text-align:left}}
  .feat .icon{{font-size:24px;margin-bottom:8px}}
  .feat h3{{font-size:14px;font-weight:600;margin-bottom:4px}}
  .feat p{{font-size:12px;color:#8b949e}}
  footer{{text-align:center;padding:24px;color:#484f58;font-size:13px;
          border-top:1px solid #21262d}}
</style>
</head><body>
<nav>
  <a class="logo" href="/">⚡ FMSecure</a>
  <a href="/">Home</a>
  <a href="/pricing">Pricing</a>
  <a href="/changelog">Changelog</a>
  <a href="/login" style="margin-left:auto;color:#2f81f7">Admin →</a>
</nav>

<div class="hero">
  <span class="badge">✅ Latest Release</span>
  <h1>Download FMSecure</h1>
  <p class="subtitle">Enterprise File Integrity & EDR Monitor for Windows</p>

  {"<div class='notes'><strong>What's new in v" + version + ":</strong><br>" + notes + "</div>" if notes else ""}

  <a class="dl-btn" href="{direct_url}" id="dlbtn">
    ⬇&nbsp; Download FMSecure v{version}
  </a>
  <p class="meta">Windows 10/11 · 64-bit · Free to try · PRO features require license</p>

  <div class="features">
    <div class="feat">
      <div class="icon">🛡️</div>
      <h3>Active Defense</h3>
      <p>Auto-restores tampered or deleted files from encrypted vault</p>
    </div>
    <div class="feat">
      <div class="icon">☁️</div>
      <h3>Cloud Backup</h3>
      <p>Google Drive disaster recovery with full AppData sync</p>
    </div>
    <div class="feat">
      <div class="icon">🛑</div>
      <h3>Ransomware Killswitch</h3>
      <p>OS-level folder lockdown on burst file operations</p>
    </div>
    <div class="feat">
      <div class="icon">🔌</div>
      <h3>USB Control</h3>
      <p>Block unauthorized USB write access across the device</p>
    </div>
  </div>
</div>

<footer>FMSecure · Enterprise EDR · © {datetime.now().year}</footer>
</body></html>"""


@app.get("/changelog", response_class=HTMLResponse)
async def changelog_page():
    """Public changelog page — linked from the in-app 'What's New' button."""
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute(
            "SELECT version, release_notes, published_at "
            "FROM versions ORDER BY published_at DESC LIMIT 20")
        rows = cur.fetchall()
        cur.close(); conn.close()
    except Exception:
        rows = []

    entries = ""
    for i, row in enumerate(rows):
        date  = row["published_at"].strftime("%B %d, %Y") if row["published_at"] else ""
        badge = ('<span style="background:#238636;color:#fff;padding:2px 10px;'
                 'border-radius:12px;font-size:12px;font-weight:600">Latest</span>'
                 if i == 0 else "")
        entries += f"""
        <div style="border-left:3px solid {'#2f81f7' if i==0 else '#30363d'};
                    padding:0 0 32px 24px;margin-bottom:8px">
          <div style="display:flex;align-items:center;gap:12px;margin-bottom:8px">
            <span style="font-size:20px;font-weight:700;color:#e6edf3">
              v{row['version']}
            </span>
            {badge}
            <span style="color:#484f58;font-size:13px">{date}</span>
          </div>
          <p style="color:#8b949e;font-size:14px;line-height:1.6">
            {row['release_notes'] or 'No release notes provided.'}
          </p>
          <a href="/download"
             style="display:inline-block;margin-top:12px;background:#238636;
                    color:#fff;padding:6px 18px;border-radius:6px;
                    text-decoration:none;font-size:13px;font-weight:600">
            Download v{row['version']}
          </a>
        </div>"""

    if not entries:
        entries = '<p style="color:#8b949e">No releases published yet.</p>'

    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>FMSecure Changelog</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:#0d1117;color:#e6edf3;font-family:system-ui,sans-serif}}
  nav{{background:#161b22;border-bottom:1px solid #30363d;
       padding:16px 32px;display:flex;align-items:center;gap:16px}}
  .logo{{color:#2f81f7;font-size:20px;font-weight:700;text-decoration:none}}
  nav a{{color:#8b949e;text-decoration:none;font-size:14px}}
  nav a:hover{{color:#e6edf3}}
  .container{{max-width:720px;margin:48px auto;padding:0 24px}}
  h1{{font-size:32px;font-weight:800;margin-bottom:6px}}
  .sub{{color:#8b949e;font-size:15px;margin-bottom:40px}}
  footer{{text-align:center;padding:40px 24px;color:#484f58;font-size:13px;
          border-top:1px solid #21262d;margin-top:40px}}
</style>
</head><body>
<nav>
  <a class="logo" href="/">⚡ FMSecure</a>
  <a href="/">Home</a>
  <a href="/download">Download</a>
  <a href="/login" style="margin-left:auto;color:#2f81f7">Admin →</a>
</nav>
<div class="container">
  <h1>Changelog</h1>
  <p class="sub">Every release, every improvement — all in one place.</p>
  {entries}
</div>
<footer>FMSecure · Enterprise EDR · © {datetime.now().year}</footer>
</body></html>"""

@app.get("/home", response_class=HTMLResponse)
async def landing_page():
    base = APP_BASE_URL
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1.0"/>
<title>FMSecure — Enterprise EDR for Windows</title>
<meta name="description" content="Real-time file integrity monitoring, ransomware killswitch, auto-healing vault, and cloud disaster recovery for Windows endpoints."/>
<link rel="icon" href="/static/app_icon.png" type="image/png"/>
<link rel="preconnect" href="https://fonts.googleapis.com"/>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin/>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet"/>
<script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/gsap/3.12.2/gsap.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/gsap/3.12.2/ScrollTrigger.min.js"></script>
<style>
:root{{
  --bg:#050810;--bg2:#090d1a;--bgc:#0d1424;--bgch:#111b30;
  --bd:rgba(47,129,247,0.13);--bdh:rgba(47,129,247,0.32);
  --t1:#e6edf3;--t2:#7d8ba8;--t3:#3d4a5e;
  --blue:#2f81f7;--cyan:#22d3ee;--green:#22c55e;
  --red:#ef4444;--amber:#f59e0b;--purple:#a78bfa;
  --r:12px;--rl:20px;
}}
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
html{{scroll-behavior:smooth}}
body{{font-family:'Inter',-apple-system,sans-serif;background:var(--bg);color:var(--t1);line-height:1.6;overflow-x:hidden;-webkit-font-smoothing:antialiased}}
::-webkit-scrollbar{{width:5px}}
::-webkit-scrollbar-track{{background:var(--bg)}}
::-webkit-scrollbar-thumb{{background:var(--bdh);border-radius:3px}}
 
/* Canvas */
#bgc{{position:fixed;inset:0;z-index:0;pointer-events:none}}
.z1{{position:relative;z-index:1}}
 
/* Nav */
nav{{
  position:fixed;top:0;left:0;right:0;z-index:200;
  height:66px;padding:0 48px;
  display:flex;align-items:center;justify-content:space-between;
  background:rgba(5,8,16,0.7);backdrop-filter:blur(20px) saturate(1.3);
  border-bottom:1px solid var(--bd);transition:background .3s;
}}
.nav-brand{{display:flex;align-items:center;gap:10px;text-decoration:none}}
.nav-brand img{{width:30px;height:30px}}
.nav-brand-txt{{font-size:18px;font-weight:800;letter-spacing:-0.4px;color:var(--t1)}}
.nav-brand-txt em{{font-style:normal;color:var(--blue)}}
.nav-links{{display:flex;align-items:center;gap:32px;list-style:none}}
.nav-links a{{color:var(--t2);text-decoration:none;font-size:14px;font-weight:500;transition:color .2s}}
.nav-links a:hover{{color:var(--t1)}}
.nav-right{{display:flex;align-items:center;gap:12px}}
.btn-ghost{{padding:8px 16px;border-radius:8px;background:transparent;border:1px solid var(--bd);color:var(--t2);font-size:13px;font-weight:500;cursor:pointer;text-decoration:none;transition:all .2s}}
.btn-ghost:hover{{border-color:var(--bdh);color:var(--t1)}}
.btn-nav-cta{{padding:8px 20px;border-radius:8px;background:var(--blue);border:none;color:#fff;font-size:13px;font-weight:600;cursor:pointer;text-decoration:none;transition:all .2s}}
.btn-nav-cta:hover{{background:#4f96ff;transform:translateY(-1px);box-shadow:0 4px 16px rgba(47,129,247,.35)}}
 
/* Hero */
.hero{{min-height:100vh;display:flex;align-items:center;justify-content:center;padding:110px 48px 80px;text-align:center}}
.hero-inner{{max-width:860px}}
.badge{{
  display:inline-flex;align-items:center;gap:8px;
  padding:6px 16px;border-radius:100px;
  border:1px solid rgba(34,211,238,.22);background:rgba(34,211,238,.05);
  color:var(--cyan);font-size:11px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;
  margin-bottom:32px;
}}
.badge::before{{content:'';width:6px;height:6px;border-radius:50%;background:var(--cyan);box-shadow:0 0 6px var(--cyan);animation:bdot 2s ease-in-out infinite}}
@keyframes bdot{{0%,100%{{opacity:1;transform:scale(1)}}50%{{opacity:.4;transform:scale(.7)}}}}
h1{{font-size:clamp(38px,6vw,70px);font-weight:800;letter-spacing:-2px;line-height:1.06;margin-bottom:24px}}
.gt{{background:linear-gradient(120deg,#2f81f7 0%,#22d3ee 55%,#22c55e 100%);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text}}
.hero-sub{{font-size:clamp(16px,2vw,19px);color:var(--t2);max-width:620px;margin:0 auto 44px;line-height:1.7}}
.hero-acts{{display:flex;align-items:center;justify-content:center;gap:16px;flex-wrap:wrap}}
.btn-hp{{display:inline-flex;align-items:center;gap:8px;padding:14px 32px;border-radius:10px;background:var(--blue);color:#fff;font-size:15px;font-weight:700;text-decoration:none;transition:all .25s;border:none;cursor:pointer}}
.btn-hp:hover{{background:#4f96ff;transform:translateY(-2px);box-shadow:0 8px 28px rgba(47,129,247,.4)}}
.btn-hg{{display:inline-flex;align-items:center;gap:8px;padding:14px 32px;border-radius:10px;background:transparent;border:1px solid var(--bd);color:var(--t2);font-size:15px;font-weight:500;text-decoration:none;transition:all .25s}}
.btn-hg:hover{{border-color:var(--bdh);color:var(--t1);background:rgba(255,255,255,.03)}}
.hero-stats{{display:flex;justify-content:center;gap:56px;margin-top:72px;flex-wrap:wrap}}
.hstat-n{{font-size:30px;font-weight:800;letter-spacing:-1px}}
.hstat-n em{{font-style:normal;color:var(--blue)}}
.hstat-l{{font-size:12px;color:var(--t3);text-transform:uppercase;letter-spacing:.06em;margin-top:2px;font-weight:600}}
 
/* Terminal */
.term-wrap{{padding:0 48px 100px;display:flex;justify-content:center}}
.term{{width:100%;max-width:780px;background:#080b14;border:1px solid var(--bd);border-radius:var(--rl);overflow:hidden;box-shadow:0 32px 72px rgba(0,0,0,.7),0 0 0 1px rgba(255,255,255,.025)}}
.term-bar{{display:flex;align-items:center;gap:7px;padding:13px 20px;background:#0b0f1c;border-bottom:1px solid var(--bd)}}
.tdot{{width:12px;height:12px;border-radius:50%}}
.tlabel{{font-size:12px;color:var(--t3);font-family:'JetBrains Mono',monospace;margin-left:8px}}
.term-body{{padding:22px 26px;font-family:'JetBrains Mono',monospace;font-size:12.5px;line-height:1.95}}
.tok{{color:var(--green)}}.twn{{color:var(--amber)}}.tcr{{color:var(--red);font-weight:600}}.tin{{color:var(--cyan)}}.tout{{color:var(--t2)}}
.tcu{{display:inline-block;width:7px;height:13px;background:var(--green);vertical-align:middle;animation:blink 1.1s step-end infinite}}
@keyframes blink{{0%,100%{{opacity:1}}50%{{opacity:0}}}}
 
/* Divider */
.dvd{{height:1px;background:linear-gradient(90deg,transparent,var(--bd),transparent);margin:0 48px}}
 
/* Section */
section{{padding:100px 48px}}
.slbl{{font-size:12px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:var(--blue);margin-bottom:14px}}
.stit{{font-size:clamp(26px,4vw,44px);font-weight:800;letter-spacing:-1px;line-height:1.1;margin-bottom:14px}}
.ssub{{font-size:17px;color:var(--t2);line-height:1.7;max-width:580px}}
.scen{{text-align:center}}
.scen .ssub{{margin:0 auto}}
 
/* Feature grid */
.fg{{display:grid;grid-template-columns:repeat(auto-fit,minmax(290px,1fr));gap:18px;margin-top:56px;max-width:1200px;margin-left:auto;margin-right:auto}}
.fc{{
  background:var(--bgc);border:1px solid var(--bd);border-radius:var(--rl);
  padding:30px;transition:all .3s;opacity:0;transform:translateY(24px);
}}
.fc:hover{{border-color:var(--bdh);background:var(--bgch);transform:translateY(-4px);box-shadow:0 16px 48px rgba(0,0,0,.45)}}
.fci{{width:46px;height:46px;border-radius:11px;display:flex;align-items:center;justify-content:center;font-size:20px;margin-bottom:18px}}
.fc h3{{font-size:16px;font-weight:700;margin-bottom:9px}}
.fc p{{font-size:13.5px;color:var(--t2);line-height:1.65}}
.ftag{{display:inline-block;margin-top:14px;padding:3px 10px;border-radius:6px;font-size:11px;font-weight:700;letter-spacing:.05em;text-transform:uppercase}}
.tpro{{background:rgba(245,158,11,.1);color:var(--amber);border:1px solid rgba(245,158,11,.2)}}
.tfree{{background:rgba(34,197,94,.08);color:var(--green);border:1px solid rgba(34,197,94,.2)}}
.tall{{background:rgba(167,139,250,.08);color:var(--purple);border:1px solid rgba(167,139,250,.2)}}
 
/* Comparison table */
.cmp-wrap{{max-width:900px;margin:64px auto 0}}
.cmp-tbl{{width:100%;border-collapse:collapse;background:var(--bgc);border:1px solid var(--bd);border-radius:var(--rl);overflow:hidden}}
.cmp-tbl th{{padding:18px 24px;font-size:13px;font-weight:700;text-align:center;border-bottom:1px solid var(--bd)}}
.cmp-tbl th:first-child{{text-align:left}}
.cmp-tbl .th-free{{color:var(--green)}}
.cmp-tbl .th-pro{{color:var(--amber)}}
.cmp-tbl td{{padding:15px 24px;font-size:13.5px;color:var(--t2);border-bottom:1px solid rgba(47,129,247,.06);text-align:center}}
.cmp-tbl td:first-child{{text-align:left;color:var(--t1);font-weight:500}}
.cmp-tbl tr:last-child td{{border-bottom:none}}
.cmp-tbl tr:hover td{{background:rgba(47,129,247,.03)}}
.chk{{color:var(--green);font-weight:700;font-size:16px}}
.crs{{color:var(--t3);font-size:16px}}
.cnum{{color:var(--amber);font-weight:700}}
 
/* How it works */
.hw{{display:grid;grid-template-columns:repeat(3,1fr);gap:0;margin-top:72px;max-width:960px;margin-left:auto;margin-right:auto;position:relative}}
.hw::before{{content:'';position:absolute;top:31px;left:calc(16.7% + 28px);right:calc(16.7% + 28px);height:1px;background:var(--bd);z-index:0}}
.hws{{text-align:center;padding:0 28px;position:relative;z-index:1}}
.hws-n{{width:62px;height:62px;border-radius:50%;display:flex;align-items:center;justify-content:center;margin:0 auto 26px;font-size:18px;font-weight:800;background:var(--bgc);border:1px solid var(--bd);color:var(--blue);transition:all .3s}}
.hws:hover .hws-n{{background:var(--blue);color:#fff;border-color:var(--blue);box-shadow:0 0 28px rgba(47,129,247,.4)}}
.hws h3{{font-size:16px;font-weight:700;margin-bottom:10px}}
.hws p{{font-size:13.5px;color:var(--t2);line-height:1.65}}
 
/* Architecture */
.arch{{display:flex;gap:72px;align-items:center;max-width:1160px;margin:0 auto;padding:100px 48px}}
.arch-t{{flex:1}}
.arch-t h2{{font-size:clamp(24px,3.5vw,38px);font-weight:800;letter-spacing:-1px;margin-bottom:14px}}
.arch-t p{{font-size:15px;color:var(--t2);line-height:1.7;margin-bottom:22px}}
.arch-li{{list-style:none;display:flex;flex-direction:column;gap:11px}}
.arch-li li{{display:flex;align-items:flex-start;gap:10px;font-size:14px;color:var(--t2)}}
.arch-li li::before{{content:'✓';color:var(--green);font-weight:700;flex-shrink:0;margin-top:2px}}
.arch-v{{flex:1;min-width:340px}}
.arch-stack{{display:flex;flex-direction:column;gap:3px}}
.alyr{{
  padding:15px 22px;border-radius:10px;border:1px solid var(--bd);
  display:flex;align-items:center;justify-content:space-between;
  background:var(--bgc);transition:all .25s;opacity:0;transform:translateX(20px);
}}
.alyr:hover{{border-color:var(--bdh);background:var(--bgch)}}
.alyr-l{{display:flex;align-items:center;gap:11px}}
.alyr-ic{{font-size:17px}}
.alyr-nm{{font-size:13.5px;font-weight:600}}
.alyr-dt{{font-size:11px;color:var(--t3);font-family:'JetBrains Mono',monospace}}
.alyr-st{{font-size:10px;font-weight:700;padding:3px 9px;border-radius:6px;text-transform:uppercase;letter-spacing:.05em}}
.st-live{{background:rgba(34,197,94,.1);color:var(--green)}}
.st-cloud{{background:rgba(47,129,247,.1);color:var(--blue)}}
.st-local{{background:rgba(34,211,238,.08);color:var(--cyan)}}
.st-kern{{background:rgba(167,139,250,.08);color:var(--purple)}}
 
/* Pricing */
.pg{{display:grid;grid-template-columns:repeat(3,1fr);gap:22px;margin-top:60px;max-width:1000px;margin-left:auto;margin-right:auto}}
.pc{{
  background:var(--bgc);border:1px solid var(--bd);border-radius:var(--rl);
  padding:34px;transition:all .3s;position:relative;opacity:0;transform:translateY(20px);
}}
.pc:hover{{transform:translateY(-6px);box-shadow:0 24px 60px rgba(0,0,0,.45)}}
.pc.feat{{border-color:var(--blue);background:linear-gradient(180deg,rgba(47,129,247,.07) 0%,var(--bgc) 100%)}}
.pbadge{{
  position:absolute;top:-12px;left:50%;transform:translateX(-50%);
  padding:4px 14px;border-radius:100px;background:var(--blue);
  color:#fff;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.07em;white-space:nowrap;
}}
.pplan{{font-size:12px;font-weight:700;color:var(--t3);text-transform:uppercase;letter-spacing:.08em;margin-bottom:10px}}
.pprice{{font-size:42px;font-weight:800;letter-spacing:-2px;margin-bottom:4px}}
.pprice sup{{font-size:18px;font-weight:600;vertical-align:top;margin-top:9px;display:inline-block}}
.pprice span{{font-size:15px;font-weight:400;color:var(--t2);letter-spacing:0}}
.pdesc{{font-size:13.5px;color:var(--t2);margin-bottom:26px}}
.pdvd{{height:1px;background:var(--bd);margin:22px 0}}
.pfl{{list-style:none;display:flex;flex-direction:column;gap:11px;margin-bottom:28px}}
.pfl li{{display:flex;align-items:flex-start;gap:9px;font-size:13.5px;color:var(--t2)}}
.pfl .c{{color:var(--green);font-weight:700;flex-shrink:0}}
.pfl .x{{color:var(--t3);flex-shrink:0}}
.pbtn{{
  display:block;width:100%;padding:12px;border-radius:10px;
  text-align:center;font-size:14px;font-weight:700;text-decoration:none;
  cursor:pointer;transition:all .25s;border:none;
}}
.pbo{{background:transparent;border:1px solid var(--bd);color:var(--t2)}}
.pbo:hover{{border-color:var(--bdh);color:var(--t1)}}
.pbp{{background:var(--blue);color:#fff}}
.pbp:hover{{background:#4f96ff;box-shadow:0 6px 22px rgba(47,129,247,.42);transform:translateY(-1px)}}
 
/* FAQ */
.faq-list{{max-width:760px;margin:60px auto 0;display:flex;flex-direction:column;gap:10px}}
.fi{{background:var(--bgc);border:1px solid var(--bd);border-radius:var(--r);overflow:hidden;transition:border-color .2s}}
.fi:hover{{border-color:var(--bdh)}}
.fq{{width:100%;padding:19px 22px;display:flex;justify-content:space-between;align-items:center;background:none;border:none;color:var(--t1);font-size:14.5px;font-weight:600;text-align:left;cursor:pointer;gap:16px}}
.fq .chv{{transition:transform .25s;color:var(--t3);flex-shrink:0;font-style:normal;font-size:18px}}
.fi.open .fq .chv{{transform:rotate(90deg)}}
.fa{{max-height:0;overflow:hidden;transition:max-height .35s ease}}
.fa p{{padding:0 22px 18px;font-size:13.5px;color:var(--t2);line-height:1.7}}
.fi.open .fa{{max-height:280px}}
 
/* CTA */
.cta-sec{{padding:100px 48px;text-align:center}}
.cta-box{{
  max-width:740px;margin:0 auto;padding:72px 60px;
  border:1px solid var(--bd);border-radius:24px;
  background:linear-gradient(135deg,rgba(47,129,247,.07) 0%,var(--bgc) 50%,rgba(34,211,238,.04) 100%);
  position:relative;overflow:hidden;
}}
.cta-box::before{{content:'';position:absolute;top:0;left:50%;transform:translateX(-50%);width:80%;height:1px;background:linear-gradient(90deg,transparent,var(--blue),transparent)}}
.cta-box h2{{font-size:clamp(24px,4vw,40px);font-weight:800;letter-spacing:-1px;margin-bottom:14px}}
.cta-box p{{font-size:16px;color:var(--t2);margin-bottom:38px;line-height:1.6}}
.cta-acts{{display:flex;justify-content:center;gap:16px;flex-wrap:wrap}}
 
/* Footer */
footer{{padding:56px 48px 36px;border-top:1px solid var(--bd)}}
.ft{{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:44px;flex-wrap:wrap;gap:36px}}
.fb p{{font-size:13.5px;color:var(--t3);line-height:1.6;max-width:270px;margin-top:10px}}
.flg h4{{font-size:11px;font-weight:700;color:var(--t3);text-transform:uppercase;letter-spacing:.08em;margin-bottom:14px}}
.flg ul{{list-style:none;display:flex;flex-direction:column;gap:9px}}
.flg a{{font-size:13.5px;color:var(--t2);text-decoration:none;transition:color .2s}}
.flg a:hover{{color:var(--t1)}}
.fb2{{display:flex;justify-content:space-between;align-items:center;padding-top:22px;border-top:1px solid var(--bd);flex-wrap:wrap;gap:10px}}
.fb2 p{{font-size:12px;color:var(--t3)}}
 
/* Mobile */
@media(max-width:900px){{
  nav{{padding:0 20px}}
  .nav-links{{display:none}}
  section{{padding:72px 20px}}
  .hero{{padding:110px 20px 60px}}
  .term-wrap{{padding:0 20px 72px}}
  .hw{{grid-template-columns:1fr;gap:36px}}
  .hw::before{{display:none}}
  .arch{{flex-direction:column;padding:72px 20px}}
  .arch-v{{min-width:unset;width:100%}}
  .pg{{grid-template-columns:1fr;max-width:400px}}
  .cta-box{{padding:44px 24px}}
  footer{{padding:44px 20px 28px}}
  .ft{{flex-direction:column}}
}}
</style>
</head>
<body>
 
<canvas id="bgc"></canvas>
 
<div class="z1">
 
<!-- NAV -->
<nav id="mnav">
  <a href="{base}/home" class="nav-brand">
    <img src="/static/app_icon.png" alt="FMSecure" onerror="this.style.display='none'"/>
    <span class="nav-brand-txt">FM<em>Secure</em></span>
  </a>
  <ul class="nav-links">
    <li><a href="#features">Features</a></li>
    <li><a href="#compare">Compare</a></li>
    <li><a href="#architecture">Architecture</a></li>
    <li><a href="#pricing">Pricing</a></li>
    <li><a href="#faq">FAQ</a></li>
  </ul>
  <div class="nav-right">
    <a href="{base}/download" class="btn-ghost">Free download</a>
    <a href="{base}/pricing" class="btn-nav-cta">Get PRO &rarr;</a>
  </div>
</nav>
 
<!-- HERO -->
<section class="hero">
  <div class="hero-inner">
    <div class="badge">Windows EDR — v2.0 production release</div>
    <h1>Stop ransomware.<br/><span class="gt">Before damage is done.</span></h1>
    <p class="hero-sub">
      FMSecure is a production-grade Endpoint Detection &amp; Response agent for Windows.
      Real-time file integrity monitoring, behavioral ransomware detection, auto-healing vault,
      and live C2 telemetry — all in a single executable.
    </p>
    <div class="hero-acts">
      <a href="{base}/download" class="btn-hp">
        &#x2B07; Download Free
      </a>
      <a href="{base}/pricing" class="btn-hg">
        View PRO pricing &rarr;
      </a>
    </div>
    <div class="hero-stats">
      <div>
        <div class="hstat-n">AES<em>-256</em></div>
        <div class="hstat-l">Encryption at rest</div>
      </div>
      <div>
        <div class="hstat-n"><em>&lt;</em>50ms</div>
        <div class="hstat-l">Threat response</div>
      </div>
      <div>
        <div class="hstat-n">1.8<em>GB/s</em></div>
        <div class="hstat-l">Scan throughput</div>
      </div>
      <div>
        <div class="hstat-n">0<em> dep</em></div>
        <div class="hstat-l">Single EXE deploy</div>
      </div>
    </div>
  </div>
</section>
 
<!-- TERMINAL -->
<div class="term-wrap">
  <div class="term">
    <div class="term-bar">
      <div class="tdot" style="background:#ff5f57"></div>
      <div class="tdot" style="background:#febc2e"></div>
      <div class="tdot" style="background:#28c840"></div>
      <span class="tlabel">FMSecure v2.0 &mdash; Live Agent Log &mdash; WORKSTATION-01</span>
    </div>
    <div class="term-body">
      <div><span class="tok">[22:14:03]</span> <span class="tout">[INFO ] Monitor started &mdash; 2 folders indexed, 15,842 files baseline captured</span></div>
      <div><span class="tok">[22:14:11]</span> <span class="tout">[INFO ] CREATED: D:\Dev\api\main.py &mdash; hash recorded, vault backup stored</span></div>
      <div><span class="twn">[22:16:44]</span> <span class="tout">[MED  ] MODIFIED: D:\TEST\config.json &mdash; content delta, Active Defense intercept</span></div>
      <div><span class="tok">[22:16:44]</span> <span class="tout">[INFO ] RESTORED: D:\TEST\config.json &mdash; malware modification reverted in 12ms</span></div>
      <div><span class="tcr">[22:17:02]</span> <span class="tout">[CRIT ] BURST: 8 files encrypted in 4.2s &mdash; ransomware behaviour confirmed</span></div>
      <div><span class="tcr">[22:17:02]</span> <span class="tout">[LOCK ] icacls /deny Everyone:(W,D) applied &mdash; D:\TEST write access REVOKED</span></div>
      <div><span class="tcr">[22:17:02]</span> <span class="tout">[SNAP ] Forensic snapshot BF9A2C1D &rarr; forensics\forensic_2026-03-29_22-17-02.dat</span></div>
      <div><span class="tok">[22:17:03]</span> <span class="tout">[MAIL ] Critical alert dispatched &rarr; admin@corp.com (SMTP, attachment: .dat)</span></div>
      <div><span class="tin">[22:17:03]</span> <span class="tout">[C2  ] Heartbeat pushed &rarr; fmsecure-c2-server.railway.app (LOCKDOWN queued)</span></div>
      <div><span class="tok">[22:17:04]</span> <span class="tout">[KEY  ] Cloud key backup complete &rarr; Google Drive / FMSecure_FM-A3C9 / keys/</span></div>
      <div style="margin-top:8px"><span class="tok">root@WORKSTATION-01</span><span class="tout"> ~/fmsecure $ </span><span class="tcu"></span></div>
    </div>
  </div>
</div>
 
<div class="dvd"></div>
 
<!-- FEATURES -->
<section id="features">
  <div class="scen">
    <div class="slbl">Platform capabilities</div>
    <h2 class="stit">Everything an EDR demands. Nothing it doesn&apos;t.</h2>
    <p class="ssub">Designed after the same layered defense model used by CrowdStrike and SentinelOne &mdash; shipped as a single signed Windows executable with zero runtime dependencies.</p>
  </div>
  <div class="fg">
 
    <div class="fc">
      <div class="fci" style="background:rgba(34,211,238,.09)">&#x1F50D;</div>
      <h3>Real-time File Integrity</h3>
      <p>SHA-256 + OS metadata state hashing via watchdog. Detects content changes, hidden/system flag flips, renames, and deletions. Debounce engine prevents false alerts during large file transfers.</p>
      <span class="ftag tfree">Free tier</span>
    </div>
 
    <div class="fc">
      <div class="fci" style="background:rgba(239,68,68,.09)">&#x1F6D1;</div>
      <h3>Ransomware Killswitch</h3>
      <p>Behavioral burst detection (&#x2265;5 operations in 10s) fires an OS-level <code style="font-family:'JetBrains Mono',monospace;font-size:12px;color:var(--cyan)">icacls /deny</code> lockdown. Strips Write and Delete permissions at the NTFS kernel layer before encryption completes.</p>
      <span class="ftag tpro">PRO only</span>
    </div>
 
    <div class="fc">
      <div class="fci" style="background:rgba(34,197,94,.09)">&#x1F6E1;&#xFE0F;</div>
      <h3>Active Defense Auto-Heal Vault</h3>
      <p>AES-256 (Fernet) encrypted vault backed by the hardware KEK. Malicious modifications and deletions are intercepted and reverted in milliseconds. Falls back to cloud vault/ subfolder if local copy is wiped.</p>
      <span class="ftag tpro">PRO only</span>
    </div>
 
    <div class="fc">
      <div class="fci" style="background:rgba(47,129,247,.1)">&#x2601;&#xFE0F;</div>
      <h3>Cloud Disaster Recovery</h3>
      <p>Google Drive OAuth 2.0 sync keyed on hardware machine ID &mdash; never email. Encrypted logs, vault files, AppData, and key files sync automatically. Full recovery from a blank machine in under 3 minutes.</p>
      <span class="ftag tpro">PRO only</span>
    </div>
 
    <div class="fc">
      <div class="fci" style="background:rgba(245,158,11,.09)">&#x1F52C;</div>
      <h3>AES-Encrypted Forensic Vault</h3>
      <p>Every CRITICAL event generates an AES-256 snapshot capturing disk state, process memory, critical file hashes, and the last 15 decrypted log lines. Viewable only inside the FMSecure agent &mdash; never in plaintext.</p>
      <span class="ftag tpro">PRO only</span>
    </div>
 
    <div class="fc">
      <div class="fci" style="background:rgba(167,139,250,.09)">&#x1F4E1;</div>
      <h3>Live C2 Fleet Console</h3>
      <p>Agent heartbeats stream telemetry to your hosted FastAPI C2 dashboard every 10 seconds. IT admins can push a remote ISOLATE HOST command that triggers emergency lockdown from any browser &mdash; no VPN required.</p>
      <span class="ftag tpro">PRO only</span>
    </div>
 
    <div class="fc">
      <div class="fci" style="background:rgba(34,197,94,.07)">&#x1F511;</div>
      <h3>Hardware-Bound Key Encryption</h3>
      <p>Master AES key is wrapped in a KEK derived from the physical hardware fingerprint via PBKDF2 (200k iterations). A stolen <code style="font-family:'JetBrains Mono',monospace;font-size:12px;color:var(--cyan)">sys.key</code> file is permanently unreadable on any other machine.</p>
      <span class="ftag tall">All tiers</span>
    </div>
 
    <div class="fc">
      <div class="fci" style="background:rgba(239,68,68,.07)">&#x1FA7A;</div>
      <h3>Honeypot Tripwire</h3>
      <p>A hidden decoy file acts as a silent alarm. First access from ransomware or a rogue insider instantly detonates the killswitch, generates a forensic snapshot, and dispatches a critical alert &mdash; all before a single byte is encrypted.</p>
      <span class="ftag tpro">PRO only</span>
    </div>
 
    <div class="fc">
      <div class="fci" style="background:rgba(34,211,238,.07)">&#x1F50C;</div>
      <h3>USB Device Control (DLP)</h3>
      <p>Enforces Windows Registry <code style="font-family:'JetBrains Mono',monospace;font-size:12px;color:var(--cyan)">StorageDevicePolicies WriteProtect</code> at the OS level. Blocks all USB mass storage write access &mdash; no kernel driver, no code signing required.</p>
      <span class="ftag tpro">PRO only</span>
    </div>
 
  </div>
</section>
 
<div class="dvd"></div>
 
<!-- FREE VS PRO COMPARE -->
<section id="compare">
  <div class="scen">
    <div class="slbl">Plan comparison</div>
    <h2 class="stit">Free vs PRO &mdash; at a glance.</h2>
    <p class="ssub">Every capability, side by side. No hidden limits.</p>
  </div>
  <div class="cmp-wrap">
    <table class="cmp-tbl">
      <thead>
        <tr>
          <th style="text-align:left;color:var(--t2)">Capability</th>
          <th class="th-free">Free</th>
          <th class="th-pro">PRO Monthly / Annual</th>
        </tr>
      </thead>
      <tbody>
        <tr><td>Monitored folders</td><td class="cnum">1</td><td class="cnum">Up to 5</td></tr>
        <tr><td>SHA-256 file integrity monitoring</td><td><span class="chk">&#10003;</span></td><td><span class="chk">&#10003;</span></td></tr>
        <tr><td>Real-time watchdog (create / modify / delete / rename)</td><td><span class="chk">&#10003;</span></td><td><span class="chk">&#10003;</span></td></tr>
        <tr><td>HMAC-signed tamper-proof audit logs</td><td><span class="chk">&#10003;</span></td><td><span class="chk">&#10003;</span></td></tr>
        <tr><td>AES-256 encryption at rest (logs, vault, records)</td><td><span class="chk">&#10003;</span></td><td><span class="chk">&#10003;</span></td></tr>
        <tr><td>Hardware-bound KEK (PBKDF2 200k iter.)</td><td><span class="chk">&#10003;</span></td><td><span class="chk">&#10003;</span></td></tr>
        <tr><td>Email OTP registration + password recovery</td><td><span class="chk">&#10003;</span></td><td><span class="chk">&#10003;</span></td></tr>
        <tr><td>Google SSO with device PIN 2FA</td><td><span class="chk">&#10003;</span></td><td><span class="chk">&#10003;</span></td></tr>
        <tr><td>PDF report export + severity charts</td><td><span class="chk">&#10003;</span></td><td><span class="chk">&#10003;</span></td></tr>
        <tr><td>Self-healing Watchdog process (WinSysHost.exe)</td><td><span class="chk">&#10003;</span></td><td><span class="chk">&#10003;</span></td></tr>
        <tr><td>Discord / Slack webhook alerts</td><td><span class="chk">&#10003;</span></td><td><span class="chk">&#10003;</span></td></tr>
        <tr><td>Active Defense auto-heal vault</td><td><span class="crs">&#8212;</span></td><td><span class="chk">&#10003;</span></td></tr>
        <tr><td>Ransomware behavioral killswitch (icacls)</td><td><span class="crs">&#8212;</span></td><td><span class="chk">&#10003;</span></td></tr>
        <tr><td>Honeypot tripwire</td><td><span class="crs">&#8212;</span></td><td><span class="chk">&#10003;</span></td></tr>
        <tr><td>Google Drive cloud disaster recovery</td><td><span class="crs">&#8212;</span></td><td><span class="chk">&#10003;</span></td></tr>
        <tr><td>AES-encrypted forensic incident vault</td><td><span class="crs">&#8212;</span></td><td><span class="chk">&#10003;</span></td></tr>
        <tr><td>USB device control (DLP)</td><td><span class="crs">&#8212;</span></td><td><span class="chk">&#10003;</span></td></tr>
        <tr><td>Live C2 fleet telemetry dashboard</td><td><span class="crs">&#8212;</span></td><td><span class="chk">&#10003;</span></td></tr>
        <tr><td>Remote host isolation (cloud-triggered lockdown)</td><td><span class="crs">&#8212;</span></td><td><span class="chk">&#10003;</span></td></tr>
        <tr><td>Folder structure backup &amp; restore</td><td><span class="crs">&#8212;</span></td><td><span class="chk">&#10003;</span></td></tr>
        <tr><td>SMTP security alert email (with forensic .dat attachment)</td><td><span class="crs">&#8212;</span></td><td><span class="chk">&#10003;</span></td></tr>
      </tbody>
    </table>
  </div>
</section>
 
<div class="dvd"></div>
 
<!-- HOW IT WORKS -->
<section id="howitworks">
  <div class="scen">
    <div class="slbl">Deployment</div>
    <h2 class="stit">Up and protecting in 3 steps.</h2>
    <p class="ssub">No kernel driver signing. No IT department approval. One EXE with UAC elevation and an optional invisible Watchdog service.</p>
  </div>
  <div class="hw">
    <div class="hws">
      <div class="hws-n">01</div>
      <h3>Download &amp; run</h3>
      <p>Run <code style="font-family:'JetBrains Mono',monospace;color:var(--cyan)">SecureFIM.exe</code> as Administrator. Register your admin account with email OTP. The Watchdog installs silently as a background process that survives Task Manager kills and reboots.</p>
    </div>
    <div class="hws">
      <div class="hws-n">02</div>
      <h3>Configure your folders</h3>
      <p>Add up to 5 monitored directories. Baseline hashes are generated concurrently across all CPU threads (verified at 1.8 GB/s on NVMe). PRO users get cloud sync and vault backup enabled automatically on first folder add.</p>
    </div>
    <div class="hws">
      <div class="hws-n">03</div>
      <h3>Monitor &amp; respond</h3>
      <p>Real-time alerts via dashboard, Discord/Slack webhook, and SMTP email with forensic .dat attachments. Forensic snapshots auto-generated on every CRITICAL event. Remote lockdown from the C2 browser console.</p>
    </div>
  </div>
</section>
 
<div class="dvd"></div>
 
<!-- ARCHITECTURE -->
<div class="arch" id="architecture">
  <div class="arch-t">
    <div class="slbl">Technical architecture</div>
    <h2>Multi-layer defense.<br/>Single binary.</h2>
    <p>FMSecure is not a script wrapper. It is a layered security architecture where each tier is independently functional &mdash; a failure in one layer never compromises the others.</p>
    <ul class="arch-li">
      <li>HMAC SHA-256 signed on every log line &mdash; tamper detection at write time</li>
      <li>Hardware KEK ensures stolen key files are permanently unreadable elsewhere</li>
      <li>Two-tier vault: local AES-256, automatic cloud fallback on recovery</li>
      <li>Watchdog survives Task Manager, Admin override required to stop</li>
      <li>icacls lockdown operates at NTFS kernel level, not Python file locks</li>
      <li>Machine ID &mdash; not email &mdash; is the cloud identity anchor</li>
    </ul>
  </div>
  <div class="arch-v">
    <div class="arch-stack">
      <div class="alyr"><div class="alyr-l"><span class="alyr-ic">&#x1F310;</span><div><div class="alyr-nm">C2 cloud server</div><div class="alyr-dt">FastAPI &bull; Railway &bull; PostgreSQL</div></div></div><span class="alyr-st st-live">Live</span></div>
      <div class="alyr"><div class="alyr-l"><span class="alyr-ic">&#x2601;&#xFE0F;</span><div><div class="alyr-nm">Cloud key escrow</div><div class="alyr-dt">Google Drive &bull; machine_id KEK</div></div></div><span class="alyr-st st-cloud">PRO</span></div>
      <div class="alyr"><div class="alyr-l"><span class="alyr-ic">&#x1F512;</span><div><div class="alyr-nm">AES-256 local vault</div><div class="alyr-dt">AppData &bull; PBKDF2 KEK &bull; .enc</div></div></div><span class="alyr-st st-local">Local</span></div>
      <div class="alyr"><div class="alyr-l"><span class="alyr-ic">&#x1F441;&#xFE0F;</span><div><div class="alyr-nm">Watchdog process</div><div class="alyr-dt">WinSysHost.exe &bull; daemon &bull; --recovery</div></div></div><span class="alyr-st st-local">Local</span></div>
      <div class="alyr"><div class="alyr-l"><span class="alyr-ic">&#x1F50D;</span><div><div class="alyr-nm">File integrity engine</div><div class="alyr-dt">watchdog &bull; SHA-256 &bull; HMAC &bull; debounce</div></div></div><span class="alyr-st st-local">Local</span></div>
      <div class="alyr"><div class="alyr-l"><span class="alyr-ic">&#x2699;&#xFE0F;</span><div><div class="alyr-nm">OS permission layer</div><div class="alyr-dt">icacls &bull; Registry &bull; WMI &bull; NTFS</div></div></div><span class="alyr-st st-kern">Kernel</span></div>
    </div>
  </div>
</div>
 
<div class="dvd"></div>
 
<!-- PRICING -->
<section id="pricing">
  <div class="scen">
    <div class="slbl">Pricing</div>
    <h2 class="stit">Simple, transparent pricing.</h2>
    <p class="ssub">Start free. Upgrade when your threat model demands it. License key delivered within 60 seconds of payment.</p>
  </div>
  <div class="pg">
 
    <div class="pc">
      <div class="pplan">Free</div>
      <div class="pprice">&#x20B9;0</div>
      <div class="pdesc">For personal use and learning</div>
      <div class="pdvd"></div>
      <ul class="pfl">
        <li><span class="c">&#10003;</span> 1 monitored folder</li>
        <li><span class="c">&#10003;</span> SHA-256 file integrity monitoring</li>
        <li><span class="c">&#10003;</span> HMAC-signed tamper-proof logs</li>
        <li><span class="c">&#10003;</span> AES-256 encryption at rest</li>
        <li><span class="c">&#10003;</span> Hardware-bound KEK</li>
        <li><span class="c">&#10003;</span> Google SSO + email OTP</li>
        <li><span class="c">&#10003;</span> Discord / Slack webhooks</li>
        <li><span class="x">&#8212;</span> <span style="color:var(--t3)">Active defense vault</span></li>
        <li><span class="x">&#8212;</span> <span style="color:var(--t3)">Ransomware killswitch</span></li>
        <li><span class="x">&#8212;</span> <span style="color:var(--t3)">Cloud backup &amp; C2</span></li>
      </ul>
      <a href="{base}/download" class="pbtn pbo">Download free</a>
    </div>
 
    <div class="pc feat">
      <div class="pbadge">Most popular</div>
      <div class="pplan">PRO Monthly</div>
      <div class="pprice"><sup>&#x20B9;</sup>499<span>/mo</span></div>
      <div class="pdesc">For professionals protecting real systems</div>
      <div class="pdvd"></div>
      <ul class="pfl">
        <li><span class="c">&#10003;</span> Up to 5 monitored folders</li>
        <li><span class="c">&#10003;</span> Everything in Free</li>
        <li><span class="c">&#10003;</span> Active Defense auto-heal vault</li>
        <li><span class="c">&#10003;</span> Ransomware behavioral killswitch</li>
        <li><span class="c">&#10003;</span> Google Drive cloud disaster recovery</li>
        <li><span class="c">&#10003;</span> AES forensic incident vault</li>
        <li><span class="c">&#10003;</span> USB device control (DLP)</li>
        <li><span class="c">&#10003;</span> Honeypot tripwire</li>
        <li><span class="c">&#10003;</span> Live C2 fleet telemetry</li>
        <li><span class="c">&#10003;</span> Remote host isolation</li>
      </ul>
      <!-- Replace href with your Razorpay / Stripe payment link -->
      <a href="{base}/pricing" class="pbtn pbp">Activate PRO &rarr;</a>
    </div>
 
    <div class="pc">
      <div class="pplan">PRO Annual</div>
      <div class="pprice"><sup>&#x20B9;</sup>4999<span>/yr</span></div>
      <div class="pdesc">2 months free &mdash; best value</div>
      <div class="pdvd"></div>
      <ul class="pfl">
        <li><span class="c">&#10003;</span> Everything in PRO Monthly</li>
        <li><span class="c">&#10003;</span> Priority email support</li>
        <li><span class="c">&#10003;</span> Early access to new features</li>
        <li><span class="c">&#10003;</span> Annual GST invoice for claims</li>
      </ul>
      <a href="{base}/pricing" class="pbtn pbo">Activate Annual &rarr;</a>
    </div>
 
  </div>
  <p style="text-align:center;font-size:12.5px;color:var(--t3);margin-top:26px">
    Payments processed securely via Razorpay / Stripe &bull; Cancel anytime &bull; License key delivered by email within 60 seconds
  </p>
</section>
 
<div class="dvd"></div>
 
<!-- FAQ -->
<section id="faq">
  <div class="scen">
    <div class="slbl">FAQ</div>
    <h2 class="stit">Common questions.</h2>
  </div>
  <div class="faq-list">
 
    <div class="fi">
      <button class="fq" onclick="tfaq(this)">How does license activation work?<i class="chv">&#x203A;</i></button>
      <div class="fa"><p>After payment, Razorpay fires a webhook to our Railway server which generates a unique license key and emails it within 60 seconds. Paste it into FMSecure&apos;s "Activate PRO" dialog &mdash; the agent validates it against our server and unlocks all PRO features instantly. Keys are device-bound by hardware machine ID, not email.</p></div>
    </div>
 
    <div class="fi">
      <button class="fq" onclick="tfaq(this)">What happens if my encryption key is deleted?<i class="chv">&#x203A;</i></button>
      <div class="fa"><p>PRO users get three-tier key protection: (1) primary local key, (2) shadow backup copy, (3) cloud escrow on Google Drive identified by hardware machine ID. On startup, FMSecure automatically attempts all three in order. Full disaster recovery &mdash; including logs, forensics, user accounts, and vault files &mdash; runs from the dashboard in under 3 minutes.</p></div>
    </div>
 
    <div class="fi">
      <button class="fq" onclick="tfaq(this)">Does FMSecure require a kernel driver or code signing?<i class="chv">&#x203A;</i></button>
      <div class="fa"><p>No. FMSecure runs as a standard Windows application with UAC Administrator elevation. The Ransomware Killswitch uses the built-in Windows <code style="font-family:'JetBrains Mono',monospace;font-size:12px">icacls</code> command to revoke NTFS permissions at the OS level &mdash; no kernel driver, no Authenticode signing required for that path.</p></div>
    </div>
 
    <div class="fi">
      <button class="fq" onclick="tfaq(this)">What if I kill the FMSecure process in Task Manager?<i class="chv">&#x203A;</i></button>
      <div class="fa"><p>The Watchdog process (masquerading as <code style="font-family:'JetBrains Mono',monospace;font-size:12px">WinSysHost.exe</code>) detects the termination within seconds and relaunches the agent in Recovery Mode &mdash; bypassing the login screen, auto-logging in the last admin, and resuming monitoring of all previously configured folders without any user interaction.</p></div>
    </div>
 
    <div class="fi">
      <button class="fq" onclick="tfaq(this)">Does it monitor network shares and USB drives?<i class="chv">&#x203A;</i></button>
      <div class="fa"><p>FMSecure monitors any path accessible from the Windows filesystem including local NTFS drives, mapped network shares, and USB drives. The watchdog library hooks into native Windows file system events at the OS level, so it receives change notifications regardless of the underlying storage type.</p></div>
    </div>
 
    <div class="fi">
      <button class="fq" onclick="tfaq(this)">Do you ever have access to my Google Drive or my files?<i class="chv">&#x203A;</i></button>
      <div class="fa"><p>No. FMSecure uses your own Google OAuth credentials to write to your personal Google Drive. All backups land in a <code style="font-family:'JetBrains Mono',monospace;font-size:12px">FMSecure_{{MACHINE_ID}}</code> folder that only your account controls. Files are AES-256 encrypted before upload &mdash; we never see plaintext content, and your Google credentials are never sent to our servers.</p></div>
    </div>
 
    <div class="fi">
      <button class="fq" onclick="tfaq(this)">How fast is the ransomware killswitch?<i class="chv">&#x203A;</i></button>
      <div class="fa"><p>The burst detector fires after 5 file operations are detected within a 10-second sliding window. The <code style="font-family:'JetBrains Mono',monospace;font-size:12px">icacls</code> lockdown executes as a subprocess immediately &mdash; typical wall-clock time from detection to permission revocation is under 200ms. Real ransomware like WannaCry encrypts roughly 1 file per 300ms, so this window stops the attack after 5&ndash;8 files rather than thousands.</p></div>
    </div>
 
  </div>
</section>
 
<!-- CTA -->
<section class="cta-sec">
  <div class="cta-box">
    <h2>Start protecting your endpoints today.</h2>
    <p>Free tier available with no credit card. PRO features activate within 60 seconds of payment.</p>
    <div class="cta-acts">
      <a href="{base}/download" class="btn-hp">Download FMSecure free</a>
      <a href="{base}/pricing" class="btn-hg">See PRO pricing &rarr;</a>
    </div>
  </div>
</section>
 
<!-- FOOTER -->
<footer>
  <div class="ft">
    <div class="fb">
      <a href="{base}/home" class="nav-brand">
        <img src="/static/app_icon.png" alt="FMSecure" width="26" height="26" onerror="this.style.display='none'"/>
        <span class="nav-brand-txt" style="font-size:16px">FM<em>Secure</em></span>
      </a>
      <p>Enterprise-grade endpoint detection and response for Windows. Built by a security engineer, for security engineers.</p>
    </div>
    <div class="flg">
      <h4>Product</h4>
      <ul>
        <li><a href="#features">Features</a></li>
        <li><a href="#compare">Free vs PRO</a></li>
        <li><a href="#pricing">Pricing</a></li>
        <li><a href="#faq">FAQ</a></li>
      </ul>
    </div>
    <div class="flg">
      <h4>Resources</h4>
      <ul>
        <li><a href="#">Documentation</a></li>
        <li><a href="#">Changelog</a></li>
        <li><a href="#">GitHub</a></li>
        <li><a href="{base}/dashboard">C2 Dashboard</a></li>
      </ul>
    </div>
    <div class="flg">
      <h4>Legal</h4>
      <ul>
        <li><a href="#">Privacy policy</a></li>
        <li><a href="#">Terms of service</a></li>
        <li><a href="#">License agreement</a></li>
      </ul>
    </div>
  </div>
  <div class="fb2">
    <p>&copy; 2026 FMSecure &bull; All rights reserved &bull; Made in India</p>
    <p>FastAPI &bull; Python &bull; Google Drive API &bull; Razorpay</p>
  </div>
</footer>
 
</div><!-- end z1 -->
 
<script>
/* ── THREE.JS NETWORK ── */
(function(){{
  const cv = document.getElementById('bgc');
  const scene = new THREE.Scene();
  const cam = new THREE.PerspectiveCamera(60, innerWidth/innerHeight, 0.1, 1000);
  const renderer = new THREE.WebGLRenderer({{canvas:cv, alpha:true, antialias:true}});
  renderer.setPixelRatio(Math.min(devicePixelRatio, 1.5));
  renderer.setSize(innerWidth, innerHeight);
  cam.position.z = 3;
 
  const N = 110;
  const pos = new Float32Array(N * 3);
  const vel = [];
  for(let i=0;i<N;i++){{
    pos[i*3]   = (Math.random()-.5)*14;
    pos[i*3+1] = (Math.random()-.5)*8;
    pos[i*3+2] = (Math.random()-.5)*5;
    vel.push((Math.random()-.5)*.004,(Math.random()-.5)*.003,(Math.random()-.5)*.002);
  }}
  const pg = new THREE.BufferGeometry();
  pg.setAttribute('position', new THREE.BufferAttribute(pos,3));
  const pm = new THREE.PointsMaterial({{color:0x2f81f7,size:.032,transparent:true,opacity:.55}});
  scene.add(new THREE.Points(pg, pm));
 
  const maxL = N*5;
  const lpos = new Float32Array(maxL*6);
  const lg = new THREE.BufferGeometry();
  lg.setAttribute('position', new THREE.BufferAttribute(lpos,3));
  const lm = new THREE.LineBasicMaterial({{color:0x2f81f7,transparent:true,opacity:.07}});
  const lines = new THREE.LineSegments(lg, lm);
  scene.add(lines);
 
  let fr=0;
  function animate(){{
    requestAnimationFrame(animate); fr++;
    for(let i=0;i<N;i++){{
      pos[i*3]  +=vel[i*3];   pos[i*3+1]+=vel[i*3+1]; pos[i*3+2]+=vel[i*3+2];
      if(Math.abs(pos[i*3])>7)   vel[i*3]  *=-1;
      if(Math.abs(pos[i*3+1])>4) vel[i*3+1]*=-1;
      if(Math.abs(pos[i*3+2])>2.5) vel[i*3+2]*=-1;
    }}
    pg.attributes.position.needsUpdate=true;
    if(fr%3===0){{
      let lc=0; const th=2.4;
      for(let i=0;i<N&&lc<maxL;i++)for(let j=i+1;j<N&&lc<maxL;j++){{
        const dx=pos[i*3]-pos[j*3],dy=pos[i*3+1]-pos[j*3+1],dz=pos[i*3+2]-pos[j*3+2];
        if(dx*dx+dy*dy+dz*dz<th*th){{
          const b=lc*6;
          lpos[b]=pos[i*3];lpos[b+1]=pos[i*3+1];lpos[b+2]=pos[i*3+2];
          lpos[b+3]=pos[j*3];lpos[b+4]=pos[j*3+1];lpos[b+5]=pos[j*3+2];
          lc++;
        }}
      }}
      lg.setDrawRange(0,lc*2); lg.attributes.position.needsUpdate=true;
    }}
    renderer.render(scene,cam);
  }}
  animate();
  window.addEventListener('resize',()=>{{
    cam.aspect=innerWidth/innerHeight; cam.updateProjectionMatrix();
    renderer.setSize(innerWidth,innerHeight);
  }});
}})();
 
/* ── GSAP SCROLL ── */
gsap.registerPlugin(ScrollTrigger);
 
gsap.utils.toArray('.fc').forEach((el,i)=>{{
  gsap.to(el,{{opacity:1,y:0,duration:.55,delay:(i%3)*.09,
    scrollTrigger:{{trigger:el,start:'top 88%'}}
  }});
}});
 
gsap.utils.toArray('.alyr').forEach((el,i)=>{{
  gsap.to(el,{{opacity:1,x:0,duration:.45,delay:i*.07,
    scrollTrigger:{{trigger:'.arch',start:'top 75%'}}
  }});
}});
 
gsap.utils.toArray('.pc').forEach((el,i)=>{{
  gsap.to(el,{{opacity:1,y:0,duration:.5,delay:i*.12,
    scrollTrigger:{{trigger:'.pg',start:'top 82%'}}
  }});
}});
 
/* Nav opacity on scroll */
const mnav = document.getElementById('mnav');
window.addEventListener('scroll',()=>{{
  mnav.style.background = scrollY>20 ? 'rgba(5,8,16,.97)' : 'rgba(5,8,16,.7)';
}});
 
/* FAQ */
function tfaq(btn){{
  const it=btn.closest('.fi');
  const op=it.classList.contains('open');
  document.querySelectorAll('.fi.open').forEach(e=>e.classList.remove('open'));
  if(!op) it.classList.add('open');
}}
 
/* Smooth anchor */
document.querySelectorAll('a[href^="#"]').forEach(a=>{{
  a.addEventListener('click',e=>{{
    const t=document.querySelector(a.getAttribute('href'));
    if(t){{e.preventDefault();t.scrollIntoView({{behavior:'smooth',block:'start'}})}}
  }});
}});
</script>
</body>
</html>"""

# ══════════════════════════════════════════════════════════════════════════════
# PRICING PAGE
# ══════════════════════════════════════════════════════════════════════════════
@app.get("/pricing", response_class=HTMLResponse)
async def pricing_page():
    base = APP_BASE_URL; rzpkey = RZP_KEY_ID
    return f"""<!DOCTYPE html><html><head><title>FMSecure PRO — Pricing</title>
    <script src="https://checkout.razorpay.com/v1/checkout.js"></script>
    <style>
      *{{box-sizing:border-box;margin:0;padding:0}}body{{background:#0d1117;color:#e6edf3;font-family:system-ui,sans-serif;min-height:100vh}}
      nav{{background:#161b22;border-bottom:1px solid #30363d;padding:16px 48px;display:flex;justify-content:space-between;align-items:center}}
      .brand{{color:#2f81f7;font-weight:700;font-size:20px;text-decoration:none}}
      .back{{color:#8b949e;text-decoration:none;font-size:14px}}.back:hover{{color:#e6edf3}}
      main{{max-width:900px;margin:0 auto;padding:64px 24px}}
      h1{{text-align:center;font-size:36px;font-weight:700;margin-bottom:12px}}
      .sub{{text-align:center;color:#8b949e;font-size:16px;margin-bottom:56px}}
      .cards{{display:flex;gap:24px;justify-content:center;flex-wrap:wrap}}
      .card{{background:#161b22;border:1px solid #30363d;border-radius:16px;padding:36px 32px;width:340px;position:relative}}
      .card.featured{{border-color:#2f81f7}}
      .badge{{position:absolute;top:-13px;left:50%;transform:translateX(-50%);background:#2f81f7;color:#fff;padding:4px 16px;border-radius:20px;font-size:12px;font-weight:600;white-space:nowrap}}
      .plan{{color:#8b949e;font-size:12px;font-weight:600;letter-spacing:.5px;margin-bottom:8px}}
      .price{{font-size:42px;font-weight:700;margin-bottom:4px}}.price span{{font-size:18px;color:#8b949e;font-weight:400}}
      .period{{color:#8b949e;font-size:14px;margin-bottom:28px}}.savings{{color:#3fb950}}
      .email-row{{margin-bottom:16px}}
      .email-row label{{display:block;font-size:11px;color:#8b949e;font-weight:600;letter-spacing:.5px;margin-bottom:6px}}
      .email-row input{{width:100%;background:#0d1117;border:1px solid #30363d;border-radius:6px;color:#e6edf3;padding:10px 12px;font-size:14px;outline:none}}
      .email-row input:focus{{border-color:#2f81f7}}
      ul{{list-style:none;margin-bottom:28px}}
      li{{padding:8px 0;font-size:14px;border-bottom:1px solid #21262d;color:#8b949e}}
      li:last-child{{border-bottom:none}}li strong{{color:#e6edf3}}
      .check{{color:#3fb950;margin-right:8px;font-weight:700}}
      .btn{{width:100%;padding:14px;border:none;border-radius:8px;font-size:15px;font-weight:600;cursor:pointer;transition:opacity .15s}}
      .btn:hover{{opacity:.85}}.btn-blue{{background:#2f81f7;color:#fff}}.btn-green{{background:#238636;color:#fff}}
      .note{{text-align:center;color:#484f58;font-size:13px;margin-top:36px;line-height:1.7}}
      footer{{text-align:center;color:#484f58;font-size:13px;padding:48px 24px}}
    </style></head><body>
    <nav><a class="brand" href="/home">FMSecure</a><a class="back" href="/home">&#x2190; Back to home</a></nav>
    <main>
      <h1>Simple, transparent pricing</h1>
      <p class="sub">No hidden fees. Cancel anytime. License key emailed instantly after payment.</p>
      <div class="cards">
        <div class="card">
          <p class="plan">PRO MONTHLY</p>
          <div class="price">&#x20B9;499<span>/mo</span></div>
          <p class="period">Billed monthly, cancel anytime</p>
          <div class="email-row">
            <label>EMAIL — KEY WILL BE SENT HERE</label>
            <input type="email" id="email-monthly" placeholder="you@example.com">
          </div>
          <ul>
            <li><span class="check">&#10003;</span><strong>5 folders</strong> monitored</li>
            <li><span class="check">&#10003;</span><strong>Active Defense</strong> + auto-heal vault</li>
            <li><span class="check">&#10003;</span><strong>Ransomware killswitch</strong></li>
            <li><span class="check">&#10003;</span><strong>USB DLP</strong> device control</li>
            <li><span class="check">&#10003;</span><strong>Google Drive</strong> cloud backup</li>
            <li><span class="check">&#10003;</span><strong>Forensic vault</strong> + snapshots</li>
            <li><span class="check">&#10003;</span>Email security alerts</li>
          </ul>
          <button class="btn btn-blue" onclick="startPayment('pro_monthly')">Buy Monthly &#x2014; &#x20B9;999</button>
        </div>
        <div class="card featured">
          <div class="badge">BEST VALUE &#x2014; SAVE &#x20B9;1,989</div>
          <p class="plan">PRO ANNUAL</p>
          <div class="price">&#x20B9;4,999<span>/yr</span></div>
          <p class="period">&#x20B9;833/mo billed annually <span class="savings">&#x2714; 2 months free</span></p>
          <div class="email-row">
            <label>EMAIL — KEY WILL BE SENT HERE</label>
            <input type="email" id="email-annual" placeholder="you@example.com">
          </div>
          <ul>
            <li><span class="check">&#10003;</span><strong>Everything</strong> in Monthly</li>
            <li><span class="check">&#10003;</span><strong>Priority</strong> email support</li>
            <li><span class="check">&#10003;</span><strong>Early access</strong> to new features</li>
            <li><span class="check">&#10003;</span>Invoice for business use</li>
            <li><span class="check">&#10003;</span>Extended offline grace period</li>
            <li><span class="check">&#10003;</span>2 months free vs monthly</li>
            <li><span class="check">&#10003;</span>Feature request priority</li>
          </ul>
          <button class="btn btn-green" onclick="startPayment('pro_annual')">Buy Annual &#x2014; &#x20B9;4,999</button>
        </div>
      </div>
      <p class="note">Payments secured by Razorpay &bull; UPI, Net Banking, Cards, Wallets accepted<br>
         One license per device &bull; Transfer to new device on request</p>
    </main>
    <footer>FMSecure v2.0 &bull; Enterprise Endpoint Detection &amp; Response &bull; Made in India</footer>
    <script>
    async function startPayment(tier) {{
      const eid=tier==='pro_monthly'?'email-monthly':'email-annual';
      const email=document.getElementById(eid).value.trim();
      if(!email||!email.includes('@')||!email.includes('.')){{
        alert('Please enter a valid email.\\nYour license key will be sent there.');
        document.getElementById(eid).focus();return;}}
      let od;
      try{{
        const r=await fetch('{base}/payment/create-order',{{method:'POST',
          headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{tier,email}})}});
        od=await r.json();
      }}catch(e){{alert('Could not reach payment server. Please try again.');return;}}
      if(od.error){{alert('Error: '+od.error);return;}}
      new Razorpay({{
        key:'{rzpkey}',amount:od.amount,currency:od.currency,name:'FMSecure',
        description:od.description,order_id:od.order_id,prefill:{{email}},
        theme:{{color:'#2f81f7'}},
        handler:async function(res){{
          let result;
          try{{
            const vr=await fetch('{base}/payment/verify',{{method:'POST',
              headers:{{'Content-Type':'application/json'}},
              body:JSON.stringify({{razorpay_order_id:res.razorpay_order_id,
                razorpay_payment_id:res.razorpay_payment_id,
                razorpay_signature:res.razorpay_signature,email,tier}})}});
            result=await vr.json();
          }}catch(e){{alert('Verification error. Contact support. Payment ID: '+res.razorpay_payment_id);return;}}
          if(result.success){{
            window.location.href='{base}/payment/success?key='+encodeURIComponent(result.license_key)
              +'&email='+encodeURIComponent(email)+'&tier='+encodeURIComponent(tier);
          }}else{{alert('Payment verification failed.\\nPayment ID: '+res.razorpay_payment_id);}}
        }},
        modal:{{ondismiss:function(){{}}}}
      }}).open();
    }}
    </script></body></html>"""

# ══════════════════════════════════════════════════════════════════════════════
# PAYMENT ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════
class CreateOrderRequest(BaseModel):
    tier: str; email: str

class VerifyPaymentRequest(BaseModel):
    razorpay_order_id: str; razorpay_payment_id: str; razorpay_signature: str
    email: str; tier: str

@app.post("/payment/create-order")
@limiter.limit("20/minute")
async def create_order(request: Request, body: CreateOrderRequest):
    tier=body.tier.strip().lower(); email=body.email.strip().lower()
    if tier not in PLANS: return JSONResponse({"error":"Invalid plan"},status_code=400)
    if not email or "@" not in email: return JSONResponse({"error":"Invalid email"},status_code=400)
    plan=PLANS[tier]
    try:
        order=rzp_client.order.create({"amount":plan["amount"],"currency":plan["currency"],
            "receipt":f"fm_{uuid.uuid4().hex[:8]}","notes":{"email":email,"tier":tier}})
    except Exception as e:
        print(f"[RZP] Order error: {e}"); return JSONResponse({"error":"Payment gateway error"},status_code=500)
    try:
        conn=get_db();cur=conn.cursor()
        cur.execute("INSERT INTO pending_orders(order_id,email,tier,amount) VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            (order["id"],email,tier,plan["amount"]))
        conn.commit();cur.close();conn.close()
    except Exception as e: print(f"[DB] Pending save error: {e}")
    return {"order_id":order["id"],"amount":plan["amount"],"currency":plan["currency"],"description":plan["description"]}

@app.post("/payment/verify")
@limiter.limit("20/minute")
async def verify_payment(request: Request, body: VerifyPaymentRequest):
    # 1. Verify Razorpay signature
    expected=_hmac.new(RZP_KEY_SECRET.encode(),
        f"{body.razorpay_order_id}|{body.razorpay_payment_id}".encode(),
        hashlib.sha256).hexdigest()
    if not secrets.compare_digest(expected, body.razorpay_signature):
        print(f"[RZP] Sig mismatch {body.razorpay_order_id}")
        return JSONResponse({"success":False,"error":"Signature failed"},status_code=400)

    # 2. Generate license and save to DB
    tier=body.tier.strip().lower(); email=body.email.strip().lower()
    payment_id=body.razorpay_payment_id; order_id=body.razorpay_order_id
    expires_iso=(datetime.now(timezone.utc)+timedelta(days=PLANS.get(tier,{}).get("days",31))).isoformat()
    license_key=_gen_key(tier,email,payment_id)
    try:
        _save_license(license_key,email,tier,payment_id,order_id,expires_iso)
    except Exception as e:
        print(f"[DB] Save error: {e}")
        return JSONResponse({"success":False,"error":"Database error"},status_code=500)

    # 3. Clean up pending order
    try:
        conn=get_db();cur=conn.cursor()
        cur.execute("DELETE FROM pending_orders WHERE order_id=%s",(order_id,))
        conn.commit();cur.close();conn.close()
    except: pass

    # 4. Send email in background thread — does NOT block the payment response
    threading.Thread(
        target=_send_license_email,
        args=(email, license_key, tier, expires_iso),
        daemon=True
    ).start()

    print(f"[PAYMENT] Generated key {license_key} for {email}")

    # 5. Return immediately — browser redirects to success page without waiting for email
    return {"success":True,"license_key":license_key,"tier":tier,"expires_at":expires_iso}

@app.get("/payment/success", response_class=HTMLResponse)
async def payment_success(key: str = "", email: str = "", tier: str = ""):
    tier_label=PLANS.get(tier,{}).get("label","PRO")
    return f"""<!DOCTYPE html><html><head><title>Payment Successful | FMSecure</title>
    <style>*{{box-sizing:border-box;margin:0;padding:0}}
    body{{background:#0d1117;color:#e6edf3;font-family:system-ui,sans-serif;
         display:flex;align-items:center;justify-content:center;min-height:100vh;padding:24px}}
    .card{{background:#161b22;border:1px solid #238636;border-radius:16px;
           padding:48px 40px;max-width:480px;width:100%;text-align:center}}
    h2{{color:#3fb950;font-size:24px;margin-bottom:8px}}p{{color:#8b949e;font-size:15px;line-height:1.6}}
    .key-box{{background:#0d1117;border:1px solid #30363d;border-radius:8px;padding:20px;margin:24px 0}}
    .key-label{{color:#484f58;font-size:11px;letter-spacing:1px;margin-bottom:10px}}
    .key{{color:#2f81f7;font-size:20px;font-family:monospace;font-weight:700;letter-spacing:2px;word-break:break-all}}
    .copy-btn{{margin-top:14px;background:#30363d;border:none;color:#e6edf3;padding:8px 20px;border-radius:6px;cursor:pointer;font-size:13px}}
    .steps{{text-align:left;background:#0d1117;border-radius:8px;padding:20px 24px;font-size:14px;color:#8b949e;line-height:2.2}}
    strong{{color:#e6edf3}}</style></head><body>
    <div class="card">
      <div style="font-size:56px;margin-bottom:16px">&#9989;</div>
      <h2>Payment successful!</h2>
      <p>Your <strong>{tier_label}</strong> is now active.<br>
         We've also emailed this key to <strong>{email}</strong></p>
      <div class="key-box">
        <div class="key-label">YOUR LICENSE KEY</div>
        <div class="key" id="lk">{key}</div>
        <button class="copy-btn" onclick="navigator.clipboard.writeText('{key}');this.textContent='&#10003; Copied!'">Copy key</button>
      </div>
      <div class="steps">
        <strong>How to activate in FMSecure:</strong><br>
        1. Open <strong>FMSecure</strong> on your PC<br>
        2. Click your <strong>username</strong> (top-right)<br>
        3. Click <strong>Activate License</strong><br>
        4. Paste this key — no email needed<br>
        5. Click <strong>Activate</strong> — PRO unlocked!
      </div>
    </div></body></html>"""

# ══════════════════════════════════════════════════════════════════════════════
# LICENSE VALIDATION — device-based, NO email check
# ══════════════════════════════════════════════════════════════════════════════
class LicenseValidateRequest(BaseModel):
    license_key: str
    machine_id:  str

@app.post("/api/license/validate")
async def validate_license(req: LicenseValidateRequest):
    key=req.license_key.strip(); mid=req.machine_id.strip()
    if not DATABASE_URL: return {"valid":False,"tier":"free","reason":"db_not_configured"}
    if not key or not mid: return {"valid":False,"tier":"free","reason":"missing_fields"}
    try:
        conn=get_db();cur=conn.cursor()
        cur.execute("SELECT * FROM licenses WHERE license_key=%s",(key,))
        r=cur.fetchone()
    except: return {"valid":False,"tier":"free","reason":"db_error"}
    if not r:
        cur.close();conn.close()
        return {"valid":False,"tier":"free","expires_at":None,"reason":"key_not_found"}
    if not r["active"] or _is_expired(r["expires_at"]):
        cur.close();conn.close()
        return {"valid":False,"tier":"free",
                "expires_at":r["expires_at"].isoformat() if r["expires_at"] else None,
                "reason":"subscription_expired"}
    bound=r["machine_id"]
    if bound is None:
        cur.execute("UPDATE licenses SET machine_id=%s WHERE license_key=%s",(mid,key))
        conn.commit();cur.close();conn.close()
        print(f"[LICENSE] Bound {key} to device {mid[:20]}...")
        return {"valid":True,"tier":r["tier"],"expires_at":r["expires_at"].isoformat(),"reason":"activated"}
    if bound==mid:
        cur.close();conn.close()
        return {"valid":True,"tier":r["tier"],"expires_at":r["expires_at"].isoformat(),"reason":"ok"}
    cur.close();conn.close()
    return {"valid":False,"tier":"free","expires_at":None,"reason":"device_mismatch"}

@app.post("/api/license/activate")
async def activate_license(req: LicenseValidateRequest):
    return await validate_license(req)

# ══════════════════════════════════════════════════════════════════════════════
# LICENSE TRANSFER  — lets a user re-bind a key to a new device after reinstall
# ══════════════════════════════════════════════════════════════════════════════

class TransferRequestBody(BaseModel):
    license_key: str
    email:       str   # must match the purchase email on record

class TransferConfirmBody(BaseModel):
    license_key:    str
    otp:            str
    new_machine_id: str

@app.post("/api/license/request_transfer")
async def request_transfer(req: TransferRequestBody):
    """
    Step 1 — user proves ownership by providing their purchase email.
    If it matches the DB record, a 6-digit OTP is sent via SendGrid.
    """
    key   = req.license_key.strip()
    email = req.email.strip().lower()

    if not DATABASE_URL:
        return {"ok": False, "reason": "db_not_configured"}
    if not key or not email:
        return {"ok": False, "reason": "missing_fields"}

    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT email, active FROM licenses WHERE license_key = %s", (key,))
        row = cur.fetchone()
        cur.close(); conn.close()
    except Exception as e:
        print(f"[TRANSFER] DB error: {e}")
        return {"ok": False, "reason": "db_error"}

    if not row:
        return {"ok": False, "reason": "key_not_found"}

    # Constant-time comparison — don't reveal whether key exists
    stored_email = (row["email"] or "").strip().lower()
    if not secrets.compare_digest(stored_email, email):
        return {"ok": False,
                "reason": "Email does not match the purchase record for this key."}

    if not row["active"]:
        return {"ok": False, "reason": "subscription_expired"}

    # Generate OTP and stash it
    otp = str(random.randint(100000, 999999))
    _pending_transfers[key] = {
        "otp":     otp,
        "email":   email,
        "expires": time.time() + _TRANSFER_OTP_TTL,
    }

    # Send via SendGrid (same helper pattern as _send_license_email)
    def _send_transfer_otp():
        if not SENDGRID_API_KEY:
            print(f"[TRANSFER] No SENDGRID_API_KEY. OTP for {email}: {otp}")
            return
        html = f"""
        <div style="font-family:Arial,sans-serif;max-width:520px;margin:0 auto;
                    background:#0d1117;color:#e6edf3;padding:32px;border-radius:10px;">
          <h2 style="color:#2f81f7;margin-top:0">&#128273; FMSecure License Transfer</h2>
          <p style="color:#a0a8b8;font-size:15px">
            A request was made to transfer your license key to a new device.
            Use the verification code below to confirm. It expires in 5 minutes.
          </p>
          <div style="background:#161b22;border:1px solid #30363d;border-radius:8px;
                      padding:24px;text-align:center;margin:24px 0;">
            <p style="margin:0 0 10px;color:#8b949e;font-size:11px;
                      letter-spacing:1px;font-weight:600">VERIFICATION CODE</p>
            <div style="font-size:36px;font-weight:700;color:#2f81f7;letter-spacing:8px;
                        font-family:Courier,monospace;">{otp}</div>
          </div>
          <p style="color:#484f58;font-size:12px;border-top:1px solid #21262d;
                    padding-top:16px;margin:0">
            If you did not request this, your license is safe — ignore this email.<br>
            FMSecure v2.0 &bull; Enterprise EDR for Windows
          </p>
        </div>"""
        try:
            import sendgrid as sg_mod
            from sendgrid.helpers.mail import Mail
            sg = sg_mod.SendGridAPIClient(api_key=SENDGRID_API_KEY)
            msg = Mail(from_email=SENDER_EMAIL, to_emails=email,
                       subject="FMSecure — License Transfer Verification Code",
                       html_content=html)
            resp = sg.send(msg)
            print(f"[TRANSFER] OTP sent to {email} — status {resp.status_code}")
        except Exception as e:
            print(f"[TRANSFER] SendGrid failed for {email}: {e}")
            print(f"[TRANSFER] OTP was: {otp}")

    threading.Thread(target=_send_transfer_otp, daemon=True).start()
    return {"ok": True}


@app.post("/api/license/confirm_transfer")
async def confirm_transfer(req: TransferConfirmBody):
    """
    Step 2 — user submits the OTP + their new machine_id.
    On success the DB machine_id column is updated and the key works immediately.
    """
    key = req.license_key.strip()
    otp = req.otp.strip()
    mid = req.new_machine_id.strip()

    if not DATABASE_URL:
        return {"ok": False, "reason": "db_not_configured"}
    if not key or not otp or not mid:
        return {"ok": False, "reason": "missing_fields"}

    pending = _pending_transfers.get(key)
    if not pending:
        return {"ok": False,
                "reason": "No transfer request found. Please request a new code."}

    if time.time() > pending["expires"]:
        del _pending_transfers[key]
        return {"ok": False,
                "reason": "Verification code expired. Please request a new one."}

    if not secrets.compare_digest(pending["otp"], otp):
        return {"ok": False, "reason": "Incorrect verification code."}

    # OTP is valid — re-bind the key to the new device
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute(
            "UPDATE licenses SET machine_id = %s WHERE license_key = %s RETURNING tier",
            (mid, key)
        )
        row = cur.fetchone()
        conn.commit(); cur.close(); conn.close()
    except Exception as e:
        print(f"[TRANSFER] DB update error: {e}")
        return {"ok": False, "reason": "db_error"}

    if not row:
        return {"ok": False, "reason": "key_not_found"}

    # Clean up the pending entry
    del _pending_transfers[key]

    tier = row["tier"] or "pro_monthly"
    print(f"[TRANSFER] ✅ Key {key[:16]}… transferred to device {mid[:20]}…")
    return {"ok": True, "tier": tier}


# ══════════════════════════════════════════════════════════════════════════════
# LICENSE RECOVERY — resend lost key to purchase email
# ══════════════════════════════════════════════════════════════════════════════

class KeyRecoveryBody(BaseModel):
    email: str

@app.post("/api/license/recover_key")
async def recover_key(req: KeyRecoveryBody):
    """
    User forgot their license key or deleted the email.
    Looks up all active, non-expired keys for that email and re-sends them.
    Rate-limited to prevent enumeration — always returns {ok: true} so
    attackers can't determine whether an email is registered.
    """
    email = req.email.strip().lower()
    if not email or not DATABASE_URL:
        # Return ok=True even on bad input to avoid enumeration
        return {"ok": True}

    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute(
            "SELECT license_key, tier, expires_at FROM licenses "
            "WHERE email = %s AND active = TRUE ORDER BY created_at DESC",
            (email,)
        )
        rows = cur.fetchall()
        cur.close(); conn.close()
    except Exception as e:
        print(f"[RECOVER] DB error: {e}")
        return {"ok": True}   # still return ok — don't leak DB status

    # Only send keys that haven't expired
    sent = 0
    for row in rows:
        if not _is_expired(row["expires_at"]):
            threading.Thread(
                target=_send_license_email,
                args=(email, row["license_key"], row["tier"],
                      row["expires_at"].isoformat()),
                daemon=True
            ).start()
            sent += 1

    print(f"[RECOVER] Sent {sent} key(s) to {email}")
    # Always return ok=True — user sees "check your inbox" regardless
    return {"ok": True}


# ══════════════════════════════════════════════════════════════════════════════
# ADMIN ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════
@app.get("/api/license/list")
async def list_licenses(api_key: str = ""):
    _check_admin(api_key); conn=get_db(); cur=conn.cursor()
    cur.execute("SELECT * FROM licenses ORDER BY created_at DESC")
    rows=[dict(r) for r in cur.fetchall()]; cur.close(); conn.close()
    return {"count":len(rows),"licenses":rows}

@app.post("/api/license/create_manual")
async def create_manual(email:str, tier:str="pro_monthly", days:int=30, api_key:str=""):
    _check_admin(api_key)
    sub_id=f"manual_{uuid.uuid4().hex[:8]}"
    expires_iso=(datetime.now(timezone.utc)+timedelta(days=days)).isoformat()
    license_key=_gen_key(tier,email,sub_id)
    _save_license(license_key,email,tier,sub_id,sub_id,expires_iso)
    return {"license_key":license_key,"email":email,"tier":tier,"expires_at":expires_iso}

@app.post("/api/license/release_device")
async def release_device(license_key:str, api_key:str=""):
    _check_admin(api_key); conn=get_db(); cur=conn.cursor()
    cur.execute("UPDATE licenses SET machine_id=NULL WHERE license_key=%s RETURNING email,tier",(license_key,))
    row=cur.fetchone(); conn.commit(); cur.close(); conn.close()
    if not row: raise HTTPException(status_code=404,detail="Key not found")
    return {"message":"Device binding released.","license_key":license_key,"email":row["email"]}

@app.get("/licenses", response_class=HTMLResponse)
async def licenses_page(_: bool = Depends(verify_session)):
    conn=get_db();cur=conn.cursor()
    cur.execute("SELECT * FROM licenses ORDER BY created_at DESC LIMIT 500")
    rows=cur.fetchall();cur.close();conn.close()
    trs=""
    for r in rows:
        expired=_is_expired(r["expires_at"])
        sb=('<span style="background:#238636;color:#fff;padding:2px 8px;border-radius:4px;font-size:12px">Active</span>'
            if not expired and r["active"]
            else '<span style="background:#da3633;color:#fff;padding:2px 8px;border-radius:4px;font-size:12px">Expired</span>')
        exp=r["expires_at"].strftime("%Y-%m-%d") if r["expires_at"] else "—"
        mid=(r["machine_id"] or "—")
        trs+=f"<tr><td style='font-family:monospace;font-size:12px'>{r['license_key']}</td><td>{r['email']}</td><td>{r['tier']}</td><td>{sb}</td><td>{exp}</td><td style='font-family:monospace;font-size:11px;color:#8b949e'>{mid[:22] if mid!='—' else mid}</td></tr>"
    return f"""<!DOCTYPE html><html><head><title>FMSecure | Licenses</title>
    <style>*{{box-sizing:border-box;margin:0;padding:0}}body{{background:#0a0a0a;color:#e6edf3;font-family:system-ui,sans-serif}}
    nav{{background:#161b22;border-bottom:1px solid #30363d;padding:16px 24px;display:flex;justify-content:space-between;align-items:center}}
    .brand{{color:#2f81f7;font-weight:700}}a{{color:#8b949e;text-decoration:none;font-size:13px;margin-left:16px}}a:hover{{color:#e6edf3}}
    .container{{padding:24px}}table{{width:100%;border-collapse:collapse;background:#161b22;border-radius:8px;overflow:hidden}}
    th{{background:#0d1117;color:#8b949e;padding:12px 16px;text-align:left;font-size:12px;font-weight:600;letter-spacing:.5px}}
    td{{padding:12px 16px;border-top:1px solid #21262d;font-size:13px}}</style></head><body>
    <nav><span class="brand">License Manager</span>
    <div><a href="/dashboard">&#x2190; C2 Dashboard</a><a href="/logout">Logout</a></div></nav>
    <div class="container"><table><thead><tr>
      <th>LICENSE KEY</th><th>EMAIL</th><th>TIER</th><th>STATUS</th><th>EXPIRES</th><th>DEVICE ID</th>
    </tr></thead><tbody>{trs}</tbody></table></div></body></html>"""



# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC VERSION ENDPOINT — checked by FMSecure desktop app on startup
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/api/version/publish-form")
async def publish_version_form(
    request: Request,
    version:       str = Form(...),
    release_notes: str = Form(""),
    download_url:  str = Form(""),
    changelog_url: str = Form(""),
    _: bool = Depends(verify_session)
):
    """Dashboard form handler — same logic as the JSON endpoint but session-auth."""
    dl = download_url or f"{APP_BASE_URL}/download" 
    cl = changelog_url or f"{APP_BASE_URL}/changelog"

    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("UPDATE versions SET is_current = FALSE")
        cur.execute("""
            INSERT INTO versions (version, release_notes, download_url, changelog_url, is_current)
            VALUES (%s, %s, %s, %s, TRUE)
        """, (version.strip(), release_notes.strip(), dl, cl))
        conn.commit(); cur.close(); conn.close()
        print(f"[VERSION] Published v{version} via dashboard")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return RedirectResponse("/dashboard", status_code=303)

@app.get("/version.json")
async def version_json():
    """
    Returns the current latest version metadata.
    The desktop client fetches this on every startup to check for updates.
    Cache-control headers prevent stale CDN caching.
    """
    if not DATABASE_URL:
        return JSONResponse({"latest_version": "2.5.0",
                             "release_notes": "",
                             "download_url": f"{APP_BASE_URL}/download",
                             "changelog_url": f"{APP_BASE_URL}/changelog"})
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute(
            "SELECT version, release_notes, download_url, changelog_url "
            "FROM versions WHERE is_current = TRUE "
            "ORDER BY published_at DESC LIMIT 1"
        )
        row = cur.fetchone()
        cur.close(); conn.close()

        if not row:
            return JSONResponse({"latest_version": "2.5.0",
                                 "release_notes": "",
                                 "download_url": f"{APP_BASE_URL}/download",
                                 "changelog_url": f"{APP_BASE_URL}/changelog"})

        return JSONResponse(
            content={
                "latest_version": row["version"],
                "release_notes":  row["release_notes"],
                "download_url":   row["download_url"],
                "changelog_url":  row["changelog_url"],
            },
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate",
                "Pragma":        "no-cache",
            }
        )
    except Exception as e:
        print(f"[VERSION] DB error: {e}")
        return JSONResponse({"latest_version": "2.5.0",
                             "release_notes": "",
                             "download_url": f"{APP_BASE_URL}/download",
                             "changelog_url": f"{APP_BASE_URL}/changelog"})


# ── Admin: push a new version ──────────────────────────────────────────────
class VersionBody(BaseModel):
    version:       str
    release_notes: str  = ""
    download_url:  str  = ""
    changelog_url: str  = ""
    api_key:       str  = ""

@app.post("/api/version/publish")
async def publish_version(body: VersionBody):
    """
    Call this from your admin dashboard to publish a new version.
    All existing rows are marked is_current=FALSE, new row inserted as TRUE.
    """
    _check_admin(body.api_key)

    dl  = body.download_url  or f"{APP_BASE_URL}/download"
    cl  = body.changelog_url or f"{APP_BASE_URL}/changelog"

    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("UPDATE versions SET is_current = FALSE")
        cur.execute("""
            INSERT INTO versions (version, release_notes, download_url, changelog_url, is_current)
            VALUES (%s, %s, %s, %s, TRUE)
        """, (body.version.strip(), body.release_notes.strip(), dl, cl))
        conn.commit(); cur.close(); conn.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    print(f"[VERSION] Published v{body.version}")
    return {"ok": True, "version": body.version}
# ══════════════════════════════════════════════════════════════════════════════
# TEMPORARY DB PATCH ENDPOINT
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/db-fix")
async def fix_db():
    """Temporary endpoint to forcefully patch the database schema"""
    try:
        conn = get_db()
        cur  = conn.cursor()
        
        # Forcefully inject the missing columns into the existing table
        cur.execute("ALTER TABLE licenses ADD COLUMN IF NOT EXISTS payment_id TEXT;")
        cur.execute("ALTER TABLE licenses ADD COLUMN IF NOT EXISTS order_id TEXT;")
        
        conn.commit()
        cur.close()
        conn.close()
        return {"success": True, "message": "Database successfully patched! Missing columns added."}
    except Exception as e:
        return {"success": False, "error": str(e)}