"""
rules_manager.py — FMSecure C2 Server  v1.1
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SERVER-SIDE RULE-PACK MANAGEMENT  (v1.1 adds GLOBAL super-admin packs)

Stores and serves the detection-rule manifest that agents pull when their
version differs from the server's. Storage layout in Neon:

    CREATE TABLE rule_packs (
        tenant_id     TEXT NOT NULL,        -- real tenant id, or '__global__'
        version       TEXT NOT NULL,
        sha256        TEXT NOT NULL,
        bundle_b64    TEXT NOT NULL,        -- base64 of zip bytes
        rule_count    INTEGER NOT NULL,
        release_notes TEXT,
        published_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        is_current    BOOLEAN NOT NULL DEFAULT TRUE,
        PRIMARY KEY (tenant_id, version)
    );

A single row holds the WHOLE rule pack for a tenant — OR — the GLOBAL pack
shared across every tenant when their own pack is unset.

NEW IN v1.1 — GLOBAL PACKS (super-admin)
────────────────────────────────────────
The super admin can publish a single rule pack under the sentinel
tenant_id = '__global__'. Every agent whose tenant has not published a
private pack will receive this global pack. When a tenant later publishes
their own pack, the per-tenant pack overrides the global one for that
tenant only.

  • bundle_b64 is base64 of a zip file structured like:
        yara/foo.yar
        yara/bar.yar
        sigma/baz.yml
        ...
  • Only ONE row per tenant_id has is_current=TRUE at any time.
  • Old rows are kept for rollback (admin can re-promote a previous version).

NEON FOOTPRINT
──────────────
At ~200 rules totalling 200 KB raw → ~280 KB base64 → ~50 KB after Neon's
TOAST compression. One row per tenant, plus one row for the global pack.
Comfortably within Neon's 512 MB free tier.

COMPUTE FOOTPRINT
─────────────────
A 60-second in-memory cache (per server worker) of "currently published
version per tenant" means the heartbeat handler does NOT hit the DB on
every heartbeat. Agents only fetch the full manifest when their local
version differs — i.e. once per publish event per agent.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

from __future__ import annotations

import os
import io
import json
import time
import base64
import hashlib
import hmac
import zipfile
import threading
from datetime import datetime, timezone
from typing import Optional, List, Tuple


# ── Configuration ─────────────────────────────────────────────────────────────
# Same secret as software/core/rule_updater.py expects in FMSECURE_RULES_HMAC.
# Reuse the server's LICENSE_HMAC_SECRET so we don't have to introduce a new env.
HMAC_SECRET = os.getenv("LICENSE_HMAC_SECRET", "")

# Sentinel tenant_id used for the super-admin global rule pack.
GLOBAL_TENANT_ID = "__global__"

# In-memory cache: {tenant_id: (version_str, expires_at_ts)}
# Used by the heartbeat hot path so we never hit Neon for the same lookup
# twice within VERSION_CACHE_TTL seconds.
_VER_CACHE: dict[str, tuple[str, float]] = {}
_VER_CACHE_LOCK = threading.Lock()
VERSION_CACHE_TTL = 60.0   # seconds


# ── Schema migration ──────────────────────────────────────────────────────────
#
# v1.1 schema change: the table previously had
#     tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE
# We now also want to store a synthetic '__global__' row, which has no
# matching row in tenants(). The migration below drops the FK if present
# (idempotent). All other behaviour is unchanged.
#
DDL_RULE_PACKS = """
CREATE TABLE IF NOT EXISTS rule_packs (
    tenant_id     TEXT NOT NULL,
    version       TEXT NOT NULL,
    sha256        TEXT NOT NULL,
    bundle_b64    TEXT NOT NULL,
    rule_count    INTEGER NOT NULL DEFAULT 0,
    release_notes TEXT,
    published_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    is_current    BOOLEAN NOT NULL DEFAULT TRUE,
    PRIMARY KEY (tenant_id, version)
);
CREATE INDEX IF NOT EXISTS idx_rule_packs_current
    ON rule_packs(tenant_id) WHERE is_current = TRUE;
