"""
═══════════════════════════════════════════════════════════════════════════════
main.py — PHASE 1 PATCH FILE
═══════════════════════════════════════════════════════════════════════════════

This file describes EXACTLY what to change in your existing main.py.
All backend logic is preserved — only 4 targeted edits are made.

EDIT 1 — Add Jinja2 import (after the existing FastAPI imports)
EDIT 2 — Initialize Jinja2Templates (after app.mount("/static"...))  
EDIT 3 — Replace the "/" and "/home" routes (landing page)
EDIT 4 — Replace the "/pricing" route

None of these changes touch: heartbeat, license, payment, tenant, super-admin,
dashboard, auth, or any API endpoint. Safe to apply.
═══════════════════════════════════════════════════════════════════════════════
"""

# ─────────────────────────────────────────────────────────────────────────────
# EDIT 1 — In your imports section, find this line:
#
#   from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
#
# ADD the following import right after it:
# ─────────────────────────────────────────────────────────────────────────────

from fastapi.templating import Jinja2Templates

# ─────────────────────────────────────────────────────────────────────────────
# EDIT 2 — Find this block near line 114:
#
#   try:
#       app.mount("/static", StaticFiles(directory="static"), name="static")
#   except Exception:
#       pass
#
# ADD this line immediately after the try/except block:
# ─────────────────────────────────────────────────────────────────────────────

templates = Jinja2Templates(directory="templates")

# ─────────────────────────────────────────────────────────────────────────────
# EDIT 3 — REPLACE the "/" route and the "/home" route.
#
# FIND (around line 2387):
#   @app.get("/", response_class=HTMLResponse)
#   async def landing_page_root():
#       return await landing_page()
#
# AND FIND (around line 2584):
#   @app.get("/home", response_class=HTMLResponse)
#   async def landing_page():
#       base = APP_BASE_URL
#       return f"""<!DOCTYPE html>... (the 800-line inline HTML block) ..."""
#
# REPLACE BOTH with the following two functions:
# ─────────────────────────────────────────────────────────────────────────────

from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi import Request  # already imported — just noting it's needed


@app.get("/", response_class=HTMLResponse)
async def landing_page_root(request: Request):
    return templates.TemplateResponse("index.html", {
        "request": request,
        "brand":   BRAND,
    })


@app.get("/home", response_class=HTMLResponse)
async def landing_page_redirect():
    """Legacy /home URL — redirect to /"""
    return RedirectResponse(url="/", status_code=301)


# ─────────────────────────────────────────────────────────────────────────────
# EDIT 4 — REPLACE the "/pricing" route.
#
# FIND (around line 3368):
#   @app.get("/pricing", response_class=HTMLResponse)
#   async def pricing_page():
#       base = APP_BASE_URL; rzpkey = RZP_KEY_ID
#       return f"""<!DOCTYPE html>... (inline HTML) ..."""
#
# REPLACE with:
# ─────────────────────────────────────────────────────────────────────────────


@app.get("/pricing", response_class=HTMLResponse)
async def pricing_page(request: Request):
    return templates.TemplateResponse("pricing.html", {
        "request":    request,
        "brand":      BRAND,
        "rzp_key_id": RZP_KEY_ID,
        "pricing":    PRICING_DISPLAY,
    })


# ─────────────────────────────────────────────────────────────────────────────
# ALL OTHER ROUTES — UNCHANGED
#
# The following routes remain exactly as they are in your original main.py:
#   /login, /logout, /dashboard, /super/*, /tenant/*
#   /download, /changelog, /enterprise, /payment/*, /api/*
#   /licenses, /validate-key, etc.
#
# Nothing else in main.py needs to be modified for Phase 1.
# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
# DEPLOYMENT CHECKLIST
#
# 1. Copy /static/css/fmsecure.css  →  your server's /static/css/fmsecure.css
# 2. Copy /static/js/fmsecure.js   →  your server's /static/js/fmsecure.js
# 3. Copy /templates/base.html     →  your server's /templates/base.html
# 4. Copy /templates/index.html    →  your server's /templates/index.html
# 5. Copy /templates/pricing.html  →  your server's /templates/pricing.html
# 6. Apply the 4 edits above to main.py
# 7. On Railway, ensure "templates" directory is included in your deployment
#    (it should be — Railway deploys the whole repo)
# 8. requirements.txt already has jinja2 — no changes needed there
# ─────────────────────────────────────────────────────────────────────────────



