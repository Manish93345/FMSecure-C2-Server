"""
main.py — FMSecure C2 + License Server
Railway deployment — PostgreSQL + Razorpay + SMTP Hardware Binding
"""

import os
import secrets
import time
import hashlib
import hmac as _hmac
import uuid
from datetime import datetime, timezone, timedelta

# Email imports
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from fastapi import FastAPI, Request, Depends, HTTPException, status, Form, Cookie
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
import psycopg2
from psycopg2.extras import RealDictCursor
import razorpay

# ── Configuration ──────────────────────────────────────────────────────────────
DATABASE_URL   = os.getenv("DATABASE_URL", "")
RZP_KEY_ID     = os.getenv("RAZORPAY_KEY_ID", "")
RZP_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "")
LICENSE_SECRET = os.getenv("LICENSE_HMAC_SECRET", "change-this-secret")
ADMIN_API_KEY  = os.getenv("ADMIN_API_KEY", "dev-only")
APP_BASE_URL   = os.getenv("APP_BASE_URL", "http://localhost:8000")
ADMIN_USER     = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASS     = os.getenv("ADMIN_PASSWORD", "password")
API_KEY        = os.getenv("API_KEY", "default-dev-key")
SESSION_TOKEN  = secrets.token_hex(16)

# SMTP Config for sending Licenses
SMTP_SERVER   = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT     = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")  # Gmail App Password

rzp_client = razorpay.Client(auth=(RZP_KEY_ID, RZP_KEY_SECRET))

# Amounts in PAISE (1 INR = 100 paise)
PLANS = {
    "pro_monthly": {
        "label":       "PRO Monthly",
        "amount":      99900,
        "currency":    "INR",
        "description": "FMSecure PRO - Monthly Subscription",
        "days":        31,
    },
    "pro_annual": {
        "label":       "PRO Annual",
        "amount":      999900,
        "currency":    "INR",
        "description": "FMSecure PRO - Annual Subscription",
        "days":        365,
    },
}

# ── App ────────────────────────────────────────────────────────────────────────
limiter = Limiter(key_func=get_remote_address)
app     = FastAPI(title="FMSecure C2")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

agents   = {}  
commands = {}

# ── Database ───────────────────────────────────────────────────────────────────
def get_db():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def init_db():
    conn = get_db()
    cur  = conn.cursor()
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS licenses (
            license_key  TEXT PRIMARY KEY,
            email        TEXT NOT NULL,
            tier         TEXT NOT NULL DEFAULT 'pro_monthly',
            payment_id   TEXT,
            order_id     TEXT,
            machine_id   TEXT,  -- NEW: Locks license to a single PC
            expires_at   TIMESTAMPTZ NOT NULL,
            active       BOOLEAN NOT NULL DEFAULT TRUE,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS pending_orders (
            order_id    TEXT PRIMARY KEY,
            email       TEXT NOT NULL,
            tier        TEXT NOT NULL,
            amount      INTEGER NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)
    conn.commit()
    cur.close()
    conn.close()

# ── Helpers ────────────────────────────────────────────────────────────────────
def _is_expired(expires_at) -> bool:
    try:
        if isinstance(expires_at, str):
            expires_at = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) > expires_at
    except Exception:
        return True