"""

# Drop the legacy FK constraint so we can store the sentinel '__global__' row.
# The constraint name PostgreSQL assigns when the original CREATE TABLE was
# emitted without a name is 'rule_packs_tenant_id_fkey'. We try that first
# and then do a defensive catalog lookup just in case it was named differently.
_DROP_LEGACY_FK = """
DO $$
DECLARE
    fk_name TEXT;
BEGIN
    SELECT con.conname
      INTO fk_name
      FROM pg_constraint con
      JOIN pg_class      rel ON rel.oid = con.conrelid
     WHERE rel.relname = 'rule_packs'
       AND con.contype = 'f'
     LIMIT 1;

    IF fk_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE rule_packs DROP CONSTRAINT %I', fk_name);
    END IF;
END$$;
"""


def ensure_schema(get_db_fn):
    """Call once at app startup. get_db_fn is the same helper used everywhere."""
    try:
        conn = get_db_fn()
        cur  = conn.cursor()
        for stmt in DDL_RULE_PACKS.strip().split(";"):
            s = stmt.strip()
            if s:
                cur.execute(s)

        # v1.1 migration: remove the legacy FK on tenant_id so the
        # '__global__' sentinel row can live in the same table.
        try:
            cur.execute(_DROP_LEGACY_FK)
        except Exception as fk_e:
            # Not fatal — most likely the FK was already dropped or never existed.
            print(f"[RULES] legacy FK drop skipped: {fk_e}")

        conn.commit()
        cur.close(); conn.close()
        print("[RULES] schema ensured.")
    except Exception as e:
        print(f"[RULES] schema init error: {e}")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_version_string() -> str:
    """E.g. '2026-06-07.142457'"""
    n = datetime.now(timezone.utc)
    return n.strftime("%Y-%m-%d.%H%M%S")


def _hmac_sign(manifest_without_sig: dict) -> str:
    if not HMAC_SECRET:
        return ""
    canonical = json.dumps(manifest_without_sig, sort_keys=True,
                           separators=(",", ":")).encode("utf-8")
    return hmac.new(HMAC_SECRET.encode("utf-8"),
                    canonical, hashlib.sha256).hexdigest()


def build_bundle_from_rules(yara_files: List[Tuple[str, str]],
                            sigma_files: List[Tuple[str, str]]) -> bytes:
    """
    yara_files  : list of (filename, text_content)  →  goes to yara/<filename>
    sigma_files : list of (filename, text_content)  →  goes to sigma/<filename>

    Returns raw ZIP bytes ready to be base64-encoded.
    Filenames are stripped of any path components for safety.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fname, body in yara_files:
            safe = os.path.basename(fname or "").strip()
            if not safe:
                continue
            if not safe.lower().endswith((".yar", ".yara")):
                safe += ".yar"
            zf.writestr(f"yara/{safe}", body)

        for fname, body in sigma_files:
            safe = os.path.basename(fname or "").strip()
            if not safe:
                continue
            if not safe.lower().endswith((".yml", ".yaml")):
                safe += ".yml"
            zf.writestr(f"sigma/{safe}", body)

    return buf.getvalue()


def build_manifest(version: str,
                   bundle_bytes: bytes,
                   rule_count: int,
                   release_notes: str = "") -> dict:
    """
    Build the JSON manifest the agent will receive at /api/rules/manifest.
    """
    sha256 = hashlib.sha256(bundle_bytes).hexdigest()
    body = {
        "version":       version,
        "sha256":        sha256,
        "bundle_b64":    base64.b64encode(bundle_bytes).decode("ascii"),
        "count":         rule_count,
        "published_at":  datetime.now(timezone.utc)
                                  .isoformat().replace("+00:00", "Z"),
        "release_notes": release_notes or "",
    }
    body["signature"] = _hmac_sign(body)
    return body


# ── Publish workflow ──────────────────────────────────────────────────────────

