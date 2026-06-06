"""
main.py — FMSecure C2 + License Server
Hosted on Render.com (free tier) + Neon PostgreSQL

Environment variables (set in Render dashboard):
  ADMIN_USERNAME      = your admin username
  ADMIN_PASSWORD      = strong password
  API_KEY             = desktop agent API key
  RAZORPAY_KEY_ID     = rzp_test_... or rzp_live_...
  RAZORPAY_KEY_SECRET = razorpay secret
  LICENSE_HMAC_SECRET = generate: python -c "import secrets;print(secrets.token_hex(32))"
  ADMIN_API_KEY       = any secret for admin endpoints
  APP_BASE_URL        = https://your-app.onrender.com
  DATABASE_URL        = from Neon PostgreSQL dashboard (postgres://...)
  GMAIL_USER          = fmsecure.team@gmail.com
  GMAIL_APP_PASSWORD  = your 16-char Google App Password (no spaces)
  SENDER_EMAIL        = fmsecure.team@gmail.com
  SUPER_ADMIN_EMAIL   = fmsecure.team@gmail.com

Email uses Gmail SMTP with App Password (smtplib, no extra packages needed).
Falls back to printing key/OTP to logs if GMAIL_USER not set.
"""
import os, secrets, time
from dotenv import load_dotenv

load_dotenv()

import secrets, time, hashlib, hmac as _hmac, uuid, threading, random
try:
    import bcrypt as _bcrypt
    _BCRYPT_AVAILABLE = True
except ImportError:
    _BCRYPT_AVAILABLE = False
    print('[AUTH] bcrypt not installed — falling back to SHA-256. Run: pip install bcrypt')

try:
    import pyotp as _pyotp
    _PYOTP_AVAILABLE = True
except ImportError:
    _PYOTP_AVAILABLE = False

try:
    from itsdangerous import URLSafeTimedSerializer as _USTS
    _ISD_AVAILABLE = True
except ImportError:
    _ISD_AVAILABLE = False
from functools import wraps
from datetime import datetime, timezone, timedelta

from fastapi import Response
from fastapi import FastAPI, Request, Depends, HTTPException, Form, Cookie
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
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
SENDGRID_API_KEY  = os.getenv("SENDGRID_API_KEY", "")   # legacy fallback, not used
GMAIL_USER        = os.getenv("GMAIL_USER", "")
GMAIL_APP_PASSWORD= os.getenv("GMAIL_APP_PASSWORD", "")
SENDER_EMAIL      = os.getenv("SENDER_EMAIL", "fmsecure.team@gmail.com")
SUPER_ADMIN_EMAIL = os.getenv("SUPER_ADMIN_EMAIL", SENDER_EMAIL)
SESSION_TOKEN     = secrets.token_hex(16)
DRIVE_FILE_ID     = os.getenv("DRIVE_FILE_ID", "1e-EnPaxiMP0ZFpkL6QpBopJ41QeQMjMM")   # Google Drive file ID for download

# Download URL — auto-derived. Just set DRIVE_FILE_ID env var on Railway.
DOWNLOAD_URL = (
    f"https://drive.google.com/uc?export=download&id={DRIVE_FILE_ID}"
    if DRIVE_FILE_ID else "#"
)
PRODUCT_PAGE_URL = os.getenv("PRODUCT_PAGE_URL", f"{APP_BASE_URL}/download")
_tenant_sessions: dict = {}   # token → {"tenant_id": str, "email": str, "role": str}
_TENANT_SESSION_TTL = 86400   # 24 hours
rzp_client = razorpay.Client(auth=(RZP_KEY_ID, RZP_KEY_SECRET))

# ── Plans — amounts in PAISE (Rs 499 = 49900) ─────────────────────────────────
# To change price: edit "amount". To change label: edit "label" AND the HTML below.
PLANS = {
    "pro_monthly": {"label":"PRO Monthly","amount":499, "currency":"INR",
                    "description":"FMSecure PRO - Monthly","days":31},
    "pro_annual":  {"label":"PRO Annual", "amount":4999,"currency":"INR",
                    "description":"FMSecure PRO - Annual","days":365},
}

# ══════════════════════════════════════════════════════════════════════════════
# CENTRAL CONFIGURATION – CHANGE ONCE, UPDATE EVERYWHERE
# ══════════════════════════════════════════════════════════════════════════════
BRAND = {
    # ── Identity ──────────────────────────────────────────────────────────
    "name":            "FMSecure",
    "tagline":         "Enterprise EDR for Windows",
    "short_desc":      "Real-time endpoint detection & response — file integrity, ransomware killswitch, AES-256 vault, and cloud C2.",
    "logo_ico":        "/static/app_icon.ico",
    "logo_png":        "/static/app_icon.png",
    "logo_text":       "FM",                       # fallback initials
    # ── Contact ───────────────────────────────────────────────────────────
    "support_email":   "support@fmsecure.in",
    "sales_email":     "sales@fmsecure.in",
    "security_email":  "security@fmsecure.in",
    # ── Legal ─────────────────────────────────────────────────────────────
    "company":         "Manish Lisa Pvt Limited",
    "company_short":   "FMSecure",
    "founded":         "2025",
    "hq":              "India",
    "copyright_year":  datetime.now().year,
    # ── Product ───────────────────────────────────────────────────────────
    "app_version":     "2.5.0",
    # ── Social ────────────────────────────────────────────────────────────
    "twitter":         "https://twitter.com/fmsecure",
    "linkedin":        "https://linkedin.com/company/fmsecure",
    "github":          "https://github.com/fmsecure",
}

# Pricing displayed on the pricing page – keep in sync with PLANS dict
PRICING_DISPLAY = {
    "pro_monthly": {"label": "PRO Monthly", "price": "499", "period": "/mo"},
    "pro_annual":  {"label": "PRO Annual",  "price": "4,999", "period": "/yr"},
}

def standard_head(title: str = None) -> str:
    """Return a standard HTML <head> with favicon and meta tags."""
    page_title = f"{title} | {BRAND['name']}" if title else BRAND['name']
    return f"""
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{page_title}</title>
    <link rel="icon" type="image/x-icon" href="{BRAND['logo_ico']}">
    <link rel="shortcut icon" href="{BRAND['logo_ico']}">
    """

# ── App setup ──────────────────────────────────────────────────────────────────
limiter = Limiter(key_func=get_remote_address)
app     = FastAPI(title="FMSecure", docs_url="/api/docs", redoc_url="/api/redoc")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
try:
    app.mount("/static", StaticFiles(directory="static"), name="static")
except Exception:
    pass

templates = Jinja2Templates(directory="templates")

agents = {}; commands = {}
_pending_transfers: dict = {}
_TRANSFER_OTP_TTL = 300   # 5 minutes

_LAST_SWEEP_AT = 0
def _maybe_sweep_offline(force=False):
    """Mark stale agents offline — but only at most once every 5 minutes,
    and only when called by a real request handler (lazy)."""
    global _LAST_SWEEP_AT
    now = time.time()
    if not force and (now - _LAST_SWEEP_AT) < 300:   # 5 min throttle
        return
    if not DATABASE_URL:
        return
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute(
            "UPDATE tenant_agents SET status='offline' "
            "WHERE status='online' AND last_seen < NOW() - INTERVAL '45 seconds'"
        )
        conn.commit(); cur.close(); conn.close()
        _LAST_SWEEP_AT = now
    except Exception as e:
        print(f"[SWEEP-LAZY] {e}")


# ── Database ───────────────────────────────────────────────────────────────────
def get_db():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def init_db():
    conn = get_db()
    cur = conn.cursor()
    
    try:
        cur.execute("""

            CREATE TABLE IF NOT EXISTS contact_submissions (
                id          TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
                name        TEXT NOT NULL,
                email       TEXT NOT NULL,
                type        TEXT NOT NULL DEFAULT 'general',
                company     TEXT NOT NULL DEFAULT '',
                seats       TEXT NOT NULL DEFAULT '',
                subject     TEXT NOT NULL DEFAULT '',
                message     TEXT NOT NULL,
                created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE TABLE IF NOT EXISTS enterprise_leads (
                id          TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
                company     TEXT NOT NULL,
                name        TEXT NOT NULL,
                email       TEXT NOT NULL,
                seats       TEXT NOT NULL DEFAULT '10',
                message     TEXT NOT NULL DEFAULT '',
                status      TEXT NOT NULL DEFAULT 'new',
                created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
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
                sigma_rule_count INTEGER DEFAULT 0,
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
                allowed_exts    TEXT NOT NULL DEFAULT '.txt,.json,.py,.html,.js,.css',
                retention_days  INTEGER NOT NULL DEFAULT 90,
                updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS erasure_log (
                id            SERIAL PRIMARY KEY,
                tenant_id     TEXT NOT NULL,
                tenant_name   TEXT NOT NULL,
                erased_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                requested_by  TEXT NOT NULL DEFAULT 'api',
                requester_ip  TEXT NOT NULL DEFAULT '',
                rows_deleted  INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_erasure_log_tenant
                ON erasure_log(tenant_id, erased_at DESC);

        
            CREATE INDEX IF NOT EXISTS idx_tenant_agents_tenant
                ON tenant_agents(tenant_id);
        
            CREATE INDEX IF NOT EXISTS idx_tenant_alerts_tenant_sev
                ON tenant_alerts(tenant_id, severity, created_at DESC);

            DO $$ BEGIN
              IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                             WHERE table_name='tenant_agents' AND column_name='sigma_rule_count') 
              THEN ALTER TABLE tenant_agents ADD COLUMN sigma_rule_count INTEGER DEFAULT 0; END IF;
            END $$;

            DO $$ BEGIN
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                                WHERE table_name='tenant_config' AND column_name='retention_days')
                THEN ALTER TABLE tenant_config ADD COLUMN retention_days INTEGER NOT NULL DEFAULT 90; END IF;

                IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                                WHERE table_name='tenant_config' AND column_name='updated_at')
                THEN ALTER TABLE tenant_config ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(); END IF;
            END $$;

        """)

        cur.execute("SELECT COUNT(*) FROM versions")
        row = cur.fetchone()
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
        conn.rollback()
        print(f"[DB] Error initializing database: {e}")
        raise e
        
    finally:
        cur.close()
        conn.close()


def _start_offline_sweeper():
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
    print("[SWEEPER] Offline sweeper started (30s interval, 45s grace).")


@app.on_event("startup")
async def startup():
  if DATABASE_URL:
    init_db()
    # _start_offline_sweeper()
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
    return "fms-tenant-" + secrets.token_urlsafe(24)
 
# ══════════════════════════════════════════════════════════════════════════════
# PASSWORD HASHING — bcrypt (cost 12) with SHA-256 legacy fallback
# ══════════════════════════════════════════════════════════════════════════════
def _hash_password(password: str) -> str:
    """Hash a new password. Always uses bcrypt when available."""
    if _BCRYPT_AVAILABLE:
        return _bcrypt.hashpw(password.encode(), _bcrypt.gensalt(rounds=12)).decode()
    return hashlib.sha256(("fmsecure_salt_v1:" + password).encode()).hexdigest()

