"""
routes/tenants.py — Super-admin tenant management and tenant client portal.

Super-admin routes  (/super/*):
  GET  /super/db-migrate
  GET  /super/tenants
  POST /super/tenants
  GET  /super/tenants/{tenant_id}
  POST /super/tenants/{tenant_id}/reset-key
  POST /super/tenants/{tenant_id}/suspend
  GET  /super/alerts
  GET  /super/dashboard
  POST /super/tenants-form
  GET  /super/tenant-detail

Tenant portal routes (/tenant/*):
  GET  /tenant/login
  POST /tenant/login
  GET  /tenant/logout
  GET  /tenant/dashboard
  POST /tenant/config
  POST /tenant/alerts/{alert_id}/ack
  POST /tenant/command/{machine_id}
"""
import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from core.auth import verify_session
from core.config import DATABASE_URL, commands
from core.database import get_db
from core.helpers import (
    _check_admin, _gen_tenant_api_key,
    _hash_password, _verify_password,
)
from core.tenant_utils import (
    _TENANT_SESSION_TTL,
    _create_tenant_session,
    _get_tenant_session,
    _get_tenant_stats,
    _tenant_sessions,
)

router = APIRouter()


# ╔══════════════════════════════════════════════════════════════════════════════
# SUPER-ADMIN ROUTES
# ╚══════════════════════════════════════════════════════════════════════════════

