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
import os, secrets, time, hashlib, hmac as _hmac, uuid, threading
from datetime import datetime, timezone, timedelta

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

rzp_client = razorpay.Client(auth=(RZP_KEY_ID, RZP_KEY_SECRET))

# ── Plans — amounts in PAISE (Rs 999 = 99900) ─────────────────────────────────
# To change price: edit "amount". To change label: edit "label" AND the HTML below.
PLANS = {
    "pro_monthly": {"label":"PRO Monthly","amount":99900, "currency":"INR",
                    "description":"FMSecure PRO - Monthly","days":31},
    "pro_annual":  {"label":"PRO Annual", "amount":999900,"currency":"INR",
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

# ── Database ───────────────────────────────────────────────────────────────────
def get_db():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def init_db():
    conn = get_db(); cur = conn.cursor()
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
        DO $$ BEGIN
          IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                         WHERE table_name='licenses' AND column_name='machine_id')
          THEN ALTER TABLE licenses ADD COLUMN machine_id TEXT DEFAULT NULL; END IF;
        END $$;
    """)
    conn.commit(); cur.close(); conn.close()
    print("[DB] Tables ready.")

@app.on_event("startup")
async def startup():
    if DATABASE_URL: init_db()
    else: print("[DB] WARNING: No DATABASE_URL")

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
        resp = RedirectResponse(url="/", status_code=302)
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
    machine_id: str; hostname: str; username: str; tier: str; is_armed: bool

@app.post("/api/heartbeat")
@limiter.limit("200/minute")
async def receive_heartbeat(request: Request, data: Heartbeat):
    if request.headers.get("x-api-key") != API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")
    agents[data.machine_id] = {"hostname": data.hostname, "username": data.username,
        "tier": data.tier, "is_armed": data.is_armed,
        "last_seen": time.time(), "ip": request.client.host}
    return {"status": "ok", "command": commands.pop(data.machine_id, "NONE")}

@app.post("/api/trigger_lockdown/{machine_id}")
async def trigger_lockdown(machine_id: str, _: bool = Depends(verify_session)):
    commands[machine_id] = "LOCKDOWN"
    return {"status": "Lockdown queued"}

@app.get("/", response_class=HTMLResponse)
async def dashboard(_: bool = Depends(verify_session)):
    now = time.time(); rows = ""
    for mid, info in agents.items():
        online = (now - info["last_seen"]) < 30
        sb = '<span style="background:#238636;color:#fff;padding:2px 8px;border-radius:4px;font-size:12px">ONLINE</span>' if online else '<span style="background:#30363d;color:#8b949e;padding:2px 8px;border-radius:4px;font-size:12px">OFFLINE</span>'
        ab = '<span style="background:#1f6feb;color:#fff;padding:2px 8px;border-radius:4px;font-size:12px">ARMED</span>' if info["is_armed"] else '<span style="background:#9e6a03;color:#fff;padding:2px 8px;border-radius:4px;font-size:12px">UNARMED</span>'
        rows += f"<tr><td style='font-family:monospace;color:#8b949e'>{mid[:14]}...</td><td><strong>{info['hostname']}</strong></td><td>{info['username']}</td><td>{info['ip']}</td><td>{sb}</td><td>{ab}</td><td><button onclick=\"lock('{mid}')\" style='background:#da3633;color:#fff;border:none;border-radius:4px;padding:4px 12px;cursor:pointer;font-size:13px'>ISOLATE</button></td></tr>"
    if not rows: rows = "<tr><td colspan='7' style='text-align:center;color:#484f58;padding:32px'>No endpoints connected</td></tr>"
    return f"""<!DOCTYPE html><html><head><title>FMSecure C2</title>
    <style>*{{box-sizing:border-box;margin:0;padding:0}}body{{background:#0a0a0a;color:#e6edf3;font-family:system-ui,sans-serif}}
    nav{{background:#161b22;border-bottom:1px solid #30363d;padding:16px 24px;display:flex;justify-content:space-between;align-items:center}}
    .brand{{color:#2f81f7;font-weight:700;font-size:18px}}a{{color:#8b949e;text-decoration:none;font-size:13px;margin-left:16px}}a:hover{{color:#e6edf3}}
    .container{{padding:24px}}table{{width:100%;border-collapse:collapse;background:#161b22;border-radius:8px;overflow:hidden}}
    th{{background:#0d1117;color:#8b949e;padding:12px 16px;text-align:left;font-size:12px;font-weight:600;letter-spacing:.5px}}
    td{{padding:12px 16px;border-top:1px solid #21262d;font-size:14px}}</style></head><body>
    <nav><span class="brand">FMSecure Global C2</span>
    <div><a href="/licenses">Licenses</a><a href="/home">Product Page</a><a href="/pricing">Pricing</a><a href="/logout">Logout</a></div></nav>
    <div class="container"><table><thead><tr><th>MACHINE ID</th><th>HOSTNAME</th><th>USER</th><th>IP</th><th>STATUS</th><th>ENGINE</th><th>ACTION</th></tr></thead>
    <tbody>{rows}</tbody></table></div>
    <script>setTimeout(()=>location.reload(),5000);async function lock(mid){{if(confirm("Isolate?")){{await fetch("/api/trigger_lockdown/"+mid,{{method:"POST"}});alert("Queued!")}}}}</script>
    </body></html>"""

# ══════════════════════════════════════════════════════════════════════════════
# PRODUCT LANDING PAGE
# ══════════════════════════════════════════════════════════════════════════════
@app.get("/home", response_class=HTMLResponse)
async def landing_page():
    base = APP_BASE_URL
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>FMSecure — Enterprise EDR for Windows</title>
    <link rel="icon" href="/static/app_icon.png" type="image/png">
    <link href="https://fonts.googleapis.com/css2?family=Inter:opsz,wght@14..32,300;14..32,400;14..32,500;14..32,600;14..32,700&display=swap" rel="stylesheet">
    <style>
        *{{margin:0;padding:0;box-sizing:border-box}}
        body{{background:#0a0c10;font-family:'Inter',system-ui,sans-serif;color:#e6edf3;line-height:1.5;scroll-behavior:smooth}}
        .container{{max-width:1200px;margin:0 auto;padding:0 24px}}
        nav{{position:sticky;top:0;z-index:100;background:rgba(10,12,16,0.85);backdrop-filter:blur(12px);border-bottom:1px solid rgba(48,54,61,0.5);padding:16px 0}}
        .nav-wrapper{{display:flex;justify-content:space-between;align-items:center;max-width:1200px;margin:0 auto;padding:0 24px}}
        .brand{{font-size:1.5rem;font-weight:700;background:linear-gradient(135deg,#2f81f7,#58a6ff);-webkit-background-clip:text;background-clip:text;color:transparent;text-decoration:none;letter-spacing:-0.5px}}
        .nav-links{{display:flex;align-items:center;gap:32px}}
        .nav-links a{{color:#8b949e;text-decoration:none;font-size:0.9rem;font-weight:500;transition:color 0.2s}}
        .nav-links a:hover{{color:#e6edf3}}
        .btn-nav{{background:#238636;color:#fff!important;padding:8px 18px;border-radius:6px;font-weight:600!important;transition:background 0.2s}}
        .btn-nav:hover{{background:#2ea043}}
        .hero{{position:relative;padding:120px 0 80px;text-align:center;overflow:hidden}}
        .hero::before{{content:'';position:absolute;width:400px;height:400px;background:radial-gradient(circle,rgba(47,129,247,0.15) 0%,rgba(10,12,16,0) 70%);top:-200px;left:50%;transform:translateX(-50%);z-index:0;border-radius:50%;animation:float 8s infinite ease-in-out}}
        @keyframes float{{0%{{transform:translateX(-50%) translateY(0px);opacity:0.6}}50%{{transform:translateX(-50%) translateY(20px);opacity:1}}100%{{transform:translateX(-50%) translateY(0px);opacity:0.6}}}}
        .badge{{display:inline-block;background:rgba(31,41,55,0.8);backdrop-filter:blur(4px);border:1px solid rgba(48,54,61,0.6);color:#8b949e;font-size:0.75rem;padding:6px 14px;border-radius:30px;margin-bottom:28px;font-weight:500}}
        .badge span{{color:#3fb950;margin-right:4px}}
        h1{{font-size:3.5rem;font-weight:700;line-height:1.2;margin-bottom:24px;letter-spacing:-0.02em}}
        .gradient-text{{background:linear-gradient(120deg,#2f81f7,#58a6ff);-webkit-background-clip:text;background-clip:text;color:transparent}}
        .hero p{{color:#8b949e;font-size:1.125rem;max-width:560px;margin:0 auto 40px}}
        .hero-btns{{display:flex;gap:20px;justify-content:center;flex-wrap:wrap}}
        .btn-primary{{background:#2f81f7;color:#fff;padding:14px 32px;border-radius:10px;text-decoration:none;font-weight:600;font-size:1rem;transition:all 0.2s;box-shadow:0 2px 8px rgba(47,129,247,0.2);display:inline-block}}
        .btn-primary:hover{{background:#1f6feb;transform:translateY(-2px);box-shadow:0 8px 20px rgba(47,129,247,0.3)}}
        .btn-secondary{{background:transparent;color:#e6edf3;padding:14px 32px;border-radius:10px;text-decoration:none;font-weight:600;font-size:1rem;border:1px solid #30363d;transition:all 0.2s;display:inline-block}}
        .btn-secondary:hover{{border-color:#8b949e;background:rgba(48,54,61,0.3);transform:translateY(-2px)}}
        .hero-image{{max-width:900px;margin:60px auto 0;border-radius:24px;overflow:hidden;box-shadow:0 20px 35px -10px rgba(0,0,0,0.5);border:1px solid #30363d}}
        .hero-image img{{width:100%;display:block}}
        .features{{padding:100px 0}}
        .section-title{{text-align:center;font-size:2.5rem;font-weight:700;margin-bottom:16px;letter-spacing:-0.02em}}
        .section-sub{{text-align:center;color:#8b949e;max-width:600px;margin:0 auto 56px;font-size:1.1rem}}
        .feature-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:32px}}
        .feature-card{{background:#161b22;border:1px solid #30363d;border-radius:20px;padding:32px;transition:all 0.25s ease;text-align:center}}
        .feature-card:hover{{transform:translateY(-6px);border-color:#2f81f7;box-shadow:0 12px 24px -12px rgba(0,0,0,0.4)}}
        .feature-icon{{width:64px;height:64px;margin:0 auto 24px;background:rgba(47,129,247,0.1);border-radius:20px;display:flex;align-items:center;justify-content:center;font-size:32px;transition:background 0.2s}}
        .feature-card:hover .feature-icon{{background:rgba(47,129,247,0.2)}}
        .feature-card h3{{font-size:1.25rem;font-weight:600;margin-bottom:12px}}
        .feature-card p{{color:#8b949e;font-size:0.9rem;line-height:1.6}}
        .demo{{padding:60px 0}}
        .demo .container{{text-align:center}}
        .demo img{{max-width:100%;border-radius:24px;border:1px solid #30363d;box-shadow:0 20px 35px -10px rgba(0,0,0,0.4)}}
        .cta{{background:linear-gradient(135deg,#0d1117 0%,#161b22 100%);border-top:1px solid #30363d;border-bottom:1px solid #30363d;padding:100px 0;text-align:center}}
        .cta h2{{font-size:2.5rem;margin-bottom:16px}}
        .cta p{{color:#8b949e;font-size:1.1rem;margin-bottom:40px}}
        footer{{text-align:center;color:#484f58;font-size:0.8rem;padding:40px 0;border-top:1px solid #21262d}}
        .fade-up{{opacity:0;transform:translateY(30px);transition:opacity 0.7s ease,transform 0.7s ease}}
        .fade-up.visible{{opacity:1;transform:translateY(0)}}
        @media (max-width:768px){{h1{{font-size:2.2rem}}.section-title{{font-size:2rem}}.hero{{padding:80px 0 60px}}.feature-grid{{gap:20px}}.nav-links{{gap:16px}}.btn-nav{{padding:6px 14px}}}}
    </style>
</head>
<body>
<nav>
    <div class="nav-wrapper">
        <a class="brand" href="/home">FMSecure</a>
        <div class="nav-links">
            <a href="#features">Features</a>
            <a href="{base}/pricing">Pricing</a>
            <a href="{base}/pricing" class="btn-nav">Buy PRO</a>
        </div>
    </div>
</nav>
<main>
    <section class="hero">
        <div class="container">
            <div class="badge fade-up"><span>✓</span> Enterprise-grade EDR for Windows</div>
            <h1 class="fade-up">Protect your files.<br><span class="gradient-text">Stop ransomware</span> before it strikes.</h1>
            <p class="fade-up">FMSecure monitors your critical files in real time, detects ransomware instantly, and locks down your system before damage spreads.</p>
            <div class="hero-btns fade-up">
                <a href="{base}/pricing" class="btn-primary">Get PRO — from ₹999/mo</a>
                <a href="#features" class="btn-secondary">See features</a>
            </div>
        </div>
        <div class="hero-image fade-up">
            <img src="/static/hero_image.png" alt="FMSecure Dashboard Preview">
        </div>
    </section>
    <section class="features" id="features">
        <div class="container">
            <h2 class="section-title fade-up">Everything you need to stay protected</h2>
            <p class="section-sub fade-up">Enterprise-grade security features built for Windows environments.</p>
            <div class="feature-grid">
                <div class="feature-card fade-up"><div class="feature-icon">🔒</div><h3>File Integrity Monitoring</h3><p>HMAC-signed hash records detect any unauthorised change to your critical files the moment it happens.</p></div>
                <div class="feature-card fade-up"><div class="feature-icon">💥</div><h3>Ransomware Killswitch</h3><p>Burst-detection triggers an OS-level folder lockdown via icacls the instant ransomware behaviour is detected.</p></div>
                <div class="feature-card fade-up"><div class="feature-icon">⚡</div><h3>Auto-Heal Vault</h3><p>AES-encrypted local backups. Deleted or modified by malware? Restored in seconds from the vault.</p></div>
                <div class="feature-card fade-up"><div class="feature-icon">☁️</div><h3>Google Drive Cloud Backup</h3><p>Encrypted vault files sync to your Google Drive automatically. Even if your drive is destroyed, your files are safe.</p></div>
                <div class="feature-card fade-up"><div class="feature-icon">🔍</div><h3>Forensic Incident Vault</h3><p>Every security event generates an AES-encrypted forensic snapshot. Readable only inside FMSecure.</p></div>
                <div class="feature-card fade-up"><div class="feature-icon">📀</div><h3>USB DLP Control</h3><p>Block USB drives from writing to your system at the registry level. Prevent data exfiltration.</p></div>
            </div>
        </div>
    </section>
    <section class="demo">
        <div class="container">
            <h2 class="section-title fade-up">Centralized Command & Control</h2>
            <p class="section-sub fade-up">Monitor your entire fleet from our live C2 dashboard — real‑time alerts, remote lockdown, and forensic insights.</p>
            <div class="fade-up">
                <img src="/static/c2.png" alt="FMSecure C2 Dashboard">
            </div>
        </div>
    </section>
    <section class="cta">
        <div class="container">
            <h2 class="fade-up">Ready to protect your business?</h2>
            <p class="fade-up">Cancel anytime. License key delivered instantly after payment.</p>
            <a href="{base}/pricing" class="btn-primary fade-up" style="display:inline-block; font-size:1.1rem; padding:16px 44px;">See pricing →</a>
        </div>
    </section>
</main>
<footer>
    <div class="container">
        <p>FMSecure v2.0 &bull; Enterprise Endpoint Detection &amp; Response &bull; Made in India</p>
    </div>
</footer>
<script>
    const fadeElements = document.querySelectorAll('.fade-up');
    const observer = new IntersectionObserver((entries) => {{
        entries.forEach(entry => {{
            if (entry.isIntersecting) {{
                entry.target.classList.add('visible');
                observer.unobserve(entry.target);
            }}
        }});
    }}, {{ threshold: 0.1, rootMargin: "0px 0px -30px 0px" }});
    fadeElements.forEach(el => observer.observe(el));
    window.addEventListener('load', () => {{
        fadeElements.forEach(el => {{
            const rect = el.getBoundingClientRect();
            if (rect.top < window.innerHeight) {{
                el.classList.add('visible');
                observer.unobserve(el);
            }}
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
          <div class="price">&#x20B9;999<span>/mo</span></div>
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
          <div class="price">&#x20B9;9,999<span>/yr</span></div>
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
          <button class="btn btn-green" onclick="startPayment('pro_annual')">Buy Annual &#x2014; &#x20B9;9,999</button>
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
    <div><a href="/">&#x2190; C2 Dashboard</a><a href="/logout">Logout</a></div></nav>
    <div class="container"><table><thead><tr>
      <th>LICENSE KEY</th><th>EMAIL</th><th>TIER</th><th>STATUS</th><th>EXPIRES</th><th>DEVICE ID</th>
    </tr></thead><tbody>{trs}</tbody></table></div></body></html>"""
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