def _hash_password_legacy(password: str) -> str:
    return hashlib.sha256(("fmsecure_salt_v1:" + password).encode()).hexdigest()

def _verify_password(password: str, hashed: str) -> bool:
    if not hashed:
        return False
    if _BCRYPT_AVAILABLE and hashed.startswith("$2b$"):
        try:
            return _bcrypt.checkpw(password.encode(), hashed.encode())
        except Exception:
            return False
    return secrets.compare_digest(_hash_password_legacy(password), hashed)

def _verify_and_upgrade_password(password: str, hashed: str, update_fn=None) -> bool:
    if not hashed:
        return False
    if _BCRYPT_AVAILABLE and hashed.startswith("$2b$"):
        try:
            return _bcrypt.checkpw(password.encode(), hashed.encode())
        except Exception:
            return False
    if secrets.compare_digest(_hash_password_legacy(password), hashed):
        if _BCRYPT_AVAILABLE and update_fn:
            try:
                new_hash = _bcrypt.hashpw(password.encode(), _bcrypt.gensalt(rounds=12)).decode()
                update_fn(new_hash)
                print("[AUTH] Password upgraded from SHA-256 to bcrypt")
            except Exception as e:
                print(f"[AUTH] bcrypt upgrade failed: {e}")
        return True
    return False

# ══════════════════════════════════════════════════════════════════════════════
# CSRF tokens
# ══════════════════════════════════════════════════════════════════════════════
_SECRET_KEY = os.getenv("SECRET_KEY", secrets.token_hex(32))

def _generate_csrf_token() -> str:
    if _ISD_AVAILABLE:
        return _USTS(_SECRET_KEY).dumps("csrf")
    return secrets.token_hex(16)

def _validate_csrf_token(token: str) -> bool:
    if not token:
        return False
    if _ISD_AVAILABLE:
        try:
            _USTS(_SECRET_KEY).loads(token, max_age=3600)
            return True
        except Exception:
            return False
    return True

# ══════════════════════════════════════════════════════════════════════════════
# ACCOUNT LOCKOUT
# ══════════════════════════════════════════════════════════════════════════════
_failed_logins: dict = {}
_MAX_ATTEMPTS    = 10
_LOCKOUT_SECONDS = 300

def _record_failed_login(email: str):
    e = email.lower()
    rec = _failed_logins.get(e, {"count": 0, "locked_until": 0})
    rec["count"] += 1
    if rec["count"] >= _MAX_ATTEMPTS:
        rec["locked_until"] = time.time() + _LOCKOUT_SECONDS
        print(f"[AUTH] Account locked: {e}")
    _failed_logins[e] = rec

def _clear_failed_logins(email: str):
    _failed_logins.pop(email.lower(), None)

def _is_account_locked(email: str) -> bool:
    rec = _failed_logins.get(email.lower())
    if not rec:
        return False
    if rec.get("locked_until", 0) > time.time():
        return True
    _failed_logins.pop(email.lower(), None)
    return False

# ══════════════════════════════════════════════════════════════════════════════
# TOTP 2FA
# ══════════════════════════════════════════════════════════════════════════════
def _verify_totp(secret: str, code: str) -> bool:
    if not _PYOTP_AVAILABLE or not secret or not code:
        return False
    try:
        return _pyotp.TOTP(secret).verify(code.strip(), valid_window=1)
    except Exception:
        return False

def _generate_totp_secret() -> str:
    if _PYOTP_AVAILABLE:
        return _pyotp.random_base32()
    return secrets.token_hex(20)

def _get_totp_uri(secret: str, email: str) -> str:
    if _PYOTP_AVAILABLE:
        return _pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name="FMSecure")
    return ""

 
def _get_tenant_by_api_key(api_key: str) -> dict | None:
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
    token = secrets.token_urlsafe(32)
    _tenant_sessions[token] = {
        "tenant_id": tenant_id,
        "email":     email,
        "role":      role,
        "created_at": time.time(),
    }
    return token
 
def _get_tenant_session(request: "Request") -> dict | None:
    token = request.cookies.get("fms_tenant_session")
    if not token:
        return None
    session = _tenant_sessions.get(token)
    if not session:
        return None
    if time.time() - session["created_at"] > _TENANT_SESSION_TTL:
        del _tenant_sessions[token]
        return None
    return session
 
def _require_tenant_session(request: "Request") -> dict:
    session = _get_tenant_session(request)
    if not session:
        raise HTTPException(
            status_code=302,
            headers={"Location": "/tenant/login"})
    return session
 
def _get_tenant_stats(tenant_id: str) -> dict:
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


# ── Gmail SMTP Helper ──────────────────────────────────────────────────────────
def _send_gmail(to: str, subject: str, html_body: str) -> bool:
    """Send an HTML email via Gmail SMTP using App Password. Returns True on success."""
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    if not GMAIL_USER or not GMAIL_APP_PASSWORD:
        print(f"[EMAIL] No GMAIL_USER/GMAIL_APP_PASSWORD set — skipping send to {to}")
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = f"FMSecure <{SENDER_EMAIL}>"
        msg["To"]      = to
        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.ehlo()
            server.starttls()
            server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
            server.sendmail(SENDER_EMAIL, to, msg.as_string())
        print(f"[EMAIL] Sent to {to} via Gmail SMTP")
        return True
    except Exception as e:
        print(f"[EMAIL] Gmail SMTP failed for {to}: {e}")
        return False


