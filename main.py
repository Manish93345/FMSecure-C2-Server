from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import time

app = FastAPI(title="FMSecure Cloud C2")

# In-memory database for the MVP
agents = {}
commands = {}

class Heartbeat(BaseModel):
    machine_id: str
    hostname: str
    username: str
    tier: str
    is_armed: bool

@app.post("/api/heartbeat")
async def receive_heartbeat(data: Heartbeat, request: Request):
    # Update the agent's last seen time and status
    agents[data.machine_id] = {
        "hostname": data.hostname,
        "username": data.username,
        "tier": data.tier,
        "is_armed": data.is_armed,
        "last_seen": time.time(),
        "ip": request.client.host
    }
    
    # Check if the Cloud Admin clicked "Isolate Host"
    cmd = commands.get(data.machine_id, "NONE")
    if cmd != "NONE":
        commands[data.machine_id] = "NONE" # Clear command after sending it once
        
    return {"status": "ok", "command": cmd}

@app.post("/api/trigger_lockdown/{machine_id}")
async def trigger_lockdown(machine_id: str):
    """Endpoint for the web dashboard to trigger the killswitch"""
    commands[machine_id] = "LOCKDOWN"
    return {"status": "Lockdown command queued"}

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    """The live Web Dashboard for the IT Admin"""
    current_time = time.time()
    
    # Generate the table rows dynamically
    rows = ""
    for mid, info in agents.items():
        # If we haven't heard from them in 30 seconds, they are offline
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
            <p class="text-muted">Enterprise Endpoint Telemetry Dashboard</p>
            
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
            // Auto-refresh the dashboard every 5 seconds to see live telemetry
            setTimeout(() => window.location.reload(), 5000);

            async function triggerLockdown(machineId) {{
                if (confirm("🚨 EMERGENCY ACTION 🚨\\n\\nAre you sure you want to isolate this host? This will trigger the Ransomware Killswitch on the target machine.")) {{
                    await fetch(`/api/trigger_lockdown/${{machineId}}`, {{ method: 'POST' }});
                    alert("Lockdown command queued! The agent will execute it on the next heartbeat.");
                }}
            }}
        </script>
    </body>
    </html>
    """
    return html_content