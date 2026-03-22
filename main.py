"""
main.py — FMSecure C2 + License Server
Railway deployment — PostgreSQL + Razorpay

Environment variables to set in Railway → Variables:
  ADMIN_USERNAME        = your admin username
  ADMIN_PASSWORD        = a strong password (not "password")
  API_KEY               = same key as in your desktop agent's integrity_core.py
  RAZORPAY_KEY_ID       = rzp_live_... (or rzp_test_... while testing)
  RAZORPAY_KEY_SECRET   = your Razorpay key secret
  LICENSE_HMAC_SECRET   = generate with: python -c "import secrets;print(secrets.token_hex(32))"
  ADMIN_API_KEY         = any secret string for admin endpoints
  APP_BASE_URL          = https://your-server.railway.app  (no trailing slash)
  DATABASE_URL          = auto-set by Railway PostgreSQL plugin — do NOT add manually
  SENDGRID_API_KEY      = (optional) auto-emails license keys to customers

Add to requirements.txt:
  razorpay
  psycopg2-binary
  slowapi
  sendgrid   (optional)
"""

import os
import secrets
import time
import hashlib
import hmac as _hmac
import uuid
from datetime import datetime, timezone, timedelta

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

rzp_client = razorpay.Client(auth=(RZP_KEY_ID, RZP_KEY_SECRET))