def _send_license_email(email: str, license_key: str, tier: str, expires_iso: str):
    tier_label  = PLANS.get(tier, {}).get("label", "PRO")
    expires_str = expires_iso[:10]

    if not GMAIL_USER:
        print(f"[EMAIL] No GMAIL_USER set. Key for {email}: {license_key}")
        return

    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:520px;margin:0 auto;
                background:#0d1117;color:#e6edf3;padding:32px;border-radius:10px;">
      <h2 style="color:#2f81f7;margin-top:0">&#128737; {BRAND['name']} PRO Activated</h2>
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
          1. Open <strong>{BRAND['name']}</strong> on your PC<br>
          2. Click your <strong>username</strong> (top-right corner)<br>
          3. Click <strong>Activate License</strong><br>
          4. Paste this key and click <strong>Activate</strong><br>
          5. PRO features unlock immediately
        </p>
      </div>
      <p style="color:#484f58;font-size:12px;border-top:1px solid #21262d;
                padding-top:16px;margin:0">
        This key activates on one device. To transfer to a new device, reply to this email.<br>
        {BRAND['name']} v2.0 &bull; {BRAND['tagline']} &bull; Made in India
      </p>
    </div>"""

    try:
        ok = _send_gmail(email, f"Your {BRAND['name']} PRO License Key", html)
        if not ok:
            print(f"[EMAIL] Key was: {license_key}")
    except Exception as e:
        print(f"[EMAIL] Failed for {email}: {e}")
        print(f"[EMAIL] Key was: {license_key}")


def send_tenant_welcome_email(org_email: str, org_name: str, api_key: str, max_agents: int, plan: str):
    if not GMAIL_USER:
        print(f"[TENANT] No GMAIL_USER set. API key for {org_email}: {api_key}")
        return

    plan_label = {"business": "Business", "enterprise": "Enterprise", "trial": "Trial"}.get(plan, "Business")
    download_url = f"{APP_BASE_URL}/download"

    html = f"""
    <div style="font-family:'Segoe UI',Arial,sans-serif;background:#0d1117;padding:30px;">
      <div style="max-width:600px;margin:0 auto;background:#161b22;border-radius:12px;
                  border:1px solid #30363d;overflow:hidden;">
        <div style="background:#2f81f7;padding:24px 32px;">
          <h1 style="margin:0;font-size:22px;color:#fff;">🛡 Welcome to {BRAND['name']} Enterprise</h1>
          <p style="margin:6px 0 0;color:#cfe2ff;font-size:14px;">Your organisation account is ready.</p>
        </div>
        <div style="padding:32px;">
          <p style="font-size:15px;color:#e6edf3;">Hi <strong>{org_name}</strong>,</p>
          <p style="color:#8b949e;font-size:14px;">
            Your {BRAND['name']} Enterprise account has been activated on the
            <strong style="color:#e6edf3">{plan_label} plan</strong> with
            <strong style="color:#e6edf3">{max_agents} seats</strong>.
          </p>
          <div style="background:#0d1117;border:1px solid #30363d;border-radius:8px;
                      padding:20px;margin:20px 0;text-align:center;">
            <p style="margin:0 0 8px;font-size:12px;color:#8b949e;text-transform:uppercase;
                      letter-spacing:1px;">Your Organisation API Key</p>
            <code style="font-size:15px;color:#2f81f7;font-family:'Courier New',monospace;
                         letter-spacing:2px;word-break:break-all;">{api_key}</code>
            <p style="margin:12px 0 0;font-size:12px;color:#f85149;">
              ⚠ Keep this key private. Do not share it publicly.
            </p>
          </div>
          <h3 style="color:#e6edf3;font-size:15px;margin-top:28px;">How to enroll your machines:</h3>
          <ol style="color:#8b949e;font-size:14px;line-height:2;">
            <li>Download {BRAND['name']}: <a href="{download_url}" style="color:#2f81f7;">{download_url}</a></li>
            <li>On first launch, select <strong style="color:#e6edf3;">"Organisation Managed"</strong></li>
            <li>Paste your API key above</li>
            <li>The machine enrolls automatically — all PRO features activate instantly</li>
          </ol>
          <div style="background:#1c2333;border-radius:8px;padding:16px;margin:24px 0;">
            <p style="margin:0;font-size:13px;color:#8b949e;">
              IT Admin Portal:<br>
              <a href="{APP_BASE_URL}/tenant/login" style="color:#2f81f7;font-size:14px;">
                {APP_BASE_URL}/tenant/login
              </a>
            </p>
          </div>
          <p style="color:#8b949e;font-size:13px;">
            Lost this key? Reply to this email and we'll resend it.<br>
            Questions? <a href="mailto:{BRAND['support_email']}" style="color:#2f81f7;">{BRAND['support_email']}</a>
          </p>
        </div>
        <div style="background:#0d1117;padding:16px 32px;text-align:center;">
          <p style="margin:0;font-size:12px;color:#484f58;">{BRAND['name']} Enterprise · {BRAND['company']}</p>
        </div>
      </div>
    </div>"""

    try:
        ok = _send_gmail(org_email, f"{BRAND['name']} Enterprise — Your API Key & Setup Instructions", html)
        if ok:
            print(f"[TENANT] Welcome email sent to {org_email}")
        else:
            print(f"[TENANT] API key was: {api_key}")
    except Exception as e:
        print(f"[TENANT] Welcome email failed: {e}")
        print(f"[TENANT] API key was: {api_key}")

# ══════════════════════════════════════════════════════════════════════════════
# AUTH PAGES
# ══════════════════════════════════════════════════════════════════════════════
@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str = ""):
    return templates.TemplateResponse(request, "auth_login.html", {
        "brand":   BRAND,
        "error":   error,
    })

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
    agent_version: str = "2.5.0"
    os_info:       str = ""
    sigma_rules:   list = []

@app.post("/api/heartbeat")
@limiter.limit("200/minute")
async def receive_heartbeat(request: Request, data: Heartbeat):
    tenant_key = request.headers.get("x-tenant-key", "")
    api_key    = request.headers.get("x-api-key",    "")
 
    if tenant_key:
        tenant = _get_tenant_by_api_key(tenant_key)
        if not tenant:
            raise HTTPException(status_code=401, detail="Invalid tenant key")

        if DATABASE_URL:
            try:
                conn = get_db(); cur = conn.cursor()
                cur.execute(
                    "SELECT id FROM tenant_agents "
                    "WHERE tenant_id=%s AND machine_id=%s",
                    (tenant["id"], data.machine_id))
                already_registered = cur.fetchone() is not None
 
                if not already_registered:
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
                        raise HTTPException(
                            status_code=402,
                            detail=(
                                f"Seat limit reached ({current_count}/{max_seats}). "
                                f"Contact your administrator to add more seats."
                            )
                        )
 
                cur.close(); conn.close()
 
            except HTTPException:
                raise
            except Exception as e:
                print(f"[SEAT] Check error (non-critical): {e}")
 
        if DATABASE_URL:
            try:
                conn = get_db(); cur = conn.cursor()
                cur.execute(
                    "UPDATE tenant_agents SET status='offline' "
                    "WHERE tenant_id=%s AND last_seen < NOW() - INTERVAL '35 seconds'",
                    (tenant["id"],))
 
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
                # --- NEW CODE: Store sigma rules count ---
                if agent_row:
                    agent_id = agent_row["id"]
                    sigma_count = len(data.sigma_rules)
                    cur.execute(
                        "UPDATE tenant_agents SET sigma_rule_count = %s WHERE id = %s",
                        (sigma_count, agent_id)
                    )
                # -----------------------------------------
                conn.commit(); cur.close(); conn.close()
 
            except Exception as e:
                print(f"[TENANT HB] DB error: {e}")
 
        cmd = commands.pop(data.machine_id, "NONE")
        return {
            "status":  "ok",
            "command": cmd,
            "tenant":  tenant["slug"],
            "tier":    tenant["plan"],
            "is_pro":  tenant["plan"] in ("pro", "business", "enterprise"),
        }
 
    if api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    # NEW CODE: Save the sigma_rule_count in the memory dict
    agents[data.machine_id] = {
        "hostname": data.hostname, "username": data.username,
        "tier": data.tier, "is_armed": data.is_armed,
        "last_seen": time.time(), "ip": request.client.host,
        "sigma_rule_count": len(data.sigma_rules)
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
        cur.execute(
            "SELECT id FROM tenant_agents "
            "WHERE tenant_id=%s AND machine_id=%s",
            (tenant["id"], data.machine_id))
        agent_row = cur.fetchone()
        agent_id  = agent_row["id"] if agent_row else None
 
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
 
    cfg = {}
    if cfg_row:
        if cfg_row.get("webhook_url"):
            cfg["webhook_url"] = cfg_row["webhook_url"]
        if cfg_row.get("alert_email"):
            cfg["admin_email"] = cfg_row["alert_email"]
        if cfg_row.get("verify_interval") and cfg_row["verify_interval"] > 0:
            cfg["verify_interval"] = cfg_row["verify_interval"]
        if cfg_row.get("max_vault_mb") and cfg_row["max_vault_mb"] > 0:
            cfg["vault_max_size_mb"] = cfg_row["max_vault_mb"]
        if cfg_row.get("allowed_exts"):
            exts = [e.strip() for e in cfg_row["allowed_exts"].split(",")
                    if e.strip().startswith(".")]
            if exts:
                cfg["vault_allowed_exts"] = exts
        if cfg_row.get("retention_days") and cfg_row["retention_days"] > 0:
            # Mirrors retention_manager.py keys
            cfg["retention_telemetry_days"]   = int(cfg_row["retention_days"])
            cfg["retention_forensics_days"]   = int(cfg_row["retention_days"]) * 2
            cfg["retention_log_history_days"] = int(cfg_row["retention_days"]) * 4

 
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
async def dashboard(request: Request, _: bool = Depends(verify_session)):
    _maybe_sweep_offline()
    now = time.time()
    agents_ctx = {}
    for mid, info in agents.items():
        agents_ctx[mid] = {
            **info,
            "online":        (now - info["last_seen"]) < 30,
            "last_seen_fmt": _fmt_ts(info.get("last_seen")),
        }
    return templates.TemplateResponse(request, "dashboard.html", {
        "brand":   BRAND,
        "agents":  agents_ctx,
    })


# ── Super Admin: DB migration helper ─────────────────────────────────────────
@app.get("/super/db-migrate")
async def super_db_migrate(api_key: str = ""):
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
    for r in rows:
        if r.get("created_at"):
            r["created_at"] = r["created_at"].isoformat()
    return {"count": len(rows), "tenants": rows}
 
# ── Super Admin: Create tenant ────────────────────────────────────────────────
class CreateTenantBody(BaseModel):
    name:          str
    slug:          str
    contact_email: str
    plan:          str  = "business"
    max_agents:    int  = 10
    notes:         str  = ""
    admin_email:   str  = ""
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
        cur.execute("""
            INSERT INTO tenants
                (id, name, slug, api_key, plan, max_agents, contact_email, notes)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        """, (tenant_id, body.name.strip(), slug, tenant_key,
              body.plan, body.max_agents,
              body.contact_email.strip(), body.notes.strip()))
        cur.execute(
            "INSERT INTO tenant_config (tenant_id) VALUES (%s)",
            (tenant_id,))
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


@app.post("/super/tenants/{tenant_id}/resend-welcome-email")
async def super_resend_welcome_email(tenant_id: str, _: bool = Depends(verify_session)):
    if not DATABASE_URL:
        raise HTTPException(status_code=500, detail="No database")
    conn = get_db(); cur = conn.cursor()
    cur.execute(
        "SELECT name, contact_email, api_key, max_agents, plan FROM tenants WHERE id=%s",
        (tenant_id,))
    row = cur.fetchone()
    cur.close(); conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Tenant not found")
    
    threading.Thread(
        target=send_tenant_welcome_email,
        args=(row["contact_email"], row["name"], row["api_key"],
              row["max_agents"], row["plan"]),
        daemon=True
    ).start()
    return RedirectResponse(
        f"/super/tenant-detail?id={tenant_id}&msg=email_sent",
        status_code=303)
 
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
        if r.get("created_at"): r["created_at"] = r["created_at"].isoformat()
    return {"count": len(rows), "alerts": rows}
 
# ── Super Admin: Visual Dashboard ────────────────────────────────────────────
@app.get("/super/dashboard", response_class=HTMLResponse)
async def super_dashboard(request: Request, _: bool = Depends(verify_session),
                           new_key: str = "", msg: str = ""):
    _maybe_sweep_offline()
    if not DATABASE_URL:
        return HTMLResponse("<h1>No database configured</h1>")

    conn = get_db(); cur = conn.cursor()
    cur.execute("""
        SELECT t.*,
          (SELECT COUNT(*) FROM tenant_agents a WHERE a.tenant_id=t.id)   AS agent_count,
          (SELECT COUNT(*) FROM tenant_agents a WHERE a.tenant_id=t.id
            AND a.status='online')                                          AS online_count,
          (SELECT COUNT(*) FROM tenant_alerts al WHERE al.tenant_id=t.id
            AND al.acknowledged=FALSE)                                      AS unacked_alerts
        FROM tenants t ORDER BY t.created_at DESC
    """)
    tenants_rows = cur.fetchall()
    cur.execute("SELECT COUNT(*) FROM tenants WHERE active=TRUE")
    total_tenants = cur.fetchone()["count"]
    cur.execute("SELECT COUNT(*) FROM tenant_agents WHERE status='online'")
    total_online = cur.fetchone()["count"]
    cur.execute("SELECT COUNT(*) FROM tenant_agents")
    total_agents = cur.fetchone()["count"]
    cur.execute("SELECT COUNT(*) FROM tenant_alerts WHERE acknowledged=FALSE AND severity='CRITICAL'")
    total_critical = cur.fetchone()["count"]
    cur.execute("""
        SELECT * FROM enterprise_leads ORDER BY created_at DESC LIMIT 20
    """)
    enterprise_leads_rows = cur.fetchall()
    cur.close(); conn.close()

    tenants_ctx = []
    for t in tenants_rows:
        tenants_ctx.append({
            **dict(t),
            "created_fmt": t["created_at"].strftime("%Y-%m-%d") if t.get("created_at") else "—",
        })

    return templates.TemplateResponse(request, "super_dashboard.html", {
        "brand":          BRAND,
        "tenants":        tenants_ctx,
        "total_tenants":  total_tenants,
        "total_online":   total_online,
        "total_agents":   total_agents,
        "total_critical": total_critical,
        "new_key":        new_key,
        "admin_api_key":  ADMIN_API_KEY,
        "enterprise_leads":   [dict(r) for r in enterprise_leads_rows],
    })

# ── Form handler for tenant creation from dashboard ───────────────────────────
@app.post("/super/tenants-form")
async def super_create_tenant_form(
    request: Request,
    name:           str = Form(...),
    contact_email:  str = Form(...),
    slug:           str = Form(""),          # optional – auto-derived from name
    plan:           str = Form("business"),
    max_agents:     int = Form(10),
    notes:          str = Form(""),
    admin_email:    str = Form(""),
    admin_password: str = Form(""),
    password:       str = Form(""),          # form field is named `password`
    _: bool = Depends(verify_session)
):
    tenant_id  = str(uuid.uuid4())
    tenant_key = _gen_tenant_api_key()

    # ── Accept either `admin_password` or `password` from the form ────────────
    if not admin_password and password:
        admin_password = password
    # ── If admin_email not provided, fall back to contact_email ───────────────
    if not admin_email:
        admin_email = contact_email

    # ── Auto-generate a URL-safe slug from the org name if none given ─────────
    import re as _re
    raw_slug = (slug or name).lower().strip()
    slug_clean = _re.sub(r"[^a-z0-9]+", "-", raw_slug).strip("-") or "org"

    # Ensure uniqueness — append short suffix if slug already exists
    try:
        _c = get_db(); _cur = _c.cursor()
        _cur.execute("SELECT 1 FROM tenants WHERE slug=%s", (slug_clean,))
        if _cur.fetchone():
            slug_clean = f"{slug_clean}-{tenant_id[:6]}"
        _cur.close(); _c.close()
    except Exception:
        pass
 
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

    threading.Thread(
        target=send_tenant_welcome_email,
        args=(contact_email.strip(), name.strip(), tenant_key, max_agents, plan),
        daemon=True
    ).start()
 
    return RedirectResponse(
        f"/super/tenant-detail?id={tenant_id}&new_key={tenant_key}",
        status_code=303)
 
# ── Tenant detail page (super admin view) ─────────────────────────────────────
@app.get("/super/tenant-detail", response_class=HTMLResponse)
async def super_tenant_detail_page(
    request: Request,
    id: str = "",
    _:  bool = Depends(verify_session),
    msg: str = "",
    new_key: str = "",
):
    if not DATABASE_URL or not id:
        return RedirectResponse("/super/dashboard", status_code=302)

    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT * FROM tenants WHERE id=%s", (id,))
    tenant = cur.fetchone()
    if not tenant:
        cur.close(); conn.close()
        return RedirectResponse("/super/dashboard", status_code=302)

    cur.execute("""
        SELECT machine_id, hostname, ip_address, username, os_info,
               status, is_armed, last_seen, tier, agent_version
        FROM tenant_agents WHERE tenant_id=%s ORDER BY status DESC, last_seen DESC
    """, (id,))
    agents_list = cur.fetchall()

    cur.execute("""
        SELECT id, severity, event_type, hostname, message, file_path,
               acknowledged, created_at
        FROM tenant_alerts WHERE tenant_id=%s
        ORDER BY created_at DESC LIMIT 100
    """, (id,))
    alert_list = cur.fetchall()

    cur.execute("""
        SELECT id, email, role, created_at FROM tenant_users WHERE tenant_id=%s
    """, (id,))
    users_list = cur.fetchall()
    cur.close(); conn.close()

    agents_ctx = [{**dict(a), "last_seen_fmt": a["last_seen"].strftime("%Y-%m-%d %H:%M") if a.get("last_seen") else "—"} for a in agents_list]
    alerts_ctx = [{**dict(al), "created_fmt": al["created_at"].strftime("%Y-%m-%d %H:%M") if al.get("created_at") else "—"} for al in alert_list]
    users_ctx  = [{**dict(u), "created_fmt": u["created_at"].strftime("%Y-%m-%d") if u.get("created_at") else "—"} for u in users_list]
    online_count = sum(1 for a in agents_ctx if a["status"] == "online")

    return templates.TemplateResponse(request, "super_tenant.html", {
        "brand":         BRAND,
        "tenant":        dict(tenant),
        "agents":        agents_ctx,
        "alerts":        alerts_ctx,
        "users":         users_ctx,
        "online_count":  online_count,
        "alert_count":   len(alerts_ctx),
        "admin_api_key": ADMIN_API_KEY,
        "new_key":       new_key,
        "msg":           msg,
    })


@app.get("/tenant/login", response_class=HTMLResponse)
async def tenant_login_page(request: Request, error: str = "", success: str = ""):
    import secrets as _secrets
    csrf = _secrets.token_hex(16)
    return templates.TemplateResponse(request, "tenant_login.html", {
        "brand":        BRAND,
        "error":        error,
        "success":      success,
        "csrf_token":   csrf,
        "show_totp":    False,
        "prefill_email": "",
    })

@app.post("/tenant/login")
async def tenant_login_post(
    request:    Request,
    email:      str = Form(...),
    password:   str = Form(...),
    totp_code:  str = Form(""),
    csrf_token: str = Form(""),
):
    # ── CSRF check ──────────────────────────────────────────────────────────
    # Soft-fail if itsdangerous not installed (logs warning)
    if not _validate_csrf_token(csrf_token):
        print(f"[CSRF] Invalid token on login from {request.client.host}")
        # Don't hard-fail in this phase; just log. Uncomment to enforce:
        # return RedirectResponse("/tenant/login?error=Invalid+form+token", 302)

    if not DATABASE_URL:
        return RedirectResponse("/tenant/login?error=Server+not+configured", 302)

    clean_email = email.strip().lower()

    # ── Account lockout ──────────────────────────────────────────────────────
    if _is_account_locked(clean_email):
        return RedirectResponse(
            "/tenant/login?error=Account+temporarily+locked.+Try+again+in+5+minutes.", 302)

    # ── DB lookup ────────────────────────────────────────────────────────────
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("""
            SELECT u.*, t.id as tenant_id, t.name as tenant_name,
                   t.active as tenant_active
            FROM tenant_users u
            JOIN tenants t ON t.id = u.tenant_id
            WHERE u.email = %s
        """, (clean_email,))
        user = cur.fetchone()
        cur.close(); conn.close()
    except Exception as e:
        print(f"[TENANT LOGIN] DB error: {e}")
        return RedirectResponse("/tenant/login?error=Server+error", 302)

    if not user:
        _record_failed_login(clean_email)
        return RedirectResponse("/tenant/login?error=Invalid+credentials", 302)

    if not user["tenant_active"]:
        return RedirectResponse(
            "/tenant/login?error=Your+organisation+account+is+suspended", 302)

    # ── Password verify + bcrypt auto-upgrade ─────────────────────────────────
    def _upgrade_pw(new_hash: str):
        try:
            uc = get_db(); ucur = uc.cursor()
            ucur.execute(
                "UPDATE tenant_users SET password_hash=%s WHERE email=%s",
                (new_hash, clean_email))
            uc.commit(); ucur.close(); uc.close()
        except Exception as ue:
            print(f"[AUTH] pw upgrade DB error: {ue}")

    if not _verify_and_upgrade_password(password, user["password_hash"], _upgrade_pw):
        _record_failed_login(clean_email)
        return RedirectResponse("/tenant/login?error=Invalid+credentials", 302)

    # ── TOTP 2FA (if user has it enabled) ────────────────────────────────────
    totp_secret = user.get("totp_secret", "")
    if totp_secret:
        if not totp_code:
            # Correct password but 2FA not submitted — show 2FA field
            import secrets as _s
            csrf = _s.token_hex(16)
            return templates.TemplateResponse(request, "tenant_login.html", {
                "brand":         BRAND,
                "error":         "",
                "success":       "",
                "csrf_token":    csrf,
                "show_totp":     True,
                "prefill_email": clean_email,
            })
        if not _verify_totp(totp_secret, totp_code):
            _record_failed_login(clean_email)
            return RedirectResponse("/tenant/login?error=Invalid+2FA+code", 302)

    # ── Success ───────────────────────────────────────────────────────────────
    _clear_failed_logins(clean_email)
    token = _create_tenant_session(user["tenant_id"], user["email"], user["role"])

    resp = RedirectResponse("/tenant/dashboard", status_code=302)
    resp.set_cookie(
        "fms_tenant_session", token,
        httponly=True,
        secure=True,          # requires HTTPS (Render provides this)
        samesite="strict",
        max_age=_TENANT_SESSION_TTL,
    )
    return resp
 
# ── Tenant Admin: Logout ──────────────────────────────────────────────────────
@app.get("/tenant/logout")
async def tenant_logout(request: Request):
    token = request.cookies.get("fms_tenant_session", "")
    _tenant_sessions.pop(token, None)
    resp = RedirectResponse("/tenant/login", status_code=302)
    resp.delete_cookie("fms_tenant_session")
    return resp


# ══════════════════════════════════════════════════════════════════════════════
# TENANT ADMIN PASSWORD RESET (FORGOT PASSWORD)
# ══════════════════════════════════════════════════════════════════════════════

_tenant_reset_otps = {}   # {email: {otp, expires, tenant_id}}

def _send_tenant_reset_otp(email: str, otp: str):
    if not GMAIL_USER:
        print(f"[RESET] No GMAIL_USER set. OTP for {email}: {otp}")
        return
    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:480px;margin:0 auto;
                background:#0d1117;color:#e6edf3;padding:32px;border-radius:12px;">
      <h2 style="color:#2f81f7;">🔐 {BRAND['name']} IT Admin – Password Reset</h2>
      <p style="color:#8b949e;">Use this code to reset your IT admin password:</p>
      <div style="background:#161b22;border:1px solid #30363d;border-radius:8px;
                  padding:24px;text-align:center;margin:20px 0;">
        <span style="font-size:36px;font-weight:700;color:#2f81f7;
                     letter-spacing:8px;font-family:Courier,monospace;">{otp}</span>
      </div>
      <p style="color:#8b949e;font-size:13px;">
        This code expires in 5 minutes. If you didn't request this, ignore this email.
      </p>
    </div>
    """
    try:
        ok = _send_gmail(email, f"{BRAND['name']} – IT Admin Password Reset Code", html)
        if ok:
            print(f"[RESET] OTP sent to {email}")
        else:
            print(f"[RESET] OTP was: {otp}")
    except Exception as e:
        print(f"[RESET] Email failed: {e}")