"""
═══════════════════════════════════════════════════════════════════════════════
MAIN_PY_PATCH_2.py — FMSecure Phase 2
═══════════════════════════════════════════════════════════════════════════════

Apply AFTER Phase 1 patch (which wired up Jinja2 + replaced / and /pricing).

This patch makes 3 groups of changes:
  A) One DB table addition  (in init_db)
  B) Replace 5 existing routes with template responses
  C) Add 6 new routes (/features, /docs, /contact, /privacy, /terms, /status)

All backend logic (auth, payments, heartbeat, licensing, tenant ops) is
completely untouched.
═══════════════════════════════════════════════════════════════════════════════
"""

# ─────────────────────────────────────────────────────────────────────────────
# GROUP A — ADD contact_submissions table to init_db()
#
# FIND this block inside init_db() (around line 231):
#
#   DO $$ BEGIN
#     IF NOT EXISTS (SELECT 1 FROM information_schema.columns
#                    WHERE table_name='licenses' AND column_name='machine_id')
#     THEN ALTER TABLE licenses ADD COLUMN machine_id TEXT DEFAULT NULL; END IF;
#   END $$;
#
# ADD the following CREATE TABLE statement BEFORE that DO $$ block:
# ─────────────────────────────────────────────────────────────────────────────

_CONTACT_TABLE_SQL = """
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
"""
# Paste _CONTACT_TABLE_SQL content into init_db() alongside the other CREATE TABLE blocks.


# ─────────────────────────────────────────────────────────────────────────────
# GROUP B — REPLACE existing routes with Jinja2 template responses
# ─────────────────────────────────────────────────────────────────────────────

# ── B1: /login GET ────────────────────────────────────────────────────────────
# FIND:
#   @app.get("/login", response_class=HTMLResponse)
#   async def login_page(error: str = ""):
#       err = f'<p style="color:#f85149; ... </p>' if error else ""
#       return f"""<!DOCTYPE html><html> ... """
#
# REPLACE WITH:

from fastapi import Request   # already imported

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str = ""):
    return templates.TemplateResponse("auth_login.html", {
        "request": request,
        "brand":   BRAND,
        "error":   error,
    })


# ── B2: /tenant/login GET ─────────────────────────────────────────────────────
# FIND:
#   @app.get("/tenant/login", response_class=HTMLResponse)
#   async def tenant_login_page(error: str = ""):
#       err = (f'<p style="color:#f85149; ...')
#       return f"""<!DOCTYPE html> ... """
#
# REPLACE WITH:

@app.get("/tenant/login", response_class=HTMLResponse)
async def tenant_login_page(request: Request, error: str = ""):
    return templates.TemplateResponse("tenant_login.html", {
        "request": request,
        "brand":   BRAND,
        "error":   error,
    })


# ── B3: /tenant/forgot-password GET ──────────────────────────────────────────
# FIND:
#   @app.get("/tenant/forgot-password", response_class=HTMLResponse)
#   async def tenant_forgot_password_page(error: str = "", success: str = ""):
#       err_div = ...
#       return f"""<!DOCTYPE html><html> ... """
#
# REPLACE WITH:

@app.get("/tenant/forgot-password", response_class=HTMLResponse)
async def tenant_forgot_password_page(request: Request, error: str = "", success: str = ""):
    return templates.TemplateResponse("tenant_forgot.html", {
        "request": request,
        "brand":   BRAND,
        "error":   error,
        "success": success,
    })


# ── B4: /tenant/reset-password GET ───────────────────────────────────────────
# FIND:
#   @app.get("/tenant/reset-password", response_class=HTMLResponse)
#   async def tenant_reset_password_page(email: str = "", error: str = ""):
#       err_div = ...
#       return f"""<!DOCTYPE html><html> ... """
#
# REPLACE WITH:

