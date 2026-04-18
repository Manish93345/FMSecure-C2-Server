from datetime import datetime

BRAND = {
    "name": "FMSecure",
    "tagline": "The Core of Endpoint Security",
    "logo_ico": "/static/logo-mark.svg",
    "logo_png": "/static/logo-mark.svg",
    "support_email": "support@fmsecure.in",
    "company": "Manish Lisa Pvt Limited",
    "copyright_year": datetime.now().year,
}

PRICING_DISPLAY = {
    "pro_monthly": {"label": "PRO Monthly", "price": "499", "period": "/mo"},
    "pro_annual": {"label": "PRO Annual", "price": "4,999", "period": "/yr"},
}

PUBLIC_NAV = [
    {"href": "/", "label": "Home"},
    {"href": "/features", "label": "Features"},
    {"href": "/pricing", "label": "Pricing"},
    {"href": "/docs", "label": "Docs"},
    {"href": "/status", "label": "Status"},
    {"href": "/contact", "label": "Contact"},
]

PRODUCT_HIGHLIGHTS = [
    {"icon": "01", "title": "Real-time file integrity monitoring", "text": "Event-driven monitoring catches create, modify, rename, and delete activity across protected folders without relying on slow polling loops."},
    {"icon": "02", "title": "Ransomware killswitch", "text": "Burst detection, honeypot tripwires, and automated isolation workflows can lock down affected endpoints before encryption spreads."},
    {"icon": "03", "title": "Auto-healing vault", "text": "Protected files can be restored from a hidden encrypted vault when tampering or deletion is detected."},
    {"icon": "04", "title": "Cloud C2 and tenant operations", "text": "A Railway-hosted FastAPI control plane gives you live heartbeats, alert feeds, tenant portals, and remote response actions."},
    {"icon": "05", "title": "Forensics and threat intelligence", "text": "Snapshots, process attribution, LOLBin detection, and MalwareBazaar lookups help responders understand what changed and why."},
    {"icon": "06", "title": "Licensing and recovery built in", "text": "Server-validated licensing, offline grace handling, cloud disaster recovery, and device transfer flows are built into the platform."},
]

FEATURE_GROUPS = [
    {"title": "Endpoint defense", "summary": "Protection primitives focused on ransomware, tamper detection, and high-fidelity change tracking.", "items": ["Real-time folder monitoring with watchdog-based event handling", "Auto-healing vault for modified and deleted files", "Ransomware burst detection and OS-level lockdown", "Honeypot files that trigger immediate critical response", "USB write-protection and policy enforcement"]},
    {"title": "Investigation and intelligence", "summary": "Built for incident response, not just notification spam.", "items": ["Process attribution and suspicious LOLBin identification", "Encrypted forensic snapshots and tamper-evident logs", "MalwareBazaar hash lookups for known families", "Registry persistence and startup-path monitoring", "Severity scoring that maps events to action"]},
    {"title": "Cloud operations", "summary": "Designed so a central team can manage multiple organisations cleanly.", "items": ["Global C2 dashboard for live endpoints and version rollouts", "Isolated multi-tenant portals for customer organisations", "Tenant API keys, seat limits, and alert acknowledgment flows", "Remote commands such as LOCKDOWN, VERIFY, and SAFE_MODE", "Server-validated licensing with device activation and transfer"]},
    {"title": "Recovery and business continuity", "summary": "Security controls are paired with restoration options so operations can resume quickly.", "items": ["Google Drive disaster recovery integration", "Consent-based restore workflows", "Folder structure backup and selective restoration", "Offline cache for license validation", "Email-driven recovery and admin onboarding flows"]},
]

SECURITY_PILLARS = [
    {"eyebrow": "Contain", "title": "Stop destructive activity early", "text": "FMSecure combines burst detection, honeypots, and host isolation workflows to reduce the blast radius of active attacks."},
    {"eyebrow": "Restore", "title": "Recover protected assets fast", "text": "When a protected file changes unexpectedly, the encrypted vault and cloud recovery flows provide structured restoration paths."},
    {"eyebrow": "Explain", "title": "Turn alerts into evidence", "text": "Event severity, process attribution, snapshots, and threat intelligence make the console useful to responders, not just observers."},
    {"eyebrow": "Operate", "title": "Run the platform as a service", "text": "Multi-tenant controls, organisation dashboards, licensing, and version publishing support a professional managed-product experience."},
]