@app.get("/tenant/forgot-password", response_class=HTMLResponse)
async def tenant_forgot_password_page(request: Request, error: str = "", success: str = ""):
    return templates.TemplateResponse(request, "tenant_forgot.html", {
        "brand":   BRAND,
        "error":   error,
        "success": success,
    })

@app.post("/tenant/forgot-password")
async def tenant_forgot_password_submit(email: str = Form(...)):
    email = email.strip().lower()
    if not DATABASE_URL:
        return RedirectResponse("/tenant/forgot-password?error=Server+not+configured", 302)

    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT id, tenant_id FROM tenant_users WHERE email = %s", (email,))
    row = cur.fetchone()
    cur.close(); conn.close()

    if row:
        import random, time
        otp = str(random.randint(100000, 999999))
        _tenant_reset_otps[email] = {
            "otp":       otp,
            "expires":   time.time() + 300,
            "tenant_id": row["tenant_id"],
        }
        threading.Thread(target=_send_tenant_reset_otp, args=(email, otp), daemon=True).start()

    return RedirectResponse("/tenant/forgot-password?success=If+that+email+is+registered,+a+reset+code+has+been+sent.", 302)


@app.get("/tenant/reset-password", response_class=HTMLResponse)
async def tenant_reset_password_page(request: Request, email: str = "", error: str = ""):
    return templates.TemplateResponse(request, "tenant_reset.html", {
        "brand":   BRAND,
        "email":   email,
        "error":   error,
    })