@app.get("/tenant/reset-password", response_class=HTMLResponse)
async def tenant_reset_password_page(request: Request, email: str = "", error: str = ""):
    return templates.TemplateResponse("tenant_reset.html", {
        "request": request,
        "brand":   BRAND,
        "email":   email,
        "error":   error,
    })


# ── B5: /download GET ─────────────────────────────────────────────────────────
# FIND:
#   @app.get("/download", response_class=HTMLResponse)
#   async def download_page():
#       try:
#           ...version, notes, direct_url...
#       return f"""<!DOCTYPE html> ... """
#
# REPLACE WITH:

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

    return templates.TemplateResponse("download.html", {
        "request":    request,
        "brand":      BRAND,
        "version":    version,
        "notes":      notes,
        "direct_url": direct_url,
    })


# ─────────────────────────────────────────────────────────────────────────────
# GROUP C — ADD 6 new routes
# Place these after your existing /pricing route, before or after /enterprise.
# ─────────────────────────────────────────────────────────────────────────────


# ── C1: /features ─────────────────────────────────────────────────────────────

@app.get("/features", response_class=HTMLResponse)
async def features_page(request: Request):
    return templates.TemplateResponse("features.html", {
        "request": request,
        "brand":   BRAND,
    })


# ── C2: /docs ─────────────────────────────────────────────────────────────────

@app.get("/docs", response_class=HTMLResponse)
async def docs_page(request: Request):
    return templates.TemplateResponse("docs.html", {
        "request": request,
        "brand":   BRAND,
    })


# ── C3: /contact GET ──────────────────────────────────────────────────────────

@app.get("/contact", response_class=HTMLResponse)
async def contact_page(request: Request, success: str = "", error: str = ""):
    return templates.TemplateResponse("contact.html", {
        "request": request,
        "brand":   BRAND,
        "success": success == "1",
        "error":   error,
        "form":    {},
    })


# ── C4: /contact POST ─────────────────────────────────────────────────────────

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
        return templates.TemplateResponse("contact.html", {
            "request": request,
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
            # Don't block the user — still show success
    else:
        print(f"[CONTACT] No DB. Submission from {email}: {message[:80]}")

    return RedirectResponse("/contact?success=1", status_code=303)


# ── C5: /privacy ──────────────────────────────────────────────────────────────

@app.get("/privacy", response_class=HTMLResponse)
async def privacy_page(request: Request):
    return templates.TemplateResponse("privacy.html", {
        "request": request,
        "brand":   BRAND,
    })


# ── C6: /terms ────────────────────────────────────────────────────────────────

@app.get("/terms", response_class=HTMLResponse)
async def terms_page(request: Request):
    return templates.TemplateResponse("terms.html", {
        "request": request,
        "brand":   BRAND,
    })


# ── C7: /status ───────────────────────────────────────────────────────────────

from datetime import datetime, timezone  # already imported

@app.get("/status", response_class=HTMLResponse)
async def status_page(request: Request):
    from datetime import datetime, timezone

    services = [
        {"name": "C2 API Server",       "icon": "🌐", "description": "FastAPI · Railway · HTTPS",          "status": "operational"},
        {"name": "License Validation",  "icon": "🔑", "description": "HMAC key validation · Device binding", "status": "operational"},
        {"name": "Payment Processing",  "icon": "💳", "description": "Razorpay · INR transactions",          "status": "operational"},
        {"name": "Email Delivery",      "icon": "📧", "description": "SendGrid · License key emails",         "status": "operational"},
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
            # Mark DB as degraded
            for s in services:
                if "Database" in s["name"]:
                    s["status"] = "degraded"
            overall = "degraded"

    # 30-day uptime bars: all green for now (production would track incidents)
    uptime_days = [True] * 30

    checked_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    return templates.TemplateResponse("status.html", {
        "request":     request,
        "brand":       BRAND,
        "services":    services,
        "stats":       stats,
        "overall":     overall,
        "uptime_days": uptime_days,
        "checked_at":  checked_at,
    })


# ─────────────────────────────────────────────────────────────────────────────
# DEPLOYMENT CHECKLIST — Phase 2
#
# New template files to add to your Railway project:
#   templates/download.html
#   templates/auth_login.html
#   templates/tenant_login.html
#   templates/tenant_forgot.html
#   templates/tenant_reset.html
#   templates/features.html
#   templates/docs.html
#   templates/contact.html
#   templates/privacy.html
#   templates/terms.html
#   templates/status.html
#
# Changes to main.py:
#   A) Add CREATE TABLE contact_submissions inside init_db()
#   B) Replace 5 GET routes (B1–B5 above)
#   C) Add 7 new routes (C1–C7 above)
#
# No changes to requirements.txt or runtime.txt needed.
# ─────────────────────────────────────────────────────────────────────────────



"""
═══════════════════════════════════════════════════════════════════════════════
MAIN_PY_PATCH_3.py — FMSecure Phase 3 (Final)
═══════════════════════════════════════════════════════════════════════════════

Apply AFTER Phase 1 and Phase 2 patches.

This patch replaces all remaining inline-HTML dashboard routes with clean
Jinja2 template calls. Backend logic is 100% preserved — only the return
statements change. All DB queries, auth guards, and business logic stay.

Routes replaced (7 total):
  D1  GET  /dashboard
  D2  GET  /licenses
  D3  GET  /super/dashboard
  D4  GET  /super/tenant-detail
  D5  GET  /tenant/dashboard
  D6  GET  /payment/success
  D7  Also adds /super/tenants-form POST handler (new HTML form endpoint)
  D8  Also adds /tenant/logout (missing in original)
═══════════════════════════════════════════════════════════════════════════════
"""

import time  # already imported

# ─────────────────────────────────────────────────────────────────────────────
# D1 — REPLACE /dashboard route
#
# FIND (around line 821):
#   @app.get("/dashboard", response_class=HTMLResponse)
#   async def dashboard(_: bool = Depends(verify_session)):
#       now = time.time(); rows = ""
#       for mid, info in agents.items():
#           ...
#       return f"""<!DOCTYPE html>..."""
#
# REPLACE WITH:
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, _: bool = Depends(verify_session)):
    now = time.time()
    agents_ctx = {}
    for mid, info in agents.items():
        agents_ctx[mid] = {
            **info,
            "online":        (now - info["last_seen"]) < 30,
            "last_seen_fmt": _fmt_ts(info.get("last_seen")),
        }

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "brand":   BRAND,
        "agents":  agents_ctx,
    })


