"""
routes/licenses.py — License validation, payment processing, and admin license tools.

Routes:
  POST /payment/create-order
  POST /payment/verify
  GET  /payment/success
  POST /api/license/validate
  POST /api/license/activate
  POST /api/license/request_transfer
  POST /api/license/confirm_transfer
  POST /api/license/recover_key
  GET  /api/license/list          (admin)
  POST /api/license/create_manual (admin)
  POST /api/license/release_device(admin)
  GET  /licenses                  (admin page)
  GET  /api/db-fix                (temp migration helper)
"""
import hashlib
import hmac as _hmac
import random
import secrets
import threading
import uuid
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from core.auth import verify_session
from core.config import (
    APP_BASE_URL, DATABASE_URL, DRIVE_FILE_ID,
    PLANS, RZP_KEY_SECRET, limiter, rzp_client,
)
from core.database import get_db
from core.email_utils import _send_license_email
from core.helpers import (
    _check_admin, _gen_key, _is_expired, _save_license,
)

router = APIRouter()

# ── In-memory OTP store for license transfer flow ──────────────────────────────
# key: license_key → {"otp": str, "email": str, "expires": float}
_pending_transfers: dict = {}
_TRANSFER_OTP_TTL = 300   # 5 minutes


# ── Pydantic models ────────────────────────────────────────────────────────────

class CreateOrderRequest(BaseModel):
    tier:  str
    email: str

class VerifyPaymentRequest(BaseModel):
    razorpay_order_id:  str
    razorpay_payment_id: str
    razorpay_signature:  str
    email: str
    tier:  str

class LicenseValidateRequest(BaseModel):
    license_key: str
    machine_id:  str

class TransferRequestBody(BaseModel):
    license_key: str
    email:       str   # must match purchase email on record

class TransferConfirmBody(BaseModel):
    license_key:    str
    otp:            str
    new_machine_id: str

class KeyRecoveryBody(BaseModel):
    email: str


# ── Payment ────────────────────────────────────────────────────────────────────

@router.post("/payment/create-order")
@limiter.limit("20/minute")
async def create_order(request: Request, body: CreateOrderRequest):
    tier  = body.tier.strip().lower()
    email = body.email.strip().lower()

    if tier not in PLANS:
        return JSONResponse({"error": "Invalid plan"}, status_code=400)
    if not email or "@" not in email:
        return JSONResponse({"error": "Invalid email"}, status_code=400)

    plan = PLANS[tier]

    try:
        order = rzp_client.order.create({
            "amount":   plan["amount"],
            "currency": plan["currency"],
            "receipt":  f"fm_{uuid.uuid4().hex[:8]}",
            "notes":    {"email": email, "tier": tier},
        })
    except Exception as e:
        print(f"[RZP] Order error: {e}")
        return JSONResponse({"error": "Payment gateway error"}, status_code=500)

    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute(
            "INSERT INTO pending_orders (order_id,email,tier,amount) "
            "VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            (order["id"], email, tier, plan["amount"]),
        )
        conn.commit(); cur.close(); conn.close()
    except Exception as e:
        print(f"[DB] Pending save error: {e}")

    return {
        "order_id":    order["id"],
        "amount":      plan["amount"],
        "currency":    plan["currency"],
        "description": plan["description"],
    }


@router.post("/payment/verify")
@limiter.limit("20/minute")
async def verify_payment(request: Request, body: VerifyPaymentRequest):
    # 1. Verify Razorpay signature
    expected = _hmac.new(
        RZP_KEY_SECRET.encode(),
        f"{body.razorpay_order_id}|{body.razorpay_payment_id}".encode(),
        hashlib.sha256,
    ).hexdigest()

    if not secrets.compare_digest(expected, body.razorpay_signature):
        print(f"[RZP] Signature mismatch for {body.razorpay_order_id}")
        return JSONResponse({"success": False, "error": "Signature failed"},
                            status_code=400)

    # 2. Generate license and save to DB
    tier        = body.tier.strip().lower()
    email       = body.email.strip().lower()
    payment_id  = body.razorpay_payment_id
    order_id    = body.razorpay_order_id
    expires_iso = (
        datetime.now(timezone.utc)
        + timedelta(days=PLANS.get(tier, {}).get("days", 31))
    ).isoformat()
    license_key = _gen_key(tier, email, payment_id)

    try:
        _save_license(license_key, email, tier, payment_id, order_id, expires_iso)
    except Exception as e:
        print(f"[DB] Save error: {e}")
        return JSONResponse({"success": False, "error": "Database error"},
                            status_code=500)

    # 3. Clean up pending order
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("DELETE FROM pending_orders WHERE order_id=%s", (order_id,))
        conn.commit(); cur.close(); conn.close()
    except Exception:
        pass

    # 4. Send email in background — does NOT block the payment response
    threading.Thread(
        target=_send_license_email,
        args=(email, license_key, tier, expires_iso),
        daemon=True,
    ).start()

    print(f"[PAYMENT] Generated key {license_key} for {email}")
    return {"success": True, "license_key": license_key,
            "tier": tier, "expires_at": expires_iso}