@app.post("/tenant/reset-password")
async def tenant_reset_password_submit(
    email: str = Form(...),
    otp: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...)
):
    email = email.strip().lower()
    record = _tenant_reset_otps.get(email)

    if not record:
        return RedirectResponse(f"/tenant/reset-password?email={email}&error=No+reset+request+found", 302)

    if time.time() > record["expires"]:
        del _tenant_reset_otps[email]
        return RedirectResponse(f"/tenant/reset-password?email={email}&error=Code+expired", 302)

    if not secrets.compare_digest(record["otp"], otp):
        return RedirectResponse(f"/tenant/reset-password?email={email}&error=Incorrect+code", 302)

    if new_password != confirm_password or len(new_password) < 8:
        return RedirectResponse(f"/tenant/reset-password?email={email}&error=Passwords+must+match+and+be+at+least+8+chars", 302)

    pw_hash = _hash_password(new_password)
    conn = get_db(); cur = conn.cursor()
    cur.execute("UPDATE tenant_users SET password_hash = %s WHERE email = %s", (pw_hash, email))
    conn.commit(); cur.close(); conn.close()

    del _tenant_reset_otps[email]
    return RedirectResponse("/tenant/login?error=Password+reset+successfully.+Please+log+in.", 302)
 
# ── Tenant Admin: Main Dashboard ──────────────────────────────────────────────
@app.get("/tenant/dashboard", response_class=HTMLResponse)
async def tenant_dashboard(request: Request, config_saved: str = ""):
    _maybe_sweep_offline()
    session = _get_tenant_session(request)
    if not session:
        return RedirectResponse("/tenant/login", status_code=302)

    tenant_id = session["tenant_id"]
    if not DATABASE_URL:
        return HTMLResponse("<h1>No database</h1>")

    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT * FROM tenants WHERE id=%s", (tenant_id,))
    tenant = cur.fetchone()
    if not tenant:
        cur.close(); conn.close()
        return RedirectResponse("/tenant/login", status_code=302)

    cur.execute(
        "UPDATE tenant_agents SET status='offline' "
        "WHERE tenant_id=%s AND last_seen < NOW() - INTERVAL '35 seconds'",
        (tenant_id,))
    conn.commit()

    cur.execute("""
        SELECT machine_id, hostname, ip_address, username, tier,
               is_armed, status, last_seen, agent_version, sigma_rule_count 
        FROM tenant_agents WHERE tenant_id=%s
        ORDER BY status DESC, last_seen DESC
    """, (tenant_id,))
    agents_list = cur.fetchall()

    cur.execute("""
        SELECT id, severity, event_type, message, hostname,
               file_path, created_at, acknowledged
        FROM tenant_alerts WHERE tenant_id=%s
        ORDER BY acknowledged ASC, created_at DESC LIMIT 50
    """, (tenant_id,))
    alert_list = cur.fetchall()

    cur.execute("SELECT * FROM tenant_config WHERE tenant_id=%s", (tenant_id,))
    config_row = cur.fetchone() or {}
    cur.close(); conn.close()

    stats        = _get_tenant_stats(tenant_id)
    online_count = sum(1 for a in agents_list if a["status"] == "online")
    armed_count  = sum(1 for a in agents_list if a["is_armed"])

    agents_ctx = [{**dict(a), "last_seen_fmt": a["last_seen"].strftime("%H:%M %d/%m") if a.get("last_seen") else "—"} for a in agents_list]
    alerts_ctx = [{**dict(al), "created_fmt": al["created_at"].strftime("%Y-%m-%d %H:%M") if al.get("created_at") else "—"} for al in alert_list]

    return templates.TemplateResponse(request, "tenant_dashboard.html", {
        "brand":         BRAND,
        "tenant":        dict(tenant),
        "session_email": session["email"],
        "agents":        agents_ctx,
        "alerts":        alerts_ctx,
        "config":        dict(config_row) if config_row else {},
        "stats":         stats,
        "online_count":  online_count,
        "armed_count":   armed_count,
        "config_saved":  config_saved == "1",
    })


@app.post("/tenant/config")
async def tenant_save_config(
    request:         Request,
    alert_email:     str = Form(""),
    webhook_url:     str = Form(""),
    verify_interval: int = Form(60),
    max_vault_mb:    int = Form(10),
    allowed_exts:    str = Form(".txt,.json,.py,.html,.js,.css"),
    retention_days:  int = Form(90),
):
    session = _get_tenant_session(request)
    if not session:
        return RedirectResponse("/tenant/login", status_code=302)
    if not DATABASE_URL:
        return RedirectResponse("/tenant/dashboard", status_code=302)

    # Clamp all inputs (defense-in-depth — HTML attrs are advisory only)
    verify_interval = max(10,  min(verify_interval, 86400))
    max_vault_mb    = max(1,   min(max_vault_mb,    500))
    retention_days  = max(30,  min(retention_days,  730))   # GDPR/PCI-DSS bounds

    conn = get_db(); cur = conn.cursor()
    cur.execute("""
        INSERT INTO tenant_config
            (tenant_id, alert_email, webhook_url,
             verify_interval, max_vault_mb, allowed_exts,
             retention_days, updated_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s, NOW())
        ON CONFLICT (tenant_id) DO UPDATE SET
            alert_email     = EXCLUDED.alert_email,
            webhook_url     = EXCLUDED.webhook_url,
            verify_interval = EXCLUDED.verify_interval,
            max_vault_mb    = EXCLUDED.max_vault_mb,
            allowed_exts    = EXCLUDED.allowed_exts,
            retention_days  = EXCLUDED.retention_days,
            updated_at      = NOW()
    """, (session["tenant_id"],
          alert_email.strip(), webhook_url.strip(),
          verify_interval, max_vault_mb, allowed_exts.strip(),
          retention_days))
    conn.commit(); cur.close(); conn.close()

    print(f"[TENANT CONFIG] tenant={session['tenant_id'][:8]}… "
          f"retention={retention_days}d verify={verify_interval}s")
    return RedirectResponse("/tenant/dashboard?config_saved=1", status_code=302)


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

@app.get("/tenant/rules", response_class=HTMLResponse)
async def tenant_rules_page(request: Request):
    # 1. Use your custom tenant session manager
    session = _get_tenant_session(request)
    if not session:
        return RedirectResponse("/tenant/login", status_code=302)
        
    tenant_id = session["tenant_id"]
    if not DATABASE_URL:
        return HTMLResponse("<h1>Database not configured</h1>")

    # 2. Fetch agents using PostgreSQL syntax
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """SELECT hostname, sigma_rule_count, last_seen, status
           FROM tenant_agents
           WHERE tenant_id = %s
           ORDER BY sigma_rule_count DESC""",
        (tenant_id,)
    )
    agents = cur.fetchall()
    cur.close()
    conn.close()
        
    # 3. Calculate Stats
    total_agents  = len(agents)
    agents_online = sum(1 for a in agents if a["status"] == "online")
    avg_rules     = (
        sum((a["sigma_rule_count"] or 0) for a in agents) // total_agents
        if total_agents else 0
    )
        
    # 4. Generate dynamic rows
    agent_rows = ""
    for a in agents:
        is_online = (a["status"] == "online")
        last_seen_str = a['last_seen'].strftime("%Y-%m-%d %H:%M") if a['last_seen'] else 'Never'
        rule_count = a['sigma_rule_count'] or 0
        agent_rows += f"""
        <tr>
            <td>{a['hostname']}</td>
            <td class="{'online' if is_online else 'offline'}">
                {'● Online' if is_online else '○ Offline'}
            </td>
            <td><span class="rules-badge">{rule_count} rules</span></td>
            <td style="color:#8b949e">{last_seen_str}</td>
        </tr>"""

    # 5. Render HTML
    html = f"""
    <html>
    <head>
        <title>Detection Rules — FMSecure</title>
        <style>
            body {{ font-family: 'Segoe UI', sans-serif; background: #0d1117; color: #e6edf3; margin: 0; padding: 24px; }}
            h1 {{ color: #2f81f7; }} 
            .stat {{ display: inline-block; background: #161b22; border: 1px solid #30363d; 
                     border-radius: 8px; padding: 16px 28px; margin: 8px; text-align: center; }}
            .stat-num {{ font-size: 32px; font-weight: bold; color: #3fb950; }}
            .stat-lbl {{ font-size: 12px; color: #8b949e; margin-top: 4px; }}
            table {{ width: 100%; border-collapse: collapse; margin-top: 24px; }}
            th {{ background: #161b22; color: #8b949e; padding: 10px 14px; text-align: left; font-size: 12px; text-transform: uppercase; }}
            td {{ padding: 10px 14px; border-bottom: 1px solid #21262d; font-size: 14px; }}
            .online {{ color: #3fb950; }} .offline {{ color: #f85149; }}
            .rules-badge {{ background: #1a4b8c; color: #58a6ff; padding: 2px 10px; border-radius: 12px; font-size: 12px; }}
            a {{ color: #2f81f7; text-decoration: none; }}
        </style>
    </head>
    <body>
        <h1>🛡 Detection Coverage</h1>
        <a href="/tenant/dashboard">← Back to Dashboard</a>
        
        <div style="margin: 20px 0;">
            <div class="stat">
                <div class="stat-num">{total_agents}</div>
                <div class="stat-lbl">Total Endpoints</div>
            </div>
            <div class="stat">
                <div class="stat-num" style="color:#2f81f7">{agents_online}</div>
                <div class="stat-lbl">Online Now</div>
            </div>
            <div class="stat">
                <div class="stat-num">{avg_rules}</div>
                <div class="stat-lbl">Avg Rules / Endpoint</div>
            </div>
        </div>
        <table>
            <tr>
                <th>Endpoint</th>
                <th>Status</th>
                <th>Active Rules</th>
                <th>Last Seen</th>
            </tr>
            {agent_rows}
        </table>
        <div style="margin-top:32px; padding:16px; background:#161b22; border-radius:8px; border:1px solid #30363d;">
            <h3 style="color:#d29922; margin-top:0;">📋 Active Detection Rules</h3>
            <p style="color:#8b949e; font-size:13px;">
                Rules are loaded from <code>core/sigma_rules/*.yml</code> on each agent.<br>
                To add new detection coverage: add a .yml file and agents will pick it up on next restart.
            </p>
            <table>
                <tr>
                    <th>Rule Name</th><th>MITRE Technique</th><th>Severity</th>
                </tr>
                <tr><td>Ransomware Burst Detected</td><td>T1486 — Data Encrypted for Impact</td><td style="color:#f85149">CRITICAL</td></tr>
                <tr><td>LOLBin Process Attribution</td><td>T1059 — Command and Scripting Interpreter</td><td style="color:#f0883e">HIGH</td></tr>
                <tr><td>File Created in Startup Folder</td><td>T1547.001 — Registry Run Keys / Startup</td><td style="color:#f0883e">HIGH</td></tr>
                <tr><td>Mass File Deletion</td><td>T1485 — Data Destruction</td><td style="color:#f0883e">HIGH</td></tr>
                <tr><td>Honeypot File Accessed</td><td>T1083 — File and Directory Discovery</td><td style="color:#f85149">CRITICAL</td></tr>
            </table>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(html)

# ══════════════════════════════════════════════════════════════════════════════
# PRODUCT LANDING PAGE
# ══════════════════════════════════════════════════════════════════════════════
@app.get("/", response_class=HTMLResponse)
async def landing_page_root(request: Request):
    return templates.TemplateResponse(request, "index.html", {
        "brand":   BRAND,
    })

# ══════════════════════════════════════════════════════════════════════════════
# PRODUCT PAGES
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/download", response_class=HTMLResponse)
async def download_page(request: Request):
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute(
            "SELECT version, release_notes, download_url "
            "FROM versions WHERE is_current = TRUE "
            "ORDER BY published_at DESC LIMIT 1")
        row = cur.fetchone()
        cur.close(); conn.close()
        version    = row["version"]      if row else "2.5.0"
        notes      = row["release_notes"] if row else ""
        direct_url = (f"https://drive.google.com/uc?export=download&id={DRIVE_FILE_ID}"
                      if DRIVE_FILE_ID else "#")
    except Exception:
        version, notes, direct_url = "2.5.0", "", "#"

    return templates.TemplateResponse(request, "download.html", {
        "brand":      BRAND,
        "version":    version,
        "notes":      notes,
        "direct_url": direct_url,
    })


@app.get("/changelog", response_class=HTMLResponse)
async def changelog_page():
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
<html lang="en"><head>{standard_head("Changelog")}
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
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
  <a class="logo" href="/" style="display: flex; align-items: center; gap: 8px;">
  <img src="{BRAND['logo_png']}" alt="Logo" height="28">
  {BRAND['name']}
</a>
  <a href="/">Home</a>
  <a href="/download">Download</a>
  <a href="/login" style="margin-left:auto;color:#2f81f7">Admin →</a>
</nav>
<div class="container">
  <h1>Changelog</h1>
  <p class="sub">Every release, every improvement — all in one place.</p>
  {entries}
</div>
<footer>{BRAND['name']} · {BRAND['tagline']} · © {BRAND['copyright_year']}</footer>
</body></html>"""