PLATFORM_PILLARS = [
    {"title": "Desktop agent", "text": "Windows endpoint engine responsible for monitoring, alerting, vaulting, and active defense actions."},
    {"title": "Cloud C2", "text": "FastAPI + PostgreSQL backend for fleet heartbeat tracking, commands, licensing, and version updates."},
    {"title": "Tenant workspace", "text": "Organisation-specific portal for viewing agents, alerts, and configuration without cross-tenant leakage."},
    {"title": "Recovery layer", "text": "Local encrypted vault plus cloud backup and consent-based restore flows for continuity after an incident."},
]

FAQ_ITEMS = [
    {"question": "What makes FMSecure different from a basic file monitor?", "answer": "It is designed as an active defense and recovery platform: killswitch logic, auto-healing, cloud C2, licensing, tenant portals, and forensics go well beyond change logging."},
    {"question": "Can I operate it for multiple customer organisations?", "answer": "Yes. The multi-tenant backend provides tenant creation, per-organisation admin users, alert isolation, seat limits, and tenant-specific endpoint dashboards."},
    {"question": "Does it support cloud deployment on Railway?", "answer": "Yes. This build keeps main.py in the project root while separating support modules, templates, and static assets so Railway can deploy directly from GitHub."},
    {"question": "How are licenses handled?", "answer": "Licenses are server-validated, device-bindable, transferable, and compatible with offline grace periods for endpoint continuity."},
    {"question": "What can tenant admins do?", "answer": "Tenant admins can monitor agent health, review alerts, acknowledge incidents, tune configuration, and queue response commands such as host lockdown."},
]

DOC_ARCHITECTURE = [
    {"title": "Control plane", "text": "FastAPI application served from Railway with PostgreSQL backing licensing, tenant operations, versions, inquiries, and alert telemetry."},
    {"title": "Agent telemetry", "text": "Endpoints send heartbeat data and alerts to the C2 using tenant API keys or a global API key depending on mode."},
    {"title": "Version channel", "text": "Admins publish a current version and release notes so running agents and download pages can surface upgrades immediately."},
    {"title": "Recovery plane", "text": "The desktop product handles local vault restore and optional cloud recovery through Google Drive integration."},
]

DOC_ENV_VARS = [
    ("DATABASE_URL", "Railway PostgreSQL connection string."),
    ("ADMIN_USERNAME", "Primary super-admin login name."),
    ("ADMIN_PASSWORD", "Primary super-admin password."),
    ("API_KEY", "Legacy/global desktop agent API key."),
    ("RAZORPAY_KEY_ID", "Razorpay public key for checkout."),
    ("RAZORPAY_KEY_SECRET", "Razorpay secret for payment verification."),
    ("LICENSE_HMAC_SECRET", "Secret used for deterministic license generation."),
    ("ADMIN_API_KEY", "Secret for API-style admin endpoints that do not rely on the session cookie."),
    ("APP_BASE_URL", "Public Railway URL for this deployment."),
    ("SENDGRID_API_KEY", "SendGrid API key for license, onboarding, and reset emails."),
    ("SENDER_EMAIL", "Verified from-address for outbound email."),
    ("DRIVE_FILE_ID", "Google Drive file id used for default product download."),
]

DOC_DEPLOYMENT_STEPS = [
    "Push the project root to GitHub with main.py, app/, templates/, static/, one_time_db_setup.py, and DEPLOYMENT_GUIDE.md present in the repository root.",
    "On Railway, connect the GitHub repo and add a PostgreSQL service so DATABASE_URL is injected automatically.",
    "Configure the required environment variables in Railway, especially admin credentials, license secret, payment keys, and email settings.",
    "Run the one_time_db_setup.py script once if you want to pre-create every table before first traffic; startup also uses CREATE TABLE IF NOT EXISTS for safety.",
    "Deploy, verify /status and /docs, then sign in to /login and /tenant/login to validate both admin surfaces.",
]

COMPARE_ROWS = [
    ("Detection approach", "Real-time event monitoring", "Mostly periodic scans or hash checks"),
    ("Response", "Lockdown, isolate, auto-heal, recover", "Alert only"),
    ("Operations", "Multi-tenant dashboards and versioning", "Single-system or lab utility"),
    ("Recovery", "Local vault plus cloud restore patterns", "Usually manual restore steps"),
]