@router.get("/payment/success", response_class=HTMLResponse)
async def payment_success(key: str = "", email: str = "", tier: str = ""):
    tier_label = PLANS.get(tier, {}).get("label", "PRO")
    return f"""<!DOCTYPE html><html><head><title>Payment Successful | FMSecure</title>
    <style>
      *{{box-sizing:border-box;margin:0;padding:0}}
      body{{background:#0d1117;color:#e6edf3;font-family:system-ui,sans-serif;
           display:flex;align-items:center;justify-content:center;
           min-height:100vh;padding:24px}}
      .card{{background:#161b22;border:1px solid #238636;border-radius:16px;
             padding:48px 40px;max-width:480px;width:100%;text-align:center}}
      h2{{color:#3fb950;font-size:24px;margin-bottom:8px}}
      p{{color:#8b949e;font-size:15px;line-height:1.6}}
      .key-box{{background:#0d1117;border:1px solid #30363d;border-radius:8px;
               padding:20px;margin:24px 0}}
      .key-label{{color:#484f58;font-size:11px;letter-spacing:1px;margin-bottom:10px}}
      .key{{color:#2f81f7;font-size:20px;font-family:monospace;font-weight:700;
            letter-spacing:2px;word-break:break-all}}
      .copy-btn{{margin-top:14px;background:#30363d;border:none;color:#e6edf3;
                 padding:8px 20px;border-radius:6px;cursor:pointer;font-size:13px}}
      .steps{{text-align:left;background:#0d1117;border-radius:8px;
              padding:20px 24px;font-size:14px;color:#8b949e;line-height:2.2}}
      strong{{color:#e6edf3}}
    </style></head><body>
    <div class="card">
      <div style="font-size:56px;margin-bottom:16px">&#9989;</div>
      <h2>Payment successful!</h2>
      <p>Your <strong>{tier_label}</strong> is now active.<br>
         We've also emailed this key to <strong>{email}</strong></p>
      <div class="key-box">
        <div class="key-label">YOUR LICENSE KEY</div>
        <div class="key" id="lk">{key}</div>
        <button class="copy-btn"
                onclick="navigator.clipboard.writeText('{key}');
                         this.textContent='&#10003; Copied!'">
          Copy key
        </button>
      </div>
      <div class="steps">
        <strong>How to activate in FMSecure:</strong><br>
        1. Open <strong>FMSecure</strong> on your PC<br>
        2. Click your <strong>username</strong> (top-right)<br>
        3. Click <strong>Activate License</strong><br>
        4. Paste this key — no email needed<br>
        5. Click <strong>Activate</strong> — PRO unlocked!
      </div>
    </div></body></html>"""


# ── License validation ─────────────────────────────────────────────────────────

