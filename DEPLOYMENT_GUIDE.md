# FMSecure Railway Deployment Guide

## What changed
- Root `main.py` remains in the repository root for Railway deployment.
- Reusable support code now lives under `app/`.
- The UI is now template-driven (`templates/`) with shared dark SaaS styling in `static/styles.css` and `static/app.js`.
- A one-time database bootstrap script is included as `one_time_db_setup.py`.

## Step-by-step deployment
1. Copy every file from this package into the repo root and commit the changes.
2. Push to GitHub.
3. In Railway, attach the GitHub repository to your service.
4. Add a PostgreSQL service so Railway injects `DATABASE_URL`.
5. Set the required environment variables:
   - `ADMIN_USERNAME`
   - `ADMIN_PASSWORD`
   - `API_KEY`
   - `LICENSE_HMAC_SECRET`
   - `ADMIN_API_KEY`
   - `APP_BASE_URL`
   - `RAZORPAY_KEY_ID`
   - `RAZORPAY_KEY_SECRET`
   - `SENDGRID_API_KEY`
   - `SENDER_EMAIL`
   - `DRIVE_FILE_ID` (optional but recommended)
6. Run the one-time setup once:
   - locally with Railway vars loaded: `python one_time_db_setup.py`
   - or in a Railway shell / temporary job with the same command.
7. Deploy the service.
8. Verify these pages after deploy:
   - `/`
   - `/features`
   - `/docs`
   - `/contact`
   - `/status`
   - `/login`
   - `/tenant/login`
   - `/dashboard` after super-admin login
   - `/super/dashboard` after super-admin login
9. If you want a pre-created tenant admin, create the tenant from `/super/dashboard` and supply the first admin email/password in the form.

## Notes
- Startup still performs `CREATE TABLE IF NOT EXISTS`, so deployments are safe even if the one-time script was skipped.
- The one-time script is the recommended way to pre-create all new tables, including `website_inquiries`.
- Existing backend logic and routes remain intact; the main changes are UI/template modularization and support-module extraction.
