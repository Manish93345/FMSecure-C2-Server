"""
core/config.py — All environment variables, constants, and shared in-memory state.

Every other module imports from here. To change a setting, change it once here.
"""
import os
import secrets
import razorpay
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address

# ── Environment variables ──────────────────────────────────────────────────────
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
DRIVE_FILE_ID     = os.getenv("DRIVE_FILE_ID", "1Wo_GwR8YR_3sZUykTcwEzdAtbKZOtmpJ")

# ── Derived constants ──────────────────────────────────────────────────────────
DOWNLOAD_URL = (
    f"https://drive.google.com/uc?export=download&id={DRIVE_FILE_ID}"
    if DRIVE_FILE_ID else "#"
)
PRODUCT_PAGE_URL = os.getenv("PRODUCT_PAGE_URL", f"{APP_BASE_URL}/download")

# ── Plans — amounts in PAISE (Rs 499 = 49900) ─────────────────────────────────
# To change price: edit "amount". To change label: edit "label" AND the HTML.
PLANS = {
    "pro_monthly": {"label": "PRO Monthly", "amount": 499,  "currency": "INR",
                    "description": "FMSecure PRO - Monthly", "days": 31},
    "pro_annual":  {"label": "PRO Annual",  "amount": 4999, "currency": "INR",
                    "description": "FMSecure PRO - Annual",  "days": 365},
}

# ── Razorpay client ────────────────────────────────────────────────────────────
rzp_client = razorpay.Client(auth=(RZP_KEY_ID, RZP_KEY_SECRET))

# ── Rate limiter ───────────────────────────────────────────────────────────────
limiter = Limiter(key_func=get_remote_address)

# ── Shared in-memory state ────────────────────────────────────────────────────
# These dicts are mutated at runtime. Railway runs a single process so
# in-memory is safe. If you ever go multi-process, move these to Redis.

# Legacy single-tenant agent tracking: machine_id → agent info dict
agents: dict = {}

# Queued commands: machine_id → command string (e.g. "LOCKDOWN")
commands: dict = {}