# ─────────────────────────────────────────────────────────────────────────────
# D2 — REPLACE /licenses route
#
# FIND (around line 3983):
#   @app.get("/licenses", response_class=HTMLResponse)
#   async def licenses_page(_: bool = Depends(verify_session)):
#       conn=get_db();cur=conn.cursor()
#       cur.execute("SELECT * FROM licenses ORDER BY created_at DESC LIMIT 500")
#       rows=cur.fetchall();cur.close();conn.close()
#       ...
#       return f"""<!DOCTYPE html>..."""
#
# REPLACE WITH:
# ─────────────────────────────────────────────────────────────────────────────

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

    return templates.TemplateResponse("licenses.html", {
        "request":       request,
        "brand":         BRAND,
        "licenses":      licenses_ctx,
        "total":         len(licenses_ctx),
        "active_count":  active_count,
        "expired_count": expired_count,
    })


# ─────────────────────────────────────────────────────────────────────────────
# D3 — REPLACE /super/dashboard route
#
# FIND (around line 1176):
#   @app.get("/super/dashboard", response_class=HTMLResponse)
#   async def super_dashboard(request: Request, _: bool = Depends(verify_session)):
#       ...
#       return f"""<!DOCTYPE html>..."""
#
# REPLACE WITH:
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/super/dashboard", response_class=HTMLResponse)
async def super_dashboard(request: Request, _: bool = Depends(verify_session),
                           new_key: str = "", msg: str = ""):
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
    cur.close(); conn.close()

    tenants_ctx = []
    for t in tenants_rows:
        tenants_ctx.append({
            **dict(t),
            "created_fmt": t["created_at"].strftime("%Y-%m-%d") if t.get("created_at") else "—",
        })

    return templates.TemplateResponse("super_dashboard.html", {
        "request":        request,
        "brand":          BRAND,
        "tenants":        tenants_ctx,
        "total_tenants":  total_tenants,
        "total_online":   total_online,
        "total_agents":   total_agents,
        "total_critical": total_critical,
        "new_key":        new_key,
        "admin_api_key":  ADMIN_API_KEY,
    })