@app.get("/home", response_class=HTMLResponse)
async def landing_page_redirect():
    """Legacy /home URL — redirect to /"""
    return RedirectResponse(url="/", status_code=301)


@app.get("/pricing", response_class=HTMLResponse)
async def pricing_page(request: Request):
    return templates.TemplateResponse(request, "pricing.html", {
        "brand":      BRAND,
        "rzp_key_id": RZP_KEY_ID,
        "pricing":    PRICING_DISPLAY,
    })

def _notify_super_admin_of_lead(company: str, name: str, email: str, seats: str, message: str):
    """Email the super-admin when a new enterprise inquiry lands."""
    if not SENDGRID_API_KEY:
        print(f"[LEAD] New enterprise lead — {company} / {name} / {email} / {seats} seats")
        return
    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:520px;background:#0d1117;color:#e6edf3;
                padding:28px;border-radius:10px;">
      <h2 style="color:#2f81f7;margin-top:0">🏢 New Enterprise Lead</h2>
      <table style="border-collapse:collapse;width:100%;font-size:14px;">
        <tr><td style="color:#8b949e;padding:6px 0;width:120px;">Company</td>
            <td style="color:#e6edf3;font-weight:600;">{company}</td></tr>
        <tr><td style="color:#8b949e;padding:6px 0;">Contact</td>
            <td style="color:#e6edf3;">{name}</td></tr>
        <tr><td style="color:#8b949e;padding:6px 0;">Email</td>
            <td><a href="mailto:{email}" style="color:#2f81f7;">{email}</a></td></tr>
        <tr><td style="color:#8b949e;padding:6px 0;">Seats</td>
            <td style="color:#e6edf3;">{seats}</td></tr>
        <tr><td style="color:#8b949e;padding:6px 0;vertical-align:top;">Message</td>
            <td style="color:#e6edf3;">{message or "—"}</td></tr>
      </table>
      <p style="margin-top:20px;">
        <a href="{APP_BASE_URL}/super/dashboard" style="background:#2f81f7;color:#fff;
           padding:10px 20px;border-radius:6px;text-decoration:none;font-weight:600;">
          Open Super Dashboard →
        </a>
      </p>
      <p style="color:#484f58;font-size:12px;margin-top:20px;">
        To convert this lead: create a tenant in the super dashboard using {email} as the contact email.
        They will automatically receive their API key and welcome email.
      </p>
    </div>"""
    try:
        ok = _send_gmail(SUPER_ADMIN_EMAIL, f"[{BRAND['name']}] New Enterprise Lead — {company}", html)
        if ok:
            print(f"[LEAD] Admin notified")
        else:
            print(f"[LEAD] Admin notification skipped (no Gmail config)")
    except Exception as e:
        print(f"[LEAD] Admin notification failed: {e}")


def _send_sales_acknowledgment(email: str, name: str, company: str):
    """No-op — we do NOT auto-email the lead to prevent spam abuse.
    The success page already tells them we'll respond within 24 hours."""
    print(f"[LEAD] Acknowledgment suppressed (anti-flood) for {name} <{email}> / {company}")

@app.get("/enterprise", response_class=HTMLResponse)
async def enterprise_sales_page(error: str = "", success: bool = False):
    if success:
        return f"""
        <!DOCTYPE html><html><head>{standard_head("Request Received")}
        <style>body{{background:#0d1117;color:#e6edf3;font-family:system-ui;display:flex;align-items:center;justify-content:center;min-height:100vh}}
        .card{{background:#161b22;border:1px solid #30363d;border-radius:16px;padding:48px;max-width:500px;text-align:center}}
        h2{{color:#3fb950;margin-bottom:8px}}p{{color:#8b949e}}</style></head><body>
        <div class="card"><h2>✅ Request Received!</h2><p>We'll send your API key and setup instructions within 24 hours.</p><p>Check your email (including spam).</p></div></body></html>"""
    err_div = f'<p style="color:#f85149;font-size:13px;margin-top:12px">{error}</p>' if error else ""
    return f"""
    <!DOCTYPE html><html><head>{standard_head("Enterprise")}
    <style>
      *{{box-sizing:border-box;margin:0;padding:0}}
      body{{font-family:'Segoe UI',Arial;background:#0d1117;display:flex;flex-direction:column;align-items:center;min-height:100vh;padding:40px 20px;color:#e6edf3}}
      .hero{{text-align:center;margin-bottom:40px}}
      .hero h1{{font-size:32px}} .hero p{{color:#8b949e}}
      .plans{{display:flex;gap:20px;margin-bottom:40px;flex-wrap:wrap;justify-content:center}}
      .plan{{background:#161b22;border:1px solid #30363d;border-radius:12px;padding:28px;width:220px;text-align:center}}
      .plan.featured{{border-color:#2f81f7}}
      .plan h3{{font-size:18px}} .plan .price{{font-size:28px;font-weight:bold;color:#2f81f7;margin:12px 0}}
      .plan .price span{{font-size:14px;color:#8b949e;font-weight:normal}}
      .plan ul{{list-style:none;text-align:left;font-size:13px;color:#8b949e}}
      .plan ul li::before{{content:"✓ ";color:#3fb950}}
      .badge{{background:#2f81f7;color:#fff;font-size:11px;padding:2px 8px;border-radius:4px;display:inline-block;margin-bottom:8px}}
      form{{background:#161b22;border:1px solid #30363d;border-radius:12px;padding:36px;width:100%;max-width:480px}}
      form h2{{font-size:20px;margin-bottom:6px}} form p{{color:#8b949e;font-size:13px;margin-bottom:24px}}
      label{{display:block;color:#8b949e;font-size:12px;text-transform:uppercase;letter-spacing:1px;margin-bottom:6px;margin-top:16px}}
      input,select,textarea{{width:100%;padding:12px;background:#0d1117;border:1px solid #30363d;border-radius:8px;color:#e6edf3;font-size:14px;outline:none}}
      input:focus,select:focus,textarea:focus{{border-color:#2f81f7}}
      textarea{{resize:vertical;min-height:80px}}
      button{{width:100%;padding:13px;background:#2f81f7;color:#fff;border:none;border-radius:8px;font-size:15px;font-weight:600;cursor:pointer;margin-top:20px}}
      button:hover{{background:#1f6feb}}
    </style></head><body>
    <div class="hero"><h1>🛡 {BRAND['name']} Enterprise</h1><p>EDR & File Integrity Monitoring for your organization</p></div>
    <div class="plans">
      <div class="plan"><h3>Starter</h3><div class="price">₹2,999 <span>/mo</span></div><ul><li>Up to 10 endpoints</li><li>IT Admin portal</li><li>All PRO features</li></ul></div>
      <div class="plan featured"><span class="badge">Most Popular</span><h3>Business</h3><div class="price">₹7,999 <span>/mo</span></div><ul><li>Up to 50 endpoints</li><li>Priority support</li><li>Threat intel engine</li></ul></div>
      <div class="plan"><h3>Enterprise</h3><div class="price">Custom</div><ul><li>Unlimited endpoints</li><li>Dedicated support</li><li>SLA guarantee</li></ul></div>
    </div>
    <form method="POST" action="/enterprise">
      <h2>Get Started</h2><p>Fill this form – we'll set up your account and email the API key within 24 hours.</p>
      <label>Company Name *</label><input type="text" name="company" placeholder="Acme Corp" required>
      <label>Your Name *</label><input type="text" name="name" placeholder="Rahul Sharma" required>
      <label>Business Email *</label><input type="email" name="email" placeholder="it@company.com" required>
      <label>Number of Endpoints</label>
      <select name="seats"><option value="5">Up to 5</option><option value="10" selected>Up to 10</option><option value="25">Up to 25</option><option value="50">Up to 50</option><option value="100">100+</option></select>
      <label>Anything else? (optional)</label><textarea name="message" placeholder="e.g. We need deployment by..."></textarea>
      <button type="submit">Request Enterprise Access →</button>
      {err_div}
    </form>
    </body></html>"""


# FIND:
@app.post("/enterprise")
async def enterprise_sales_submit(
    company: str = Form(...),
    name: str = Form(...),
    email: str = Form(...),
    seats: str = Form("10"),
    message: str = Form("")
):
    threading.Thread(target=_notify_super_admin_of_lead,
                     args=(company, name, email, seats, message), daemon=True).start()
    threading.Thread(target=_send_sales_acknowledgment,
                     args=(email, name, company), daemon=True).start()
    return RedirectResponse("/enterprise?success=1", 302)

