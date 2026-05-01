"""
config.py — FMSecure Central Configuration
═══════════════════════════════════════════
Single source of truth for all brand, pricing, feature flags, and
environment variables. Import this everywhere — never hardcode values.

To change the brand name, email, or pricing: edit THIS FILE ONLY.
"""
import os
from datetime import datetime

# ══════════════════════════════════════════════════════════════════════════════
# BRAND IDENTITY  — edit once, reflects everywhere
# ══════════════════════════════════════════════════════════════════════════════
BRAND = {
    "name":            "FMSecure",
    "tagline":         "Enterprise EDR for Windows",
    "short_desc":      "Real-time endpoint detection & response — file integrity, ransomware killswitch, AES-256 vault, and cloud C2.",
    "logo_ico":        "/static/app_icon.ico",
    "logo_png":        "/static/app_icon.png",
    "logo_text":       "FM",                          # fallback initials if PNG fails
    "support_email":   "support@fmsecure.in",
    "sales_email":     "sales@fmsecure.in",
    "security_email":  "security@fmsecure.in",
    "company":         "Manish Lisa Pvt Limited",
    "company_short":   "FMSecure",
    "founded":         "2025",
    "hq":              "India",
    "copyright_year":  datetime.now().year,
    "app_version":     "2.5.0",
    "twitter":         "https://twitter.com/fmsecure",
    "linkedin":        "https://linkedin.com/company/fmsecure",
    "github":          "https://github.com/fmsecure",
    "status_page":     "/status",
    "trust_url":       "/security",
    "careers_url":     "/careers",
}

# ══════════════════════════════════════════════════════════════════════════════
# NAV LINKS — drives base.html navbar. Add/remove here only.
# ══════════════════════════════════════════════════════════════════════════════
NAV = {
    "product": [
        {"label": "Features",       "href": "/features"},
        {"label": "Integrations",   "href": "/integrations"},
        {"label": "Roadmap",        "href": "/roadmap"},
        {"label": "What's New",     "href": "/roadmap#changelog"},
    ],
    "solutions": [
        {"label": "For IT Admins",    "href": "/features#it-admin"},
        {"label": "For Enterprises",  "href": "/enterprise"},
        {"label": "For MSPs",         "href": "/partners"},
        {"label": "Case Studies",     "href": "/case-studies"},
    ],
    "resources": [
        {"label": "Documentation",    "href": "/documentation"},
        {"label": "Help Center",      "href": "/help"},
        {"label": "Blog & Insights",  "href": "/blog"},
        {"label": "Security",         "href": "/security"},
        {"label": "System Status",    "href": "/status"},
    ],
    "company": [
        {"label": "About Us",    "href": "/about"},
        {"label": "Careers",     "href": "/careers"},
        {"label": "Partners",    "href": "/partners"},
        {"label": "Contact",     "href": "/contact"},
    ],
}

# ══════════════════════════════════════════════════════════════════════════════
# FOOTER LINKS — grouped columns
# ══════════════════════════════════════════════════════════════════════════════
FOOTER = {
    "Product": [
        ("Features",        "/features"),
        ("Integrations",    "/integrations"),
        ("Pricing",         "/pricing"),
        ("Download",        "/download"),
        ("Roadmap",         "/roadmap"),
        ("Changelog",       "/roadmap#changelog"),
    ],
    "Company": [
        ("About Us",        "/about"),
        ("Careers",         "/careers"),
        ("Partners",        "/partners"),
        ("Blog",            "/blog"),
        ("Contact",         "/contact"),
        ("System Status",   "/status"),
    ],
    "Resources": [
        ("Documentation",   "/documentation"),
        ("Help Center",     "/help"),
        ("Case Studies",    "/case-studies"),
        ("Security",        "/security"),
        ("API Reference",   "/documentation#api"),
        ("Release Notes",   "/roadmap#changelog"),
    ],
    "Legal": [
        ("Privacy Policy",  "/privacy"),
        ("Terms of Service","/terms"),
        ("Cookie Policy",   "/cookie-policy"),
        ("GDPR",            "/cookie-policy#gdpr"),
        ("SLA",             "/terms#sla"),
    ],
    "Portal": [
        ("IT Admin Login",  "/tenant/login"),
        ("C2 Dashboard",    "/login"),
        ("License Lookup",  "/licenses"),
        ("Support",         f"mailto:support@fmsecure.in"),
    ],
}