def publish_pack(get_db_fn,
                 tenant_id: str,
                 yara_files: List[Tuple[str, str]],
                 sigma_files: List[Tuple[str, str]],
                 release_notes: str = "",
                 version: Optional[str] = None) -> dict:
    """
    Take user-supplied rule files (already parsed as lists of (name, text)),
    pack them into a bundle, store in Neon, and invalidate the version cache.

    For super-admin GLOBAL publishes, pass tenant_id = GLOBAL_TENANT_ID.

    Returns: {version, sha256, count, size_b64}
    """
    version = version or _now_version_string()
    bundle  = build_bundle_from_rules(yara_files, sigma_files)
    sha256  = hashlib.sha256(bundle).hexdigest()
    b64     = base64.b64encode(bundle).decode("ascii")
    count   = len(yara_files) + len(sigma_files)

    conn = get_db_fn(); cur = conn.cursor()
    try:
        # Mark all previous rows for this tenant as not-current
        cur.execute(
            "UPDATE rule_packs SET is_current = FALSE WHERE tenant_id = %s",
            (tenant_id,))
        # Insert the new current row
        cur.execute(
            """
            INSERT INTO rule_packs
                (tenant_id, version, sha256, bundle_b64, rule_count,
                 release_notes, is_current)
            VALUES (%s, %s, %s, %s, %s, %s, TRUE)
            ON CONFLICT (tenant_id, version) DO UPDATE SET
                sha256        = EXCLUDED.sha256,
                bundle_b64    = EXCLUDED.bundle_b64,
                rule_count    = EXCLUDED.rule_count,
                release_notes = EXCLUDED.release_notes,
                is_current    = TRUE,
                published_at  = NOW()
            """,
            (tenant_id, version, sha256, b64, count, release_notes)
        )
        conn.commit()
    finally:
        cur.close(); conn.close()

    # Bust the in-memory version cache for this tenant.
    # If we just published a NEW GLOBAL pack, every tenant that was relying on
    # the global fallback must also be re-evaluated, so we bust the whole cache.
    with _VER_CACHE_LOCK:
        if tenant_id == GLOBAL_TENANT_ID:
            _VER_CACHE.clear()
        else:
            _VER_CACHE.pop(tenant_id, None)

    return {
        "version":  version,
        "sha256":   sha256,
        "count":    count,
        "size_b64": len(b64),
    }


# ── Read paths (used by heartbeat + manifest endpoint) ───────────────────────

def _db_current_version(get_db_fn, tenant_id: str) -> str:
    """Return the currently-published version for a tenant_id, or '' if none."""
    try:
        conn = get_db_fn(); cur = conn.cursor()
        cur.execute(
            "SELECT version FROM rule_packs "
            "WHERE tenant_id = %s AND is_current = TRUE "
            "ORDER BY published_at DESC LIMIT 1",
            (tenant_id,))
        row = cur.fetchone()
        cur.close(); conn.close()
        if not row:
            return ""
        return row["version"] if isinstance(row, dict) else row[0]
    except Exception as e:
        print(f"[RULES] version lookup error: {e}")
        return ""


def get_current_version(get_db_fn, tenant_id: str) -> str:
    """
    Hot path — called from /api/heartbeat. Uses a 60-second in-memory cache
    so we don't hit Neon every 10 seconds per agent.

    Resolution order:
        1. The tenant's own pack, if they ever published one.
        2. Otherwise, the super-admin GLOBAL pack, if it exists.
        3. Otherwise '' — agents keep using their bundled rules.

    Returns '' if neither a per-tenant nor global pack has ever been published.
    """
    now = time.time()
    with _VER_CACHE_LOCK:
        cached = _VER_CACHE.get(tenant_id)
        if cached and cached[1] > now:
            return cached[0]

    # 1. Per-tenant pack first
    version = _db_current_version(get_db_fn, tenant_id)

    # 2. Fall back to global pack
    if not version and tenant_id != GLOBAL_TENANT_ID:
        version = _db_current_version(get_db_fn, GLOBAL_TENANT_ID)

    with _VER_CACHE_LOCK:
        _VER_CACHE[tenant_id] = (version, now + VERSION_CACHE_TTL)
    return version


