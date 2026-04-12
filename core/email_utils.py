"""
core/email_utils.py — SendGrid email helpers.

HTTP-based (not SMTP) so it works on Railway's free tier where port 25/587
are blocked.  Falls back to a print statement if no API key is configured.
"""
from core.config import SENDGRID_API_KEY, SENDER_EMAIL, PLANS


def _send_license_email(email: str, license_key: str,
                        tier: str, expires_iso: str) -> None:
    """
    Send the license key to the customer via SendGrid.
    Designed to run in a background thread so it never blocks payment responses.
    """
    tier_label  = PLANS.get(tier, {}).get("label", "PRO")
    expires_str = expires_iso[:10]

    if not SENDGRID_API_KEY:
        print(f"[EMAIL] No SENDGRID_API_KEY set. Key for {email}: {license_key}")
        return

    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:520px;margin:0 auto;
                background:#0d1117;color:#e6edf3;padding:32px;border-radius:10px;">
      <h2 style="color:#2f81f7;margin-top:0">&#128737; FMSecure PRO Activated</h2>
      <p style="color:#a0a8b8;font-size:15px">
        Your <strong style="color:#e6edf3">{tier_label}</strong>
        is active until <strong style="color:#e6edf3">{expires_str}</strong>.
      </p>
      <div style="background:#161b22;border:1px solid #30363d;border-radius:8px;
                  padding:24px;text-align:center;margin:24px 0;">
        <p style="margin:0 0 10px;color:#8b949e;font-size:11px;
                  letter-spacing:1px;font-weight:600">YOUR LICENSE KEY</p>
        <div style="font-size:22px;font-weight:700;color:#2f81f7;letter-spacing:3px;
                    font-family:Courier,monospace;word-break:break-all">
          {license_key}
        </div>
      </div>
      <div style="background:#1c2333;border-left:4px solid #2f81f7;
                  border-radius:4px;padding:16px;margin-bottom:20px">
        <p style="margin:0;color:#a0a8b8;font-size:14px;line-height:1.8">
          <strong style="color:#e6edf3">How to activate:</strong><br>
          1. Open <strong>FMSecure</strong> on your PC<br>
          2. Click your <strong>username</strong> (top-right corner)<br>
          3. Click <strong>Activate License</strong><br>
          4. Paste this key and click <strong>Activate</strong><br>
          5. PRO features unlock immediately
        </p>
      </div>
      <p style="color:#484f58;font-size:12px;border-top:1px solid #21262d;
                padding-top:16px;margin:0">
        This key activates on one device. To transfer to a new device, reply to this email.<br>
        FMSecure v2.0 &bull; Enterprise EDR for Windows &bull; Made in India
      </p>
    </div>"""

    try:
        import sendgrid
        from sendgrid.helpers.mail import Mail

        sg      = sendgrid.SendGridAPIClient(api_key=SENDGRID_API_KEY)
        message = Mail(
            from_email=SENDER_EMAIL,
            to_emails=email,
            subject="Your FMSecure PRO License Key",
            html_content=html,
        )
        resp = sg.send(message)
        print(f"[EMAIL] Sent to {email} — status {resp.status_code}")
    except Exception as e:
        print(f"[EMAIL] SendGrid failed for {email}: {e}")
        print(f"[EMAIL] Key was: {license_key}")
