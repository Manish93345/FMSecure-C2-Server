"""
main.py — FMSecure C2 + License Server

Environment variables (set in Railway):
  ADMIN_USERNAME      super-admin username
  ADMIN_PASSWORD      super-admin password
  API_KEY             legacy desktop agent API key
  RAZORPAY_KEY_ID     rzp_test_... or rzp_live_...
  RAZORPAY_KEY_SECRET Razorpay webhook secret
  LICENSE_HMAC_SECRET generate: python -c "import secrets;print(secrets.token_hex(32))"
  ADMIN_API_KEY       any secret for admin JSON endpoints
  APP_BASE_URL        https://your-server.railway.app
  DATABASE_URL        auto-set by Railway PostgreSQL plugin
  SENDGRID_API_KEY    get free at sendgrid.com (100 emails/day free)
  SENDER_EMAIL        the FROM address in sent emails
  DRIVE_FILE_ID       Google Drive file ID for the installer download

Project layout:
  main.py              ← this file (app setup, startup, router includes)
  core/
    config.py          ← env vars, PLANS, razorpay client, shared state
    database.py        ← get_db(), init_db(), offline sweeper
    helpers.py         ← pure utility functions
    auth.py            ← SESSION_TOKEN, verify_session dependency
    email_utils.py     ← SendGrid email helpers
    tenant_utils.py    ← tenant session store + helpers
  routes/
    auth_routes.py     ← GET/POST /login, GET /logout
    agents.py          ← /api/heartbeat, /api/agent/alert, /agent/config,
                          /api/trigger_lockdown, /dashboard
    tenants.py         ← /super/* and /tenant/* routes
    licenses.py        ← /api/license/*, /payment/*, /licenses page
    versions.py        ← /version.json, /api/version/*
    pages.py           ← /, /home, /download, /changelog, /pricing
  static/              ← app_icon.ico, app_icon.png, c2.png, hero_image.png
"""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from slowapi.errors import RateLimitExceeded

from slowapi import _rate_limit_exceeded_handler

from core.config import DATABASE_URL, limiter
from core.database import init_db, _start_offline_sweeper

# ── Route modules ──────────────────────────────────────────────────────────────
from routes.auth_routes import router as auth_router
from routes.agents      import router as agents_router
from routes.tenants     import router as tenants_router
from routes.licenses    import router as licenses_router
from routes.versions    import router as versions_router
from routes.pages       import router as pages_router


# ── App factory ────────────────────────────────────────────────────────────────
app = FastAPI(title="FMSecure")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Static files (silently skipped if the directory doesn't exist yet)
try:
    app.mount("/static", StaticFiles(directory="static"), name="static")
except Exception:
    pass

# ── Register routers ───────────────────────────────────────────────────────────
# Order matters only for docs — functionally equivalent.
app.include_router(auth_router)
app.include_router(agents_router)
app.include_router(tenants_router)
app.include_router(licenses_router)
app.include_router(versions_router)
app.include_router(pages_router)


# ── Startup ────────────────────────────────────────────────────────────────────
@app.on_event("startup")
async def startup():
    if DATABASE_URL:
        init_db()
        _start_offline_sweeper()
    else:
        print("[DB] WARNING: No DATABASE_URL — running without persistence")