def _db_current_manifest(get_db_fn, tenant_id: str) -> Optional[dict]:
    """Read the raw current rule_packs row for a tenant_id, or None."""
    try:
        conn = get_db_fn(); cur = conn.cursor()
        cur.execute(
            "SELECT version, sha256, bundle_b64, rule_count, "
            "       release_notes, published_at "
            "FROM rule_packs "
            "WHERE tenant_id = %s AND is_current = TRUE "
            "ORDER BY published_at DESC LIMIT 1",
            (tenant_id,))
        row = cur.fetchone()
        cur.close(); conn.close()
    except Exception as e:
        print(f"[RULES] manifest lookup error: {e}")
        return None

    if not row:
        return None

    # Re-build the signed manifest from stored data
    bundle_b64 = row["bundle_b64"]
    body = {
        "version":       row["version"],
        "sha256":        row["sha256"],
        "bundle_b64":    bundle_b64,
        "count":         row["rule_count"],
        "published_at":  (row["published_at"].astimezone(timezone.utc)
                          .isoformat().replace("+00:00", "Z")
                          if row.get("published_at") else ""),
        "release_notes": row.get("release_notes") or "",
    }
    body["signature"] = _hmac_sign(body)
    return body


def get_current_manifest(get_db_fn, tenant_id: str) -> Optional[dict]:
    """
    Cold path — called from /api/rules/manifest. Reads the full bundle row
    (one DB query, ~50-300 KB result). Agents only call this when their
    local version differs, so this fires once per agent per publish event.

    Resolution order mirrors get_current_version(): tenant-own → global.
    """
    # 1. Per-tenant manifest first
    body = _db_current_manifest(get_db_fn, tenant_id)
    if body:
        return body

    # 2. Fall back to global pack
    if tenant_id != GLOBAL_TENANT_ID:
        return _db_current_manifest(get_db_fn, GLOBAL_TENANT_ID)

    return None


def list_history(get_db_fn, tenant_id: str, limit: int = 20) -> List[dict]:
    """For the admin UI — show last N published rule packs for this tenant_id."""
    try:
        conn = get_db_fn(); cur = conn.cursor()
        cur.execute(
            "SELECT version, sha256, rule_count, release_notes, "
            "       published_at, is_current "
            "FROM rule_packs WHERE tenant_id = %s "
            "ORDER BY published_at DESC LIMIT %s",
            (tenant_id, limit))
        rows = cur.fetchall()
        cur.close(); conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"[RULES] history error: {e}")
        return []


def list_current_rules(get_db_fn, tenant_id: str) -> dict:
    """
    Return {'yara': [(filename, text), ...], 'sigma': [...]} for the
    currently published pack — used to pre-fill the editor UI.

    NOTE: This deliberately does NOT fall back to the global pack — the
    editor UI is per-tenant (or per-global, depending on caller) and
    pre-filling tenant editors with global rule text would be misleading.
    Callers that want the global pack should pass tenant_id=GLOBAL_TENANT_ID.
    """
    out: dict = {"yara": [], "sigma": []}
    try:
        conn = get_db_fn(); cur = conn.cursor()
        cur.execute(
            "SELECT bundle_b64 FROM rule_packs "
            "WHERE tenant_id = %s AND is_current = TRUE "
            "ORDER BY published_at DESC LIMIT 1",
            (tenant_id,))
        row = cur.fetchone()
        cur.close(); conn.close()
    except Exception as e:
        print(f"[RULES] list_current_rules DB error: {e}")
        return out

    if not row:
        return out

    try:
        bundle = base64.b64decode(row["bundle_b64"])
        with zipfile.ZipFile(io.BytesIO(bundle), "r") as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                fn = info.filename.replace("\\", "/")
                lower = fn.lower()
                if lower.startswith("yara/") and lower.endswith((".yar", ".yara")):
                    out["yara"].append(
                        (os.path.basename(fn),
                         zf.read(info).decode("utf-8", errors="replace")))
                elif lower.startswith("sigma/") and lower.endswith((".yml", ".yaml")):
                    out["sigma"].append(
                        (os.path.basename(fn),
                         zf.read(info).decode("utf-8", errors="replace")))
    except Exception as e:
        print(f"[RULES] list_current_rules unzip error: {e}")

    return out
