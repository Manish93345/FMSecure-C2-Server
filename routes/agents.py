"""
routes/agents.py — Agent-facing endpoints and the C2 dashboard.

Routes:
  POST /api/heartbeat
  POST /api/agent/alert
  GET  /agent/config
  POST /api/trigger_lockdown/{machine_id}
  GET  /dashboard
"""
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from core.auth import verify_session
from core.config import API_KEY, DATABASE_URL, limiter
from core.database import get_db
from core.tenant_utils import _get_tenant_by_api_key
from core.config import agents, commands

router = APIRouter()


# ── Pydantic models ────────────────────────────────────────────────────────────

class Heartbeat(BaseModel):
    machine_id:    str
    hostname:      str
    username:      str
    tier:          str
    is_armed:      bool
    agent_version: str = "2.5.0"
    os_info:       str = ""


class AgentAlert(BaseModel):
    machine_id: str
    hostname:   str
    severity:   str
    event_type: str
    message:    str
    file_path:  str = ""


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.post("/api/heartbeat")
@limiter.limit("200/minute")
async def receive_heartbeat(request: Request, data: Heartbeat):
    tenant_key = request.headers.get("x-tenant-key", "")
    api_key    = request.headers.get("x-api-key",    "")

    # ── Multi-tenant path ──────────────────────────────────────────────────────
    if tenant_key:
        tenant = _get_tenant_by_api_key(tenant_key)
        if not tenant:
            raise HTTPException(status_code=401, detail="Invalid tenant key")

        # Seat enforcement: reject genuinely new machines at capacity
        if DATABASE_URL:
            try:
                conn = get_db(); cur = conn.cursor()

                cur.execute(
                    "SELECT id FROM tenant_agents "
                    "WHERE tenant_id=%s AND machine_id=%s",
                    (tenant["id"], data.machine_id))
                already_registered = cur.fetchone() is not None

                if not already_registered:
                    cur.execute(
                        "SELECT COUNT(*) FROM tenant_agents WHERE tenant_id=%s",
                        (tenant["id"],))
                    current_count = cur.fetchone()["count"]
                    max_seats     = tenant.get("max_agents", 10)

                    if current_count >= max_seats:
                        cur.close(); conn.close()
                        print(f"[SEAT] Tenant {tenant['slug']} at capacity "
                              f"({current_count}/{max_seats}). "
                              f"Rejecting {data.machine_id[:16]}…")
                        raise HTTPException(
                            status_code=402,
                            detail=(
                                f"Seat limit reached ({current_count}/{max_seats}). "
                                f"Contact your administrator to add more seats."
                            ),
                        )

                cur.close(); conn.close()

            except HTTPException:
                raise
            except Exception as e:
                print(f"[SEAT] Check error (non-critical): {e}")

        # Upsert agent record
        if DATABASE_URL:
            try:
                conn = get_db(); cur = conn.cursor()

                cur.execute(
                    "UPDATE tenant_agents SET status='offline' "
                    "WHERE tenant_id=%s AND last_seen < NOW() - INTERVAL '35 seconds'",
                    (tenant["id"],))

                cur.execute("""
                    INSERT INTO tenant_agents
                        (tenant_id, machine_id, hostname, ip_address, username,
                         tier, is_armed, status, agent_version, os_info, last_seen)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,'online',%s,%s,NOW())
                    ON CONFLICT (tenant_id, machine_id) DO UPDATE SET
                        hostname      = EXCLUDED.hostname,
                        ip_address    = EXCLUDED.ip_address,
                        username      = EXCLUDED.username,
                        tier          = EXCLUDED.tier,
                        is_armed      = EXCLUDED.is_armed,
                        status        = 'online',
                        agent_version = EXCLUDED.agent_version,
                        os_info       = EXCLUDED.os_info,
                        last_seen     = NOW()
                    RETURNING id
                """, (
                    tenant["id"], data.machine_id, data.hostname,
                    request.client.host, data.username, data.tier,
                    data.is_armed, data.agent_version, data.os_info,
                ))
                conn.commit(); cur.close(); conn.close()

            except Exception as e:
                print(f"[TENANT HB] DB error: {e}")

        cmd = commands.pop(data.machine_id, "NONE")
        return {"status": "ok", "command": cmd, "tenant": tenant["slug"]}

    # ── Legacy single-user path ────────────────────────────────────────────────
    if api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")
    agents[data.machine_id] = {
        "hostname":  data.hostname,
        "username":  data.username,
        "tier":      data.tier,
        "is_armed":  data.is_armed,
        "last_seen": time.time(),
        "ip":        request.client.host,
    }
    return {"status": "ok", "command": commands.pop(data.machine_id, "NONE")}