# Amounts in PAISE (1 INR = 100 paise)
PLANS = {
    "pro_monthly": {
        "label":       "PRO Monthly",
        "amount":      99900,   # Rs. 999
        "currency":    "INR",
        "description": "FMSecure PRO - Monthly Subscription",
        "days":        31,
    },
    "pro_annual": {
        "label":       "PRO Annual",
        "amount":      999900,  # Rs. 9,999
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
try:
    app.mount("/static", StaticFiles(directory="static"), name="static")
except Exception:
    pass

agents   = {}  # C2 telemetry — in-memory, resets on restart by design
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
    print("[DB] Tables ready.")

@app.on_event("startup")
async def startup():
    if DATABASE_URL:
        init_db()
    else:
        print("[DB] WARNING: No DATABASE_URL. Add the PostgreSQL plugin on Railway.")


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
    """Deterministic HMAC key — same inputs always give the same key."""
    payload = f"{tier}:{email.lower()}:{payment_id}"
    sig = _hmac.new(
        LICENSE_SECRET.encode(), payload.encode(), hashlib.sha256
    ).hexdigest()[:16].upper()
    prefix = "PRA" if "annual" in tier else "PRM"
    return f"FMSECURE-{prefix}-{sig}"

def _save_license(license_key, email, tier, payment_id, order_id, expires_iso):
    conn = get_db()
    cur  = conn.cursor()
    cur.execute("""
        INSERT INTO licenses (license_key, email, tier, payment_id, order_id, expires_at, active)
        VALUES (%s, %s, %s, %s, %s, %s, TRUE)
        ON CONFLICT (license_key) DO UPDATE SET
            expires_at = EXCLUDED.expires_at, active = TRUE
    """, (license_key, email.lower(), tier, payment_id, order_id, expires_iso))
    conn.commit()
    cur.close()
    conn.close()

def _check_admin_key(api_key: str):
    if api_key != ADMIN_API_KEY:
        raise HTTPException(status_code=403, detail="Forbidden")

async def verify_session(fmsecure_session: str = Cookie(None)):
    if not fmsecure_session or not secrets.compare_digest(fmsecure_session, SESSION_TOKEN):
        raise HTTPException(status_code=302, headers={"Location": "/login"})
    return True

def _send_license_email(email: str, license_key: str, tier: str, expires_iso: str):
    tier_label  = PLANS.get(tier, {}).get("label", "PRO")
    expires_str = expires_iso[:10]
    sgkey       = os.getenv("SENDGRID_API_KEY", "")
    if not sgkey:
        print(f"[EMAIL] No SendGrid key. Key for {email}: {license_key}")
        return
    try:
        import sendgrid
        from sendgrid.helpers.mail import Mail
        sendgrid.SendGridAPIClient(api_key=sgkey).send(Mail(
            from_email   = "noreply@fmsecure.app",
            to_emails    = email,
            subject      = "Your FMSecure PRO License Key",
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
                To activate: Open FMSecure &rarr; click your username &rarr;
                Activate License &rarr; enter your email + this key.
              </p>
            </div>"""
        ))
        print(f"[EMAIL] Sent to {email}")
    except Exception as e:
        print(f"[EMAIL] Failed for {email}: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# AUTH
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
             padding:12px;font-size:14px;font-weight:600;cursor:pointer}}
    button:hover{{background:#2ea043}}</style></head><body>
    <div class="card">
      <h3>FMSecure C2</h3><p class="sub">Enterprise Authentication</p>
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
    machine_id: str
    hostname: str
    username: str
    tier: str
    is_armed: bool

@app.post("/api/heartbeat")
@limiter.limit("200/minute")
async def receive_heartbeat(request: Request, data: Heartbeat):
    if request.headers.get("x-api-key") != API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")
    agents[data.machine_id] = {
        "hostname": data.hostname, "username": data.username,
        "tier": data.tier, "is_armed": data.is_armed,
        "last_seen": time.time(), "ip": request.client.host
    }
    cmd = commands.pop(data.machine_id, "NONE")
    return {"status": "ok", "command": cmd}

@app.post("/api/trigger_lockdown/{machine_id}")
async def trigger_lockdown(machine_id: str, _: bool = Depends(verify_session)):
    commands[machine_id] = "LOCKDOWN"
    return {"status": "Lockdown command queued"}

@app.get("/", response_class=HTMLResponse)
async def dashboard(_: bool = Depends(verify_session)):
    now = time.time()
    rows = ""
    for mid, info in agents.items():
        online = (now - info["last_seen"]) < 30
        sb = ('<span style="background:#238636;color:#fff;padding:2px 8px;border-radius:4px;font-size:12px">ONLINE</span>'
              if online else '<span style="background:#30363d;color:#8b949e;padding:2px 8px;border-radius:4px;font-size:12px">OFFLINE</span>')
        ab = ('<span style="background:#1f6feb;color:#fff;padding:2px 8px;border-radius:4px;font-size:12px">ARMED</span>'
              if info["is_armed"] else '<span style="background:#9e6a03;color:#fff;padding:2px 8px;border-radius:4px;font-size:12px">UNARMED</span>')
        rows += (f"<tr><td style='font-family:monospace;color:#8b949e'>{mid[:14]}...</td>"
                 f"<td><strong>{info['hostname']}</strong></td><td>{info['username']}</td>"
                 f"<td>{info['ip']}</td><td>{sb}</td><td>{ab}</td>"
                 f"<td><button onclick=\"lock('{mid}')\" style='background:#da3633;color:#fff;"
                 f"border:none;border-radius:4px;padding:4px 12px;cursor:pointer;font-size:13px'>ISOLATE</button></td></tr>")
    if not rows:
        rows = "<tr><td colspan='7' style='text-align:center;color:#484f58;padding:32px'>No endpoints connected</td></tr>"

    return f"""<!DOCTYPE html><html><head><title>FMSecure C2</title>
    <style>*{{box-sizing:border-box;margin:0;padding:0}}
    body{{background:#0a0a0a;color:#e6edf3;font-family:system-ui,sans-serif}}
    nav{{background:#161b22;border-bottom:1px solid #30363d;padding:16px 24px;
         display:flex;justify-content:space-between;align-items:center}}
    .brand{{color:#2f81f7;font-weight:700;font-size:18px}}
    a{{color:#8b949e;text-decoration:none;font-size:13px;margin-left:16px}}a:hover{{color:#e6edf3}}
    .container{{padding:24px}}
    table{{width:100%;border-collapse:collapse;background:#161b22;border-radius:8px;overflow:hidden}}
    th{{background:#0d1117;color:#8b949e;padding:12px 16px;text-align:left;
        font-size:12px;font-weight:600;letter-spacing:.5px}}
    td{{padding:12px 16px;border-top:1px solid #21262d;font-size:14px}}</style></head><body>
    <nav><span class="brand">FMSecure Global C2</span>
    <div><a href="/licenses">Licenses</a><a href="/pricing">Pricing</a><a href="/logout">Logout</a></div></nav>
    <div class="container"><table><thead><tr>
      <th>MACHINE ID</th><th>HOSTNAME</th><th>USER</th><th>IP</th>
      <th>STATUS</th><th>ENGINE</th><th>ACTION</th>
    </tr></thead><tbody>{rows}</tbody></table></div>
    <script>
      setTimeout(()=>location.reload(),5000);
      async function lock(mid){{
        if(confirm("Isolate this endpoint?")){{
          await fetch("/api/trigger_lockdown/"+mid,{{method:"POST"}});
          alert("Lockdown queued!");}}}}
    </script></body></html>"""


# ══════════════════════════════════════════════════════════════════════════════
# PRICING PAGE (public — no login required)
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/pricing", response_class=HTMLResponse)
async def pricing_page():
    base   = APP_BASE_URL
    rzpkey = RZP_KEY_ID
    return f"""<!DOCTYPE html><html><head><title>FMSecure PRO — Pricing</title>
    <script src="https://checkout.razorpay.com/v1/checkout.js"></script>
    <style>
      *{{box-sizing:border-box;margin:0;padding:0}}
      body{{background:#0d1117;color:#e6edf3;font-family:system-ui,sans-serif;min-height:100vh}}
      nav{{background:#161b22;border-bottom:1px solid #30363d;padding:16px 48px;
           display:flex;justify-content:space-between;align-items:center}}
      .brand{{color:#2f81f7;font-weight:700;font-size:20px;text-decoration:none}}
      main{{max-width:900px;margin:0 auto;padding:64px 24px}}
      h1{{text-align:center;font-size:36px;font-weight:700;margin-bottom:12px}}
      .sub{{text-align:center;color:#8b949e;font-size:16px;margin-bottom:56px}}
      .cards{{display:flex;gap:24px;justify-content:center;flex-wrap:wrap}}
      .card{{background:#161b22;border:1px solid #30363d;border-radius:16px;
             padding:36px 32px;width:340px;position:relative}}
      .card.featured{{border-color:#2f81f7}}
      .badge{{position:absolute;top:-13px;left:50%;transform:translateX(-50%);
              background:#2f81f7;color:#fff;padding:4px 16px;border-radius:20px;
              font-size:12px;font-weight:600;letter-spacing:.5px;white-space:nowrap}}
      .plan{{color:#8b949e;font-size:12px;font-weight:600;letter-spacing:.5px;margin-bottom:8px}}
      .price{{font-size:42px;font-weight:700;margin-bottom:4px}}
      .price span{{font-size:18px;color:#8b949e;font-weight:400}}
      .period{{color:#8b949e;font-size:14px;margin-bottom:28px}}
      .savings{{color:#3fb950}}
      .email-row{{margin-bottom:16px}}
      .email-row label{{display:block;font-size:11px;color:#8b949e;font-weight:600;
                        letter-spacing:.5px;margin-bottom:6px}}
      .email-row input{{width:100%;background:#0d1117;border:1px solid #30363d;
                        border-radius:6px;color:#e6edf3;padding:10px 12px;
                        font-size:14px;outline:none}}
      .email-row input:focus{{border-color:#2f81f7}}
      ul{{list-style:none;margin-bottom:28px}}
      li{{padding:8px 0;font-size:14px;border-bottom:1px solid #21262d;color:#8b949e}}
      li:last-child{{border-bottom:none}}
      li strong{{color:#e6edf3}}
      .check{{color:#3fb950;margin-right:8px;font-weight:700}}
      .btn{{width:100%;padding:14px;border:none;border-radius:8px;font-size:15px;
             font-weight:600;cursor:pointer;transition:opacity .15s}}
      .btn:hover{{opacity:.85}}
      .btn-blue{{background:#2f81f7;color:#fff}}
      .btn-green{{background:#238636;color:#fff}}
      .note{{text-align:center;color:#484f58;font-size:13px;margin-top:36px;line-height:1.7}}
      footer{{text-align:center;color:#484f58;font-size:13px;padding:48px 24px}}
    </style></head><body>
    <nav>
      <a class="brand" href="/pricing">FMSecure</a>
      <span style="color:#8b949e;font-size:14px">Enterprise EDR for Windows</span>
    </nav>
    <main>
      <h1>Simple, transparent pricing</h1>
      <p class="sub">No hidden fees. Cancel anytime. Your license key is emailed instantly.</p>
      <div class="cards">

        <div class="card">
          <p class="plan">PRO MONTHLY</p>
          <div class="price">&#x20B9;999<span>/mo</span></div>
          <p class="period">Billed monthly, cancel anytime</p>
          <div class="email-row">
            <label>YOUR EMAIL (license will be sent here)</label>
            <input type="email" id="email-monthly" placeholder="you@example.com">
          </div>
          <ul>
            <li><span class="check">&#10003;</span><strong>5 folders</strong> monitored simultaneously</li>
            <li><span class="check">&#10003;</span><strong>Active Defense</strong> + auto-heal vault</li>
            <li><span class="check">&#10003;</span><strong>Ransomware killswitch</strong></li>
            <li><span class="check">&#10003;</span><strong>USB DLP</strong> device control</li>
            <li><span class="check">&#10003;</span><strong>Google Drive</strong> cloud backup</li>
            <li><span class="check">&#10003;</span><strong>Forensic vault</strong> + incident snapshots</li>
            <li><span class="check">&#10003;</span>Email security alerts</li>
          </ul>
          <button class="btn btn-blue" onclick="startPayment('pro_monthly')">
            Buy Monthly &#x2014; &#x20B9;999
          </button>
        </div>

        <div class="card featured">
          <div class="badge">BEST VALUE &#x2014; SAVE &#x20B9;1,989</div>
          <p class="plan">PRO ANNUAL</p>
          <div class="price">&#x20B9;9,999<span>/yr</span></div>
          <p class="period">&#x20B9;833/mo billed annually <span class="savings">&#x2714; 2 months free</span></p>
          <div class="email-row">
            <label>YOUR EMAIL (license will be sent here)</label>
            <input type="email" id="email-annual" placeholder="you@example.com">
          </div>
          <ul>
            <li><span class="check">&#10003;</span><strong>Everything</strong> in Monthly</li>
            <li><span class="check">&#10003;</span><strong>Priority</strong> email support</li>
            <li><span class="check">&#10003;</span><strong>Early access</strong> to new features</li>
            <li><span class="check">&#10003;</span>Feature request priority</li>
            <li><span class="check">&#10003;</span>Invoice / receipt for business</li>
            <li><span class="check">&#10003;</span>Extended 30-day offline grace period</li>
            <li><span class="check">&#10003;</span>2 months free vs monthly billing</li>
          </ul>
          <button class="btn btn-green" onclick="startPayment('pro_annual')">
            Buy Annual &#x2014; &#x20B9;9,999
          </button>
        </div>

      </div>
      <p class="note">
        Payments secured by Razorpay &bull; UPI, Net Banking, Credit/Debit Cards, Wallets accepted<br>
        Your license key is emailed immediately after payment &bull; Works on Windows 10/11
      </p>
    </main>
    <footer>FMSecure v2.0 &bull; Enterprise Endpoint Detection &amp; Response &bull; Made in India</footer>

    <script>
    async function startPayment(tier) {{
      const emailField = tier === 'pro_monthly' ? 'email-monthly' : 'email-annual';
      const email = document.getElementById(emailField).value.trim();

      if (!email || !email.includes('@') || !email.includes('.')) {{
        alert('Please enter a valid email address before paying.\\nYour license key will be sent there.');
        document.getElementById(emailField).focus();
        return;
      }}

      // Step 1: Ask our server to create a Razorpay order
      let orderData;
      try {{
        const resp = await fetch('{base}/payment/create-order', {{
          method: 'POST',
          headers: {{'Content-Type': 'application/json'}},
          body: JSON.stringify({{ tier, email }})
        }});
        orderData = await resp.json();
      }} catch(e) {{
        alert('Could not connect to payment server. Please try again in a moment.');
        return;
      }}

      if (orderData.error) {{
        alert('Error: ' + orderData.error);
        return;
      }}

      // Step 2: Open Razorpay checkout popup
      const options = {{
        key:         '{rzpkey}',
        amount:      orderData.amount,
        currency:    orderData.currency,
        name:        'FMSecure',
        description: orderData.description,
        order_id:    orderData.order_id,
        prefill:     {{ email: email }},
        theme:       {{ color: '#2f81f7' }},

        handler: async function(response) {{
          // Step 3: Payment done — verify the signature on our server
          let result;
          try {{
            const vResp = await fetch('{base}/payment/verify', {{
              method: 'POST',
              headers: {{'Content-Type': 'application/json'}},
              body: JSON.stringify({{
                razorpay_order_id:   response.razorpay_order_id,
                razorpay_payment_id: response.razorpay_payment_id,
                razorpay_signature:  response.razorpay_signature,
                email: email,
                tier:  tier
              }})
            }});
            result = await vResp.json();
          }} catch(e) {{
            alert('Verification error. Please contact support with your payment ID: ' + response.razorpay_payment_id);
            return;
          }}

          if (result.success) {{
            window.location.href = '{base}/payment/success?key='
              + encodeURIComponent(result.license_key)
              + '&email=' + encodeURIComponent(email)
              + '&tier=' + encodeURIComponent(tier);
          }} else {{
            alert('Payment verification failed. Please contact support.\\nPayment ID: ' + response.razorpay_payment_id);
          }}
        }},

        modal: {{ ondismiss: function() {{}} }}
      }};

      new Razorpay(options).open();
    }}
    </script>
    </body></html>"""


# ══════════════════════════════════════════════════════════════════════════════
# PAYMENT ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

class CreateOrderRequest(BaseModel):
    tier:  str
    email: str

class VerifyPaymentRequest(BaseModel):
    razorpay_order_id:   str
    razorpay_payment_id: str
    razorpay_signature:  str
    email:               str
    tier:                str


@app.post("/payment/create-order")
@limiter.limit("20/minute")
async def create_order(request: Request, body: CreateOrderRequest):
    """
    Called by the browser before the checkout popup opens.
    Creates a Razorpay order and returns the order_id.
    """
    tier  = body.tier.strip().lower()
    email = body.email.strip().lower()

    if tier not in PLANS:
        return JSONResponse({"error": "Invalid plan"}, status_code=400)
    if not email or "@" not in email:
        return JSONResponse({"error": "Invalid email"}, status_code=400)

    plan = PLANS[tier]
    try:
        order = rzp_client.order.create({
            "amount":   plan["amount"],
            "currency": plan["currency"],
            "receipt":  f"fm_{uuid.uuid4().hex[:8]}",
            "notes":    {"email": email, "tier": tier}
        })
    except Exception as e:
        print(f"[RZP] Order creation failed: {e}")
        return JSONResponse({"error": "Payment gateway error"}, status_code=500)

    # Save pending order so we can reference it later
    try:
        conn = get_db()
        cur  = conn.cursor()
        cur.execute("""
            INSERT INTO pending_orders (order_id, email, tier, amount)
            VALUES (%s, %s, %s, %s) ON CONFLICT (order_id) DO NOTHING
        """, (order["id"], email, tier, plan["amount"]))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"[DB] Pending order save error: {e}")

    return {
        "order_id":    order["id"],
        "amount":      plan["amount"],
        "currency":    plan["currency"],
        "description": plan["description"],
    }


@app.post("/payment/verify")
@limiter.limit("20/minute")
async def verify_payment(request: Request, body: VerifyPaymentRequest):
    """
    Called by the browser after the Razorpay popup closes successfully.
    """
    # 1. Official Razorpay Signature Verification
    try:
        rzp_client.utility.verify_payment_signature({
            'razorpay_order_id': body.razorpay_order_id,
            'razorpay_payment_id': body.razorpay_payment_id,
            'razorpay_signature': body.razorpay_signature
        })
    except razorpay.errors.SignatureVerificationError:
        print(f"❌ [RZP ERROR] Signature mismatch! Check if RAZORPAY_KEY_SECRET is correct in Railway.")
        return JSONResponse({"success": False, "error": "Signature verification failed"}, status_code=400)

    # 2. Signature valid — generate the license
    tier        = body.tier.strip().lower()
    email       = body.email.strip().lower()
    payment_id  = body.razorpay_payment_id
    order_id    = body.razorpay_order_id
    days        = PLANS.get(tier, {}).get("days", 31)
    expires_iso = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
    license_key = _generate_license_key(tier, email, payment_id)

    # 3. Save to PostgreSQL Database
    try:
        _save_license(license_key, email, tier, payment_id, order_id, expires_iso)
    except Exception as e:
        print(f"❌ [DB ERROR] Failed to save license to Postgres: {e}")
        return JSONResponse({"success": False, "error": "Database error — please contact support"}, status_code=500)

    # 4. Clean up pending order
    try:
        conn = get_db()
        cur  = conn.cursor()
        cur.execute("DELETE FROM pending_orders WHERE order_id = %s", (order_id,))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"⚠️ [DB WARNING] Could not delete pending order: {e}")

    # 5. Send Email and Return Success
    _send_license_email(email, license_key, tier, expires_iso)
    print(f"✅ [PAYMENT SUCCESS] Generated key {license_key} for {email}")

    return {"success": True, "license_key": license_key, "tier": tier, "expires_at": expires_iso}


@app.get("/payment/success", response_class=HTMLResponse)
async def payment_success(key: str = "", email: str = "", tier: str = ""):
    tier_label = PLANS.get(tier, {}).get("label", "PRO")
    return f"""<!DOCTYPE html><html><head><title>Payment Successful | FMSecure</title>
    <style>*{{box-sizing:border-box;margin:0;padding:0}}
    body{{background:#0d1117;color:#e6edf3;font-family:system-ui,sans-serif;
         display:flex;align-items:center;justify-content:center;min-height:100vh;padding:24px}}
    .card{{background:#161b22;border:1px solid #238636;border-radius:16px;
           padding:48px 40px;max-width:480px;width:100%;text-align:center}}
    .icon{{font-size:56px;margin-bottom:16px}}
    h2{{color:#3fb950;font-size:24px;margin-bottom:8px}}
    p{{color:#8b949e;font-size:15px;margin-bottom:0;line-height:1.6}}
    .key-box{{background:#0d1117;border:1px solid #30363d;border-radius:8px;
              padding:20px;margin:24px 0}}
    .key-label{{color:#484f58;font-size:11px;letter-spacing:1px;margin-bottom:10px}}
    .key{{color:#2f81f7;font-size:20px;font-family:monospace;font-weight:700;
          letter-spacing:2px;word-break:break-all}}
    .copy-btn{{margin-top:14px;background:#30363d;border:none;color:#e6edf3;
               padding:8px 20px;border-radius:6px;cursor:pointer;font-size:13px}}
    .copy-btn:hover{{background:#3d444d}}
    .steps{{text-align:left;background:#0d1117;border-radius:8px;padding:20px 24px;
            font-size:14px;color:#8b949e;line-height:2.2;margin-top:0}}
    strong{{color:#e6edf3}}</style></head><body>
    <div class="card">
      <div class="icon">&#9989;</div>
      <h2>Payment successful!</h2>
      <p>Your <strong>{tier_label}</strong> is now active.<br>
         We've also emailed this key to <strong>{email}</strong></p>
      <div class="key-box">
        <div class="key-label">YOUR LICENSE KEY</div>
        <div class="key" id="lk">{key}</div>
        <button class="copy-btn" onclick="navigator.clipboard.writeText('{key}');this.textContent='&#10003; Copied!'">
          Copy key
        </button>
      </div>
      <div class="steps">
        <strong>How to activate in FMSecure:</strong><br>
        1. Open <strong>FMSecure</strong> on your PC<br>
        2. Click your <strong>username</strong> (top-right corner)<br>
        3. Click <strong>Activate License</strong><br>
        4. Enter your email + paste this key<br>
        5. Click <strong>Activate</strong> &#x2014; PRO unlocked!
      </div>
    </div></body></html>"""


# ══════════════════════════════════════════════════════════════════════════════
# LICENSE VALIDATION (called by the desktop app)
# ══════════════════════════════════════════════════════════════════════════════

class LicenseValidateRequest(BaseModel):
    email:       str
    license_key: str

@app.post("/api/license/validate")
async def validate_license(req: LicenseValidateRequest):
    key   = req.license_key.strip()
    email = req.email.strip().lower()
    if not DATABASE_URL:
        return {"valid": False, "tier": "free", "reason": "db_not_configured"}
    try:
        conn = get_db()
        cur  = conn.cursor()
        cur.execute("SELECT * FROM licenses WHERE license_key = %s", (key,))
        r = cur.fetchone()
        cur.close()
        conn.close()
    except Exception:
        return {"valid": False, "tier": "free", "reason": "db_error"}
    if not r:
        return {"valid": False, "tier": "free", "expires_at": None, "reason": "key_not_found"}
    if r["email"].lower() != email:
        return {"valid": False, "tier": "free", "expires_at": None, "reason": "email_mismatch"}
    if not r["active"] or _is_expired(r["expires_at"]):
        return {"valid": False, "tier": "free",
                "expires_at": r["expires_at"].isoformat() if r["expires_at"] else None,
                "reason": "subscription_expired"}
    return {"valid": True, "tier": r["tier"],
            "expires_at": r["expires_at"].isoformat(), "reason": "ok"}

@app.post("/api/license/activate")
async def activate_license(req: LicenseValidateRequest):
    return await validate_license(req)


# ══════════════════════════════════════════════════════════════════════════════
# ADMIN ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/license/list")
async def list_licenses(api_key: str = ""):
    _check_admin_key(api_key)
    conn = get_db()
    cur  = conn.cursor()
    cur.execute("SELECT * FROM licenses ORDER BY created_at DESC")
    rows = [dict(r) for r in cur.fetchall()]
    cur.close()
    conn.close()
    return {"count": len(rows), "licenses": rows}

@app.post("/api/license/create_manual")
async def create_manual_license(email: str, tier: str = "pro_monthly",
                                 days: int = 30, api_key: str = ""):
    """Create a license without payment — for testers and beta users."""
    _check_admin_key(api_key)
    sub_id      = f"manual_{uuid.uuid4().hex[:8]}"
    expires_iso = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
    license_key = _generate_license_key(tier, email, sub_id)
    _save_license(license_key, email, tier, sub_id, sub_id, expires_iso)
    return {"license_key": license_key, "email": email,
            "tier": tier, "expires_at": expires_iso}

@app.get("/licenses", response_class=HTMLResponse)
async def licenses_page(_: bool = Depends(verify_session)):
    conn = get_db()
    cur  = conn.cursor()
    cur.execute("SELECT * FROM licenses ORDER BY created_at DESC LIMIT 500")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    table_rows = ""
    for r in rows:
        expired = _is_expired(r["expires_at"])
        sb = ('<span style="background:#238636;color:#fff;padding:2px 8px;border-radius:4px;font-size:12px">Active</span>'
              if not expired and r["active"]
              else '<span style="background:#da3633;color:#fff;padding:2px 8px;border-radius:4px;font-size:12px">Expired</span>')
        exp = r["expires_at"].strftime("%Y-%m-%d") if r["expires_at"] else "—"
        table_rows += (f"<tr><td style='font-family:monospace;font-size:12px'>{r['license_key']}</td>"
                       f"<td>{r['email']}</td><td>{r['tier']}</td><td>{sb}</td><td>{exp}</td></tr>")
    return f"""<!DOCTYPE html><html><head><title>FMSecure | Licenses</title>
    <style>*{{box-sizing:border-box;margin:0;padding:0}}
    body{{background:#0a0a0a;color:#e6edf3;font-family:system-ui,sans-serif}}
    nav{{background:#161b22;border-bottom:1px solid #30363d;padding:16px 24px;
         display:flex;justify-content:space-between;align-items:center}}
    .brand{{color:#2f81f7;font-weight:700}}
    a{{color:#8b949e;text-decoration:none;font-size:13px;margin-left:16px}}a:hover{{color:#e6edf3}}
    .container{{padding:24px}}
    table{{width:100%;border-collapse:collapse;background:#161b22;border-radius:8px;overflow:hidden}}
    th{{background:#0d1117;color:#8b949e;padding:12px 16px;text-align:left;
        font-size:12px;font-weight:600;letter-spacing:.5px}}
    td{{padding:12px 16px;border-top:1px solid #21262d;font-size:13px}}</style></head><body>
    <nav><span class="brand">License Manager</span>
    <div><a href="/">&#x2190; C2 Dashboard</a><a href="/logout">Logout</a></div></nav>
    <div class="container"><table><thead><tr>
      <th>LICENSE KEY</th><th>EMAIL</th><th>TIER</th><th>STATUS</th><th>EXPIRES</th>
    </tr></thead><tbody>{table_rows}</tbody></table></div></body></html>"""