# ══════════════════════════════════════════════════════════════════════════════
# PRICING — change price HERE ONLY (syncs to pricing page + payment handler)
# ══════════════════════════════════════════════════════════════════════════════
PRICING_DISPLAY = {
    "free": {
        "label":       "Free",
        "price":       "0",
        "period":      "",
        "currency":    "₹",
        "description": "For individuals & testing",
        "cta":         "Download Free",
        "cta_href":    "/download",
    },
    "pro_monthly": {
        "label":       "PRO",
        "price":       "499",
        "period":      "/mo",
        "currency":    "₹",
        "description": "For professionals & small teams",
        "cta":         "Start PRO Monthly",
        "cta_href":    "/pricing",
    },
    "pro_annual": {
        "label":       "PRO Annual",
        "price":       "4,999",
        "period":      "/yr",
        "currency":    "₹",
        "description": "Best value — 2 months free",
        "cta":         "Start PRO Annual",
        "cta_href":    "/pricing",
    },
    "enterprise": {
        "label":       "Enterprise",
        "price":       "Custom",
        "period":      "",
        "currency":    "",
        "description": "For large teams & MSPs",
        "cta":         "Contact Sales",
        "cta_href":    "/contact",
    },
}

# ══════════════════════════════════════════════════════════════════════════════
# FEATURE FLAGS
# ══════════════════════════════════════════════════════════════════════════════
FEATURES = {
    "razorpay_enabled":     bool(os.getenv("RAZORPAY_KEY_ID")),
    "email_enabled":        bool(os.getenv("GMAIL_USER")),
    "totp_2fa_enabled":     True,
    "threat_intel_enabled": True,
    "registry_monitor":     True,
    "active_defense":       True,
}

# ══════════════════════════════════════════════════════════════════════════════
# ENVIRONMENT VARIABLES — all in one place
# ══════════════════════════════════════════════════════════════════════════════
DATABASE_URL       = os.getenv("DATABASE_URL", "")
RZP_KEY_ID         = os.getenv("RAZORPAY_KEY_ID", "")
RZP_KEY_SECRET     = os.getenv("RAZORPAY_KEY_SECRET", "")
LICENSE_SECRET     = os.getenv("LICENSE_HMAC_SECRET", "change-me-in-production")
ADMIN_API_KEY      = os.getenv("ADMIN_API_KEY", "dev-only")
APP_BASE_URL       = os.getenv("APP_BASE_URL", "http://localhost:8000")
ADMIN_USER         = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASS         = os.getenv("ADMIN_PASSWORD", "password")
API_KEY            = os.getenv("API_KEY", "default-dev-key")
GMAIL_USER         = os.getenv("GMAIL_USER", "")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")
SENDER_EMAIL       = os.getenv("SENDER_EMAIL", "fmsecure.team@gmail.com")
SUPER_ADMIN_EMAIL  = os.getenv("SUPER_ADMIN_EMAIL", SENDER_EMAIL)
DRIVE_FILE_ID      = os.getenv("DRIVE_FILE_ID", "1e-EnPaxiMP0ZFpkL6QpBopJ41QeQMjMM")

DOWNLOAD_URL = (
    f"https://drive.google.com/uc?export=download&id={DRIVE_FILE_ID}"
    if DRIVE_FILE_ID else "#"
)
PRODUCT_PAGE_URL = os.getenv("PRODUCT_PAGE_URL", f"{APP_BASE_URL}/download")

# Session
TENANT_SESSION_TTL = 86400   # 24 hours

# Plans — amounts in paise (INR ×100)
PLANS = {
    "pro_monthly": {
        "label":       "PRO Monthly",
        "amount":      499,
        "currency":    "INR",
        "description": "FMSecure PRO - Monthly",
        "days":        31,
    },
    "pro_annual": {
        "label":       "PRO Annual",
        "amount":      4999,
        "currency":    "INR",
        "description": "FMSecure PRO - Annual",
        "days":        365,
    },
}
