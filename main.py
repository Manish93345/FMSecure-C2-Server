import os
import secrets
import time
from fastapi import FastAPI, Request, Depends, HTTPException, status, Header
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel

app = FastAPI(title="FMSecure Cloud C2")

# --- 🔒 SECURITY VAULT (Reads from Railway Variables) ---
ADMIN_USER = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASS = os.getenv("ADMIN_PASSWORD", "password")
API_KEY = os.getenv("API_KEY", "default-dev-key")

security = HTTPBasic()

def verify_admin(credentials: HTTPBasicCredentials = Depends(security)):
    """Verifies the browser login prompt"""
    correct_username = secrets.compare_digest(credentials.username, ADMIN_USER)
    correct_password = secrets.compare_digest(credentials.password, ADMIN_PASS)
    if not (correct_username and correct_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username

# --- DATABASE ---
agents = {}
commands = {}

class Heartbeat(BaseModel):
    machine_id: str
    hostname: str
    username: str
    tier: str
    is_armed: bool

# --- 🚀 API ENDPOINTS ---

@app.post("/api/heartbeat")
async def receive_heartbeat(data: Heartbeat, request: Request, x_api_key: str = Header(None)):
    """Receives agent telemetry. Protected by API Key."""
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized: Invalid API Key")
        
    agents[data.machine_id] = {
        "hostname": data.hostname,
        "username": data.username,
        "tier": data.tier,
        "is_armed": data.is_armed,
        "last_seen": time.time(),
        "ip": request.client.host
    }
    
    cmd = commands.get(data.machine_id, "NONE")
    if cmd != "NONE":
        commands[data.machine_id] = "NONE" 
        
    return {"status": "ok", "command": cmd}

@app.post("/api/trigger_lockdown/{machine_id}")
async def trigger_lockdown(machine_id: str, username: str = Depends(verify_admin)):
    """Triggers the killswitch. Protected by Admin Login."""
    commands[machine_id] = "LOCKDOWN"
    return {"status": "Lockdown command queued"}

# --- 🖥️ WEB DASHBOARD ---

@app.get("/", response_class=HTMLResponse)
async def dashboard(username: str = Depends(verify_admin)):
    """The live Web Dashboard. Protected by Admin Login."""
    current_time = time.time()
    
    rows = ""
    for mid, info in agents.items():
        is_online = (current_time - info['last_seen']) < 30
        status_badge = '<span class="badge bg-success">ONLINE</span>' if is_online else '<span class="badge bg-secondary">OFFLINE</span>'
        armed_badge = '<span class="badge bg-primary">ARMED</span>' if info['is_armed'] else '<span class="badge bg-warning text-dark">UNARMED</span>'
        
        rows += f"""
        <tr>
            <td class="font-monospace text-secondary">{mid[:12]}...</td>
            <td><strong>{info['hostname']}</strong></td>
            <td>{info['username']}</td>
            <td>{info['ip']}</td>
            <td>{status_badge}</td>
            <td>{armed_badge}</td>
            <td>
                <button onclick="triggerLockdown('{mid}')" class="btn btn-sm btn-danger fw-bold">
                    ⚠️ ISOLATE HOST
                </button>
            </td>
        </tr>
        """
        
    if not rows:
        rows = "<tr><td colspan='7' class='text-center text-muted py-4'>No endpoints connected. Waiting for telemetry...</td></tr>"

    html_content = f"""
    <!DOCTYPE html>
    <html data-bs-theme="dark">
    <head>
        <title>FMSecure C2</title>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
        <style>
            body {{ background-color: #0a0a0a; color: #e6edf3; }}
            .card {{ background-color: #161b22; border: 1px solid #30363d; }}
            .table {{ color: #e6edf3; }}
            .table th {{ border-bottom: 2px solid #30363d; color: #8b949e; }}
            .table td {{ border-bottom: 1px solid #21262d; vertical-align: middle; }}
        </style>
    </head>
    <body>
        <div class="container-fluid py-4 px-4">
            <h2 class="text-primary mb-0 fw-bold">☁️ FMSecure Global C2</h2>
            <p class="text-muted">Enterprise Endpoint Telemetry Dashboard | Logged in as: {username}</p>
            
            <div class="card mt-4 shadow-lg">
                <div class="card-body p-0">
                    <table class="table table-hover mb-0">
                        <thead>
                            <tr>
                                <th>MACHINE ID</th>
                                <th>HOSTNAME</th>
                                <th>USER</th>
                                <th>IP ADDRESS</th>
                                <th>NETWORK</th>
                                <th>ENGINE</th>
                                <th>ACTION</th>
                            </tr>
                        </thead>
                        <tbody>
                            {rows}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>

        <script>
            setTimeout(() => window.location.reload(), 5000);

            async function triggerLockdown(machineId) {{
                if (confirm("🚨 EMERGENCY ACTION 🚨\\n\\nAre you sure you want to isolate this host?")) {{
                    await fetch(`/api/trigger_lockdown/${{machineId}}`, {{ method: 'POST' }});
                    alert("Lockdown command queued!");
                }}
            }}
        </script>
    </body>
    </html>
    """
    return html_content