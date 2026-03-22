"""
main.py — FMSecure C2 + License Server
Railway deployment — PostgreSQL backed

Environment variables required in Railway:
  ADMIN_USERNAME         = your admin username
  ADMIN_PASSWORD         = a strong password
  API_KEY                = your desktop agent API key
  STRIPE_SECRET_KEY      = sk_live_... (or sk_test_... for testing)
  STRIPE_WEBHOOK_SECRET  = whsec_... (from Stripe Dashboard → Webhooks)
  LICENSE_HMAC_SECRET    = any long random string (keep secret)
  ADMIN_API_KEY          = any secret string for /api/license/list
  DATABASE_URL           = auto-set by Railway when you add PostgreSQL plugin
  SENDGRID_API_KEY       = (optional) for emailing license keys to customers
"""

import os
import secrets
import time
import hashlib
import hmac as _hmac
import uuid
from datetime import datetime, timezone, timedelta

from fastapi import FastAPI, Request, Depends, HTTPException, status, Form, Cookie
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
import stripe

# ── Database ───────────────────────────────────────────────────────────────────
# Using psycopg2 directly — no ORM overhead, easier to reason about.
# Railway sets DATABASE_URL automatically when you add the PostgreSQL plugin.
import psycopg2
from psycopg2.extras import RealDictCursor

DATABASE_URL = os.getenv("DATABASE_URL", "")

def get_db():
    """Get a database connection. Call this inside each endpoint."""
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    return conn

def init_db():
    """
    Create tables on startup if they don't exist.
    Safe to call every restart — uses IF NOT EXISTS.
    """
    conn = get_db()
    cur  = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS licenses (
            license_key     TEXT PRIMARY KEY,
            email           TEXT NOT NULL,
            tier            TEXT NOT NULL DEFAULT 'pro_monthly',
            stripe_sub_id   TEXT,
            expires_at      TIMESTAMPTZ NOT NULL,
            active          BOOLEAN NOT NULL DEFAULT TRUE,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)
    conn.commit()
    cur.close()
    conn.close()
    print("[DB] Tables ready.")


# ── App setup ──────────────────────────────────────────────────────────────────
stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")

limiter      = Limiter(key_func=get_remote_address)
app          = FastAPI(title="FMSecure Cloud C2")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.mount("/static", StaticFiles(directory="static"), name="static")

ADMIN_USER    = os.getenv("ADMIN_USERNAME",  "admin")
ADMIN_PASS    = os.getenv("ADMIN_PASSWORD",  "password")
API_KEY       = os.getenv("API_KEY",         "default-dev-key")
SESSION_TOKEN = secrets.token_hex(16)

# In-memory agent table (C2 telemetry — intentionally not persisted)
agents   = {}
commands = {}


@app.on_event("startup")
async def startup():
    if DATABASE_URL:
        init_db()
    else:
        print("[DB] WARNING: No DATABASE_URL set. Add the PostgreSQL plugin on Railway.")


# ── Pydantic models ────────────────────────────────────────────────────────────
class LicenseValidateRequest(BaseModel):
    email:       str
    license_key: str

class Heartbeat(BaseModel):
    machine_id: str
    hostname:   str
    username:   str
    tier:       str
    is_armed:   bool


# ── Auth ───────────────────────────────────────────────────────────────────────
async def verify_session(fmsecure_session: str = Cookie(None)):
    if not fmsecure_session or not secrets.compare_digest(fmsecure_session, SESSION_TOKEN):
        raise HTTPException(status_code=302, headers={"Location": "/login"})
    return True


# ── Helper functions ───────────────────────────────────────────────────────────
def _is_expired(expires_at) -> bool:
    """Works with both datetime objects and ISO strings."""
    try:
        if isinstance(expires_at, str):
            expires_at = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) > expires_at
    except Exception:
        return True


def _generate_license_key(tier: str, email: str, sub_id: str) -> str:
    """Deterministic license key — same inputs always produce same key."""
    _SECRET     = os.getenv("LICENSE_HMAC_SECRET", "fmsecure-license-secret-change-me")
    payload_str = f"{tier}:{email.lower()}:{sub_id}"
    sig = _hmac.new(
        _SECRET.encode(), payload_str.encode(), hashlib.sha256
    ).hexdigest()[:16].upper()
    prefix = "PRO" if "annual" in tier.lower() else "PRM"
    return f"FMSECURE-{prefix}-{sig}"