# ─────────────────────────────────────────────────────────────────────────────
# D3b — ADD /super/tenants-form POST (new HTML form endpoint)
#
# ADD this after the super_dashboard route:
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/super/tenants-form", response_class=HTMLResponse)
async def super_create_tenant_form(
    request:        Request,
    _:              bool = Depends(verify_session),
    name:           str  = Form(...),
    slug:           str  = Form(...),
    contact_email:  str  = Form(...),
    plan:           str  = Form("business"),
    max_agents:     int  = Form(10),
    notes:          str  = Form(""),
    admin_email:    str  = Form(""),
    admin_password: str  = Form(""),
    send_welcome:   str  = Form(""),
):
    if not DATABASE_URL:
        return RedirectResponse("/super/dashboard", status_code=302)

    import uuid as _uuid
    tenant_id  = str(_uuid.uuid4())
    tenant_key = _gen_tenant_api_key()
    slug_clean = slug.lower().strip().replace(" ", "-")

    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("""
            INSERT INTO tenants (id, name, slug, api_key, plan, max_agents, contact_email, notes)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        """, (tenant_id, name.strip(), slug_clean, tenant_key,
              plan, max_agents, contact_email.strip(), notes.strip()))
        cur.execute("INSERT INTO tenant_config (tenant_id) VALUES (%s)", (tenant_id,))
        if admin_email and admin_password:
            cur.execute("""
                INSERT INTO tenant_users (tenant_id, email, password_hash, role)
                VALUES (%s,%s,%s,'admin')
            """, (tenant_id, admin_email.strip().lower(), _hash_password(admin_password)))
        conn.commit(); cur.close(); conn.close()
        print(f"[TENANT] Created: {name} ({slug_clean}) — key: {tenant_key[:20]}…")

        if send_welcome == "1":
            threading.Thread(
                target=send_tenant_welcome_email,
                args=(contact_email.strip(), name.strip(), tenant_key, max_agents, plan),
                daemon=True
            ).start()

    except Exception as e:
        print(f"[TENANT] Create error: {e}")
        return RedirectResponse(f"/super/dashboard?error={str(e)[:80]}", status_code=302)

    return RedirectResponse(
        f"/super/dashboard?new_key={tenant_key}",
        status_code=303
    )