@router.post("/api/license/validate")
async def validate_license(req: LicenseValidateRequest):
    key = req.license_key.strip()
    mid = req.machine_id.strip()

    if not DATABASE_URL:
        return {"valid": False, "tier": "free", "reason": "db_not_configured"}
    if not key or not mid:
        return {"valid": False, "tier": "free", "reason": "missing_fields"}

    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT * FROM licenses WHERE license_key=%s", (key,))
        r = cur.fetchone()
    except Exception:
        return {"valid": False, "tier": "free", "reason": "db_error"}

    if not r:
        cur.close(); conn.close()
        return {"valid": False, "tier": "free",
                "expires_at": None, "reason": "key_not_found"}

    if not r["active"] or _is_expired(r["expires_at"]):
        cur.close(); conn.close()
        return {"valid": False, "tier": "free",
                "expires_at": r["expires_at"].isoformat() if r["expires_at"] else None,
                "reason": "subscription_expired"}

    bound = r["machine_id"]

    if bound is None:
        # First activation — bind to this device
        cur.execute("UPDATE licenses SET machine_id=%s WHERE license_key=%s", (mid, key))
        conn.commit(); cur.close(); conn.close()
        print(f"[LICENSE] Bound {key} to device {mid[:20]}…")
        return {"valid": True, "tier": r["tier"],
                "expires_at": r["expires_at"].isoformat(), "reason": "activated"}

    if bound == mid:
        cur.close(); conn.close()
        return {"valid": True, "tier": r["tier"],
                "expires_at": r["expires_at"].isoformat(), "reason": "ok"}

    cur.close(); conn.close()
    return {"valid": False, "tier": "free",
            "expires_at": None, "reason": "device_mismatch"}


@router.post("/api/license/activate")
async def activate_license(req: LicenseValidateRequest):
    """Alias for /api/license/validate — some client versions call this endpoint."""
    return await validate_license(req)


# ── License transfer ───────────────────────────────────────────────────────────

@router.post("/api/license/request_transfer")
async def request_transfer(req: TransferRequestBody):
    """
    Step 1 — user proves ownership via purchase email.
    A 6-digit OTP is sent via SendGrid if the email matches.
    """
    key   = req.license_key.strip()
    email = req.email.strip().lower()

    if not DATABASE_URL:
        return {"ok": False, "reason": "db_not_configured"}
    if not key or not email:
        return {"ok": False, "reason": "missing_fields"}

    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute(
            "SELECT email, active FROM licenses WHERE license_key = %s", (key,))
        row = cur.fetchone()
        cur.close(); conn.close()
    except Exception as e:
        print(f"[TRANSFER] DB error: {e}")
        return {"ok": False, "reason": "db_error"}

    if not row:
        return {"ok": False, "reason": "key_not_found"}

    stored_email = (row["email"] or "").strip().lower()
    if not secrets.compare_digest(stored_email, email):
        return {"ok": False,
                "reason": "Email does not match the purchase record for this key."}

    if not row["active"]:
        return {"ok": False, "reason": "subscription_expired"}

    otp = str(random.randint(100000, 999999))
    _pending_transfers[key] = {
        "otp":     otp,
        "email":   email,
        "expires": __import__("time").time() + _TRANSFER_OTP_TTL,
    }

    def _send_transfer_otp():
        from core.config import SENDGRID_API_KEY, SENDER_EMAIL
        if not SENDGRID_API_KEY:
            print(f"[TRANSFER] No SENDGRID_API_KEY. OTP for {email}: {otp}")
            return
        html = f"""
        <div style="font-family:Arial,sans-serif;max-width:520px;margin:0 auto;
                    background:#0d1117;color:#e6edf3;padding:32px;border-radius:10px;">
          <h2 style="color:#2f81f7;margin-top:0">&#128273; FMSecure License Transfer</h2>
          <p style="color:#a0a8b8;font-size:15px">
            A request was made to transfer your license key to a new device.
            Use the verification code below to confirm. It expires in 5 minutes.
          </p>
          <div style="background:#161b22;border:1px solid #30363d;border-radius:8px;
                      padding:24px;text-align:center;margin:24px 0;">
            <p style="margin:0 0 10px;color:#8b949e;font-size:11px;
                      letter-spacing:1px;font-weight:600">VERIFICATION CODE</p>
            <div style="font-size:36px;font-weight:700;color:#2f81f7;
                        letter-spacing:8px;font-family:Courier,monospace;">{otp}</div>
          </div>
          <p style="color:#484f58;font-size:12px;border-top:1px solid #21262d;
                    padding-top:16px;margin:0">
            If you did not request this, your license is safe — ignore this email.<br>
            FMSecure v2.0 &bull; Enterprise EDR for Windows
          </p>
        </div>"""
        try:
            import sendgrid as sg_mod
            from sendgrid.helpers.mail import Mail
            sg  = sg_mod.SendGridAPIClient(api_key=SENDGRID_API_KEY)
            msg = Mail(from_email=SENDER_EMAIL, to_emails=email,
                       subject="FMSecure — License Transfer Verification Code",
                       html_content=html)
            resp = sg.send(msg)
            print(f"[TRANSFER] OTP sent to {email} — status {resp.status_code}")
        except Exception as e:
            print(f"[TRANSFER] SendGrid failed for {email}: {e}")
            print(f"[TRANSFER] OTP was: {otp}")

    threading.Thread(target=_send_transfer_otp, daemon=True).start()
    return {"ok": True}