def _save_license(license_key: str, email: str, tier: str,
                  stripe_sub_id: str, expires_iso: str):
    """Upsert a license record into PostgreSQL."""
    conn = get_db()
    cur  = conn.cursor()
    cur.execute("""
        INSERT INTO licenses (license_key, email, tier, stripe_sub_id, expires_at, active)
        VALUES (%s, %s, %s, %s, %s, TRUE)
        ON CONFLICT (license_key) DO UPDATE SET
            tier          = EXCLUDED.tier,
            stripe_sub_id = EXCLUDED.stripe_sub_id,
            expires_at    = EXCLUDED.expires_at,
            active        = TRUE
    """, (license_key, email.lower(), tier, stripe_sub_id, expires_iso))
    conn.commit()
    cur.close()
    conn.close()


def _deactivate_by_sub_id(sub_id: str):
    """Mark a license as inactive when Stripe subscription is cancelled/failed."""
    conn = get_db()
    cur  = conn.cursor()
    cur.execute("""
        UPDATE licenses
        SET active = FALSE, expires_at = NOW()
        WHERE stripe_sub_id = %s
    """, (sub_id,))
    conn.commit()
    cur.close()
    conn.close()


def _send_license_email(email: str, license_key: str, tier: str, expires_iso: str):
    """
    Email the license key to the customer after payment.
    Uses SendGrid if SENDGRID_API_KEY is set, otherwise just prints.
    """
    sendgrid_key = os.getenv("SENDGRID_API_KEY", "")
    tier_label   = "PRO Annual" if "annual" in tier else "PRO Monthly"
    expires_str  = expires_iso[:10]  # Just the date

    if not sendgrid_key:
        print(f"[EMAIL] Would send key {license_key} to {email} (no SendGrid key set)")
        return

    try:
        import sendgrid
        from sendgrid.helpers.mail import Mail

        message = Mail(
            from_email = "noreply@fmsecure.app",
            to_emails  = email,
            subject    = "Your FMSecure PRO License Key",
            html_content = f"""
            <div style="font-family:Arial,sans-serif;max-width:500px;margin:0 auto;
                        background:#0d1117;color:#e6edf3;padding:32px;border-radius:12px;">
              <h2 style="color:#2f81f7;margin-top:0">FMSecure PRO Activated</h2>
              <p>Thank you for your purchase! Your <strong>{tier_label}</strong>
                 subscription is now active.</p>
              <div style="background:#161b22;border:1px solid #30363d;border-radius:8px;
                          padding:20px;text-align:center;margin:24px 0;">
                <p style="margin:0 0 8px;color:#8b949e;font-size:12px;">YOUR LICENSE KEY</p>
                <code style="font-size:18px;color:#2f81f7;letter-spacing:2px;">
                  {license_key}
                </code>
              </div>
              <p style="color:#8b949e;font-size:13px;">
                Subscription renews: {expires_str}<br>
                To activate: open FMSecure → click your username → Activate License
              </p>
              <p style="color:#8b949e;font-size:12px;">Keep this key safe — it's tied to your email address.</p>
            </div>
            """
        )
        sg = sendgrid.SendGridAPIClient(api_key=sendgrid_key)
        sg.send(message)
        print(f"[EMAIL] License key sent to {email}")
    except Exception as e:
        print(f"[EMAIL] Failed to send to {email}: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# AUTH ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/login", response_class=HTMLResponse)
