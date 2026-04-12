"""
core/helpers.py — Pure utility / helper functions shared across route modules.

No FastAPI imports here — this module stays framework-agnostic so it can be
unit-tested without spinning up the app.
"""
import hashlib
import hmac as _hmac
import secrets
import uuid
from datetime import datetime, timezone, timedelta

from fastapi import HTTPException

from core.config import LICENSE_SECRET, ADMIN_API_KEY
from core.database import get_db


# ── Time helpers ───────────────────────────────────────────────────────────────

def _is_expired(e) -> bool:
    """Return True if the given datetime (or ISO string) is in the past."""
    try:
        if isinstance(e, str):
            e = datetime.fromisoformat(e.replace("Z", "+00:00"))
        if e.tzinfo is None:
            e = e.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) > e
    except Exception:
        return True


# ── License helpers ────────────────────────────────────────────────────────────

def _gen_key(tier: str, email: str, payment_id: str) -> str:
    """Generate a deterministic, HMAC-signed license key."""
    sig = _hmac.new(
        LICENSE_SECRET.encode(),
        f"{tier}:{email.lower()}:{payment_id}".encode(),
        hashlib.sha256,
    ).hexdigest()[:16].upper()
    prefix = "PRA" if "annual" in tier else "PRM"
    return f"FMSECURE-{prefix}-{sig}"


def _save_license(key: str, email: str, tier: str,
                  payment_id: str, order_id: str, expires_iso: str) -> None:
    """Upsert a license row into the database."""
    conn = get_db(); cur = conn.cursor()
    cur.execute("""
        INSERT INTO licenses
            (license_key, email, tier, payment_id, order_id, expires_at, active)
        VALUES (%s, %s, %s, %s, %s, %s, TRUE)
        ON CONFLICT (license_key)
        DO UPDATE SET expires_at = EXCLUDED.expires_at, active = TRUE
    """, (key, email.lower(), tier, payment_id, order_id, expires_iso))
    conn.commit(); cur.close(); conn.close()


# ── Auth / security helpers ────────────────────────────────────────────────────

def _check_admin(api_key: str) -> None:
    """Raise 403 if the provided API key is not the admin key."""
    if api_key != ADMIN_API_KEY:
        raise HTTPException(status_code=403, detail="Forbidden")


def _hash_password(password: str) -> str:
    """SHA-256 password hash with a fixed salt prefix."""
    return hashlib.sha256(
        ("fmsecure_salt_v1:" + password).encode()
    ).hexdigest()


def _verify_password(password: str, hashed: str) -> bool:
    """Constant-time password comparison."""
    return secrets.compare_digest(_hash_password(password), hashed)


# ── Tenant helpers ─────────────────────────────────────────────────────────────

def _gen_tenant_api_key() -> str:
    """Generate a secure, prefixed tenant API key."""
    return "fms-tenant-" + secrets.token_urlsafe(24)