def _generate_license_key(tier: str, email: str, payment_id: str) -> str:
    payload = f"{tier}:{email.lower()}:{payment_id}"
    sig = _hmac.new(LICENSE_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()[:16].upper()
    prefix = "PRA" if "annual" in tier else "PRM"
    return f"FMSECURE-{prefix}-{sig}"

def _save_license(license_key, email, tier, payment_id, order_id, expires_iso):
    conn = get_db()
    cur  = conn.cursor()
    # machine_id starts as NULL. It gets populated on first activation.
    cur.execute("""
        INSERT INTO licenses (license_key, email, tier, payment_id, order_id, expires_at, active)
        VALUES (%s, %s, %s, %s, %s, %s, TRUE)
        ON CONFLICT (license_key) DO UPDATE SET
            expires_at = EXCLUDED.expires_at, active = TRUE
    """, (license_key, email.lower(), tier, payment_id, order_id, expires_iso))
    conn.commit()
    cur.close()
    conn.close()

async def verify_session(fmsecure_session: str = Cookie(None)):
    if not fmsecure_session or not secrets.compare_digest(fmsecure_session, SESSION_TOKEN):
        raise HTTPException(status_code=302, headers={"Location": "/login"})
    return True

def _send_license_email(email_to: str, license_key: str, tier: str, expires_iso: str):
    """Sends the License Key via standard Gmail SMTP"""
    tier_label  = PLANS.get(tier, {}).get("label", "PRO")
    expires_str = expires_iso[:10]
    
    if not SMTP_USERNAME or not SMTP_PASSWORD:
        print(f"[EMAIL ERROR] Missing SMTP Variables in Railway. Key: {license_key}")
        return

    html_content = f"""
    <div style="font-family:Arial,sans-serif;max-width:520px;margin:0 auto;
                background:#0d1117;color:#e6edf3;padding:32px;border-radius:12px;">
      <h2 style="color:#2f81f7;margin-top:0">FMSecure PRO Activated</h2>
      <p>Your <strong>{tier_label}</strong> is now active. Valid until {expires_str}.</p>
      <div style="background:#161b22;border:1px solid #30363d;border-radius:8px;
                  padding:20px;text-align:center;margin:24px 0;">
        <p style="margin:0 0 8px;color:#8b949e;font-size:12px">YOUR LICENSE KEY</p>
        <code style="font-size:20px;color:#2f81f7;letter-spacing:2px;font-weight:bold">
          {license_key}
        </code>
      </div>
      <p style="color:#8b949e;font-size:13px">
        To activate: Open FMSecure Desktop &rarr; Click your Username &rarr;
        Click "Upgrade to PRO" &rarr; Enter this key.
      </p>
    </div>"""
    
    msg = MIMEMultipart()
    msg['From'] = SMTP_USERNAME
    msg['To'] = email_to
    msg['Subject'] = "Your FMSecure PRO License Key"
    msg.attach(MIMEText(html_content, 'html'))

    try:
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SMTP_USERNAME, SMTP_PASSWORD)
        server.send_message(msg)
        server.quit()
        print(f"[EMAIL SUCCESS] License sent to {email_to}")
    except Exception as e:
        print(f"[EMAIL FAILED] Could not send to {email_to}: {e}")

# ══════════════════════════════════════════════════════════════════════════════
# AUTH, C2 DASHBOARD, PRICING, PAYMENT ENDPOINTS (Unchanged)
# ══════════════════════════════════════════════════════════════════════════════
# [Keeping all your login, dashboard, and razorpay payment routing exactly the same]
# I have intentionally compressed this section so you can just paste the whole file
@app.get("/login", response_class=HTMLResponse)
async def login_page(error: str = ""): return f"""<!DOCTYPE html><html><body><form method="post" action="/login"><input name="username" required><input name="password" type="password" required><button type="submit">Login</button></form></body></html>"""
@app.post("/login")
async def process_login(username: str = Form(...), password: str = Form(...)):
    if secrets.compare_digest(username, ADMIN_USER) and secrets.compare_digest(password, ADMIN_PASS):
        resp = RedirectResponse(url="/", status_code=302)
        resp.set_cookie("fmsecure_session", SESSION_TOKEN, httponly=True, max_age=86400)
        return resp
    return RedirectResponse(url="/login?error=Invalid", status_code=302)
@app.get("/logout")
async def logout():
    resp = RedirectResponse(url="/login", status_code=302); resp.delete_cookie("fmsecure_session"); return resp

class Heartbeat(BaseModel): machine_id: str; hostname: str; username: str; tier: str; is_armed: bool
@app.post("/api/heartbeat")
@limiter.limit("200/minute")
async def receive_heartbeat(request: Request, data: Heartbeat):
    if request.headers.get("x-api-key") != API_KEY: raise HTTPException(status_code=401, detail="Unauthorized")
    agents[data.machine_id] = {"hostname": data.hostname, "username": data.username, "tier": data.tier, "is_armed": data.is_armed, "last_seen": time.time(), "ip": request.client.host}
    cmd = commands.pop(data.machine_id, "NONE")
    return {"status": "ok", "command": cmd}

@app.post("/api/trigger_lockdown/{machine_id}")
async def trigger_lockdown(machine_id: str, _: bool = Depends(verify_session)):
    commands[machine_id] = "LOCKDOWN"; return {"status": "Lockdown command queued"}