async def login_page(error: str = ""):
    error_msg = f'<div class="alert alert-danger p-2 text-center" style="font-size:14px">{error}</div>' if error else ""
    return f"""<!DOCTYPE html><html data-bs-theme="dark"><head>
    <title>FMSecure | Login</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body{{background:#0a0a0a;display:flex;align-items:center;justify-content:center;height:100vh;margin:0}}
        .card{{background:#161b22;border:1px solid #30363d;border-radius:12px;padding:40px;width:100%;max-width:400px}}
        .form-control{{background:#0d1117;border:1px solid #30363d;color:#c9d1d9}}
        .form-control:focus{{background:#0d1117;border-color:#58a6ff;color:#c9d1d9;box-shadow:0 0 0 3px rgba(88,166,255,.3)}}
        .btn-primary{{background:#238636;border-color:rgba(240,246,252,.1)}}
    </style></head><body><div class="card">
    <h3 class="text-center fw-bold mb-1" style="color:#58a6ff">FMSecure C2</h3>
    <p class="text-center text-muted mb-4" style="font-size:14px">Enterprise Authentication</p>
    {error_msg}
    <form action="/login" method="post">
        <div class="mb-3"><label class="form-label text-muted small fw-bold">USERNAME</label>
            <input type="text" name="username" class="form-control" required autofocus></div>
        <div class="mb-4"><label class="form-label text-muted small fw-bold">PASSWORD</label>
            <input type="password" name="password" class="form-control" required></div>
        <button type="submit" class="btn btn-primary w-100 fw-bold">Authenticate</button>
    </form></div></body></html>"""

@app.post("/login")
async def process_login(username: str = Form(...), password: str = Form(...)):
    if secrets.compare_digest(username, ADMIN_USER) and secrets.compare_digest(password, ADMIN_PASS):
        response = RedirectResponse(url="/", status_code=302)
        response.set_cookie("fmsecure_session", SESSION_TOKEN, httponly=True, max_age=86400)
        return response
    return RedirectResponse(url="/login?error=Invalid+Credentials", status_code=302)

@app.get("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie("fmsecure_session")
    return response


# ══════════════════════════════════════════════════════════════════════════════
# C2 DASHBOARD
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/api/heartbeat")
@limiter.limit("200/minute")
async def receive_heartbeat(request: Request, data: Heartbeat):
    api_key_header = request.headers.get("x-api-key")
    if api_key_header != API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")
    agents[data.machine_id] = {
        "hostname": data.hostname, "username": data.username,
        "tier": data.tier, "is_armed": data.is_armed,
        "last_seen": time.time(), "ip": request.client.host
    }
    cmd = commands.get(data.machine_id, "NONE")
    if cmd != "NONE":
        commands[data.machine_id] = "NONE"
    return {"status": "ok", "command": cmd}

@app.post("/api/trigger_lockdown/{machine_id}")
async def trigger_lockdown(machine_id: str, _: bool = Depends(verify_session)):
    commands[machine_id] = "LOCKDOWN"
    return {"status": "Lockdown command queued"}

@app.get("/", response_class=HTMLResponse)
async def dashboard(_: bool = Depends(verify_session)):
    current_time = time.time()
    rows = ""
    for mid, info in agents.items():
        online = (current_time - info['last_seen']) < 30
        status_badge = '<span class="badge bg-success">ONLINE</span>' if online else '<span class="badge bg-secondary">OFFLINE</span>'
        armed_badge  = '<span class="badge bg-primary">ARMED</span>' if info['is_armed'] else '<span class="badge bg-warning text-dark">UNARMED</span>'
        rows += f"""<tr>
            <td class="font-monospace text-secondary">{mid[:12]}...</td>
            <td><strong>{info['hostname']}</strong></td>
            <td>{info['username']}</td><td>{info['ip']}</td>
            <td>{status_badge}</td><td>{armed_badge}</td>
            <td><button onclick="triggerLockdown('{mid}')" class="btn btn-sm btn-danger fw-bold">ISOLATE</button></td>
        </tr>"""
    if not rows:
        rows = "<tr><td colspan='7' class='text-center text-muted py-4'>No endpoints connected.</td></tr>"

    return f"""<!DOCTYPE html><html data-bs-theme="dark"><head>
    <title>FMSecure C2</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>body{{background:#0a0a0a;color:#e6edf3}}.navbar{{background:#161b22;border-bottom:1px solid #30363d}}
    .card{{background:#161b22;border:1px solid #30363d}}.table{{color:#e6edf3}}
    .table th{{border-bottom:2px solid #30363d;color:#8b949e}}.table td{{border-bottom:1px solid #21262d;vertical-align:middle}}</style>
    </head><body>
    <nav class="navbar px-4 py-3 mb-4"><div class="container-fluid">
        <span class="navbar-brand text-primary fw-bold">FMSecure Global C2</span>
        <div class="d-flex"><span class="navbar-text me-4 text-muted">Endpoint Telemetry</span>
            <a href="/licenses" class="btn btn-outline-info btn-sm me-2">Licenses</a>
            <a href="/logout" class="btn btn-outline-danger btn-sm fw-bold">Logout</a></div>
    </div></nav>
    <div class="container-fluid px-4"><div class="card shadow-lg"><div class="card-body p-0">
    <table class="table table-hover mb-0"><thead><tr>
        <th>MACHINE ID</th><th>HOSTNAME</th><th>USER</th><th>IP</th>
        <th>NETWORK</th><th>ENGINE</th><th>ACTION</th></tr></thead>
    <tbody>{rows}</tbody></table></div></div></div>
    <script>
        setTimeout(()=>window.location.reload(),5000);
        async function triggerLockdown(mid){{
            if(confirm("ISOLATE this host?")){{
                await fetch(`/api/trigger_lockdown/${{mid}}`,{{method:'POST'}});
                alert("Lockdown queued!");}}}}
    </script></body></html>"""


# ══════════════════════════════════════════════════════════════════════════════
# LICENSE ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/api/license/validate")
async def validate_license(req: LicenseValidateRequest):
    """Called by the desktop app to check if a license key is valid."""
    key   = req.license_key.strip()
    email = req.email.strip().lower()

    if not DATABASE_URL:
        return {"valid": False, "tier": "free", "reason": "db_not_configured"}

    try:
        conn = get_db()
        cur  = conn.cursor()
        cur.execute("SELECT * FROM licenses WHERE license_key = %s", (key,))
        record = cur.fetchone()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"[LICENSE] DB error: {e}")
        return {"valid": False, "tier": "free", "reason": "db_error"}

    if not record:
        return {"valid": False, "tier": "free", "expires_at": None, "reason": "key_not_found"}

    if record["email"].lower() != email:
        return {"valid": False, "tier": "free", "expires_at": None, "reason": "email_mismatch"}

    if not record["active"] or _is_expired(record["expires_at"]):
        return {"valid": False, "tier": "free",
                "expires_at": record["expires_at"].isoformat() if record["expires_at"] else None,
                "reason": "subscription_expired"}

    return {
        "valid":      True,
        "tier":       record["tier"],
        "expires_at": record["expires_at"].isoformat(),
        "reason":     "ok",
    }