@router.post("/api/agent/alert")
@limiter.limit("60/minute")
async def receive_agent_alert(request: Request, data: AgentAlert):
    tenant_key = request.headers.get("x-tenant-key", "")
    if not tenant_key:
        raise HTTPException(status_code=400, detail="x-tenant-key required")

    tenant = _get_tenant_by_api_key(tenant_key)
    if not tenant:
        raise HTTPException(status_code=401, detail="Invalid tenant key")

    if not DATABASE_URL:
        return {"status": "ok", "stored": False}

    try:
        conn = get_db(); cur = conn.cursor()

        cur.execute(
            "SELECT id FROM tenant_agents "
            "WHERE tenant_id=%s AND machine_id=%s",
            (tenant["id"], data.machine_id))
        agent_row = cur.fetchone()
        agent_id  = agent_row["id"] if agent_row else None

        cur.execute("""
            INSERT INTO tenant_alerts
                (tenant_id, agent_id, machine_id, hostname,
                 severity, event_type, message, file_path)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        """, (
            tenant["id"], agent_id, data.machine_id, data.hostname,
            data.severity.upper(), data.event_type,
            data.message[:1000], data.file_path[:500],
        ))
        conn.commit(); cur.close(); conn.close()
        return {"status": "ok", "stored": True}

    except Exception as e:
        print(f"[ALERT] DB error: {e}")
        return {"status": "ok", "stored": False}


@router.get("/agent/config")
async def get_agent_config(request: Request):
    """
    Returns the tenant's policy config for this agent.
    Auth: x-tenant-key header (same as heartbeat).
    """
    tenant_key = request.headers.get("x-tenant-key", "")
    if not tenant_key:
        raise HTTPException(status_code=400, detail="x-tenant-key header required")

    tenant = _get_tenant_by_api_key(tenant_key)
    if not tenant:
        raise HTTPException(status_code=401, detail="Invalid tenant key")

    if not DATABASE_URL:
        return JSONResponse({"tenant_name": tenant["name"], "config": {}})

    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute(
            "SELECT * FROM tenant_config WHERE tenant_id = %s",
            (tenant["id"],))
        cfg_row = cur.fetchone()
        cur.close(); conn.close()
    except Exception as e:
        print(f"[CONFIG] DB error: {e}")
        return JSONResponse({"tenant_name": tenant["name"], "config": {}})

    cfg = {}
    if cfg_row:
        if cfg_row.get("webhook_url"):
            cfg["webhook_url"]     = cfg_row["webhook_url"]
        if cfg_row.get("alert_email"):
            cfg["admin_email"]     = cfg_row["alert_email"]
        if cfg_row.get("verify_interval") and cfg_row["verify_interval"] > 0:
            cfg["verify_interval"] = cfg_row["verify_interval"]
        if cfg_row.get("max_vault_mb") and cfg_row["max_vault_mb"] > 0:
            cfg["vault_max_size_mb"] = cfg_row["max_vault_mb"]
        if cfg_row.get("allowed_exts"):
            exts = [e.strip() for e in cfg_row["allowed_exts"].split(",")
                    if e.strip().startswith(".")]
            if exts:
                cfg["vault_allowed_exts"] = exts

    return JSONResponse(
        content={
            "tenant_name": tenant["name"],
            "tenant_slug": tenant["slug"],
            "plan":        tenant["plan"],
            "config":      cfg,
        },
        headers={"Cache-Control": "no-store"},
    )


@router.post("/api/trigger_lockdown/{machine_id}")
async def trigger_lockdown(machine_id: str, _: bool = Depends(verify_session)):
    commands[machine_id] = "LOCKDOWN"
    return {"status": "Lockdown queued"}


# ── C2 Dashboard ───────────────────────────────────────────────────────────────