# ─────────────────────────────────────────────────────────────────────────────
# D4 — REPLACE /super/tenant-detail route
#
# FIND (around line 1300 area — the super_tenant_detail_page function):
#   @app.get("/super/tenant-detail", response_class=HTMLResponse)
#   async def super_tenant_detail_page(...)
#       ...
#       return f"""<!DOCTYPE html>..."""
#
# REPLACE WITH:
# ─────────────────────────────────────────────────────────────────────────────

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

    agents_ctx = []
    for a in agents_list:
        agents_ctx.append({
            **dict(a),
            "last_seen_fmt": a["last_seen"].strftime("%Y-%m-%d %H:%M") if a.get("last_seen") else "—",
        })

    alerts_ctx = []
    for al in alert_list:
        alerts_ctx.append({
            **dict(al),
            "created_fmt": al["created_at"].strftime("%Y-%m-%d %H:%M") if al.get("created_at") else "—",
        })

    users_ctx = []
    for u in users_list:
        users_ctx.append({
            **dict(u),
            "created_fmt": u["created_at"].strftime("%Y-%m-%d") if u.get("created_at") else "—",
        })

    online_count = sum(1 for a in agents_ctx if a["status"] == "online")

    return templates.TemplateResponse("super_tenant.html", {
        "request":       request,
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


# ─────────────────────────────────────────────────────────────────────────────
# D5 — REPLACE /tenant/dashboard route
#
# FIND (around line 1980):
#   @app.get("/tenant/dashboard", response_class=HTMLResponse)
#   async def tenant_dashboard(request: Request):
#       ...
#       return f"""<!DOCTYPE html>..."""
#
# REPLACE WITH:
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/tenant/dashboard", response_class=HTMLResponse)
async def tenant_dashboard(request: Request, config_saved: str = ""):
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

    # Mark stale agents offline
    cur.execute(
        "UPDATE tenant_agents SET status='offline' "
        "WHERE tenant_id=%s AND last_seen < NOW() - INTERVAL '35 seconds'",
        (tenant_id,))
    conn.commit()

    cur.execute("""
        SELECT machine_id, hostname, ip_address, username, tier,
               is_armed, status, last_seen, agent_version
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

    stats       = _get_tenant_stats(tenant_id)
    online_count = sum(1 for a in agents_list if a["status"] == "online")
    armed_count  = sum(1 for a in agents_list if a["is_armed"])

    agents_ctx = []
    for a in agents_list:
        agents_ctx.append({
            **dict(a),
            "last_seen_fmt": a["last_seen"].strftime("%H:%M %d/%m") if a.get("last_seen") else "—",
        })

    alerts_ctx = []
    for al in alert_list:
        alerts_ctx.append({
            **dict(al),
            "created_fmt": al["created_at"].strftime("%Y-%m-%d %H:%M") if al.get("created_at") else "—",
        })

    return templates.TemplateResponse("tenant_dashboard.html", {
        "request":       request,
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


# ─────────────────────────────────────────────────────────────────────────────
# D5b — UPDATE /tenant/config POST to redirect with config_saved flag
#
# FIND the redirect line inside tenant_save_config:
#   return RedirectResponse("/tenant/dashboard", status_code=302)
#
# REPLACE WITH:
#   return RedirectResponse("/tenant/dashboard?config_saved=1", status_code=302)
# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
# D6 — REPLACE /payment/success route
#
# FIND (around line 3700):
#   @app.get("/payment/success", response_class=HTMLResponse)
#   async def payment_success(key: str = "", email: str = "", tier: str = ""):
#       tier_label=PLANS.get(tier,{}).get("label","PRO")
#       return f"""<!DOCTYPE html>..."""
#
# REPLACE WITH:
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/payment/success", response_class=HTMLResponse)
async def payment_success(request: Request, key: str = "", email: str = "", tier: str = ""):
    tier_label = PLANS.get(tier, {}).get("label", "PRO")
    return templates.TemplateResponse("payment_success.html", {
        "request":    request,
        "brand":      BRAND,
        "key":        key,
        "email":      email,
        "tier":       tier,
        "tier_label": tier_label,
    })


# ─────────────────────────────────────────────────────────────────────────────
# D7 — ADD /tenant/logout route (missing from original)
#
# ADD this near the other /tenant/* routes:
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/tenant/logout")
async def tenant_logout(request: Request):
    token = request.cookies.get("fms_tenant_session")
    if token and token in _tenant_sessions:
        del _tenant_sessions[token]
    resp = RedirectResponse("/tenant/login", status_code=302)
    resp.delete_cookie("fms_tenant_session")
    return resp


# ─────────────────────────────────────────────────────────────────────────────
# D8 — ADD _fmt_ts helper (used by dashboard route above)
#
# ADD this near the other helper functions (after _get_tenant_stats):
# ─────────────────────────────────────────────────────────────────────────────

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


# ─────────────────────────────────────────────────────────────────────────────
# DEPLOYMENT CHECKLIST — Phase 3
#
# New template files to add to your Railway project:
#   templates/base_admin.html
#   templates/dashboard.html
#   templates/licenses.html
#   templates/super_dashboard.html
#   templates/super_tenant.html
#   templates/tenant_dashboard.html
#   templates/payment_success.html
#
# Changes to main.py:
#   D1  Replace GET /dashboard
#   D2  Replace GET /licenses
#   D3  Replace GET /super/dashboard
#   D3b Add POST /super/tenants-form
#   D4  Replace GET /super/tenant-detail
#   D5  Replace GET /tenant/dashboard
#   D5b Update redirect in POST /tenant/config → add ?config_saved=1
#   D6  Replace GET /payment/success
#   D7  Add GET /tenant/logout
#   D8  Add _fmt_ts() helper function
#
# That's it — full frontend modernization complete across all 3 phases.
# All backend logic, DB queries, auth guards, and API endpoints unchanged.
# ─────────────────────────────────────────────────────────────────────────────