@app.get("/", response_class=HTMLResponse)
async def dashboard(_: bool = Depends(verify_session)):
    now = time.time(); rows = ""
    for mid, info in agents.items():
        sb = '<span style="color:#238636">ONLINE</span>' if (now - info["last_seen"]) < 30 else '<span style="color:#8b949e">OFFLINE</span>'
        rows += f"<tr><td>{mid[:14]}...</td><td>{info['hostname']}</td><td>{info['username']}</td><td>{info['ip']}</td><td>{sb}</td></tr>"
    return f"<!DOCTYPE html><html><body><nav>FMSecure C2 <a href='/licenses'>Licenses</a></nav><table>{rows}</table></body></html>"

@app.get("/pricing", response_class=HTMLResponse)
async def pricing_page():
    return f"""<!DOCTYPE html><html><head><title>FMSecure PRO</title><script src="https://checkout.razorpay.com/v1/checkout.js"></script></head>
    <body style="background:#0d1117;color:#e6edf3;font-family:sans-serif;text-align:center;padding:50px;">
    <h1>Get FMSecure PRO</h1>
    <input type="email" id="email" placeholder="Enter email to receive license key" style="padding:10px;width:300px;margin:20px 0;"><br>
    <button onclick="startPayment('pro_monthly')" style="padding:10px 20px;background:#2f81f7;color:#fff;border:none;border-radius:5px;cursor:pointer;margin:10px;">Buy Monthly (Rs.999)</button>
    <button onclick="startPayment('pro_annual')" style="padding:10px 20px;background:#238636;color:#fff;border:none;border-radius:5px;cursor:pointer;margin:10px;">Buy Annual (Rs.9999)</button>
    <script>
    async function startPayment(tier) {{
      const email = document.getElementById('email').value.trim();
      if (!email) return alert('Enter email');
      const resp = await fetch('{APP_BASE_URL}/payment/create-order', {{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{tier,email}})}});
      const orderData = await resp.json();
      new Razorpay({{
        key: '{RZP_KEY_ID}', amount: orderData.amount, currency: orderData.currency, name: 'FMSecure', order_id: orderData.order_id, prefill: {{email: email}},
        handler: async function(response) {{
          const vResp = await fetch('{APP_BASE_URL}/payment/verify', {{method:'POST',headers:{{'Content-Type':'application/json'}},
            body:JSON.stringify({{razorpay_order_id:response.razorpay_order_id,razorpay_payment_id:response.razorpay_payment_id,razorpay_signature:response.razorpay_signature,email:email,tier:tier}})}});
          const result = await vResp.json();
          if(result.success) window.location.href = '{APP_BASE_URL}/payment/success?key='+result.license_key+'&email='+email;
          else alert('Failed');
        }}
      }}).open();
    }}
    </script></body></html>"""

class CreateOrderRequest(BaseModel): tier: str; email: str
class VerifyPaymentRequest(BaseModel): razorpay_order_id: str; razorpay_payment_id: str; razorpay_signature: str; email: str; tier: str

@app.post("/payment/create-order")
@limiter.limit("20/minute")
async def create_order(request: Request, body: CreateOrderRequest):
    tier = body.tier.strip().lower(); email = body.email.strip().lower()
    plan = PLANS[tier]
    order = rzp_client.order.create({"amount": plan["amount"], "currency": plan["currency"], "receipt": f"fm_{uuid.uuid4().hex[:8]}"})
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("INSERT INTO pending_orders (order_id, email, tier, amount) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING", (order["id"], email, tier, plan["amount"]))
        conn.commit(); cur.close(); conn.close()
    except: pass
    return {"order_id": order["id"], "amount": plan["amount"], "currency": plan["currency"]}

@app.post("/payment/verify")
@limiter.limit("20/minute")
async def verify_payment(request: Request, body: VerifyPaymentRequest):
    try: rzp_client.utility.verify_payment_signature({'razorpay_order_id': body.razorpay_order_id, 'razorpay_payment_id': body.razorpay_payment_id, 'razorpay_signature': body.razorpay_signature})
    except: return JSONResponse({"success": False}, status_code=400)
    tier = body.tier.strip().lower(); email = body.email.strip().lower()
    days = PLANS.get(tier, {}).get("days", 31)
    expires_iso = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
    license_key = _generate_license_key(tier, email, body.razorpay_payment_id)
    _save_license(license_key, email, tier, body.razorpay_payment_id, body.razorpay_order_id, expires_iso)
    _send_license_email(email, license_key, tier, expires_iso)
    return {"success": True, "license_key": license_key, "tier": tier, "expires_at": expires_iso}

