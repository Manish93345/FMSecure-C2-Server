"""
core/auth.py — Super-admin session token and FastAPI dependency.

SESSION_TOKEN is generated once at startup; it is stored as a cookie so the
admin browser proves identity without a database lookup.
"""
import secrets

from fastapi import Cookie, HTTPException


# Generated fresh on every cold start — all existing sessions are invalidated
# when the Railway dyno restarts, which is acceptable for an admin-only app.
SESSION_TOKEN: str = secrets.token_hex(16)


async def verify_session(fmsecure_session: str = Cookie(None)) -> bool:
    """
    FastAPI dependency: raises 302 → /login if the admin session cookie is
    missing or wrong.  Use as: `_: bool = Depends(verify_session)`.
    """
    if (
        not fmsecure_session
        or not secrets.compare_digest(fmsecure_session, SESSION_TOKEN)
    ):
        raise HTTPException(
            status_code=302,
            headers={"Location": "/login"},
        )
    return True