@router.post("/api/license/confirm_transfer")
async def confirm_transfer(req: TransferConfirmBody):
    """
    Step 2 — user submits OTP + new machine_id.
    On success the DB machine_id is updated and the key works on the new device immediately.
    """
    key = req.license_key.strip()
    otp = req.otp.strip()
    mid = req.new_machine_id.strip()

    if not DATABASE_URL:
        return {"ok": False, "reason": "db_not_configured"}
    if not key or not otp or not mid:
        return {"ok": False, "reason": "missing_fields"}

    pending = _pending_transfers.get(key)
    if not pending:
        return {"ok": False,
                "reason": "No transfer request found. Please request a new code."}

    import time as _time
    if _time.time() > pending["expires"]:
        del _pending_transfers[key]
        return {"ok": False,
                "reason": "Verification code expired. Please request a new one."}

    if not secrets.compare_digest(pending["otp"], otp):
        return {"ok": False, "reason": "Incorrect verification code."}

    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute(
            "UPDATE licenses SET machine_id = %s "
            "WHERE license_key = %s RETURNING tier",
            (mid, key),
        )
        row = cur.fetchone()
        conn.commit(); cur.close(); conn.close()
    except Exception as e:
        print(f"[TRANSFER] DB update error: {e}")
        return {"ok": False, "reason": "db_error"}

    if not row:
        return {"ok": False, "reason": "key_not_found"}

    del _pending_transfers[key]
    tier = row["tier"] or "pro_monthly"
    print(f"[TRANSFER] ✅ Key {key[:16]}… transferred to device {mid[:20]}…")
    return {"ok": True, "tier": tier}


# ── License recovery ───────────────────────────────────────────────────────────

@router.post("/api/license/recover_key")
async def recover_key(req: KeyRecoveryBody):
    """
    Re-send lost license key(s) for a given email.
    Always returns {ok: true} to prevent email enumeration.
    """
    email = req.email.strip().lower()
    if not email or not DATABASE_URL:
        return {"ok": True}

    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute(
            "SELECT license_key, tier, expires_at FROM licenses "
            "WHERE email = %s AND active = TRUE ORDER BY created_at DESC",
            (email,),
        )
        rows = cur.fetchall()
        cur.close(); conn.close()
    except Exception as e:
        print(f"[RECOVER] DB error: {e}")
        return {"ok": True}

    sent = 0
    for row in rows:
        if not _is_expired(row["expires_at"]):
            threading.Thread(
                target=_send_license_email,
                args=(email, row["license_key"], row["tier"],
                      row["expires_at"].isoformat()),
                daemon=True,
            ).start()
            sent += 1

    print(f"[RECOVER] Sent {sent} key(s) to {email}")
    return {"ok": True}


# ── Admin endpoints ────────────────────────────────────────────────────────────

@router.get("/api/license/list")
async def list_licenses(api_key: str = ""):
    _check_admin(api_key)
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT * FROM licenses ORDER BY created_at DESC")
    rows = [dict(r) for r in cur.fetchall()]
    cur.close(); conn.close()
    return {"count": len(rows), "licenses": rows}


@router.post("/api/license/create_manual")
async def create_manual(email: str, tier: str = "pro_monthly",
                        days: int = 30, api_key: str = ""):
    _check_admin(api_key)
    sub_id      = f"manual_{uuid.uuid4().hex[:8]}"
    expires_iso = (
        datetime.now(timezone.utc) + timedelta(days=days)
    ).isoformat()
    license_key = _gen_key(tier, email, sub_id)
    _save_license(license_key, email, tier, sub_id, sub_id, expires_iso)
    return {"license_key": license_key, "email": email,
            "tier": tier, "expires_at": expires_iso}