@app.post("/api/license/activate")
async def activate_license(req: LicenseValidateRequest):
    """Alias for validate — called when user first enters their key."""
    return await validate_license(req)


# ══════════════════════════════════════════════════════════════════════════════
# STRIPE WEBHOOK
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/api/stripe/webhook")
async def stripe_webhook(request: Request):
    """
    Stripe fires this on every subscription event.
    This is the ONLY place license records get created/renewed/cancelled.
    """
    payload        = await request.body()
    sig_header     = request.headers.get("stripe-signature", "")
    webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET", "")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid Stripe signature")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    event_type = event["type"]
    data       = event["data"]["object"]

    # Payment succeeded — create or renew the license
    if event_type in ("invoice.payment_succeeded", "customer.subscription.created"):
        sub_id      = data.get("subscription") or data.get("id")
        customer_id = data.get("customer")

        try:
            sub      = stripe.Subscription.retrieve(sub_id)
            customer = stripe.Customer.retrieve(customer_id)
            email    = customer.get("email", "").lower()

            # Determine tier from Stripe price lookup_key
            # Set lookup_key = "pro_monthly" or "pro_annual" in your Stripe Dashboard
            lookup_key = sub["items"]["data"][0]["price"].get("lookup_key", "pro_monthly") or "pro_monthly"
            tier = "pro_annual" if "annual" in lookup_key.lower() else "pro_monthly"

            # Expiry = end of current billing period
            expires_ts  = sub.get("current_period_end", 0)
            expires_iso = datetime.fromtimestamp(expires_ts, tz=timezone.utc).isoformat()

            license_key = _generate_license_key(tier, email, sub_id)
            _save_license(license_key, email, tier, sub_id, expires_iso)
            _send_license_email(email, license_key, tier, expires_iso)

            print(f"[WEBHOOK] License created: {license_key} for {email}")

        except Exception as e:
            print(f"[WEBHOOK] Error on payment_succeeded: {e}")

    # Subscription cancelled or payment failed — deactivate
    elif event_type in ("customer.subscription.deleted", "invoice.payment_failed"):
        sub_id = data.get("id") or data.get("subscription")
        if sub_id:
            _deactivate_by_sub_id(sub_id)
            print(f"[WEBHOOK] Deactivated subscription: {sub_id}")

    return {"received": True}