# REPLACE WITH:
@app.post("/enterprise")
async def enterprise_sales_submit(
    company: str = Form(...),
    name: str = Form(...),
    email: str = Form(...),
    seats: str = Form("10"),
    message: str = Form("")
):
    # Save to DB
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("""
            INSERT INTO enterprise_leads (company, name, email, seats, message)
            VALUES (%s, %s, %s, %s, %s)
        """, (company.strip(), name.strip(), email.strip().lower(),
              seats.strip(), message.strip()))
        conn.commit(); cur.close(); conn.close()
    except Exception as e:
        print(f"[LEAD] DB save failed: {e}")

    # Notify admin (background thread so form returns instantly)
    threading.Thread(target=_notify_super_admin_of_lead,
                     args=(company, name, email, seats, message), daemon=True).start()
    threading.Thread(target=_send_sales_acknowledgment,
                     args=(email, name, company), daemon=True).start()
    return RedirectResponse("/enterprise?success=1", 302)

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
    expected=_hmac.new(RZP_KEY_SECRET.encode(),
        f"{body.razorpay_order_id}|{body.razorpay_payment_id}".encode(),
        hashlib.sha256).hexdigest()
    if not secrets.compare_digest(expected, body.razorpay_signature):
        print(f"[RZP] Sig mismatch {body.razorpay_order_id}")
        return JSONResponse({"success":False,"error":"Signature failed"},status_code=400)

    tier=body.tier.strip().lower(); email=body.email.strip().lower()
    payment_id=body.razorpay_payment_id; order_id=body.razorpay_order_id
    expires_iso=(datetime.now(timezone.utc)+timedelta(days=PLANS.get(tier,{}).get("days",31))).isoformat()
    license_key=_gen_key(tier,email,payment_id)
    try:
        _save_license(license_key,email,tier,payment_id,order_id,expires_iso)
    except Exception as e:
        print(f"[DB] Save error: {e}")
        return JSONResponse({"success":False,"error":"Database error"},status_code=500)

    try:
        conn=get_db();cur=conn.cursor()
        cur.execute("DELETE FROM pending_orders WHERE order_id=%s",(order_id,))
        conn.commit();cur.close();conn.close()
    except: pass

    threading.Thread(
        target=_send_license_email,
        args=(email, license_key, tier, expires_iso),
        daemon=True
    ).start()

    print(f"[PAYMENT] Generated key {license_key} for {email}")
    return {"success":True,"license_key":license_key,"tier":tier,"expires_at":expires_iso}

@app.get("/payment/success", response_class=HTMLResponse)
async def payment_success(request: Request, key: str = "", email: str = "", tier: str = ""):
    tier_label = PLANS.get(tier, {}).get("label", "PRO")
    return templates.TemplateResponse(request, "payment_success.html", {
        "brand":      BRAND,
        "key":        key,
        "email":      email,
        "tier":       tier,
        "tier_label": tier_label,
    })


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
# LICENSE TRANSFER
# ══════════════════════════════════════════════════════════════════════════════

class TransferRequestBody(BaseModel):
    license_key: str
    email:       str

class TransferConfirmBody(BaseModel):
    license_key:    str
    otp:            str
    new_machine_id: str

@app.post("/api/license/request_transfer")
async def request_transfer(req: TransferRequestBody):
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

    stored_email = (row["email"] or "").strip().lower()
    if not secrets.compare_digest(stored_email, email):
        return {"ok": False,
                "reason": "Email does not match the purchase record for this key."}

    if not row["active"]:
        return {"ok": False, "reason": "subscription_expired"}

    otp = str(random.randint(100000, 999999))
    _pending_transfers[key] = {
        "otp":     otp,
        "email":   email,
        "expires": time.time() + _TRANSFER_OTP_TTL,
    }

    def _send_transfer_otp():
        if not SENDGRID_API_KEY:
            print(f"[TRANSFER] No SENDGRID_API_KEY. OTP for {email}: {otp}")
            return
        html = f"""
        <div style="font-family:Arial,sans-serif;max-width:520px;margin:0 auto;
                    background:#0d1117;color:#e6edf3;padding:32px;border-radius:10px;">
          <h2 style="color:#2f81f7;margin-top:0">&#128273; {BRAND['name']} License Transfer</h2>
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
            {BRAND['name']} v2.0 &bull; {BRAND['tagline']}
          </p>
        </div>"""
        try:
            ok = _send_gmail(email, f"{BRAND['name']} — License Transfer Verification Code", html)
            if ok:
                print(f"[TRANSFER] OTP sent to {email}")
            else:
                print(f"[TRANSFER] OTP was: {otp}")
        except Exception as e:
            print(f"[TRANSFER] Email failed for {email}: {e}")
            print(f"[TRANSFER] OTP was: {otp}")

    threading.Thread(target=_send_transfer_otp, daemon=True).start()
    return {"ok": True}


@app.post("/api/license/confirm_transfer")
async def confirm_transfer(req: TransferConfirmBody):
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
    email = req.email.strip().lower()
    if not email or not DATABASE_URL:
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
        return {"ok": True}

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
async def licenses_page(request: Request, _: bool = Depends(verify_session)):
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT * FROM licenses ORDER BY created_at DESC LIMIT 500")
    rows = cur.fetchall(); cur.close(); conn.close()

    licenses_ctx = []
    for r in rows:
        expired   = _is_expired(r["expires_at"])
        is_active = not expired and r["active"]
        licenses_ctx.append({
            "license_key":  r["license_key"],
            "email":        r["email"],
            "tier":         r["tier"],
            "is_active":    is_active,
            "is_expired":   expired,
            "expires_fmt":  r["expires_at"].strftime("%Y-%m-%d") if r["expires_at"] else "—",
            "created_fmt":  r["created_at"].strftime("%Y-%m-%d") if r.get("created_at") else "—",
            "machine_id":   r.get("machine_id"),
        })

    active_count  = sum(1 for l in licenses_ctx if l["is_active"])
    expired_count = len(licenses_ctx) - active_count

    return templates.TemplateResponse(request, "licenses.html", {
        "brand":         BRAND,
        "licenses":      licenses_ctx,
        "total":         len(licenses_ctx),
        "active_count":  active_count,
        "expired_count": expired_count,
    })


# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC VERSION ENDPOINT
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


class VersionBody(BaseModel):
    version:       str
    release_notes: str  = ""
    download_url:  str  = ""
    changelog_url: str  = ""
    api_key:       str  = ""

@app.post("/api/version/publish")
async def publish_version(body: VersionBody):
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
    try:
        conn = get_db()
        cur  = conn.cursor()
        cur.execute("ALTER TABLE licenses ADD COLUMN IF NOT EXISTS payment_id TEXT;")
        cur.execute("ALTER TABLE licenses ADD COLUMN IF NOT EXISTS order_id TEXT;")
        conn.commit()
        cur.close()
        conn.close()
        return {"success": True, "message": "Database successfully patched! Missing columns added."}
    except Exception as e:
        return {"success": False, "error": str(e)}

# ══════════════════════════════════════════════════════════════════════════════
# HELPER — timestamp formatter (used by /dashboard template route)
# ══════════════════════════════════════════════════════════════════════════════

def _fmt_ts(ts) -> str:
    """Format a Unix timestamp or datetime to HH:MM DD/MM string."""
    if ts is None:
        return "—"
    try:
        if isinstance(ts, (int, float)):
            from datetime import datetime
            return datetime.fromtimestamp(ts).strftime("%H:%M %d/%m")
        return ts.strftime("%H:%M %d/%m")
    except Exception:
        return "—"


# ══════════════════════════════════════════════════════════════════════════════
# NEW PUBLIC PAGES
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/features", response_class=HTMLResponse)
async def features_page(request: Request):
    return templates.TemplateResponse(request, "features.html", {
        "brand":   BRAND,
    })


@app.get("/documentation", response_class=HTMLResponse)
async def docs_page(request: Request):
    return templates.TemplateResponse(request, "docs.html", {
        "brand":   BRAND,
    })


@app.get("/contact", response_class=HTMLResponse)
async def contact_page(request: Request, success: str = "", error: str = ""):
    return templates.TemplateResponse(request, "contact.html", {
        "brand":   BRAND,
        "success": success == "1",
        "error":   error,
        "form":    {},
    })