@router.get("/super/db-migrate")
async def super_db_migrate(api_key: str = ""):
    """Idempotent migration — create all tenant tables. Safe to run many times."""
    _check_admin(api_key)
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tenants (
                id            TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
                name          TEXT NOT NULL,
                slug          TEXT UNIQUE NOT NULL,
                api_key       TEXT UNIQUE NOT NULL,
                plan          TEXT NOT NULL DEFAULT 'business',
                max_agents    INTEGER NOT NULL DEFAULT 10,
                contact_email TEXT NOT NULL DEFAULT '',
                notes         TEXT NOT NULL DEFAULT '',
                active        BOOLEAN NOT NULL DEFAULT TRUE,
                created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE TABLE IF NOT EXISTS tenant_agents (
                id            TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
                tenant_id     TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                machine_id    TEXT NOT NULL,
                hostname      TEXT NOT NULL DEFAULT '',
                ip_address    TEXT NOT NULL DEFAULT '',
                os_info       TEXT NOT NULL DEFAULT '',
                agent_version TEXT NOT NULL DEFAULT '2.5.0',
                username      TEXT NOT NULL DEFAULT '',
                tier          TEXT NOT NULL DEFAULT 'free',
                is_armed      BOOLEAN NOT NULL DEFAULT FALSE,
                status        TEXT NOT NULL DEFAULT 'offline',
                last_seen     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                registered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE(tenant_id, machine_id)
            );
            CREATE TABLE IF NOT EXISTS tenant_users (
                id            TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
                tenant_id     TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                email         TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                role          TEXT NOT NULL DEFAULT 'admin',
                created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE(tenant_id, email)
            );
            CREATE TABLE IF NOT EXISTS tenant_alerts (
                id           TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
                tenant_id    TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                agent_id     TEXT REFERENCES tenant_agents(id) ON DELETE SET NULL,
                machine_id   TEXT NOT NULL DEFAULT '',
                hostname     TEXT NOT NULL DEFAULT '',
                severity     TEXT NOT NULL DEFAULT 'INFO',
                event_type   TEXT NOT NULL DEFAULT '',
                message      TEXT NOT NULL DEFAULT '',
                file_path    TEXT NOT NULL DEFAULT '',
                acknowledged BOOLEAN NOT NULL DEFAULT FALSE,
                created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE TABLE IF NOT EXISTS tenant_config (
                tenant_id       TEXT PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
                webhook_url     TEXT NOT NULL DEFAULT '',
                alert_email     TEXT NOT NULL DEFAULT '',
                verify_interval INTEGER NOT NULL DEFAULT 60,
                max_vault_mb    INTEGER NOT NULL DEFAULT 10,
                allowed_exts    TEXT NOT NULL DEFAULT '.txt,.json,.py,.html,.js,.css'
            );
            CREATE INDEX IF NOT EXISTS idx_tenant_agents_tenant
                ON tenant_agents(tenant_id);
            CREATE INDEX IF NOT EXISTS idx_tenant_alerts_tenant_sev
                ON tenant_alerts(tenant_id, severity, created_at DESC);
        """)
        conn.commit(); cur.close(); conn.close()
        return {"ok": True,
                "message": "All tenant tables created / verified successfully."}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── List all tenants (JSON) ────────────────────────────────────────────────────

@router.get("/super/tenants")
async def super_list_tenants(api_key: str = ""):
    _check_admin(api_key)
    if not DATABASE_URL:
        return {"tenants": []}
    conn = get_db(); cur = conn.cursor()
    cur.execute(
        "SELECT t.*, "
        "  (SELECT COUNT(*) FROM tenant_agents a WHERE a.tenant_id=t.id) as agent_count, "
        "  (SELECT COUNT(*) FROM tenant_users  u WHERE u.tenant_id=t.id) as user_count "
        "FROM tenants t ORDER BY t.created_at DESC"
    )
    rows = [dict(r) for r in cur.fetchall()]
    cur.close(); conn.close()
    for r in rows:
        if r.get("created_at"):
            r["created_at"] = r["created_at"].isoformat()
    return {"count": len(rows), "tenants": rows}


# ── Create tenant (JSON) ───────────────────────────────────────────────────────

class CreateTenantBody(BaseModel):
    name:           str
    slug:           str
    contact_email:  str
    plan:           str = "business"
    max_agents:     int = 10
    notes:          str = ""
    admin_email:    str = ""
    admin_password: str = ""
    api_key:        str = ""


@router.post("/super/tenants")
async def super_create_tenant(body: CreateTenantBody):
    _check_admin(body.api_key)
    if not DATABASE_URL:
        raise HTTPException(status_code=500, detail="No database")

    tenant_id  = str(uuid.uuid4())
    tenant_key = _gen_tenant_api_key()
    slug       = body.slug.lower().strip().replace(" ", "-")

    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("""
            INSERT INTO tenants
                (id, name, slug, api_key, plan, max_agents, contact_email, notes)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        """, (tenant_id, body.name.strip(), slug, tenant_key,
              body.plan, body.max_agents,
              body.contact_email.strip(), body.notes.strip()))
        cur.execute(
            "INSERT INTO tenant_config (tenant_id) VALUES (%s)", (tenant_id,))
        if body.admin_email and body.admin_password:
            cur.execute("""
                INSERT INTO tenant_users
                    (tenant_id, email, password_hash, role)
                VALUES (%s,%s,%s,'admin')
            """, (tenant_id, body.admin_email.strip().lower(),
                  _hash_password(body.admin_password)))
        conn.commit(); cur.close(); conn.close()
        print(f"[TENANT] Created: {body.name} ({slug}) — key: {tenant_key}")
        return {
            "ok":        True,
            "tenant_id": tenant_id,
            "api_key":   tenant_key,
            "slug":      slug,
            "message":   f"Tenant '{body.name}' created.",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Get single tenant (JSON) ───────────────────────────────────────────────────

@router.get("/super/tenants/{tenant_id}")
async def super_get_tenant(tenant_id: str, api_key: str = ""):
    _check_admin(api_key)
    if not DATABASE_URL:
        raise HTTPException(status_code=500, detail="No database")
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT * FROM tenants WHERE id=%s", (tenant_id,))
    tenant = cur.fetchone()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    cur.execute(
        "SELECT machine_id,hostname,status,is_armed,last_seen,tier "
        "FROM tenant_agents WHERE tenant_id=%s ORDER BY last_seen DESC",
        (tenant_id,))
    agents_rows = [dict(r) for r in cur.fetchall()]
    cur.execute(
        "SELECT severity,event_type,message,hostname,created_at "
        "FROM tenant_alerts WHERE tenant_id=%s "
        "ORDER BY created_at DESC LIMIT 50",
        (tenant_id,))
    alert_rows = [dict(r) for r in cur.fetchall()]
    cur.close(); conn.close()

    for r in agents_rows:
        if r.get("last_seen"):
            r["last_seen"] = r["last_seen"].isoformat()
    for r in alert_rows:
        if r.get("created_at"):
            r["created_at"] = r["created_at"].isoformat()

    return {
        "tenant": dict(tenant),
        "agents": agents_rows,
        "alerts": alert_rows,
        "stats":  _get_tenant_stats(tenant_id),
    }


# ── Reset tenant API key ───────────────────────────────────────────────────────

@router.post("/super/tenants/{tenant_id}/reset-key")
async def super_reset_tenant_key(tenant_id: str, api_key: str = ""):
    _check_admin(api_key)
    new_key = _gen_tenant_api_key()
    conn = get_db(); cur = conn.cursor()
    cur.execute(
        "UPDATE tenants SET api_key=%s WHERE id=%s RETURNING name",
        (new_key, tenant_id))
    row = cur.fetchone()
    conn.commit(); cur.close(); conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return {"ok": True, "new_api_key": new_key, "tenant": row["name"]}


# ── Suspend / unsuspend tenant ─────────────────────────────────────────────────

@router.post("/super/tenants/{tenant_id}/suspend")
async def super_suspend_tenant(tenant_id: str,
                                suspend: bool = True, api_key: str = ""):
    _check_admin(api_key)
    conn = get_db(); cur = conn.cursor()
    cur.execute(
        "UPDATE tenants SET active=%s WHERE id=%s RETURNING name",
        (not suspend, tenant_id))
    row = cur.fetchone()
    conn.commit(); cur.close(); conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return {"ok": True, "active": not suspend, "tenant": row["name"]}


# ── Global alert view (JSON) ───────────────────────────────────────────────────

@router.get("/super/alerts")
async def super_all_alerts(api_key: str = "", limit: int = 100):
    _check_admin(api_key)
    if not DATABASE_URL:
        return {"alerts": []}
    conn = get_db(); cur = conn.cursor()
    cur.execute("""
        SELECT a.*, t.name as tenant_name, t.slug as tenant_slug
        FROM tenant_alerts a
        JOIN tenants t ON t.id = a.tenant_id
        ORDER BY a.created_at DESC
        LIMIT %s
    """, (min(limit, 500),))
    rows = [dict(r) for r in cur.fetchall()]
    cur.close(); conn.close()
    for r in rows:
        if r.get("created_at"):
            r["created_at"] = r["created_at"].isoformat()
    return {"count": len(rows), "alerts": rows}


# ── Super-admin visual dashboard ───────────────────────────────────────────────

@router.get("/super/dashboard", response_class=HTMLResponse)
async def super_dashboard(request: Request, _: bool = Depends(verify_session)):
    if not DATABASE_URL:
        return HTMLResponse("<h1>No database configured</h1>")

    conn = get_db(); cur = conn.cursor()
    cur.execute("""
        SELECT t.*,
          (SELECT COUNT(*) FROM tenant_agents a WHERE a.tenant_id=t.id)           AS agent_count,
          (SELECT COUNT(*) FROM tenant_agents a WHERE a.tenant_id=t.id
            AND a.status='online')                                                  AS online_count,
          (SELECT COUNT(*) FROM tenant_alerts al WHERE al.tenant_id=t.id
            AND al.acknowledged=FALSE)                                              AS unacked_alerts
        FROM tenants t ORDER BY t.created_at DESC
    """)
    tenants = cur.fetchall()

    cur.execute("SELECT COUNT(*) FROM tenants WHERE active=TRUE")
    total_tenants  = cur.fetchone()["count"]
    cur.execute("SELECT COUNT(*) FROM tenant_agents WHERE status='online'")
    total_online   = cur.fetchone()["count"]
    cur.execute(
        "SELECT COUNT(*) FROM tenant_alerts "
        "WHERE acknowledged=FALSE AND severity='CRITICAL'")
    total_critical = cur.fetchone()["count"]
    cur.close(); conn.close()

    tenant_rows = ""
    for t in tenants:
        status_badge = (
            '<span style="background:#238636;color:#fff;padding:2px 8px;'
            'border-radius:4px;font-size:12px">Active</span>'
            if t["active"] else
            '<span style="background:#6e7681;color:#fff;padding:2px 8px;'
            'border-radius:4px;font-size:12px">Suspended</span>'
        )
        alert_badge = ""
        if t["unacked_alerts"]:
            alert_badge = (
                f'<span style="background:#da3633;color:#fff;padding:2px 8px;'
                f'border-radius:4px;font-size:11px">⚠ {t["unacked_alerts"]}</span>'
            )
        created = t["created_at"].strftime("%Y-%m-%d") if t["created_at"] else "—"
        tenant_rows += f"""
        <tr>
          <td><strong>{t['name']}</strong><br>
              <span style="font-family:monospace;color:#8b949e;font-size:11px">
                {t['slug']}
              </span>
          </td>
          <td>{t['plan'].upper()}</td>
          <td>{t['contact_email']}</td>
          <td>
            <span style="color:#3fb950;font-weight:600">{t['online_count']}</span>
            <span style="color:#8b949e">/ {t['agent_count']} / {t['max_agents']}</span>
          </td>
          <td>{status_badge}</td>
          <td>{alert_badge or '<span style="color:#484f58">—</span>'}</td>
          <td>{created}</td>
          <td style="white-space:nowrap">
            <a href="/super/tenant-detail?id={t['id']}"
               style="color:#2f81f7;text-decoration:none;font-size:12px;margin-right:8px">
              View ›
            </a>
          </td>
        </tr>"""

    if not tenant_rows:
        tenant_rows = (
            "<tr><td colspan='8' style='text-align:center;color:#484f58;"
            "padding:32px'>No tenants yet — create your first one below</td></tr>")

    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><title>FMSecure Super Admin</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:#0a0a0a;color:#e6edf3;font-family:system-ui,sans-serif}}
  nav{{background:#161b22;border-bottom:1px solid #30363d;padding:16px 24px;
       display:flex;justify-content:space-between;align-items:center}}
  .brand{{color:#f0883e;font-weight:700;font-size:18px}}
  nav a{{color:#8b949e;text-decoration:none;font-size:13px;margin-left:16px}}
  nav a:hover{{color:#e6edf3}}
  .container{{padding:28px 32px;max-width:1400px;margin:0 auto}}
  .stat-row{{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-bottom:28px}}
  .stat{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:20px 24px}}
  .stat .num{{font-size:32px;font-weight:800;margin-bottom:4px}}
  .stat .lbl{{color:#8b949e;font-size:13px}}
  .card{{background:#161b22;border:1px solid #30363d;border-radius:8px;
          padding:24px;margin-bottom:24px}}
  .card h3{{margin:0 0 16px;font-size:16px}}
  table{{width:100%;border-collapse:collapse}}
  th{{background:#0d1117;color:#8b949e;padding:10px 16px;text-align:left;
      font-size:11px;font-weight:600;letter-spacing:.5px}}
  td{{padding:12px 16px;border-top:1px solid #21262d;font-size:13px}}
  label{{display:block;color:#8b949e;font-size:11px;font-weight:600;
         letter-spacing:.5px;margin-bottom:6px;margin-top:12px}}
  input,select{{width:100%;background:#0d1117;color:#e6edf3;
                border:1px solid #30363d;border-radius:6px;
                padding:8px 12px;font-size:13px;outline:none}}
  input:focus,select:focus{{border-color:#2f81f7}}
  .grid2{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}
  .grid3{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px}}
  .btn{{background:#238636;color:#fff;border:none;border-radius:6px;
        padding:10px 24px;font-size:13px;font-weight:600;cursor:pointer;margin-top:16px}}
  .btn:hover{{background:#2ea043}}
</style></head><body>
<nav>
  <span class="brand">
    <img src="/static/app_icon.png" width="24" height="24"
         style="vertical-align:middle;margin-right:8px"
         onerror="this.style.display='none'">
    FMSecure — Super Admin
  </span>
  <div>
    <a href="/dashboard">C2 Dashboard</a>
    <a href="/licenses">Licenses</a>
    <a href="/super/dashboard">Tenants</a>
    <a href="/logout">Logout</a>
  </div>
</nav>

<div class="container">
  <div class="stat-row">
    <div class="stat">
      <div class="num" style="color:#2f81f7">{total_tenants}</div>
      <div class="lbl">Active Tenants</div>
    </div>
    <div class="stat">
      <div class="num" style="color:#3fb950">{total_online}</div>
      <div class="lbl">Agents Online Right Now</div>
    </div>
    <div class="stat">
      <div class="num" style="color:#f85149">{total_critical}</div>
      <div class="lbl">Unacknowledged Critical Alerts</div>
    </div>
  </div>

  <div class="card">
    <h3>All Tenants</h3>
    <table>
      <thead><tr>
        <th>TENANT</th><th>PLAN</th><th>CONTACT</th>
        <th>AGENTS (online/total/limit)</th>
        <th>STATUS</th><th>ALERTS</th><th>CREATED</th><th>ACTION</th>
      </tr></thead>
      <tbody>{tenant_rows}</tbody>
    </table>
  </div>

  <div class="card">
    <h3>➕ Create New Tenant</h3>
    <form method="POST" action="/super/tenants-form">
      <div class="grid3">
        <div>
          <label>COMPANY NAME *</label>
          <input name="name" placeholder="Acme Corp" required>
        </div>
        <div>
          <label>SLUG (URL identifier) *</label>
          <input name="slug" placeholder="acme-corp" required
                 pattern="[a-z0-9-]+"
                 title="Lowercase letters, numbers, hyphens only">
        </div>
        <div>
          <label>CONTACT EMAIL *</label>
          <input name="contact_email" type="email" placeholder="it@acme.com" required>
        </div>
      </div>
      <div class="grid3">
        <div>
          <label>PLAN</label>
          <select name="plan">
            <option value="business">Business</option>
            <option value="enterprise">Enterprise</option>
            <option value="trial">Trial (7 days)</option>
          </select>
        </div>
        <div>
          <label>MAX AGENTS (seats)</label>
          <input name="max_agents" type="number" value="10" min="1" max="10000">
        </div>
        <div>
          <label>NOTES (internal)</label>
          <input name="notes" placeholder="e.g. Direct sale, 3-month deal">
        </div>
      </div>
      <div class="grid2" style="margin-top:4px">
        <div>
          <label>FIRST ADMIN EMAIL (optional)</label>
          <input name="admin_email" type="email" placeholder="admin@acme.com">
        </div>
        <div>
          <label>FIRST ADMIN PASSWORD (min 8 chars)</label>
          <input name="admin_password" type="password" placeholder="Strong password">
        </div>
      </div>
      <button class="btn" type="submit">Create Tenant & Generate API Key →</button>
    </form>
  </div>
</div>
</body></html>"""


# ── Form handler: create tenant ────────────────────────────────────────────────

@router.post("/super/tenants-form")
async def super_create_tenant_form(
    request:        Request,
    name:           str = Form(...),
    slug:           str = Form(...),
    contact_email:  str = Form(...),
    plan:           str = Form("business"),
    max_agents:     int = Form(10),
    notes:          str = Form(""),
    admin_email:    str = Form(""),
    admin_password: str = Form(""),
    _: bool = Depends(verify_session),
):
    tenant_id  = str(uuid.uuid4())
    tenant_key = _gen_tenant_api_key()
    slug_clean = slug.lower().strip().replace(" ", "-")

    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("""
            INSERT INTO tenants
                (id, name, slug, api_key, plan, max_agents, contact_email, notes)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        """, (tenant_id, name.strip(), slug_clean, tenant_key,
              plan, max_agents, contact_email.strip(), notes.strip()))
        cur.execute(
            "INSERT INTO tenant_config (tenant_id) VALUES (%s)", (tenant_id,))
        if admin_email and admin_password:
            cur.execute("""
                INSERT INTO tenant_users
                    (tenant_id, email, password_hash, role)
                VALUES (%s,%s,%s,'admin')
            """, (tenant_id, admin_email.strip().lower(),
                  _hash_password(admin_password)))
        conn.commit(); cur.close(); conn.close()
        print(f"[TENANT] Created via dashboard: {name} — {tenant_key}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return RedirectResponse(
        f"/super/tenant-detail?id={tenant_id}&new_key={tenant_key}",
        status_code=303)


# ── Tenant detail page (super-admin view) ─────────────────────────────────────

@router.get("/super/tenant-detail", response_class=HTMLResponse)
async def super_tenant_detail(
    request: Request,
    id: str = "",
    new_key: str = "",
    _: bool = Depends(verify_session),
):
    if not id or not DATABASE_URL:
        return RedirectResponse("/super/dashboard")

    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT * FROM tenants WHERE id=%s", (id,))
    tenant = cur.fetchone()
    if not tenant:
        cur.close(); conn.close()
        return RedirectResponse("/super/dashboard")

    cur.execute(
        "SELECT machine_id,hostname,ip_address,username,tier,"
        "is_armed,status,last_seen,agent_version "
        "FROM tenant_agents WHERE tenant_id=%s ORDER BY last_seen DESC",
        (id,))
    agents_list = cur.fetchall()

    cur.execute(
        "SELECT severity,event_type,message,hostname,created_at,acknowledged "
        "FROM tenant_alerts WHERE tenant_id=%s "
        "ORDER BY created_at DESC LIMIT 100",
        (id,))
    alert_list = cur.fetchall()

    cur.execute(
        "SELECT email,role,created_at FROM tenant_users WHERE tenant_id=%s",
        (id,))
    user_list = cur.fetchall()
    cur.close(); conn.close()

    # ── Build agent rows ───────────────────────────────────────────────────────
    agent_rows = ""
    for a in agents_list:
        online = a["status"] == "online"
        sb = ('<span style="color:#3fb950;font-weight:600">● ONLINE</span>'
              if online else '<span style="color:#6e7681">○ OFFLINE</span>')
        arm = ('<span style="color:#1f6feb">ARMED</span>'
               if a["is_armed"] else '<span style="color:#9e6a03">UNARMED</span>')
        ls = a["last_seen"].strftime("%H:%M:%S %d/%m") if a["last_seen"] else "—"
        agent_rows += f"""
        <tr>
          <td style="font-family:monospace;font-size:11px;color:#8b949e">
            {a['machine_id'][:20]}…
          </td>
          <td><strong>{a['hostname']}</strong></td>
          <td>{a['username']}</td>
          <td>{a['ip_address']}</td>
          <td>{sb}</td><td>{arm}</td>
          <td>{a['tier'].upper()}</td>
          <td>{ls}</td>
        </tr>"""
    if not agent_rows:
        agent_rows = (
            "<tr><td colspan='8' style='text-align:center;color:#484f58;"
            "padding:24px'>No agents registered yet</td></tr>")

    # ── Build alert rows ───────────────────────────────────────────────────────
    alert_rows = ""
    sev_colors = {"CRITICAL": "#f85149", "HIGH": "#f0883e",
                  "MEDIUM": "#d29922",   "INFO": "#3fb950"}
    for al in alert_list:
        clr = sev_colors.get(al["severity"], "#8b949e")
        ts  = al["created_at"].strftime("%Y-%m-%d %H:%M") if al["created_at"] else "—"
        ack = "✓" if al["acknowledged"] else f'<span style="color:{clr}">●</span>'
        alert_rows += f"""
        <tr>
          <td><span style="color:{clr};font-weight:600">{al['severity']}</span></td>
          <td style="font-size:12px">{al['event_type']}</td>
          <td>{al['hostname']}</td>
          <td style="font-size:12px;max-width:300px;overflow:hidden;
                     text-overflow:ellipsis;white-space:nowrap">
            {al['message'][:120]}
          </td>
          <td style="font-size:11px;color:#8b949e">{ts}</td>
          <td style="text-align:center">{ack}</td>
        </tr>"""
    if not alert_rows:
        alert_rows = (
            "<tr><td colspan='6' style='text-align:center;color:#484f58;"
            "padding:24px'>No alerts recorded</td></tr>")

    # ── New API key banner ─────────────────────────────────────────────────────
    new_key_banner = ""
    if new_key:
        new_key_banner = f"""
        <div style="background:#0c2d0c;border:1px solid #238636;border-radius:8px;
                    padding:20px 24px;margin-bottom:24px">
          <div style="font-weight:700;color:#3fb950;margin-bottom:8px">
            ✅ Tenant Created Successfully
          </div>
          <div style="color:#8b949e;font-size:13px;margin-bottom:10px">
            Hand this API key to the firm's IT administrator.
            Store it safely — it won't be shown again in full.
          </div>
          <div style="font-family:monospace;font-size:16px;color:#e6edf3;
                      background:#0d1117;padding:14px 18px;border-radius:6px;
                      letter-spacing:1px;word-break:break-all">
            {new_key}
          </div>
          <div style="color:#484f58;font-size:12px;margin-top:8px">
            Desktop agents include this key as the
            <code>x-tenant-key</code> header in every heartbeat request.
          </div>
        </div>"""

    # ── User list rows ─────────────────────────────────────────────────────────
    user_rows = ""
    for u in user_list:
        ts = u["created_at"].strftime("%Y-%m-%d") if u["created_at"] else "—"
        user_rows += f"""
        <tr>
          <td>{u['email']}</td>
          <td>{u['role'].upper()}</td>
          <td style="color:#8b949e;font-size:12px">{ts}</td>
        </tr>"""
    if not user_rows:
        user_rows = (
            "<tr><td colspan='3' style='color:#484f58;padding:16px'>"
            "No admin users yet</td></tr>")

    created = (tenant["created_at"].strftime("%Y-%m-%d %H:%M")
               if tenant["created_at"] else "—")

    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8">
<title>FMSecure | {tenant['name']}</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:#0a0a0a;color:#e6edf3;font-family:system-ui,sans-serif}}
  nav{{background:#161b22;border-bottom:1px solid #30363d;padding:16px 24px;
       display:flex;justify-content:space-between;align-items:center}}
  .brand{{color:#f0883e;font-weight:700}}
  nav a{{color:#8b949e;text-decoration:none;font-size:13px;margin-left:16px}}
  nav a:hover{{color:#e6edf3}}
  .container{{padding:28px 32px;max-width:1400px;margin:0 auto}}
  .card{{background:#161b22;border:1px solid #30363d;border-radius:8px;
          padding:24px;margin-bottom:20px}}
  .card h3{{margin:0 0 16px;font-size:15px;color:#8b949e;
            letter-spacing:.5px;text-transform:uppercase}}
  table{{width:100%;border-collapse:collapse}}
  th{{background:#0d1117;color:#8b949e;padding:10px 16px;text-align:left;
      font-size:11px;font-weight:600;letter-spacing:.5px}}
  td{{padding:10px 16px;border-top:1px solid #21262d;font-size:13px}}
  .meta-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;
              margin-bottom:20px}}
  .meta{{background:#161b22;border:1px solid #30363d;border-radius:8px;
         padding:16px 20px}}
  .meta .lbl{{color:#8b949e;font-size:11px;font-weight:600;
              letter-spacing:.5px;margin-bottom:4px}}
  .meta .val{{font-size:18px;font-weight:700}}
</style></head><body>
<nav>
  <span class="brand">
    <img src="/static/app_icon.png" width="24" height="24"
         style="vertical-align:middle;margin-right:8px"
         onerror="this.style.display='none'">
    FMSecure
  </span>
  <div>
    <a href="/super/dashboard">← All Tenants</a>
    <a href="/dashboard">C2</a>
    <a href="/logout">Logout</a>
  </div>
</nav>

<div class="container">
  <div style="margin-bottom:20px">
    <h1 style="font-size:24px">{tenant['name']}</h1>
    <span style="color:#8b949e;font-size:13px">
      {tenant['slug']} · {tenant['plan'].upper()} · {tenant['contact_email']}
      · Created {created}
    </span>
  </div>

  {new_key_banner}

  <div class="meta-grid">
    <div class="meta">
      <div class="lbl">AGENTS REGISTERED</div>
      <div class="val">{len(agents_list)} / {tenant['max_agents']}</div>
    </div>
    <div class="meta">
      <div class="lbl">ONLINE NOW</div>
      <div class="val" style="color:#3fb950">
        {sum(1 for a in agents_list if a['status']=='online')}
      </div>
    </div>
    <div class="meta">
      <div class="lbl">TOTAL ALERTS</div>
      <div class="val">{len(alert_list)}</div>
    </div>
    <div class="meta">
      <div class="lbl">STATUS</div>
      <div class="val" style="color:{'#3fb950' if tenant['active'] else '#f85149'}">
        {'Active' if tenant['active'] else 'Suspended'}
      </div>
    </div>
  </div>

  <div class="card">
    <h3>Agents</h3>
    <table>
      <thead><tr>
        <th>MACHINE ID</th><th>HOSTNAME</th><th>USER</th><th>IP</th>
        <th>STATUS</th><th>ENGINE</th><th>TIER</th><th>LAST SEEN</th>
      </tr></thead>
      <tbody>{agent_rows}</tbody>
    </table>
  </div>

  <div class="card">
    <h3>Recent Alerts (last 100)</h3>
    <table>
      <thead><tr>
        <th>SEV</th><th>TYPE</th><th>HOST</th>
        <th>MESSAGE</th><th>TIME</th><th>ACK</th>
      </tr></thead>
      <tbody>{alert_rows}</tbody>
    </table>
  </div>

  <div class="card">
    <h3>Admin Users</h3>
    <table>
      <thead><tr><th>EMAIL</th><th>ROLE</th><th>CREATED</th></tr></thead>
      <tbody>{user_rows}</tbody>
    </table>
  </div>
</div>
</body></html>"""


# ╔══════════════════════════════════════════════════════════════════════════════
# TENANT CLIENT PORTAL ROUTES
# ╚══════════════════════════════════════════════════════════════════════════════

@router.get("/tenant/login", response_class=HTMLResponse)
async def tenant_login_page(error: str = ""):
    err = (
        f'<p style="color:#f85149;background:#2d1c1c;padding:10px;'
        f'border-radius:6px;margin-bottom:16px;font-size:14px">{error}</p>'
        if error else ""
    )
    return f"""<!DOCTYPE html>
<html><head>
<meta charset="UTF-8">
<title>FMSecure | Organisation Login</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:#0a0a0a;color:#e6edf3;display:flex;align-items:center;
       justify-content:center;min-height:100vh;font-family:system-ui,sans-serif}}
  .card{{background:#161b22;border:1px solid #30363d;border-radius:12px;
         padding:40px;width:380px}}
  h2{{color:#2f81f7;margin-bottom:4px;font-size:22px}}
  p.sub{{color:#8b949e;font-size:13px;margin-bottom:24px}}
  label{{display:block;color:#8b949e;font-size:11px;font-weight:600;
         letter-spacing:.5px;margin-bottom:6px}}
  input{{width:100%;background:#0d1117;border:1px solid #30363d;border-radius:6px;
         color:#e6edf3;padding:10px 14px;font-size:14px;outline:none;
         margin-bottom:16px}}
  input:focus{{border-color:#2f81f7}}
  button{{width:100%;background:#2f81f7;border:none;border-radius:6px;
          color:#fff;padding:12px;font-size:14px;font-weight:600;cursor:pointer}}
  button:hover{{background:#4f96ff}}
</style>
</head><body>
<div class="card">
  <img src="/static/app_icon.png" width="52" height="52"
       style="margin-bottom:12px" onerror="this.style.display='none'">
  <h2>FMSecure</h2>
  <p class="sub">Organisation Security Portal</p>
  {err}
  <form method="POST" action="/tenant/login">
    <label>EMAIL ADDRESS</label>
    <input name="email" type="email" required autofocus
           placeholder="admin@yourcompany.com">
    <label>PASSWORD</label>
    <input name="password" type="password" required placeholder="••••••••">
    <button type="submit">Sign In →</button>
  </form>
  <p style="color:#484f58;font-size:12px;text-align:center;margin-top:20px">
    Contact your FMSecure account manager if you need access.
  </p>
</div>
</body></html>"""


@router.post("/tenant/login")
async def tenant_login_post(
    email:    str = Form(...),
    password: str = Form(...),
):
    if not DATABASE_URL:
        return RedirectResponse(
            "/tenant/login?error=Server+not+configured", status_code=302)

    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("""
            SELECT u.*, t.id as tenant_id, t.name as tenant_name,
                   t.active as tenant_active
            FROM tenant_users u
            JOIN tenants t ON t.id = u.tenant_id
            WHERE u.email = %s
        """, (email.strip().lower(),))
        user = cur.fetchone()
        cur.close(); conn.close()
    except Exception as e:
        print(f"[TENANT LOGIN] DB error: {e}")
        return RedirectResponse(
            "/tenant/login?error=Server+error", status_code=302)

    if not user:
        return RedirectResponse(
            "/tenant/login?error=Invalid+credentials", status_code=302)
    if not user["tenant_active"]:
        return RedirectResponse(
            "/tenant/login?error=Your+organisation+account+is+suspended",
            status_code=302)
    if not _verify_password(password, user["password_hash"]):
        return RedirectResponse(
            "/tenant/login?error=Invalid+credentials", status_code=302)

    token = _create_tenant_session(
        user["tenant_id"], user["email"], user["role"])
    resp = RedirectResponse("/tenant/dashboard", status_code=302)
    resp.set_cookie(
        "fms_tenant_session", token,
        httponly=True, max_age=_TENANT_SESSION_TTL)
    return resp


@router.get("/tenant/logout")
async def tenant_logout(request: Request):
    token = request.cookies.get("fms_tenant_session", "")
    _tenant_sessions.pop(token, None)
    resp = RedirectResponse("/tenant/login", status_code=302)
    resp.delete_cookie("fms_tenant_session")
    return resp


@router.get("/tenant/dashboard", response_class=HTMLResponse)
async def tenant_dashboard(request: Request):
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

    cur.execute(
        "UPDATE tenant_agents SET status='offline' "
        "WHERE tenant_id=%s AND last_seen < NOW() - INTERVAL '35 seconds'",
        (tenant_id,))
    conn.commit()

    cur.execute("""
        SELECT machine_id,hostname,ip_address,username,tier,
               is_armed,status,last_seen,agent_version
        FROM tenant_agents WHERE tenant_id=%s
        ORDER BY status DESC, last_seen DESC
    """, (tenant_id,))
    agents_list = cur.fetchall()

    cur.execute("""
        SELECT severity,event_type,message,hostname,file_path,
               created_at,acknowledged,id
        FROM tenant_alerts WHERE tenant_id=%s
        ORDER BY acknowledged ASC, created_at DESC
        LIMIT 50
    """, (tenant_id,))
    alert_list = cur.fetchall()

    cur.execute("SELECT * FROM tenant_config WHERE tenant_id=%s", (tenant_id,))
    config = cur.fetchone() or {}
    cur.close(); conn.close()

    stats  = _get_tenant_stats(tenant_id)
    online = sum(1 for a in agents_list if a["status"] == "online")
    armed  = sum(1 for a in agents_list if a["is_armed"])

    # ── Agent rows ─────────────────────────────────────────────────────────────
    agent_rows = ""
    for a in agents_list:
        is_online = a["status"] == "online"
        s_dot = ('<span style="color:#3fb950">●</span>'
                 if is_online else '<span style="color:#6e7681">○</span>')
        arm_badge = (
            '<span style="background:#1f2d4d;color:#2f81f7;padding:2px 8px;'
            'border-radius:4px;font-size:11px">ARMED</span>'
            if a["is_armed"] else
            '<span style="background:#2d2208;color:#d29922;padding:2px 8px;'
            'border-radius:4px;font-size:11px">UNARMED</span>'
        )
        ls = a["last_seen"].strftime("%H:%M %d/%m") if a["last_seen"] else "—"
        agent_rows += f"""
        <tr>
          <td>{s_dot} <strong>{a['hostname']}</strong></td>
          <td>{a['username']}</td>
          <td style="font-size:12px;color:#8b949e">{a['ip_address']}</td>
          <td>{arm_badge}</td>
          <td style="font-size:12px;color:#8b949e">{a['tier'].upper()}</td>
          <td style="font-size:12px;color:#8b949e">{a['agent_version']}</td>
          <td style="font-size:11px;color:#8b949e">{ls}</td>
          <td>
            <button onclick="sendCommand('{a['machine_id']}','LOCKDOWN')"
                    style="background:#da3633;color:#fff;border:none;
                           border-radius:4px;padding:3px 10px;
                           cursor:pointer;font-size:11px">
              Isolate
            </button>
          </td>
        </tr>"""
    if not agent_rows:
        agent_rows = (
            "<tr><td colspan='8' style='text-align:center;color:#484f58;"
            "padding:32px'>No agents connected yet. Install FMSecure on endpoints "
            "and configure the tenant API key.</td></tr>")

    # ── Alert rows ─────────────────────────────────────────────────────────────
    alert_rows = ""
    sev_colors = {"CRITICAL": "#f85149", "HIGH": "#f0883e",
                  "MEDIUM": "#d29922",   "INFO": "#3fb950"}
    for al in alert_list:
        clr        = sev_colors.get(al["severity"], "#8b949e")
        ts         = al["created_at"].strftime("%Y-%m-%d %H:%M") if al["created_at"] else "—"
        acked      = al["acknowledged"]
        row_style  = "opacity:.5" if acked else ""
        ack_html   = (
            '<span style="color:#3fb950;font-size:12px">✓ Acked</span>'
            if acked else
            f'<button onclick="ackAlert(\'{al["id"]}\')"'
            f' style="background:#21262d;color:#8b949e;border:1px solid #30363d;'
            f'border-radius:4px;padding:2px 8px;cursor:pointer;font-size:11px">'
            f'Acknowledge</button>'
        )
        alert_rows += f"""
        <tr style="{row_style}">
          <td><span style="color:{clr};font-weight:700;font-size:12px">{al['severity']}</span></td>
          <td style="font-size:12px">{al['event_type']}</td>
          <td>{al['hostname']}</td>
          <td style="font-size:12px;max-width:280px;overflow:hidden;
                     text-overflow:ellipsis;white-space:nowrap">
            {al['message'][:100]}
          </td>
          <td style="font-size:11px;color:#8b949e">{ts}</td>
          <td>{ack_html}</td>
        </tr>"""
    if not alert_rows:
        alert_rows = (
            "<tr><td colspan='6' style='text-align:center;color:#484f58;"
            "padding:24px'>No alerts recorded</td></tr>")

    webhook_val = (config.get("webhook_url") or "") if config else ""
    email_val   = (config.get("alert_email")  or "") if config else ""

    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8">
<title>FMSecure | {tenant['name']} Dashboard</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:#0a0a0a;color:#e6edf3;font-family:system-ui,sans-serif}}
  nav{{background:#161b22;border-bottom:1px solid #30363d;padding:14px 28px;
       display:flex;justify-content:space-between;align-items:center}}
  .brand{{color:#2f81f7;font-weight:700;font-size:16px}}
  nav a{{color:#8b949e;text-decoration:none;font-size:13px;margin-left:16px}}
  nav a:hover{{color:#e6edf3}}
  .org{{color:#e6edf3;font-weight:600;font-size:14px}}
  .container{{padding:24px 32px;max-width:1400px;margin:0 auto}}
  .stat-row{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;
             margin-bottom:24px}}
  .stat{{background:#161b22;border:1px solid #30363d;border-radius:8px;
         padding:18px 22px}}
  .stat .num{{font-size:28px;font-weight:800;margin-bottom:2px}}
  .stat .lbl{{color:#8b949e;font-size:12px}}
  .card{{background:#161b22;border:1px solid #30363d;border-radius:8px;
          padding:22px;margin-bottom:20px}}
  .card h3{{margin:0 0 14px;font-size:13px;color:#8b949e;
            font-weight:600;letter-spacing:.5px;text-transform:uppercase}}
  table{{width:100%;border-collapse:collapse}}
  th{{background:#0d1117;color:#8b949e;padding:9px 14px;text-align:left;
      font-size:11px;font-weight:600;letter-spacing:.5px}}
  td{{padding:10px 14px;border-top:1px solid #21262d;font-size:13px}}
  label{{display:block;color:#8b949e;font-size:11px;font-weight:600;
         letter-spacing:.5px;margin-bottom:5px;margin-top:12px}}
  input{{width:100%;background:#0d1117;color:#e6edf3;
         border:1px solid #30363d;border-radius:6px;
         padding:8px 12px;font-size:13px;outline:none}}
  input:focus{{border-color:#2f81f7}}
  .save-btn{{background:#238636;color:#fff;border:none;border-radius:6px;
             padding:9px 22px;font-size:13px;font-weight:600;
             cursor:pointer;margin-top:14px}}
  .save-btn:hover{{background:#2ea043}}
</style>
</head><body>
<nav>
  <span class="brand">
    <img src="/static/app_icon.png" width="24" height="24"
         style="vertical-align:middle;margin-right:8px"
         onerror="this.style.display='none'">
    FMSecure
  </span>
  <span class="org">🏢 {tenant['name']}</span>
  <div>
    <span style="color:#8b949e;font-size:12px">
      Signed in as {session['email']}
    </span>
    <a href="/tenant/logout">Sign Out</a>
  </div>
</nav>

<div class="container">
  {'<div style="background:#2d0d0d;border:1px solid #f85149;border-radius:8px;padding:14px 20px;margin-bottom:20px;color:#f85149;font-weight:600">⚠ ' + str(stats["critical_alerts"]) + ' unacknowledged CRITICAL alert(s) require your attention</div>' if stats['critical_alerts'] else ''}

  <div class="stat-row">
    <div class="stat">
      <div class="num">{stats['total_agents']}</div>
      <div class="lbl">Total Endpoints</div>
    </div>
    <div class="stat">
      <div class="num" style="color:#3fb950">{online}</div>
      <div class="lbl">Online Now</div>
    </div>
    <div class="stat">
      <div class="num" style="color:#2f81f7">{armed}</div>
      <div class="lbl">Engines Armed</div>
    </div>
    <div class="stat">
      <div class="num" style="color:{'#f85149' if stats['unacked_alerts'] else '#3fb950'}">
        {stats['unacked_alerts']}
      </div>
      <div class="lbl">Unacknowledged Alerts</div>
    </div>
  </div>

  <div style="color:#8b949e;font-size:12px;margin-bottom:16px">
    Seat usage: {stats['total_agents']} / {tenant['max_agents']} agents
    · Plan: <strong style="color:#e6edf3">{tenant['plan'].upper()}</strong>
  </div>

  <div class="card">
    <h3>Endpoints</h3>
    <table>
      <thead><tr>
        <th>HOSTNAME</th><th>USER</th><th>IP</th>
        <th>ENGINE</th><th>TIER</th><th>VERSION</th>
        <th>LAST SEEN</th><th>ACTION</th>
      </tr></thead>
      <tbody>{agent_rows}</tbody>
    </table>
  </div>

  <div class="card">
    <h3>Security Alerts</h3>
    <table>
      <thead><tr>
        <th>SEV</th><th>TYPE</th><th>HOST</th>
        <th>MESSAGE</th><th>TIME</th><th>STATUS</th>
      </tr></thead>
      <tbody>{alert_rows}</tbody>
    </table>
  </div>

  <div class="card">
    <h3>Policy Settings</h3>
    <p style="color:#8b949e;font-size:12px;margin-bottom:14px">
      These settings are pushed to all enrolled agents automatically.
    </p>
    <form method="POST" action="/tenant/config">
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">
        <div>
          <label>ALERT EMAIL</label>
          <input name="alert_email" type="email"
                 value="{email_val}"
                 placeholder="it-alerts@yourcompany.com">
        </div>
        <div>
          <label>DISCORD / SLACK WEBHOOK URL</label>
          <input name="webhook_url"
                 value="{webhook_val}"
                 placeholder="https://discord.com/api/webhooks/...">
        </div>
        <div>
          <label>VERIFY INTERVAL (seconds, min 10)</label>
          <input name="verify_interval" type="number"
                 value="{(config.get('verify_interval') or 60) if config else 60}"
                 min="10" max="86400">
        </div>
        <div>
          <label>MAX VAULT FILE SIZE (MB)</label>
          <input name="max_vault_mb" type="number"
                 value="{(config.get('max_vault_mb') or 10) if config else 10}"
                 min="1" max="500">
        </div>
        <div style="grid-column:1/-1">
          <label>ALLOWED VAULT EXTENSIONS (comma-separated)</label>
          <input name="allowed_exts"
                 value="{(config.get('allowed_exts') or '.txt,.json,.py') if config else '.txt,.json,.py'}"
                 placeholder=".txt,.json,.py,.html,...">
        </div>
      </div>
      <button class="save-btn" type="submit">Save Policy →</button>
    </form>
  </div>
</div>

<script>
setInterval(() => {{
  if (!document.querySelector('input:focus')) location.reload();
}}, 15000);

async function ackAlert(alertId) {{
  try {{
    await fetch('/tenant/alerts/' + alertId + '/ack', {{method:'POST'}});
    location.reload();
  }} catch(e) {{ alert('Failed to acknowledge: ' + e); }}
}}

async function sendCommand(machineId, cmd) {{
  if (!confirm('Send ' + cmd + ' to ' + machineId + '?')) return;
  try {{
    const resp = await fetch('/tenant/command/' + machineId + '?cmd=' + cmd,
                             {{method:'POST'}});
    const data = await resp.json();
    alert(data.message || 'Command queued.');
  }} catch(e) {{ alert('Failed: ' + e); }}
}}
</script>
</body></html>"""


@router.post("/tenant/config")
async def tenant_save_config(
    request:         Request,
    alert_email:     str = Form(""),
    webhook_url:     str = Form(""),
    verify_interval: int = Form(60),
    max_vault_mb:    int = Form(10),
    allowed_exts:    str = Form(".txt,.json,.py,.html,.js,.css"),
):
    session = _get_tenant_session(request)
    if not session:
        return RedirectResponse("/tenant/login", status_code=302)
    if not DATABASE_URL:
        return RedirectResponse("/tenant/dashboard", status_code=302)

    verify_interval = max(10, min(verify_interval, 86400))
    max_vault_mb    = max(1,  min(max_vault_mb, 500))

    conn = get_db(); cur = conn.cursor()
    cur.execute("""
        INSERT INTO tenant_config
            (tenant_id, alert_email, webhook_url,
             verify_interval, max_vault_mb, allowed_exts)
        VALUES (%s,%s,%s,%s,%s,%s)
        ON CONFLICT (tenant_id) DO UPDATE SET
            alert_email     = EXCLUDED.alert_email,
            webhook_url     = EXCLUDED.webhook_url,
            verify_interval = EXCLUDED.verify_interval,
            max_vault_mb    = EXCLUDED.max_vault_mb,
            allowed_exts    = EXCLUDED.allowed_exts
    """, (session["tenant_id"],
          alert_email.strip(), webhook_url.strip(),
          verify_interval, max_vault_mb, allowed_exts.strip()))
    conn.commit(); cur.close(); conn.close()
    print(f"[TENANT CONFIG] Updated for tenant {session['tenant_id'][:8]}…")
    return RedirectResponse("/tenant/dashboard", status_code=302)


@router.post("/tenant/alerts/{alert_id}/ack")
async def tenant_ack_alert(alert_id: str, request: Request):
    session = _get_tenant_session(request)
    if not session:
        raise HTTPException(status_code=401)
    if not DATABASE_URL:
        return {"ok": False}
    conn = get_db(); cur = conn.cursor()
    cur.execute(
        "UPDATE tenant_alerts SET acknowledged=TRUE "
        "WHERE id=%s AND tenant_id=%s",
        (alert_id, session["tenant_id"]))
    conn.commit(); cur.close(); conn.close()
    return {"ok": True}


@router.post("/tenant/command/{machine_id}")
async def tenant_send_command(
    machine_id: str,
    request:    Request,
    cmd:        str = "LOCKDOWN",
):
    session = _get_tenant_session(request)
    if not session:
        raise HTTPException(status_code=401)

    allowed_cmds = {"LOCKDOWN", "VERIFY", "SAFE_MODE"}
    if cmd not in allowed_cmds:
        raise HTTPException(status_code=400, detail="Unknown command")

    if DATABASE_URL:
        conn = get_db(); cur = conn.cursor()
        cur.execute(
            "SELECT id FROM tenant_agents "
            "WHERE machine_id=%s AND tenant_id=%s",
            (machine_id, session["tenant_id"]))
        row = cur.fetchone()
        cur.close(); conn.close()
        if not row:
            raise HTTPException(
                status_code=403,
                detail="Agent does not belong to your organisation")

    commands[machine_id] = cmd
    print(f"[TENANT CMD] {cmd} queued for {machine_id} by {session['email']}")
    return {"ok": True, "message": f"{cmd} queued for {machine_id}"}