# ══════════════════════════════════════════════════════════════════════════════
# ADMIN ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

def _check_admin_key(api_key: str):
    admin_key = os.getenv("ADMIN_API_KEY", "dev-only")
    if api_key != admin_key:
        raise HTTPException(status_code=403, detail="Forbidden")

@app.get("/api/license/list")
async def list_licenses(api_key: str = ""):
    """Admin: see all licenses in the database."""
    _check_admin_key(api_key)
    conn = get_db()
    cur  = conn.cursor()
    cur.execute("SELECT * FROM licenses ORDER BY created_at DESC")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return {"licenses": [dict(r) for r in rows]}

@app.post("/api/license/create_manual")
async def create_manual_license(
    email:   str,
    tier:    str = "pro_monthly",
    days:    int = 30,
    api_key: str = ""
):
    """
    Admin: manually create a license (for testers, early customers, beta users).
    Use this while Stripe is in test mode or before your payment page is live.

    Example:
      POST https://your-server.railway.app/api/license/create_manual
           ?email=test@example.com&tier=pro_monthly&days=30&api_key=YOUR_ADMIN_KEY
    """
    _check_admin_key(api_key)

    sub_id      = f"manual_{uuid.uuid4().hex[:8]}"
    expires_iso = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
    license_key = _generate_license_key(tier, email, sub_id)

    _save_license(license_key, email, tier, sub_id, expires_iso)

    return {
        "license_key": license_key,
        "email":       email,
        "tier":        tier,
        "expires_at":  expires_iso,
        "message":     "License created. Email this key to the customer manually."
    }


# ── License admin web page ────────────────────────────────────────────────────
@app.get("/licenses", response_class=HTMLResponse)
async def licenses_page(_: bool = Depends(verify_session)):
    """Web UI for viewing all licenses — accessible from the dashboard nav."""
    conn = get_db()
    cur  = conn.cursor()
    cur.execute("SELECT * FROM licenses ORDER BY created_at DESC LIMIT 200")
    rows = cur.fetchall()
    cur.close()
    conn.close()

    table_rows = ""
    for r in rows:
        expired = _is_expired(r["expires_at"])
        status_badge = (
            '<span class="badge bg-danger">Expired</span>' if expired or not r["active"]
            else '<span class="badge bg-success">Active</span>'
        )
        expires_str = r["expires_at"].strftime("%Y-%m-%d") if r["expires_at"] else "—"
        table_rows += f"""<tr>
            <td class="font-monospace" style="font-size:12px">{r['license_key']}</td>
            <td>{r['email']}</td>
            <td><span class="badge bg-info text-dark">{r['tier']}</span></td>
            <td>{status_badge}</td>
            <td>{expires_str}</td>
        </tr>"""

    return f"""<!DOCTYPE html><html data-bs-theme="dark"><head>
    <title>FMSecure | Licenses</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>body{{background:#0a0a0a;color:#e6edf3}}.navbar{{background:#161b22;border-bottom:1px solid #30363d}}
    .card{{background:#161b22;border:1px solid #30363d}}.table{{color:#e6edf3}}
    .table th{{border-bottom:2px solid #30363d;color:#8b949e}}.table td{{border-bottom:1px solid #21262d;vertical-align:middle}}</style>
    </head><body>
    <nav class="navbar px-4 py-3 mb-4"><div class="container-fluid">
        <span class="navbar-brand text-primary fw-bold">License Manager</span>
        <div class="d-flex">
            <a href="/" class="btn btn-outline-secondary btn-sm me-2">← C2 Dashboard</a>
            <a href="/logout" class="btn btn-outline-danger btn-sm">Logout</a>
        </div>
    </div></nav>
    <div class="container-fluid px-4"><div class="card shadow-lg"><div class="card-body p-0">
    <table class="table table-hover mb-0"><thead><tr>
        <th>LICENSE KEY</th><th>EMAIL</th><th>TIER</th><th>STATUS</th><th>EXPIRES</th>
    </tr></thead><tbody>{table_rows}</tbody></table>
    </div></div></div></body></html>"""