@app.post("/contact", response_class=HTMLResponse)
async def contact_submit(
    request: Request,
    name:    str = Form(...),
    email:   str = Form(...),
    type:    str = Form("general"),
    company: str = Form(""),
    seats:   str = Form(""),
    message: str = Form(...),
):
    name    = name.strip()[:200]
    email   = email.strip().lower()[:200]
    message = message.strip()[:2000]
    company = company.strip()[:200]
    seats   = seats.strip()[:50]
    type_   = type.strip()[:50]

    if not name or not email or not message or "@" not in email:
        return templates.TemplateResponse(request, "contact.html", {
        "brand":   BRAND,
            "success": False,
            "error":   "Please fill in all required fields with a valid email address.",
            "form":    {"name": name, "email": email, "type": type_, "message": message},
        })

    if DATABASE_URL:
        try:
            conn = get_db(); cur = conn.cursor()
            cur.execute("""
                INSERT INTO contact_submissions
                    (name, email, type, company, seats, message)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (name, email, type_, company, seats, message))
            conn.commit(); cur.close(); conn.close()
            print(f"[CONTACT] New submission from {email} (type={type_})")
        except Exception as e:
            print(f"[CONTACT] DB error: {e}")
    else:
        print(f"[CONTACT] No DB. Submission from {email}: {message[:80]}")

    return RedirectResponse("/contact?success=1", status_code=303)


@app.get("/privacy", response_class=HTMLResponse)
async def privacy_page(request: Request):
    return templates.TemplateResponse(request, "privacy.html", {
        "brand":   BRAND,
    })


@app.get("/terms", response_class=HTMLResponse)
async def terms_page(request: Request):
    return templates.TemplateResponse(request, "terms.html", {
        "brand":   BRAND,
    })


@app.get("/status", response_class=HTMLResponse)
async def status_page(request: Request):
    services = [
        {"name": "C2 API Server",       "icon": "🌐", "description": "FastAPI · Railway · HTTPS",          "status": "operational"},
        {"name": "License Validation",  "icon": "🔑", "description": "HMAC key validation · Device binding", "status": "operational"},
        {"name": "Payment Processing",  "icon": "💳", "description": "Razorpay · INR transactions",          "status": "operational"},
        {"name": "Email Delivery",      "icon": "📧", "description": "Gmail SMTP · License key emails",         "status": "operational"},
        {"name": "Tenant Fleet API",    "icon": "📡", "description": "Heartbeat · Remote commands",           "status": "operational"},
        {"name": "Cloud Database",      "icon": "🗄",  "description": "PostgreSQL · Railway managed",          "status": "operational"},
    ]
    stats = {"online_agents": 0, "total_tenants": 0, "total_licenses": 0, "uptime_pct": 99.9}
    overall = "operational"

    if DATABASE_URL:
        try:
            conn = get_db(); cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM tenant_agents WHERE status='online'")
            stats["online_agents"] = cur.fetchone()["count"]
            cur.execute("SELECT COUNT(*) FROM tenants WHERE active=TRUE")
            stats["total_tenants"] = cur.fetchone()["count"]
            cur.execute("SELECT COUNT(*) FROM licenses WHERE active=TRUE")
            stats["total_licenses"] = cur.fetchone()["count"]
            cur.close(); conn.close()
        except Exception as e:
            print(f"[STATUS] DB error: {e}")
            for s in services:
                if "Database" in s["name"]:
                    s["status"] = "degraded"
            overall = "degraded"

    uptime_days = [True] * 30
    checked_at  = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    return templates.TemplateResponse(request, "status.html", {
        "brand":       BRAND,
        "services":    services,
        "stats":       stats,
        "overall":     overall,
        "uptime_days": uptime_days,
        "checked_at":  checked_at,
    })

# ══════════════════════════════════════════════════════════════════════════════
# NEW PAGES — Phase 1 additions (config-driven, all use BRAND from config.py)
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/about", response_class=HTMLResponse)
async def about_page(request: Request):
    return templates.TemplateResponse(request, "about.html", {"brand": BRAND})

@app.get("/careers", response_class=HTMLResponse)
async def careers_page(request: Request):
    return templates.TemplateResponse(request, "careers.html", {"brand": BRAND})

@app.get("/security", response_class=HTMLResponse)
async def security_page(request: Request):
    return templates.TemplateResponse(request, "security.html", {"brand": BRAND})

@app.get("/blog", response_class=HTMLResponse)
async def blog_page(request: Request):
    return templates.TemplateResponse(request, "blog.html", {"brand": BRAND})

@app.get("/blog/{slug}", response_class=HTMLResponse)
async def blog_post(request: Request, slug: str):
    # Future: look up post by slug from DB; for now redirect to blog index
    return RedirectResponse("/blog", status_code=302)

@app.get("/case-studies", response_class=HTMLResponse)
async def case_studies_page(request: Request):
    return templates.TemplateResponse(request, "case_studies.html", {"brand": BRAND})

@app.get("/partners", response_class=HTMLResponse)
async def partners_page(request: Request):
    return templates.TemplateResponse(request, "partners.html", {"brand": BRAND})

@app.get("/integrations", response_class=HTMLResponse)
async def integrations_page(request: Request):
    return templates.TemplateResponse(request, "integrations.html", {"brand": BRAND})

@app.get("/roadmap", response_class=HTMLResponse)
async def roadmap_page(request: Request):
    return templates.TemplateResponse(request, "roadmap.html", {"brand": BRAND})

@app.get("/help", response_class=HTMLResponse)
async def help_page(request: Request):
    return templates.TemplateResponse(request, "help.html", {"brand": BRAND})

@app.get("/cookie-policy", response_class=HTMLResponse)
async def cookie_policy_page(request: Request):
    return templates.TemplateResponse(request, "cookie_policy.html", {"brand": BRAND})

@app.get("/enterprise", response_class=HTMLResponse)
async def enterprise_page_get(request: Request):
    return templates.TemplateResponse(request, "features.html", {
        "brand": BRAND,
        "_section": "enterprise",
    })

# ── Error handlers ─────────────────────────────────────────────────────────
@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    return templates.TemplateResponse(request, "404.html", {"brand": BRAND}, status_code=404)

@app.exception_handler(500)
async def server_error_handler(request: Request, exc):
    return templates.TemplateResponse(request, "500.html", {"brand": BRAND}, status_code=500)

@app.get("/tenant/forgot", response_class=HTMLResponse)
async def tenant_forgot_alias(request: Request, error: str = "", success: str = ""):
    import secrets as _sec
    return templates.TemplateResponse(request, "tenant_forgot.html", {
        "brand":      BRAND,
        "error":      error,
        "success":    success,
        "csrf_token": _sec.token_hex(16),
    })

@app.post("/tenant/forgot", response_class=HTMLResponse)
async def tenant_forgot_post(request: Request, email: str = Form(...)):
    return RedirectResponse("/tenant/forgot-password?email=" + email, 302)



# ══════════════════════════════════════════════════════════════════════════════
# TOTP 2FA SETUP — tenant users
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/tenant/setup-2fa", response_class=HTMLResponse)
async def tenant_setup_2fa_get(request: Request):
    session = _get_tenant_session(request)
    if not session:
        return RedirectResponse("/tenant/login", 302)

    import secrets as _s
    csrf = _s.token_hex(16)

    # Check if user already has 2FA enabled
    already_enabled = False
    totp_secret = ""
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT totp_secret FROM tenant_users WHERE email=%s",
                    (session["email"],))
        row = cur.fetchone()
        cur.close(); conn.close()
        if row and row.get("totp_secret"):
            already_enabled = True
        else:
            totp_secret = _generate_totp_secret()
    except Exception as e:
        print(f"[2FA] DB error: {e}")
        totp_secret = _generate_totp_secret()

    # Generate QR code
    qr_b64 = ""
    if not already_enabled:
        try:
            import qrcode, io, base64
            uri = _get_totp_uri(totp_secret, session["email"])
            img = qrcode.make(uri)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            qr_b64 = base64.b64encode(buf.getvalue()).decode()
        except Exception as e:
            print(f"[2FA] QR generation error: {e}")

    return templates.TemplateResponse(request, "tenant_setup_2fa.html", {
        "brand":           BRAND,
        "csrf_token":      csrf,
        "secret":          totp_secret,
        "qr_b64":          qr_b64,
        "already_enabled": already_enabled,
        "error":           "",
    })


@app.post("/tenant/setup-2fa", response_class=HTMLResponse)
async def tenant_setup_2fa_post(
    request:    Request,
    secret:     str = Form(...),
    totp_code:  str = Form(...),
    csrf_token: str = Form(""),
):
    session = _get_tenant_session(request)
    if not session:
        return RedirectResponse("/tenant/login", 302)

    # Verify the code before saving
    if not _verify_totp(secret, totp_code):
        import secrets as _s
        # Re-generate QR for retry
        qr_b64 = ""
        try:
            import qrcode, io, base64
            uri = _get_totp_uri(secret, session["email"])
            img = qrcode.make(uri)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            qr_b64 = base64.b64encode(buf.getvalue()).decode()
        except Exception:
            pass
        return templates.TemplateResponse(request, "tenant_setup_2fa.html", {
            "brand": BRAND, "csrf_token": _s.token_hex(16),
            "secret": secret, "qr_b64": qr_b64,
            "already_enabled": False,
            "error": "Invalid code — please try again.",
        })

    # Save secret to DB
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("UPDATE tenant_users SET totp_secret=%s WHERE email=%s",
                    (secret, session["email"]))
        conn.commit(); cur.close(); conn.close()
        print(f"[2FA] Enabled for {session['email']}")
    except Exception as e:
        print(f"[2FA] DB save error: {e}")

    return RedirectResponse("/tenant/dashboard?msg=2fa_enabled", 302)


@app.post("/tenant/disable-2fa")
async def tenant_disable_2fa(request: Request, csrf_token: str = Form("")):
    session = _get_tenant_session(request)
    if not session:
        return RedirectResponse("/tenant/login", 302)
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("UPDATE tenant_users SET totp_secret=NULL WHERE email=%s",
                    (session["email"],))
        conn.commit(); cur.close(); conn.close()
    except Exception as e:
        print(f"[2FA] Disable error: {e}")
    return RedirectResponse("/tenant/setup-2fa", 302)


# ── Alert acknowledge route ───────────────────────────────────────────────────
@app.post("/tenant/alerts/{alert_id}/ack")
async def tenant_ack_alert(request: Request, alert_id: str):
    session = _get_tenant_session(request)
    if not session:
        raise HTTPException(status_code=401, detail="Unauthorized")
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute(
            "UPDATE tenant_alerts SET acknowledged=TRUE WHERE id=%s AND tenant_id=%s",
            (alert_id, session["tenant_id"]))
        conn.commit(); cur.close(); conn.close()
        return JSONResponse({"ok": True})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



# ══════════════════════════════════════════════════════════════════════════════
# GDPR Article 17 — Right to Erasure
# ══════════════════════════════════════════════════════════════════════════════
@app.delete("/api/v1/tenant/data")
async def gdpr_erase_tenant_data(request: Request):
    """
    GDPR Article 17 — Right to Erasure.
    Permanently deletes all telemetry, alerts and registered agents for the
    requesting tenant. The tenant account itself, billing records and the
    erasure_log audit row are PRESERVED (we need them for legal traceability
    and to prove the erasure happened — GDPR Art. 5(2) accountability).

    Auth: x-tenant-key header (matches tenants.api_key).
    """
    if not DATABASE_URL:
        return JSONResponse({"ok": False, "reason": "Database not configured"},
                            status_code=503)

    tenant_key = request.headers.get("x-tenant-key", "").strip()
    if not tenant_key:
        return JSONResponse({"ok": False, "reason": "Missing x-tenant-key header"},
                            status_code=401)

    # Look up tenant — api_key is stored plaintext in this codebase
    # (matches /agent/config and /api/heartbeat behaviour).
    tenant = _get_tenant_by_api_key(tenant_key)
    if not tenant:
        return JSONResponse({"ok": False, "reason": "Invalid tenant key"},
                            status_code=401)

    tenant_id   = tenant["id"]
    tenant_name = tenant["name"]
    requester   = request.headers.get("x-requested-by", "tenant-portal")
    client_ip   = (request.client.host if request.client else "") or ""

    conn = get_db()
    cur  = conn.cursor()
    try:
        # Single transaction so we either erase everything or nothing.
        cur.execute("DELETE FROM tenant_alerts WHERE tenant_id = %s", (tenant_id,))
        deleted_alerts = cur.rowcount
        cur.execute("DELETE FROM tenant_agents WHERE tenant_id = %s", (tenant_id,))
        deleted_agents = cur.rowcount
        total_deleted  = deleted_alerts + deleted_agents

        # Audit row — kept regardless, so the erasure itself is traceable.
        cur.execute(
            """INSERT INTO erasure_log
               (tenant_id, tenant_name, requested_by, requester_ip, rows_deleted)
               VALUES (%s, %s, %s, %s, %s)""",
            (tenant_id, tenant_name, requester, client_ip, total_deleted)
        )
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"[GDPR] Erasure failed for tenant {tenant_id}: {e}")
        return JSONResponse({"ok": False, "reason": "Internal error"},
                            status_code=500)
    finally:
        cur.close(); conn.close()

    print(f"[GDPR] Tenant '{tenant_name}' ({tenant_id[:8]}…) erased "
          f"{total_deleted} rows ({deleted_alerts} alerts, {deleted_agents} agents) "
          f"by {requester} from {client_ip}")

    return JSONResponse({
        "ok":             True,
        "message":        f"All security data for tenant '{tenant_name}' has been erased.",
        "erased_at":      datetime.now(timezone.utc).isoformat(),
        "rows_deleted":   total_deleted,
        "alerts_deleted": deleted_alerts,
        "agents_deleted": deleted_agents,
        "gdpr_article":   "Article 17 — Right to Erasure",
        "audit_retained": "Tenant account, billing records and erasure audit log are preserved per GDPR Art. 5(2)."
    })
