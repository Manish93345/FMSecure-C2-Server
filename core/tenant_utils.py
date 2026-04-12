"""
core/tenant_utils.py — Tenant session management and shared tenant helpers.

Tenant sessions live in the module-level _tenant_sessions dict.
Railway runs a single process so in-memory is safe; move to Redis if you
ever go multi-process.
"""
import secrets
import time
from typing import Optional

from fastapi import HTTPException, Request

from core.config import DATABASE_URL
from core.database import get_db


# ── Session store ──────────────────────────────────────────────────────────────
# token → {"tenant_id": str, "email": str, "role": str, "created_at": float}
_tenant_sessions: dict = {}
_TENANT_SESSION_TTL = 86400   # 24 hours


def _create_tenant_session(tenant_id: str, email: str, role: str) -> str:
    """Create a session token for a tenant admin. Returns the token string."""
    token = secrets.token_urlsafe(32)
    _tenant_sessions[token] = {
        "tenant_id":  tenant_id,
        "email":      email,
        "role":       role,
        "created_at": time.time(),
    }
    return token


def _get_tenant_session(request: Request) -> Optional[dict]:
    """
    Read and validate the tenant session from the request cookie.
    Cleans up expired sessions automatically.
    Returns the session dict, or None if not authenticated.
    """
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


def _require_tenant_session(request: Request) -> dict:
    """
    Raises a 302 redirect to /tenant/login if there is no valid tenant session.
    Use this as a guard at the top of tenant dashboard handlers.
    """
    session = _get_tenant_session(request)
    if not session:
        raise HTTPException(
            status_code=302,
            headers={"Location": "/tenant/login"},
        )
    return session


def _get_tenant_by_api_key(api_key: str) -> Optional[dict]:
    """
    Look up an active tenant by their API key.
    Returns the tenant row as a dict, or None if not found / inactive.
    """
    if not api_key or not DATABASE_URL:
        return None
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute(
            "SELECT * FROM tenants WHERE api_key = %s AND active = TRUE",
            (api_key,),
        )
        row = cur.fetchone()
        cur.close(); conn.close()
        return dict(row) if row else None
    except Exception as e:
        print(f"[TENANT] API key lookup error: {e}")
        return None


def _get_tenant_stats(tenant_id: str) -> dict:
    """Return agent / alert counts for a given tenant. Safe — returns zeros on error."""
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
        return {
            "total_agents": 0, "online_agents": 0,
            "unacked_alerts": 0, "critical_alerts": 0,
        }