@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(_: bool = Depends(verify_session)):
    now  = time.time()
    rows = ""

    for mid, info in agents.items():
        online = (now - info["last_seen"]) < 30
        sb = (
            '<span style="background:#238636;color:#fff;padding:2px 8px;'
            'border-radius:4px;font-size:12px">ONLINE</span>'
            if online else
            '<span style="background:#30363d;color:#8b949e;padding:2px 8px;'
            'border-radius:4px;font-size:12px">OFFLINE</span>'
        )
        ab = (
            '<span style="background:#1f6feb;color:#fff;padding:2px 8px;'
            'border-radius:4px;font-size:12px">ARMED</span>'
            if info["is_armed"] else
            '<span style="background:#9e6a03;color:#fff;padding:2px 8px;'
            'border-radius:4px;font-size:12px">UNARMED</span>'
        )
        rows += (
            f"<tr>"
            f"<td style='font-family:monospace;color:#8b949e'>{mid[:14]}...</td>"
            f"<td><strong>{info['hostname']}</strong></td>"
            f"<td>{info['username']}</td>"
            f"<td>{info['ip']}</td>"
            f"<td>{sb}</td>"
            f"<td>{ab}</td>"
            f"<td><button onclick=\"lock('{mid}')\" style='background:#da3633;color:#fff;"
            f"border:none;border-radius:4px;padding:4px 12px;cursor:pointer;"
            f"font-size:13px'>ISOLATE</button></td>"
            f"</tr>"
        )

    if not rows:
        rows = ("<tr><td colspan='7' style='text-align:center;color:#484f58;"
                "padding:32px'>No endpoints connected</td></tr>")

    return f"""<!DOCTYPE html><html><head><title>FMSecure C2</title>
    <style>
      *{{box-sizing:border-box;margin:0;padding:0}}
      body{{background:#0a0a0a;color:#e6edf3;font-family:system-ui,sans-serif}}
      nav{{background:#161b22;border-bottom:1px solid #30363d;padding:16px 24px;
           display:flex;justify-content:space-between;align-items:center}}
      .brand{{color:#2f81f7;font-weight:700;font-size:18px}}
      a{{color:#8b949e;text-decoration:none;font-size:13px;margin-left:16px}}
      a:hover{{color:#e6edf3}}
      .container{{padding:24px}}
      table{{width:100%;border-collapse:collapse;background:#161b22;
             border-radius:8px;overflow:hidden}}
      th{{background:#0d1117;color:#8b949e;padding:12px 16px;text-align:left;
          font-size:12px;font-weight:600;letter-spacing:.5px}}
      td{{padding:12px 16px;border-top:1px solid #21262d;font-size:14px}}
    </style></head><body>
    <nav>
      <span class="brand">FMSecure Global C2</span>
      <div>
        <a href="/licenses">Licenses</a>
        <a href="/super/dashboard" style="color:#f0883e">Tenants</a>
        <a href="/tenant/login" style="color:#8b949e">Client Portal</a>
        <a href="/">Product Page</a>
        <a href="/pricing">Pricing</a>
        <a href="/logout">Logout</a>
      </div>
    </nav>

    <div class="container">
      <div style="background:#161b22;border:1px solid #30363d;border-radius:8px;
                  padding:24px;margin-bottom:24px">
        <h3 style="color:#e6edf3;margin:0 0 6px">🚀 Publish New Version</h3>
        <p style="color:#8b949e;font-size:13px;margin:0 0 18px">
          When you ship a new EXE, fill this in. Every running copy of FMSecure
          will show an update banner within seconds.
        </p>
        <form method="POST" action="/api/version/publish-form"
              style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
          <div>
            <label style="color:#8b949e;font-size:12px;display:block;margin-bottom:4px">
              VERSION NUMBER *
            </label>
            <input name="version" placeholder="e.g. 2.6.0" required
                   style="width:100%;background:#0d1117;color:#e6edf3;
                          border:1px solid #30363d;border-radius:6px;
                          padding:8px 12px;font-size:14px">
          </div>
          <div>
            <label style="color:#8b949e;font-size:12px;display:block;margin-bottom:4px">
              RELEASE NOTES (shown in banner)
            </label>
            <input name="release_notes" placeholder="Bug fixes, new cloud features…"
                   style="width:100%;background:#0d1117;color:#e6edf3;
                          border:1px solid #30363d;border-radius:6px;
                          padding:8px 12px;font-size:14px">
          </div>
          <div>
            <label style="color:#8b949e;font-size:12px;display:block;margin-bottom:4px">
              DOWNLOAD URL (leave blank for default)
            </label>
            <input name="download_url" placeholder="https://…"
                   style="width:100%;background:#0d1117;color:#e6edf3;
                          border:1px solid #30363d;border-radius:6px;
                          padding:8px 12px;font-size:14px">
          </div>
          <div>
            <label style="color:#8b949e;font-size:12px;display:block;margin-bottom:4px">
              CHANGELOG URL (leave blank for default)
            </label>
            <input name="changelog_url" placeholder="https://…"
                   style="width:100%;background:#0d1117;color:#e6edf3;
                          border:1px solid #30363d;border-radius:6px;
                          padding:8px 12px;font-size:14px">
          </div>
          <div style="grid-column:1/-1">
            <button type="submit"
                    style="background:#238636;color:#fff;border:none;
                           border-radius:6px;padding:10px 24px;
                           font-size:14px;font-weight:600;cursor:pointer">
              Publish Version →
            </button>
          </div>
        </form>
      </div>

      <table>
        <thead>
          <tr>
            <th>MACHINE ID</th><th>HOSTNAME</th><th>USER</th><th>IP</th>
            <th>STATUS</th><th>ENGINE</th><th>ACTION</th>
          </tr>
        </thead>
        <tbody>{rows}</tbody>
      </table>
    </div>

    <script>
      setInterval(() => {{
        const isTyping = document.querySelector('input:focus, textarea:focus');
        if (!isTyping) location.reload();
      }}, 5000);

      async function lock(mid) {{
        if (confirm("Isolate?")) {{
          await fetch("/api/trigger_lockdown/" + mid, {{method: "POST"}});
          alert("Queued!");
        }}
      }}
    </script>
    </body></html>"""