@router.post("/api/license/release_device")
async def release_device(license_key: str, api_key: str = ""):
    _check_admin(api_key)
    conn = get_db(); cur = conn.cursor()
    cur.execute(
        "UPDATE licenses SET machine_id=NULL WHERE license_key=%s "
        "RETURNING email,tier",
        (license_key,),
    )
    row = cur.fetchone()
    conn.commit(); cur.close(); conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Key not found")
    return {"message": "Device binding released.",
            "license_key": license_key, "email": row["email"]}


# ── Admin: License list page ───────────────────────────────────────────────────

@router.get("/licenses", response_class=HTMLResponse)
async def licenses_page(_: bool = Depends(verify_session)):
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT * FROM licenses ORDER BY created_at DESC LIMIT 500")
    rows = cur.fetchall(); cur.close(); conn.close()

    trs = ""
    for r in rows:
        expired = _is_expired(r["expires_at"])
        sb = (
            '<span style="background:#238636;color:#fff;padding:2px 8px;'
            'border-radius:4px;font-size:12px">Active</span>'
            if not expired and r["active"] else
            '<span style="background:#da3633;color:#fff;padding:2px 8px;'
            'border-radius:4px;font-size:12px">Expired</span>'
        )
        exp = r["expires_at"].strftime("%Y-%m-%d") if r["expires_at"] else "—"
        mid = (r["machine_id"] or "—")
        trs += (
            f"<tr>"
            f"<td style='font-family:monospace;font-size:12px'>{r['license_key']}</td>"
            f"<td>{r['email']}</td>"
            f"<td>{r['tier']}</td>"
            f"<td>{sb}</td>"
            f"<td>{exp}</td>"
            f"<td style='font-family:monospace;font-size:11px;color:#8b949e'>"
            f"{mid[:22] if mid != '—' else mid}"
            f"</td></tr>"
        )

    return f"""<!DOCTYPE html><html><head><title>FMSecure | Licenses</title>
    <style>
      *{{box-sizing:border-box;margin:0;padding:0}}
      body{{background:#0a0a0a;color:#e6edf3;font-family:system-ui,sans-serif}}
      nav{{background:#161b22;border-bottom:1px solid #30363d;padding:16px 24px;
           display:flex;justify-content:space-between;align-items:center}}
      .brand{{color:#2f81f7;font-weight:700}}
      a{{color:#8b949e;text-decoration:none;font-size:13px;margin-left:16px}}
      a:hover{{color:#e6edf3}}
      .container{{padding:24px}}
      table{{width:100%;border-collapse:collapse;background:#161b22;
             border-radius:8px;overflow:hidden}}
      th{{background:#0d1117;color:#8b949e;padding:12px 16px;text-align:left;
          font-size:12px;font-weight:600;letter-spacing:.5px}}
      td{{padding:12px 16px;border-top:1px solid #21262d;font-size:13px}}
    </style></head><body>
    <nav>
      <span class="brand">License Manager</span>
      <div>
        <a href="/dashboard">&#x2190; C2 Dashboard</a>
        <a href="/logout">Logout</a>
      </div>
    </nav>
    <div class="container">
      <table>
        <thead><tr>
          <th>LICENSE KEY</th><th>EMAIL</th><th>TIER</th>
          <th>STATUS</th><th>EXPIRES</th><th>DEVICE ID</th>
        </tr></thead>
        <tbody>{trs}</tbody>
      </table>
    </div></body></html>"""


# ── Temporary DB patch endpoint ───────────────────────────────────────────────

@router.get("/api/db-fix")
async def fix_db():
    """One-shot schema patch — safe to call multiple times (IF NOT EXISTS)."""
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("ALTER TABLE licenses ADD COLUMN IF NOT EXISTS payment_id TEXT;")
        cur.execute("ALTER TABLE licenses ADD COLUMN IF NOT EXISTS order_id TEXT;")
        conn.commit(); cur.close(); conn.close()
        return {"success": True,
                "message": "Database successfully patched! Missing columns added."}
    except Exception as e:
        return {"success": False, "error": str(e)}
