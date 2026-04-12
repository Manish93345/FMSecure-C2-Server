"""
routes/auth_routes.py — Super-admin login / logout pages.

Routes:
  GET  /login
  POST /login
  GET  /logout
"""
import secrets

from fastapi import APIRouter, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from core.config import ADMIN_USER, ADMIN_PASS
from core.auth import SESSION_TOKEN

router = APIRouter()


@router.get("/login", response_class=HTMLResponse)
async def login_page(error: str = ""):
    err = (
        f'<p style="color:#f85149;background:#2d1c1c;padding:10px;'
        f'border-radius:6px;margin-bottom:16px;font-size:14px">{error}</p>'
        if error else ""
    )
    return f"""<!DOCTYPE html><html><head><title>FMSecure | Login</title>
    <style>
      *{{box-sizing:border-box;margin:0;padding:0}}
      body{{background:#0a0a0a;color:#e6edf3;display:flex;align-items:center;
           justify-content:center;min-height:100vh;font-family:system-ui,sans-serif}}
      .card{{background:#161b22;border:1px solid #30363d;border-radius:12px;
             padding:40px;width:360px}}
      h3{{color:#2f81f7;text-align:center;margin-bottom:4px}}
      p.sub{{color:#8b949e;text-align:center;font-size:13px;margin-bottom:24px}}
      label{{display:block;color:#8b949e;font-size:11px;font-weight:600;
             letter-spacing:.5px;margin-bottom:6px}}
      input{{width:100%;background:#0d1117;border:1px solid #30363d;border-radius:6px;
             color:#e6edf3;padding:10px 14px;font-size:14px;outline:none;margin-bottom:16px}}
      input:focus{{border-color:#2f81f7}}
      button{{width:100%;background:#238636;border:none;border-radius:6px;color:#fff;
               padding:12px;font-size:14px;font-weight:600;cursor:pointer}}
    </style></head><body>
    <div class="card">
      <h3>FMSecure C2</h3>
      <p class="sub">Enterprise Authentication</p>
      {err}
      <form method="post" action="/login">
        <label>USERNAME</label>
        <input name="username" type="text" required autofocus>
        <label>PASSWORD</label>
        <input name="password" type="password" required>
        <button type="submit">Authenticate</button>
      </form>
    </div></body></html>"""


@router.post("/login")
async def process_login(
    username: str = Form(...),
    password: str = Form(...),
):
    if (
        secrets.compare_digest(username, ADMIN_USER)
        and secrets.compare_digest(password, ADMIN_PASS)
    ):
        resp = RedirectResponse(url="/dashboard", status_code=302)
        resp.set_cookie("fmsecure_session", SESSION_TOKEN,
                        httponly=True, max_age=86400)
        return resp
    return RedirectResponse(url="/login?error=Invalid+credentials",
                            status_code=302)


@router.get("/logout")
async def logout():
    resp = RedirectResponse(url="/login", status_code=302)
    resp.delete_cookie("fmsecure_session")
    return resp
