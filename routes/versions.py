"""
routes/versions.py — Version management endpoints.

Routes:
  GET  /version.json                 ← polled by desktop app on startup
  POST /api/version/publish          ← admin JSON endpoint
  POST /api/version/publish-form     ← dashboard form handler
"""
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel

from core.auth import verify_session
from core.config import APP_BASE_URL, DATABASE_URL
from core.database import get_db
from core.helpers import _check_admin

router = APIRouter()


# ── Desktop client update check ────────────────────────────────────────────────

@router.get("/version.json")
async def version_json():
    """
    Returns current version metadata.
    Cache-control headers prevent stale CDN responses.
    """
    fallback = {
        "latest_version": "2.5.0",
        "release_notes":  "",
        "download_url":   f"{APP_BASE_URL}/download",
        "changelog_url":  f"{APP_BASE_URL}/changelog",
    }

    if not DATABASE_URL:
        return JSONResponse(fallback)

    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute(
            "SELECT version, release_notes, download_url, changelog_url "
            "FROM versions WHERE is_current = TRUE "
            "ORDER BY published_at DESC LIMIT 1"
        )
        row = cur.fetchone()
        cur.close(); conn.close()

        if not row:
            return JSONResponse(fallback)

        return JSONResponse(
            content={
                "latest_version": row["version"],
                "release_notes":  row["release_notes"],
                "download_url":   row["download_url"],
                "changelog_url":  row["changelog_url"],
            },
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate",
                "Pragma":        "no-cache",
            },
        )
    except Exception as e:
        print(f"[VERSION] DB error: {e}")
        return JSONResponse(fallback)


# ── Admin: publish a new version (JSON) ───────────────────────────────────────

class VersionBody(BaseModel):
    version:       str
    release_notes: str = ""
    download_url:  str = ""
    changelog_url: str = ""
    api_key:       str = ""


@router.post("/api/version/publish")
async def publish_version(body: VersionBody):
    """
    Publish a new version via JSON. Marks all existing rows is_current=FALSE
    then inserts the new row as TRUE.
    """
    _check_admin(body.api_key)

    dl = body.download_url  or f"{APP_BASE_URL}/download"
    cl = body.changelog_url or f"{APP_BASE_URL}/changelog"

    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("UPDATE versions SET is_current = FALSE")
        cur.execute("""
            INSERT INTO versions
                (version, release_notes, download_url, changelog_url, is_current)
            VALUES (%s, %s, %s, %s, TRUE)
        """, (body.version.strip(), body.release_notes.strip(), dl, cl))
        conn.commit(); cur.close(); conn.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    print(f"[VERSION] Published v{body.version}")
    return {"ok": True, "version": body.version}


# ── Dashboard form handler ────────────────────────────────────────────────────

@router.post("/api/version/publish-form")
async def publish_version_form(
    request:       Request,
    version:       str = Form(...),
    release_notes: str = Form(""),
    download_url:  str = Form(""),
    changelog_url: str = Form(""),
    _: bool = Depends(verify_session),
):
    """Same logic as the JSON endpoint but authenticated via session cookie."""
    dl = download_url  or f"{APP_BASE_URL}/download"
    cl = changelog_url or f"{APP_BASE_URL}/changelog"

    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("UPDATE versions SET is_current = FALSE")
        cur.execute("""
            INSERT INTO versions
                (version, release_notes, download_url, changelog_url, is_current)
            VALUES (%s, %s, %s, %s, TRUE)
        """, (version.strip(), release_notes.strip(), dl, cl))
        conn.commit(); cur.close(); conn.close()
        print(f"[VERSION] Published v{version} via dashboard")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return RedirectResponse("/dashboard", status_code=303)
