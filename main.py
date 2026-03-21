import os
import secrets
import time
from fastapi import FastAPI, Request, Depends, HTTPException, status, Form, Cookie
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from fastapi.staticfiles import StaticFiles


# --- RATE LIMITER SETUP ---
limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="FMSecure Cloud C2")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.mount("/static", StaticFiles(directory="static"), name="static")

# --- 🔒 SECURITY VAULT ---
ADMIN_USER = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASS = os.getenv("ADMIN_PASSWORD", "password")
API_KEY = os.getenv("API_KEY", "default-dev-key")

# Generates a random session token every time Railway boots up. 
# Highly secure: forces re-login on server restarts.
SESSION_TOKEN = secrets.token_hex(16) 

# --- DATABASE ---
agents = {}
commands = {}

class Heartbeat(BaseModel):
    machine_id: str
    hostname: str
    username: str
    tier: str
    is_armed: bool

# --- 🛡️ AUTHENTICATION LOGIC ---

async def verify_session(fmsecure_session: str = Cookie(None)):
    """Checks if the user's browser has the correct secret cookie."""
    if not fmsecure_session or not secrets.compare_digest(fmsecure_session, SESSION_TOKEN):
        raise HTTPException(status_code=status.HTTP_302_FOUND, headers={"Location": "/login"})
    return True

@app.get("/login", response_class=HTMLResponse)
async def login_page(error: str = ""):
    """The sleek, branded Login Portal"""
    error_msg = f'<div class="alert alert-danger p-2 text-center" style="font-size: 14px;">{error}</div>' if error else ""
    
    return f"""
    <!DOCTYPE html>
    <html data-bs-theme="dark">
    <head>
        <title>FMSecure | Login</title>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
        <style>
            body {{ background-color: #0a0a0a; color: #e6edf3; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }}
            .login-card {{ background-color: #161b22; border: 1px solid #30363d; border-radius: 12px; padding: 40px; width: 100%; max-width: 400px; box-shadow: 0 8px 24px rgba(0,0,0,0.5); }}
            .form-control {{ background-color: #0d1117; border: 1px solid #30363d; color: #c9d1d9; }}
            .form-control:focus {{ background-color: #0d1117; border-color: #58a6ff; color: #c9d1d9; box-shadow: 0 0 0 3px rgba(88,166,255,0.3); }}
            .btn-primary {{ background-color: #238636; border-color: rgba(240,246,252,0.1); }}
            .btn-primary:hover {{ background-color: #2ea043; }}
        </style>
    </head>
    <body>
        <div class="login-card">
            <h3 class="text-center fw-bold mb-1" style="color: #58a6ff;"><span><img src="/static/app_icon.png" alt="" height="50"></span> FMSecure C2</h3>
            <p class="text-center text-muted mb-4" style="font-size: 14px;">Enterprise Authentication</p>
            {error_msg}
            <form action="/login" method="post">
                <div class="mb-3">
                    <label class="form-label text-muted small fw-bold">ADMIN USERNAME</label>
                    <input type="text" name="username" class="form-control" required autofocus>
                </div>
                <div class="mb-4">
                    <label class="form-label text-muted small fw-bold">PASSWORD</label>
                    <input type="password" name="password" class="form-control" required>
                </div>
                <button type="submit" class="btn btn-primary w-100 fw-bold">Authenticate</button>
            </form>
        </div>
    </body>
    </html>
    """

@app.post("/login")
async def process_login(username: str = Form(...), password: str = Form(...)):
    """Verifies credentials and issues the session cookie"""
    if secrets.compare_digest(username, ADMIN_USER) and secrets.compare_digest(password, ADMIN_PASS):
        # Success! Redirect to dashboard and plant the cookie
        response = RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
        response.set_cookie(key="fmsecure_session", value=SESSION_TOKEN, httponly=True, max_age=86400)
        return response
    
    # Failed! Send them back with an error
    return RedirectResponse(url="/login?error=Invalid Credentials", status_code=status.HTTP_302_FOUND)

@app.get("/logout")
async def logout():
    """Destroys the session cookie"""
    response = RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
    response.delete_cookie("fmsecure_session")
    return response

# --- 🚀 API ENDPOINTS ---

@app.post("/api/heartbeat")
@limiter.limit("200/minute")  # 🚦 RATE LIMITER: Prevents DDoS / Spam
async def receive_heartbeat(request: Request, data: Heartbeat, x_api_key: str = None):
    # Manually check header to avoid FastAPI auto-blocking missing headers before Rate Limit
    api_key_header = request.headers.get("x-api-key")
    if api_key_header != API_KEY:
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
async def trigger_lockdown(machine_id: str, is_authenticated: bool = Depends(verify_session)):
    """Triggers the killswitch. Protected by Session Cookie."""
    commands[machine_id] = "LOCKDOWN"
    return {"status": "Lockdown command queued"}

# --- 🖥️ WEB DASHBOARD ---

@app.get("/", response_class=HTMLResponse)
async def dashboard(is_authenticated: bool = Depends(verify_session)):
    """The live Web Dashboard. Protected by Session Cookie."""
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
                    ⚠️ ISOLATE
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
            .navbar {{ background-color: #161b22; border-bottom: 1px solid #30363d; }}
            .card {{ background-color: #161b22; border: 1px solid #30363d; }}
            .table {{ color: #e6edf3; }}
            .table th {{ border-bottom: 2px solid #30363d; color: #8b949e; }}
            .table td {{ border-bottom: 1px solid #21262d; vertical-align: middle; }}
        </style>
    </head>
    <body>
        <nav class="navbar navbar-expand-lg px-4 py-3 mb-4">
            <div class="container-fluid">
                <span class="navbar-brand text-primary fw-bold"><span><img src="/static/app_icon.png" alt="" height="50"></span> FMSecure Global C2</span>
                <div class="d-flex">
                    <span class="navbar-text me-4 text-muted">Enterprise Endpoint Telemetry Dashboard</span>
                    <a href="/logout" class="btn btn-outline-danger btn-sm fw-bold">Logout</a>
                </div>
            </div>
        </nav>

        <div class="container-fluid px-4">
            <div class="card shadow-lg">
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