@app.get("/payment/success", response_class=HTMLResponse)
async def payment_success(key: str = "", email: str = ""):
    return f"""<!DOCTYPE html><html><body style="background:#0d1117;color:#e6edf3;font-family:sans-serif;text-align:center;padding:50px;">
    <h2 style="color:#3fb950">Payment successful!</h2>
    <p>We've emailed this key to <strong>{email}</strong></p>
    <div style="background:#161b22;padding:20px;margin:20px auto;width:300px;border-radius:8px">
      <div style="font-family:monospace;color:#2f81f7;font-size:20px">{key}</div>
    </div>
    <p style="color:#8b949e">Enter this key in the FMSecure Desktop App to activate PRO.</p>
    </body></html>"""

# ══════════════════════════════════════════════════════════════════════════════
# NEW: DEVICE-BOUND LICENSE VALIDATION 
# ══════════════════════════════════════════════════════════════════════════════

class LicenseValidateRequest(BaseModel):
    license_key: str
    machine_id: str  # We now require Hardware ID instead of an Email

@app.post("/api/license/validate")
async def validate_license(req: LicenseValidateRequest):
    key  = req.license_key.strip()
    hwid = req.machine_id.strip()  # Hardware ID passed from desktop app
    
    if not DATABASE_URL:
        return {"valid": False, "tier": "free", "reason": "db_not_configured"}
        
    try:
        conn = get_db()
        cur  = conn.cursor()
        cur.execute("SELECT * FROM licenses WHERE license_key = %s", (key,))
        r = cur.fetchone()
        
        # 1. Does the key exist?
        if not r:
            cur.close()
            conn.close()
            return {"valid": False, "tier": "free", "reason": "key_not_found", "message": "Invalid License Key."}

        # 2. Is it expired?
        if not r["active"] or _is_expired(r["expires_at"]):
            cur.close()
            conn.close()
            return {"valid": False, "tier": "free", "reason": "subscription_expired", "message": "License expired."}

        # 3. DEVICE BINDING LOGIC
        if r["machine_id"] is None:
            # First time use! Bind it to this PC permanently
            cur.execute("UPDATE licenses SET machine_id = %s WHERE license_key = %s", (hwid, key))
            conn.commit()
            print(f"🔒 Bounded License {key} to Machine ID {hwid}")
            
        elif r["machine_id"] != hwid:
            # Somebody is trying to use this key on a 2nd computer! Block it.
            cur.close()
            conn.close()
            return {"valid": False, "tier": "free", "reason": "device_mismatch", 
                    "message": "This License is already in use on another computer."}

        cur.close()
        conn.close()
        return {"valid": True, "tier": r["tier"], "expires_at": r["expires_at"].isoformat(), "reason": "ok"}
        
    except Exception as e:
        print(f"Validation Error: {e}")
        return {"valid": False, "tier": "free", "reason": "db_error"}

@app.post("/api/license/activate")
async def activate_license(req: LicenseValidateRequest):
    return await validate_license(req)


# ══════════════════════════════════════════════════════════════════════════════
# DB PATCH ENDPOINT
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/db-fix")
async def fix_db():
    """Patches the database to add the new machine_id column"""
    try:
        conn = get_db()
        cur  = conn.cursor()
        cur.execute("ALTER TABLE licenses ADD COLUMN IF NOT EXISTS payment_id TEXT;")
        cur.execute("ALTER TABLE licenses ADD COLUMN IF NOT EXISTS order_id TEXT;")
        cur.execute("ALTER TABLE licenses ADD COLUMN IF NOT EXISTS machine_id TEXT;")
        conn.commit()
        cur.close()
        conn.close()
        return {"success": True, "message": "Database patched! Hardware Binding enabled."}
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.get("/licenses", response_class=HTMLResponse)
async def licenses_page(_: bool = Depends(verify_session)):
    conn = get_db(); cur = conn.cursor(); cur.execute("SELECT * FROM licenses ORDER BY created_at DESC"); rows = cur.fetchall(); cur.close(); conn.close()
    tr = "".join([f"<tr><td>{r['license_key']}</td><td>{r['email']}</td><td>{r['machine_id'] or 'Unbound'}</td><td>{r['expires_at'].strftime('%Y-%m-%d')}</td></tr>" for r in rows])
    return f"<!DOCTYPE html><html><body><nav>License Manager <a href='/'>C2 Dashboard</a></nav><table border=1><tr><th>KEY</th><th>EMAIL</th><th>MACHINE ID</th><th>EXPIRES</th></tr>{tr}</table></body></html>"