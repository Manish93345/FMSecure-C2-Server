"""
routes/pages.py — Public-facing product pages.

Routes:
  GET /           (redirects to /home)
  GET /home       (landing page)
  GET /download
  GET /changelog
  GET /pricing
"""
from datetime import datetime

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, RedirectResponse

from core.config import APP_BASE_URL, DRIVE_FILE_ID, RZP_KEY_ID, DATABASE_URL
from core.database import get_db

router = APIRouter()

# ── Root redirect ──────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def landing_page_root():
    return await landing_page()


# ── Landing / home page ────────────────────────────────────────────────────────

@router.get("/home", response_class=HTMLResponse)
async def landing_page():
    base = APP_BASE_URL
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1.0"/>
<title>FMSecure — Enterprise EDR for Windows</title>
<meta name="description" content="Real-time file integrity monitoring, ransomware killswitch, auto-healing vault, and cloud disaster recovery for Windows endpoints."/>
<link rel="icon" href="/static/app_icon.png" type="image/png"/>
<link rel="preconnect" href="https://fonts.googleapis.com"/>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin/>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet"/>
<script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/gsap/3.12.2/gsap.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/gsap/3.12.2/ScrollTrigger.min.js"></script>
<style>
:root{{
  --bg:#050810;--bg2:#080d1a;--bg3:#0c1220;
  --t1:#f0f4ff;--t2:#a8b4cc;--t3:#5c6880;
  --blue:#2f81f7;--cyan:#4dd0e1;--purple:#a371f7;
  --green:#3fb950;--red:#f85149;--orange:#f0883e;
  --border:#1e2738;--card:#0e1523;
}}
*{{box-sizing:border-box;margin:0;padding:0}}
html{{scroll-behavior:smooth}}
body{{background:var(--bg);color:var(--t1);font-family:'Inter',system-ui,sans-serif;
     overflow-x:hidden}}
/* ── canvas bg ── */
#bgc{{position:fixed;top:0;left:0;width:100%;height:100%;
      pointer-events:none;z-index:0}}
#z1{{position:relative;z-index:1}}
/* ── nav ── */
#mnav{{position:fixed;top:0;left:0;right:0;z-index:100;
       background:rgba(5,8,16,.7);backdrop-filter:blur(12px);
       border-bottom:1px solid var(--border);
       padding:0 48px;height:64px;display:flex;align-items:center;gap:32px;
       transition:background .3s}}
.nav-brand{{display:flex;align-items:center;gap:10px;text-decoration:none;margin-right:auto}}
.nav-brand-txt{{font-size:20px;font-weight:800;color:var(--t1)}}
.nav-brand-txt em{{color:var(--blue);font-style:normal}}
#mnav a:not(.nav-brand){{color:var(--t2);text-decoration:none;font-size:14px;
  font-weight:500;transition:color .2s}}
#mnav a:not(.nav-brand):hover{{color:var(--t1)}}
.nav-cta{{background:var(--blue);color:#fff!important;padding:8px 20px;
          border-radius:8px;font-weight:600!important}}
.nav-cta:hover{{background:#4f96ff!important;color:#fff!important}}
/* ── hero ── */
.hero{{min-height:100vh;display:flex;flex-direction:column;align-items:center;
      justify-content:center;text-align:center;padding:100px 24px 80px}}
.hero-badge{{background:rgba(47,129,247,.12);border:1px solid rgba(47,129,247,.3);
             color:var(--blue);padding:6px 16px;border-radius:20px;font-size:13px;
             font-weight:600;display:inline-block;margin-bottom:24px;
             letter-spacing:.5px}}
.hero h1{{font-size:clamp(36px,6vw,76px);font-weight:800;line-height:1.1;
          margin-bottom:20px}}
.hero h1 em{{font-style:normal;background:linear-gradient(135deg,var(--blue),var(--purple));
             -webkit-background-clip:text;-webkit-text-fill-color:transparent}}
.hero p{{color:var(--t2);font-size:clamp(15px,2vw,19px);max-width:580px;
         margin:0 auto 40px;line-height:1.65}}
.hero-acts{{display:flex;gap:14px;flex-wrap:wrap;justify-content:center}}
.btn-hp{{background:var(--blue);color:#fff;padding:14px 32px;border-radius:10px;
         font-size:16px;font-weight:700;text-decoration:none;transition:all .2s}}
.btn-hp:hover{{background:#4f96ff;transform:translateY(-1px)}}
.btn-hg{{background:transparent;color:var(--t1);padding:14px 32px;
         border-radius:10px;font-size:16px;font-weight:600;
         text-decoration:none;border:1px solid var(--border);transition:all .2s}}
.btn-hg:hover{{border-color:var(--blue);color:var(--blue)}}
/* ── trust bar ── */
.trust{{display:flex;gap:36px;justify-content:center;flex-wrap:wrap;
        padding:32px 24px;border-top:1px solid var(--border);
        border-bottom:1px solid var(--border);margin-top:60px}}
.titem{{color:var(--t3);font-size:13px;font-weight:600;
        letter-spacing:.5px;text-transform:uppercase}}
.titem span{{color:var(--t2);margin-right:8px}}
/* ── sections ── */
.dvd{{height:1px;background:var(--border);margin:0 48px}}
section{{padding:80px 48px;max-width:1200px;margin:0 auto}}
.scen{{text-align:center;margin-bottom:60px}}
.slbl{{color:var(--blue);font-size:12px;font-weight:700;
       letter-spacing:1.5px;text-transform:uppercase;margin-bottom:12px}}
.stit{{font-size:clamp(28px,4vw,44px);font-weight:800;margin-bottom:16px;
       line-height:1.15}}
.ssub{{color:var(--t2);font-size:17px;max-width:600px;margin:0 auto;line-height:1.6}}
/* ── feature cards ── */
.fg{{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));
     gap:20px}}
.fc{{background:var(--card);border:1px solid var(--border);border-radius:14px;
     padding:28px;opacity:0;transform:translateY(20px);transition:.3s}}
.fc:hover{{border-color:var(--blue);transform:translateY(-3px)}}
.fc .ic{{font-size:30px;margin-bottom:14px}}
.fc h3{{font-size:17px;font-weight:700;margin-bottom:8px}}
.fc p{{color:var(--t2);font-size:14px;line-height:1.6}}
/* ── compare table ── */
.ct{{width:100%;border-collapse:collapse;background:var(--card);border-radius:14px;
     overflow:hidden}}
.ct th{{padding:16px 24px;text-align:center;font-size:13px;font-weight:700;
        letter-spacing:.5px;background:#080d1a;color:var(--t2)}}
.ct th:first-child{{text-align:left}}
.ct td{{padding:12px 24px;border-top:1px solid var(--border);font-size:14px;color:var(--t2)}}
.ct td:first-child{{color:var(--t1)}}
.ct td:not(:first-child){{text-align:center}}
.chk{{color:var(--green);font-weight:700}}
.crs{{color:var(--t3)}}
/* ── how it works ── */
.hw{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:32px}}
.hws{{position:relative;padding-left:48px}}
.hws-n{{position:absolute;left:0;top:0;width:32px;height:32px;border-radius:50%;
        background:rgba(47,129,247,.15);border:1px solid rgba(47,129,247,.4);
        color:var(--blue);font-size:13px;font-weight:700;
        display:flex;align-items:center;justify-content:center}}
.hws h3{{font-size:17px;font-weight:700;margin-bottom:10px}}
.hws p{{color:var(--t2);font-size:14px;line-height:1.65}}
/* ── arch ── */
.arch{{display:grid;grid-template-columns:1fr 1fr;gap:60px;
       padding:80px 48px;max-width:1200px;margin:0 auto;align-items:center}}
@media(max-width:768px){{.arch{{grid-template-columns:1fr;padding:60px 24px}}}}
.arch-t h2{{font-size:clamp(24px,3.5vw,38px);font-weight:800;margin-bottom:16px;line-height:1.2}}
.arch-t p{{color:var(--t2);font-size:15px;line-height:1.7;margin-bottom:20px}}
.arch-li{{list-style:none;padding-left:0}}
.arch-li li{{color:var(--t2);font-size:13px;padding:6px 0 6px 22px;
             position:relative;border-bottom:1px solid var(--border)}}
.arch-li li::before{{content:"›";position:absolute;left:0;color:var(--blue);font-weight:700}}
.arch-stack{{display:flex;flex-direction:column;gap:10px}}
.alyr{{background:var(--card);border:1px solid var(--border);border-radius:10px;
       padding:14px 18px;display:flex;align-items:center;
       justify-content:space-between;opacity:0;transform:translateX(20px)}}
.alyr-l{{display:flex;align-items:center;gap:14px}}
.alyr-ic{{font-size:22px}}
.alyr-nm{{font-size:14px;font-weight:600}}
.alyr-dt{{font-size:11px;color:var(--t3);margin-top:2px}}
.alyr-st{{font-size:11px;font-weight:700;padding:3px 10px;border-radius:12px}}
.st-live{{background:rgba(63,185,80,.15);color:var(--green)}}
.st-cloud{{background:rgba(47,129,247,.15);color:var(--blue)}}
.st-local{{background:rgba(208,153,34,.12);color:#d29922}}
.st-kern{{background:rgba(163,113,247,.15);color:var(--purple)}}
/* ── pricing ── */
.pg{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));
     gap:20px;max-width:900px;margin:0 auto}}
.pc{{background:var(--card);border:1px solid var(--border);border-radius:16px;
     padding:36px 28px;position:relative;opacity:0;transform:translateY(20px)}}
.pc.feat{{border-color:var(--blue)}}
.pbadge{{position:absolute;top:-13px;left:50%;transform:translateX(-50%);
         background:var(--blue);color:#fff;padding:4px 16px;
         border-radius:20px;font-size:12px;font-weight:600;white-space:nowrap}}
.pplan{{color:var(--t3);font-size:11px;font-weight:700;
        letter-spacing:1px;text-transform:uppercase;margin-bottom:8px}}
.pprice{{font-size:44px;font-weight:800;margin-bottom:4px}}
.pprice sup{{font-size:20px;vertical-align:super}}
.pprice span{{font-size:16px;color:var(--t2);font-weight:400}}
.pdesc{{color:var(--t2);font-size:13px;margin-bottom:20px}}
.pdvd{{height:1px;background:var(--border);margin:20px 0}}
.pfl{{list-style:none;margin-bottom:28px}}
.pfl li{{font-size:13px;padding:6px 0;color:var(--t2);
         border-bottom:1px solid var(--border)}}
.pfl li:last-child{{border:none}}
.pfl .c{{color:var(--green);margin-right:6px;font-weight:700}}
.pfl .x{{color:var(--t3);margin-right:6px}}
.pbtn{{display:block;width:100%;padding:13px;border:none;border-radius:9px;
       font-size:14px;font-weight:700;cursor:pointer;text-decoration:none;
       text-align:center;transition:opacity .2s}}
.pbtn:hover{{opacity:.85}}
.pbp{{background:var(--blue);color:#fff}}
.pbo{{background:transparent;color:var(--blue);border:1px solid var(--blue)}}
/* ── faq ── */
.faq-list{{max-width:780px;margin:0 auto}}
.fi{{border:1px solid var(--border);border-radius:10px;margin-bottom:12px;
     overflow:hidden}}
.fq{{width:100%;background:var(--card);border:none;color:var(--t1);
     padding:18px 24px;font-size:15px;font-weight:600;cursor:pointer;
     text-align:left;display:flex;justify-content:space-between;
     align-items:center}}
.fq:hover{{background:#111827}}
.chv{{color:var(--t3);font-size:20px;transition:transform .3s}}
.fi.open .chv{{transform:rotate(90deg)}}
.fa{{display:none;padding:18px 24px;background:#080d1a;
     border-top:1px solid var(--border)}}
.fi.open .fa{{display:block}}
.fa p{{color:var(--t2);font-size:14px;line-height:1.7}}
/* ── CTA ── */
.cta-sec{{padding:80px 24px;text-align:center}}
.cta-box{{background:linear-gradient(135deg,rgba(47,129,247,.08),rgba(163,113,247,.08));
          border:1px solid rgba(47,129,247,.2);border-radius:20px;
          padding:64px 40px;max-width:700px;margin:0 auto}}
.cta-box h2{{font-size:clamp(24px,4vw,38px);font-weight:800;margin-bottom:14px}}
.cta-box p{{color:var(--t2);font-size:16px;margin-bottom:32px}}
.cta-acts{{display:flex;gap:14px;justify-content:center;flex-wrap:wrap}}
/* ── footer ── */
footer{{background:#030508;border-top:1px solid var(--border);padding:60px 48px 32px}}
.ft{{display:grid;grid-template-columns:2fr 1fr 1fr 1fr;gap:40px;
     max-width:1200px;margin:0 auto 40px}}
@media(max-width:768px){{.ft{{grid-template-columns:1fr 1fr}}}}
.fb p{{color:var(--t3);font-size:13px;line-height:1.6;margin-top:12px;max-width:280px}}
.flg h4{{font-size:12px;font-weight:700;color:var(--t3);
         letter-spacing:1px;text-transform:uppercase;margin-bottom:14px}}
.flg ul{{list-style:none}}
.flg li{{margin-bottom:8px}}
.flg a{{color:var(--t2);text-decoration:none;font-size:13px;transition:color .2s}}
.flg a:hover{{color:var(--t1)}}
.fb2{{border-top:1px solid var(--border);padding-top:24px;
      display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px;
      max-width:1200px;margin:0 auto}}
.fb2 p{{color:var(--t3);font-size:12px}}
@media(max-width:768px){{
  section{{padding:60px 24px}}
  .arch{{padding:60px 24px}}
  #mnav{{padding:0 24px;gap:16px}}
  #mnav a:not(.nav-brand):not(.nav-cta){{display:none}}
  footer{{padding:48px 24px 24px}}
  .ft{{grid-template-columns:1fr}}
}}
</style>
</head>
<body>
<canvas id="bgc"></canvas>
<div id="z1">

<!-- NAV -->
<nav id="mnav">
  <a href="{base}/home" class="nav-brand">
    <img src="/static/app_icon.png" alt="FMSecure" width="32" height="32"
         onerror="this.style.display='none'"/>
    <span class="nav-brand-txt">FM<em>Secure</em></span>
  </a>
  <a href="#features">Features</a>
  <a href="#compare">Compare</a>
  <a href="#pricing">Pricing</a>
  <a href="#faq">FAQ</a>
  <a href="{base}/download" class="nav-cta">Download free</a>
</nav>

<!-- HERO -->
<section class="hero">
  <span class="hero-badge">🛡 Enterprise EDR for Windows</span>
  <h1>Stop threats before<br/><em>files are lost forever</em></h1>
  <p>Real-time file integrity monitoring with HMAC-signed logs, AES-256 encrypted vault, ransomware killswitch, and live C2 fleet dashboard.</p>
  <div class="hero-acts">
    <a href="{base}/download" class="btn-hp">⬇ Download free</a>
    <a href="#pricing" class="btn-hg">See PRO pricing →</a>
  </div>
  <div class="trust">
    <span class="titem"><span>🔒</span>AES-256 at rest</span>
    <span class="titem"><span>⚡</span>Real-time watchdog</span>
    <span class="titem"><span>☁️</span>Cloud disaster recovery</span>
    <span class="titem"><span>🛑</span>Ransomware killswitch</span>
    <span class="titem"><span>🖥</span>Windows 10 / 11</span>
  </div>
</section>

<div class="dvd"></div>

<!-- FEATURES -->
<section id="features">
  <div class="scen">
    <div class="slbl">Core capabilities</div>
    <h2 class="stit">Everything your threat model demands.</h2>
    <p class="ssub">Six independent security layers. A failure in one never compromises the others.</p>
  </div>
  <div class="fg">
    <div class="fc">
      <div class="ic">🔍</div>
      <h3>File Integrity Monitoring</h3>
      <p>SHA-256 hashing across all monitored directories at 1.8 GB/s using concurrent CPU threads. HMAC-signed tamper-proof audit logs — every event is cryptographically bound.</p>
    </div>
    <div class="fc">
      <div class="ic">🛡️</div>
      <h3>Active Defense Vault</h3>
      <p>AES-256 encrypted vault automatically restores any deleted or tampered file within milliseconds. The vault itself is hardware-bound — useless if stolen.</p>
    </div>
    <div class="fc">
      <div class="ic">🛑</div>
      <h3>Ransomware Killswitch</h3>
      <p>Burst detector fires after 5 file ops in 10 seconds. icacls revokes NTFS permissions at the OS level in under 200ms — stops WannaCry-class attacks after 5–8 files.</p>
    </div>
    <div class="fc">
      <div class="ic">☁️</div>
      <h3>Cloud Disaster Recovery</h3>
      <p>Three-tier key protection: local primary, shadow copy, and Google Drive cloud escrow keyed to your hardware machine ID. Full recovery in under 3 minutes.</p>
    </div>
    <div class="fc">
      <div class="ic">🔌</div>
      <h3>USB Device Control</h3>
      <p>Block write access on any unauthorized USB storage device using Windows NTFS ACLs. No kernel driver required. Policy enforced at hardware insertion.</p>
    </div>
    <div class="fc">
      <div class="ic">🌐</div>
      <h3>Live C2 Fleet Dashboard</h3>
      <p>Real-time endpoint telemetry across your entire fleet. Remote host isolation, LOCKDOWN command delivery, and per-tenant policy management.</p>
    </div>
  </div>
</section>

<div class="dvd"></div>

<!-- COMPARE TABLE -->
<section id="compare">
  <div class="scen">
    <div class="slbl">Comparison</div>
    <h2 class="stit">Free vs PRO</h2>
  </div>
  <table class="ct">
    <thead>
      <tr>
        <th style="text-align:left">Feature</th>
        <th>Free</th>
        <th style="color:var(--blue)">PRO</th>
      </tr>
    </thead>
    <tbody>
      <tr><td>Monitored folders</td><td>1</td><td style="color:var(--blue);font-weight:700">5</td></tr>
      <tr><td>Real-time watchdog (create / modify / delete / rename)</td><td><span class="chk">&#10003;</span></td><td><span class="chk">&#10003;</span></td></tr>
      <tr><td>HMAC-signed tamper-proof audit logs</td><td><span class="chk">&#10003;</span></td><td><span class="chk">&#10003;</span></td></tr>
      <tr><td>AES-256 encryption at rest (logs, vault, records)</td><td><span class="chk">&#10003;</span></td><td><span class="chk">&#10003;</span></td></tr>
      <tr><td>Hardware-bound KEK (PBKDF2 200k iter.)</td><td><span class="chk">&#10003;</span></td><td><span class="chk">&#10003;</span></td></tr>
      <tr><td>Email OTP registration + password recovery</td><td><span class="chk">&#10003;</span></td><td><span class="chk">&#10003;</span></td></tr>
      <tr><td>Google SSO with device PIN 2FA</td><td><span class="chk">&#10003;</span></td><td><span class="chk">&#10003;</span></td></tr>
      <tr><td>PDF report export + severity charts</td><td><span class="chk">&#10003;</span></td><td><span class="chk">&#10003;</span></td></tr>
      <tr><td>Self-healing Watchdog process (WinSysHost.exe)</td><td><span class="chk">&#10003;</span></td><td><span class="chk">&#10003;</span></td></tr>
      <tr><td>Discord / Slack webhook alerts</td><td><span class="chk">&#10003;</span></td><td><span class="chk">&#10003;</span></td></tr>
      <tr><td>Active Defense auto-heal vault</td><td><span class="crs">&#8212;</span></td><td><span class="chk">&#10003;</span></td></tr>
      <tr><td>Ransomware behavioral killswitch (icacls)</td><td><span class="crs">&#8212;</span></td><td><span class="chk">&#10003;</span></td></tr>
      <tr><td>Honeypot tripwire</td><td><span class="crs">&#8212;</span></td><td><span class="chk">&#10003;</span></td></tr>
      <tr><td>Google Drive cloud disaster recovery</td><td><span class="crs">&#8212;</span></td><td><span class="chk">&#10003;</span></td></tr>
      <tr><td>AES-encrypted forensic incident vault</td><td><span class="crs">&#8212;</span></td><td><span class="chk">&#10003;</span></td></tr>
      <tr><td>USB device control (DLP)</td><td><span class="crs">&#8212;</span></td><td><span class="chk">&#10003;</span></td></tr>
      <tr><td>Live C2 fleet telemetry dashboard</td><td><span class="crs">&#8212;</span></td><td><span class="chk">&#10003;</span></td></tr>
      <tr><td>Remote host isolation (cloud-triggered lockdown)</td><td><span class="crs">&#8212;</span></td><td><span class="chk">&#10003;</span></td></tr>
    </tbody>
  </table>
</section>

<div class="dvd"></div>

<!-- HOW IT WORKS -->
<section id="howitworks">
  <div class="scen">
    <div class="slbl">Deployment</div>
    <h2 class="stit">Up and protecting in 3 steps.</h2>
    <p class="ssub">No kernel driver signing. No IT department approval. One EXE with UAC elevation and an optional invisible Watchdog service.</p>
  </div>
  <div class="hw">
    <div class="hws">
      <div class="hws-n">01</div>
      <h3>Download &amp; run</h3>
      <p>Run <code style="font-family:'JetBrains Mono',monospace;color:var(--cyan)">SecureFIM.exe</code> as Administrator. Register your admin account with email OTP. The Watchdog installs silently as a background process that survives Task Manager kills and reboots.</p>
    </div>
    <div class="hws">
      <div class="hws-n">02</div>
      <h3>Configure your folders</h3>
      <p>Add up to 5 monitored directories. Baseline hashes are generated concurrently across all CPU threads (verified at 1.8 GB/s on NVMe). PRO users get cloud sync and vault backup enabled automatically on first folder add.</p>
    </div>
    <div class="hws">
      <div class="hws-n">03</div>
      <h3>Monitor &amp; respond</h3>
      <p>Real-time alerts via dashboard, Discord/Slack webhook, and SMTP email with forensic .dat attachments. Forensic snapshots auto-generated on every CRITICAL event. Remote lockdown from the C2 browser console.</p>
    </div>
  </div>
</section>

<div class="dvd"></div>

<!-- ARCHITECTURE -->
<div class="arch" id="architecture">
  <div class="arch-t">
    <div class="slbl">Technical architecture</div>
    <h2>Multi-layer defense.<br/>Single binary.</h2>
    <p>FMSecure is not a script wrapper. It is a layered security architecture where each tier is independently functional — a failure in one layer never compromises the others.</p>
    <ul class="arch-li">
      <li>HMAC SHA-256 signed on every log line — tamper detection at write time</li>
      <li>Hardware KEK ensures stolen key files are permanently unreadable elsewhere</li>
      <li>Two-tier vault: local AES-256, automatic cloud fallback on recovery</li>
      <li>Watchdog survives Task Manager, Admin override required to stop</li>
      <li>icacls lockdown operates at NTFS kernel level, not Python file locks</li>
      <li>Machine ID — not email — is the cloud identity anchor</li>
    </ul>
  </div>
  <div class="arch-v">
    <div class="arch-stack">
      <div class="alyr"><div class="alyr-l"><span class="alyr-ic">&#x1F310;</span><div><div class="alyr-nm">C2 cloud server</div><div class="alyr-dt">FastAPI &bull; Railway &bull; PostgreSQL</div></div></div><span class="alyr-st st-live">Live</span></div>
      <div class="alyr"><div class="alyr-l"><span class="alyr-ic">&#x2601;&#xFE0F;</span><div><div class="alyr-nm">Cloud key escrow</div><div class="alyr-dt">Google Drive &bull; machine_id KEK</div></div></div><span class="alyr-st st-cloud">PRO</span></div>
      <div class="alyr"><div class="alyr-l"><span class="alyr-ic">&#x1F512;</span><div><div class="alyr-nm">AES-256 local vault</div><div class="alyr-dt">AppData &bull; PBKDF2 KEK &bull; .enc</div></div></div><span class="alyr-st st-local">Local</span></div>
      <div class="alyr"><div class="alyr-l"><span class="alyr-ic">&#x1F441;&#xFE0F;</span><div><div class="alyr-nm">Watchdog process</div><div class="alyr-dt">WinSysHost.exe &bull; daemon &bull; --recovery</div></div></div><span class="alyr-st st-local">Local</span></div>
      <div class="alyr"><div class="alyr-l"><span class="alyr-ic">&#x1F50D;</span><div><div class="alyr-nm">File integrity engine</div><div class="alyr-dt">watchdog &bull; SHA-256 &bull; HMAC &bull; debounce</div></div></div><span class="alyr-st st-local">Local</span></div>
      <div class="alyr"><div class="alyr-l"><span class="alyr-ic">&#x2699;&#xFE0F;</span><div><div class="alyr-nm">OS permission layer</div><div class="alyr-dt">icacls &bull; Registry &bull; WMI &bull; NTFS</div></div></div><span class="alyr-st st-kern">Kernel</span></div>
    </div>
  </div>
</div>

<div class="dvd"></div>

<!-- PRICING -->
<section id="pricing">
  <div class="scen">
    <div class="slbl">Pricing</div>
    <h2 class="stit">Simple, transparent pricing.</h2>
    <p class="ssub">Start free. Upgrade when your threat model demands it. License key delivered within 60 seconds of payment.</p>
  </div>
  <div class="pg">
    <div class="pc">
      <div class="pplan">Free</div>
      <div class="pprice">&#x20B9;0</div>
      <div class="pdesc">For personal use and learning</div>
      <div class="pdvd"></div>
      <ul class="pfl">
        <li><span class="c">&#10003;</span> 1 monitored folder</li>
        <li><span class="c">&#10003;</span> SHA-256 file integrity monitoring</li>
        <li><span class="c">&#10003;</span> HMAC-signed tamper-proof logs</li>
        <li><span class="c">&#10003;</span> AES-256 encryption at rest</li>
        <li><span class="c">&#10003;</span> Hardware-bound KEK</li>
        <li><span class="c">&#10003;</span> Google SSO + email OTP</li>
        <li><span class="c">&#10003;</span> Discord / Slack webhooks</li>
        <li><span class="x">&#8212;</span> <span style="color:var(--t3)">Active defense vault</span></li>
        <li><span class="x">&#8212;</span> <span style="color:var(--t3)">Ransomware killswitch</span></li>
        <li><span class="x">&#8212;</span> <span style="color:var(--t3)">Cloud backup &amp; C2</span></li>
      </ul>
      <a href="{base}/download" class="pbtn pbo">Download free</a>
    </div>
    <div class="pc feat">
      <div class="pbadge">Most popular</div>
      <div class="pplan">PRO Monthly</div>
      <div class="pprice"><sup>&#x20B9;</sup>499<span>/mo</span></div>
      <div class="pdesc">For professionals protecting real systems</div>
      <div class="pdvd"></div>
      <ul class="pfl">
        <li><span class="c">&#10003;</span> Up to 5 monitored folders</li>
        <li><span class="c">&#10003;</span> Everything in Free</li>
        <li><span class="c">&#10003;</span> Active Defense auto-heal vault</li>
        <li><span class="c">&#10003;</span> Ransomware behavioral killswitch</li>
        <li><span class="c">&#10003;</span> Google Drive cloud disaster recovery</li>
        <li><span class="c">&#10003;</span> AES forensic incident vault</li>
        <li><span class="c">&#10003;</span> USB device control (DLP)</li>
        <li><span class="c">&#10003;</span> Honeypot tripwire</li>
        <li><span class="c">&#10003;</span> Live C2 fleet telemetry</li>
        <li><span class="c">&#10003;</span> Remote host isolation</li>
      </ul>
      <a href="{base}/pricing" class="pbtn pbp">Activate PRO &rarr;</a>
    </div>
    <div class="pc">
      <div class="pplan">PRO Annual</div>
      <div class="pprice"><sup>&#x20B9;</sup>4,999<span>/yr</span></div>
      <div class="pdesc">2 months free &mdash; best value</div>
      <div class="pdvd"></div>
      <ul class="pfl">
        <li><span class="c">&#10003;</span> Everything in PRO Monthly</li>
        <li><span class="c">&#10003;</span> Priority email support</li>
        <li><span class="c">&#10003;</span> Early access to new features</li>
        <li><span class="c">&#10003;</span> Annual GST invoice for claims</li>
      </ul>
      <a href="{base}/pricing" class="pbtn pbo">Activate Annual &rarr;</a>
    </div>
  </div>
  <p style="text-align:center;font-size:12.5px;color:var(--t3);margin-top:26px">
    Payments processed securely via Razorpay &bull; Cancel anytime &bull; License key delivered by email within 60 seconds
  </p>
</section>

<div class="dvd"></div>

<!-- FAQ -->
<section id="faq">
  <div class="scen">
    <div class="slbl">FAQ</div>
    <h2 class="stit">Common questions.</h2>
  </div>
  <div class="faq-list">
    <div class="fi">
      <button class="fq" onclick="tfaq(this)">How does license activation work?<i class="chv">&#x203A;</i></button>
      <div class="fa"><p>After payment, Razorpay fires a webhook to our Railway server which generates a unique license key and emails it within 60 seconds. Paste it into FMSecure&apos;s "Activate PRO" dialog — the agent validates it against our server and unlocks all PRO features instantly. Keys are device-bound by hardware machine ID, not email.</p></div>
    </div>
    <div class="fi">
      <button class="fq" onclick="tfaq(this)">What happens if my encryption key is deleted?<i class="chv">&#x203A;</i></button>
      <div class="fa"><p>PRO users get three-tier key protection: (1) primary local key, (2) shadow backup copy, (3) cloud escrow on Google Drive identified by hardware machine ID. On startup, FMSecure automatically attempts all three in order. Full disaster recovery runs from the dashboard in under 3 minutes.</p></div>
    </div>
    <div class="fi">
      <button class="fq" onclick="tfaq(this)">Does FMSecure require a kernel driver or code signing?<i class="chv">&#x203A;</i></button>
      <div class="fa"><p>No. FMSecure runs as a standard Windows application with UAC Administrator elevation. The Ransomware Killswitch uses the built-in Windows <code style="font-family:'JetBrains Mono',monospace;font-size:12px">icacls</code> command to revoke NTFS permissions at the OS level — no kernel driver, no Authenticode signing required.</p></div>
    </div>
    <div class="fi">
      <button class="fq" onclick="tfaq(this)">What if I kill the FMSecure process in Task Manager?<i class="chv">&#x203A;</i></button>
      <div class="fa"><p>The Watchdog process (masquerading as <code style="font-family:'JetBrains Mono',monospace;font-size:12px">WinSysHost.exe</code>) detects the termination within seconds and relaunches the agent in Recovery Mode — bypassing the login screen, auto-logging in the last admin, and resuming monitoring without any user interaction.</p></div>
    </div>
    <div class="fi">
      <button class="fq" onclick="tfaq(this)">Do you ever have access to my Google Drive or my files?<i class="chv">&#x203A;</i></button>
      <div class="fa"><p>No. FMSecure uses your own Google OAuth credentials to write to your personal Google Drive. All backups land in a <code style="font-family:'JetBrains Mono',monospace;font-size:12px">FMSecure_{{MACHINE_ID}}</code> folder that only your account controls. Files are AES-256 encrypted before upload — we never see plaintext content.</p></div>
    </div>
    <div class="fi">
      <button class="fq" onclick="tfaq(this)">How fast is the ransomware killswitch?<i class="chv">&#x203A;</i></button>
      <div class="fa"><p>The burst detector fires after 5 file operations are detected within a 10-second sliding window. The <code style="font-family:'JetBrains Mono',monospace;font-size:12px">icacls</code> lockdown executes as a subprocess immediately — typical wall-clock time from detection to permission revocation is under 200ms.</p></div>
    </div>
  </div>
</section>

<!-- CTA -->
<section class="cta-sec">
  <div class="cta-box">
    <h2>Start protecting your endpoints today.</h2>
    <p>Free tier available with no credit card. PRO features activate within 60 seconds of payment.</p>
    <div class="cta-acts">
      <a href="{base}/download" class="btn-hp">Download FMSecure free</a>
      <a href="{base}/pricing" class="btn-hg">See PRO pricing &rarr;</a>
    </div>
  </div>
</section>

<!-- FOOTER -->
<footer>
  <div class="ft">
    <div class="fb">
      <a href="{base}/home" class="nav-brand">
        <img src="/static/app_icon.png" alt="FMSecure" width="26" height="26"
             onerror="this.style.display='none'"/>
        <span class="nav-brand-txt" style="font-size:16px">FM<em>Secure</em></span>
      </a>
      <p>Enterprise-grade endpoint detection and response for Windows. Built by a security engineer, for security engineers.</p>
    </div>
    <div class="flg">
      <h4>Product</h4>
      <ul>
        <li><a href="#features">Features</a></li>
        <li><a href="#compare">Free vs PRO</a></li>
        <li><a href="#pricing">Pricing</a></li>
        <li><a href="#faq">FAQ</a></li>
      </ul>
    </div>
    <div class="flg">
      <h4>Resources</h4>
      <ul>
        <li><a href="#">Documentation</a></li>
        <li><a href="/changelog">Changelog</a></li>
        <li><a href="#">GitHub</a></li>
        <li><a href="{base}/dashboard">C2 Dashboard</a></li>
        <li><a href="{base}/tenant/login">Client Portal</a></li>
      </ul>
    </div>
    <div class="flg">
      <h4>Legal</h4>
      <ul>
        <li><a href="#">Privacy policy</a></li>
        <li><a href="#">Terms of service</a></li>
        <li><a href="#">License agreement</a></li>
      </ul>
    </div>
  </div>
  <div class="fb2">
    <p>&copy; {datetime.now().year} FMSecure &bull; All rights reserved &bull; Made in India</p>
    <p>FastAPI &bull; Python &bull; Google Drive API &bull; Razorpay</p>
  </div>
</footer>

</div><!-- end z1 -->

<script>
/* THREE.JS NETWORK BACKGROUND */
(function(){{
  const cv = document.getElementById('bgc');
  const scene = new THREE.Scene();
  const cam = new THREE.PerspectiveCamera(60, innerWidth/innerHeight, 0.1, 1000);
  const renderer = new THREE.WebGLRenderer({{canvas:cv, alpha:true, antialias:true}});
  renderer.setPixelRatio(Math.min(devicePixelRatio, 1.5));
  renderer.setSize(innerWidth, innerHeight);
  cam.position.z = 3;
  const N = 110;
  const pos = new Float32Array(N * 3);
  const vel = [];
  for(let i=0;i<N;i++){{
    pos[i*3]   = (Math.random()-.5)*14;
    pos[i*3+1] = (Math.random()-.5)*8;
    pos[i*3+2] = (Math.random()-.5)*5;
    vel.push((Math.random()-.5)*.004,(Math.random()-.5)*.003,(Math.random()-.5)*.002);
  }}
  const pg = new THREE.BufferGeometry();
  pg.setAttribute('position', new THREE.BufferAttribute(pos,3));
  const pm = new THREE.PointsMaterial({{color:0x2f81f7,size:.032,transparent:true,opacity:.55}});
  scene.add(new THREE.Points(pg, pm));
  const maxL = N*5;
  const lpos = new Float32Array(maxL*6);
  const lg = new THREE.BufferGeometry();
  lg.setAttribute('position', new THREE.BufferAttribute(lpos,3));
  const lm = new THREE.LineBasicMaterial({{color:0x2f81f7,transparent:true,opacity:.07}});
  const lines = new THREE.LineSegments(lg, lm);
  scene.add(lines);
  let fr=0;
  function animate(){{
    requestAnimationFrame(animate); fr++;
    for(let i=0;i<N;i++){{
      pos[i*3]  +=vel[i*3];   pos[i*3+1]+=vel[i*3+1]; pos[i*3+2]+=vel[i*3+2];
      if(Math.abs(pos[i*3])>7)   vel[i*3]  *=-1;
      if(Math.abs(pos[i*3+1])>4) vel[i*3+1]*=-1;
      if(Math.abs(pos[i*3+2])>2.5) vel[i*3+2]*=-1;
    }}
    pg.attributes.position.needsUpdate=true;
    if(fr%3===0){{
      let lc=0; const th=2.4;
      for(let i=0;i<N&&lc<maxL;i++)for(let j=i+1;j<N&&lc<maxL;j++){{
        const dx=pos[i*3]-pos[j*3],dy=pos[i*3+1]-pos[j*3+1],dz=pos[i*3+2]-pos[j*3+2];
        if(dx*dx+dy*dy+dz*dz<th*th){{
          const b=lc*6;
          lpos[b]=pos[i*3];lpos[b+1]=pos[i*3+1];lpos[b+2]=pos[i*3+2];
          lpos[b+3]=pos[j*3];lpos[b+4]=pos[j*3+1];lpos[b+5]=pos[j*3+2];
          lc++;
        }}
      }}
      lg.setDrawRange(0,lc*2); lg.attributes.position.needsUpdate=true;
    }}
    renderer.render(scene,cam);
  }}
  animate();
  window.addEventListener('resize',()=>{{
    cam.aspect=innerWidth/innerHeight; cam.updateProjectionMatrix();
    renderer.setSize(innerWidth,innerHeight);
  }});
}})();

/* GSAP SCROLL ANIMATIONS */
gsap.registerPlugin(ScrollTrigger);
gsap.utils.toArray('.fc').forEach((el,i)=>{{
  gsap.to(el,{{opacity:1,y:0,duration:.55,delay:(i%3)*.09,
    scrollTrigger:{{trigger:el,start:'top 88%'}}
  }});
}});
gsap.utils.toArray('.alyr').forEach((el,i)=>{{
  gsap.to(el,{{opacity:1,x:0,duration:.45,delay:i*.07,
    scrollTrigger:{{trigger:'.arch',start:'top 75%'}}
  }});
}});
gsap.utils.toArray('.pc').forEach((el,i)=>{{
  gsap.to(el,{{opacity:1,y:0,duration:.5,delay:i*.12,
    scrollTrigger:{{trigger:'.pg',start:'top 82%'}}
  }});
}});

/* Nav opacity on scroll */
const mnav = document.getElementById('mnav');
window.addEventListener('scroll',()=>{{
  mnav.style.background = scrollY>20 ? 'rgba(5,8,16,.97)' : 'rgba(5,8,16,.7)';
}});

/* FAQ */
function tfaq(btn){{
  const it=btn.closest('.fi');
  const op=it.classList.contains('open');
  document.querySelectorAll('.fi.open').forEach(e=>e.classList.remove('open'));
  if(!op) it.classList.add('open');
}}

/* Smooth anchor scrolling */
document.querySelectorAll('a[href^="#"]').forEach(a=>{{
  a.addEventListener('click',e=>{{
    const t=document.querySelector(a.getAttribute('href'));
    if(t){{e.preventDefault();t.scrollIntoView({{behavior:'smooth',block:'start'}})}}
  }});
}});
</script>
</body>
</html>"""


# ── Download page ──────────────────────────────────────────────────────────────

@router.get("/download", response_class=HTMLResponse)
async def download_page():
    """Public download page — linked from the in-app update banner."""
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute(
            "SELECT version, release_notes, download_url "
            "FROM versions WHERE is_current = TRUE "
            "ORDER BY published_at DESC LIMIT 1")
        row = cur.fetchone()
        cur.close(); conn.close()
        version    = row["version"]      if row else "2.5.0"
        notes      = row["release_notes"] if row else ""
        direct_url = (f"https://drive.google.com/uc?export=download&id={DRIVE_FILE_ID}"
                      if DRIVE_FILE_ID else "#")
    except Exception:
        version, notes, direct_url = "2.5.0", "", "#"

    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Download FMSecure v{version}</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:#0d1117;color:#e6edf3;font-family:system-ui,sans-serif;
       min-height:100vh;display:flex;flex-direction:column}}
  nav{{background:#161b22;border-bottom:1px solid #30363d;
       padding:16px 32px;display:flex;align-items:center;gap:16px}}
  .logo{{color:#2f81f7;font-size:20px;font-weight:700;text-decoration:none}}
  nav a{{color:#8b949e;text-decoration:none;font-size:14px}}
  nav a:hover{{color:#e6edf3}}
  .hero{{flex:1;display:flex;flex-direction:column;align-items:center;
         justify-content:center;padding:60px 24px;text-align:center}}
  .badge{{background:#238636;color:#fff;padding:4px 14px;border-radius:20px;
          font-size:13px;font-weight:600;display:inline-block;margin-bottom:20px}}
  h1{{font-size:42px;font-weight:800;margin-bottom:12px;
      background:linear-gradient(135deg,#2f81f7,#a371f7);
      -webkit-background-clip:text;-webkit-text-fill-color:transparent}}
  .subtitle{{color:#8b949e;font-size:18px;margin-bottom:36px}}
  .notes{{background:#161b22;border:1px solid #30363d;border-radius:8px;
          padding:16px 24px;margin-bottom:36px;font-size:14px;
          color:#8b949e;max-width:520px}}
  .notes strong{{color:#e6edf3}}
  .dl-btn{{background:#238636;color:#fff;padding:16px 48px;border-radius:8px;
           font-size:17px;font-weight:700;text-decoration:none;
           border:none;cursor:pointer;transition:background .2s}}
  .dl-btn:hover{{background:#2ea043}}
  .meta{{margin-top:20px;color:#484f58;font-size:13px}}
  .features{{display:flex;gap:24px;margin-top:60px;flex-wrap:wrap;
             justify-content:center;max-width:800px}}
  .feat{{background:#161b22;border:1px solid #30363d;border-radius:8px;
         padding:20px 24px;width:220px;text-align:left}}
  .feat .icon{{font-size:24px;margin-bottom:8px}}
  .feat h3{{font-size:14px;font-weight:600;margin-bottom:4px}}
  .feat p{{font-size:12px;color:#8b949e}}
  footer{{text-align:center;padding:24px;color:#484f58;font-size:13px;
          border-top:1px solid #21262d}}
</style>
</head><body>
<nav>
  <a class="logo" href="/" style="display:flex;align-items:center;gap:8px">
    <img src="/static/app_icon.png" alt="Logo" height="28">
    FMSecure
  </a>
  <a href="/">Home</a>
  <a href="/pricing">Pricing</a>
  <a href="/changelog">Changelog</a>
  <a href="/login" style="margin-left:auto;color:#2f81f7">Admin →</a>
</nav>
<div class="hero">
  <span class="badge">✅ Latest Release</span>
  <h1>Download FMSecure</h1>
  <p class="subtitle">Enterprise File Integrity & EDR Monitor for Windows</p>
  {"<div class='notes'><strong>What's new in v" + version + ":</strong><br>" + notes + "</div>" if notes else ""}
  <a class="dl-btn" href="{direct_url}">
    ⬇&nbsp; Download FMSecure v{version}
  </a>
  <p class="meta">Windows 10/11 · 64-bit · Free to try · PRO features require license</p>
  <div class="features">
    <div class="feat"><div class="icon">🛡️</div><h3>Active Defense</h3><p>Auto-restores tampered or deleted files from encrypted vault</p></div>
    <div class="feat"><div class="icon">☁️</div><h3>Cloud Backup</h3><p>Google Drive disaster recovery with full AppData sync</p></div>
    <div class="feat"><div class="icon">🛑</div><h3>Ransomware Killswitch</h3><p>OS-level folder lockdown on burst file operations</p></div>
    <div class="feat"><div class="icon">🔌</div><h3>USB Control</h3><p>Block unauthorized USB write access across the device</p></div>
  </div>
</div>
<footer>FMSecure · Enterprise EDR · © {datetime.now().year}</footer>
</body></html>"""


# ── Changelog page ─────────────────────────────────────────────────────────────

@router.get("/changelog", response_class=HTMLResponse)
async def changelog_page():
    """Public changelog page — linked from the in-app 'What's New' button."""
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute(
            "SELECT version, release_notes, published_at "
            "FROM versions ORDER BY published_at DESC LIMIT 20")
        rows = cur.fetchall()
        cur.close(); conn.close()
    except Exception:
        rows = []

    entries = ""
    for i, row in enumerate(rows):
        date  = row["published_at"].strftime("%B %d, %Y") if row["published_at"] else ""
        badge = (
            '<span style="background:#238636;color:#fff;padding:2px 10px;'
            'border-radius:12px;font-size:12px;font-weight:600">Latest</span>'
            if i == 0 else ""
        )
        entries += f"""
        <div style="border-left:3px solid {'#2f81f7' if i==0 else '#30363d'};
                    padding:0 0 32px 24px;margin-bottom:8px">
          <div style="display:flex;align-items:center;gap:12px;margin-bottom:8px">
            <span style="font-size:20px;font-weight:700;color:#e6edf3">
              v{row['version']}
            </span>
            {badge}
            <span style="color:#484f58;font-size:13px">{date}</span>
          </div>
          <p style="color:#8b949e;font-size:14px;line-height:1.6">
            {row['release_notes'] or 'No release notes provided.'}
          </p>
          <a href="/download"
             style="display:inline-block;margin-top:12px;background:#238636;
                    color:#fff;padding:6px 18px;border-radius:6px;
                    text-decoration:none;font-size:13px;font-weight:600">
            Download v{row['version']}
          </a>
        </div>"""

    if not entries:
        entries = '<p style="color:#8b949e">No releases published yet.</p>'

    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>FMSecure Changelog</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:#0d1117;color:#e6edf3;font-family:system-ui,sans-serif}}
  nav{{background:#161b22;border-bottom:1px solid #30363d;
       padding:16px 32px;display:flex;align-items:center;gap:16px}}
  .logo{{color:#2f81f7;font-size:20px;font-weight:700;text-decoration:none}}
  nav a{{color:#8b949e;text-decoration:none;font-size:14px}}
  nav a:hover{{color:#e6edf3}}
  .container{{max-width:720px;margin:48px auto;padding:0 24px}}
  h1{{font-size:32px;font-weight:800;margin-bottom:6px}}
  .sub{{color:#8b949e;font-size:15px;margin-bottom:40px}}
  footer{{text-align:center;padding:40px 24px;color:#484f58;font-size:13px;
          border-top:1px solid #21262d;margin-top:40px}}
</style>
</head><body>
<nav>
  <a class="logo" href="/" style="display:flex;align-items:center;gap:8px">
    <img src="/static/app_icon.png" alt="Logo" height="28">
    FMSecure
  </a>
  <a href="/">Home</a>
  <a href="/download">Download</a>
  <a href="/login" style="margin-left:auto;color:#2f81f7">Admin →</a>
</nav>
<div class="container">
  <h1>Changelog</h1>
  <p class="sub">Every release, every improvement — all in one place.</p>
  {entries}
</div>
<footer>FMSecure · Enterprise EDR · © {datetime.now().year}</footer>
</body></html>"""


# ── Pricing page ───────────────────────────────────────────────────────────────

@router.get("/pricing", response_class=HTMLResponse)
async def pricing_page():
    base   = APP_BASE_URL
    rzpkey = RZP_KEY_ID
    return f"""<!DOCTYPE html><html><head><title>FMSecure PRO — Pricing</title>
    <script src="https://checkout.razorpay.com/v1/checkout.js"></script>
    <style>
      *{{box-sizing:border-box;margin:0;padding:0}}
      body{{background:#0d1117;color:#e6edf3;font-family:system-ui,sans-serif;min-height:100vh}}
      nav{{background:#161b22;border-bottom:1px solid #30363d;padding:16px 48px;
           display:flex;justify-content:space-between;align-items:center}}
      .brand{{color:#2f81f7;font-weight:700;font-size:20px;text-decoration:none}}
      .back{{color:#8b949e;text-decoration:none;font-size:14px}}
      .back:hover{{color:#e6edf3}}
      main{{max-width:900px;margin:0 auto;padding:64px 24px}}
      h1{{text-align:center;font-size:36px;font-weight:700;margin-bottom:12px}}
      .sub{{text-align:center;color:#8b949e;font-size:16px;margin-bottom:56px}}
      .cards{{display:flex;gap:24px;justify-content:center;flex-wrap:wrap}}
      .card{{background:#161b22;border:1px solid #30363d;border-radius:16px;
             padding:36px 32px;width:340px;position:relative}}
      .card.featured{{border-color:#2f81f7}}
      .badge{{position:absolute;top:-13px;left:50%;transform:translateX(-50%);
              background:#2f81f7;color:#fff;padding:4px 16px;border-radius:20px;
              font-size:12px;font-weight:600;white-space:nowrap}}
      .plan{{color:#8b949e;font-size:12px;font-weight:600;
             letter-spacing:.5px;margin-bottom:8px}}
      .price{{font-size:42px;font-weight:700;margin-bottom:4px}}
      .price span{{font-size:18px;color:#8b949e;font-weight:400}}
      .period{{color:#8b949e;font-size:14px;margin-bottom:28px}}
      .savings{{color:#3fb950}}
      .email-row{{margin-bottom:16px}}
      .email-row label{{display:block;font-size:11px;color:#8b949e;
                        font-weight:600;letter-spacing:.5px;margin-bottom:6px}}
      .email-row input{{width:100%;background:#0d1117;border:1px solid #30363d;
                        border-radius:6px;color:#e6edf3;padding:10px 12px;
                        font-size:14px;outline:none}}
      .email-row input:focus{{border-color:#2f81f7}}
      ul{{list-style:none;margin-bottom:28px}}
      li{{padding:8px 0;font-size:14px;border-bottom:1px solid #21262d;color:#8b949e}}
      li:last-child{{border-bottom:none}}
      li strong{{color:#e6edf3}}
      .check{{color:#3fb950;margin-right:8px;font-weight:700}}
      .btn{{width:100%;padding:14px;border:none;border-radius:8px;font-size:15px;
            font-weight:600;cursor:pointer;transition:opacity .15s}}
      .btn:hover{{opacity:.85}}
      .btn-blue{{background:#2f81f7;color:#fff}}
      .btn-green{{background:#238636;color:#fff}}
      .note{{text-align:center;color:#484f58;font-size:13px;margin-top:36px;line-height:1.7}}
      footer{{text-align:center;color:#484f58;font-size:13px;padding:48px 24px}}
    </style></head><body>
    <nav>
      <a class="brand" href="/home">FMSecure</a>
      <a class="back" href="/home">&#x2190; Back to home</a>
    </nav>
    <main>
      <h1>Simple, transparent pricing</h1>
      <p class="sub">No hidden fees. Cancel anytime. License key emailed instantly after payment.</p>
      <div class="cards">
        <div class="card">
          <p class="plan">PRO MONTHLY</p>
          <div class="price">&#x20B9;499<span>/mo</span></div>
          <p class="period">Billed monthly, cancel anytime</p>
          <div class="email-row">
            <label>EMAIL — KEY WILL BE SENT HERE</label>
            <input type="email" id="email-monthly" placeholder="you@example.com">
          </div>
          <ul>
            <li><span class="check">&#10003;</span><strong>5 folders</strong> monitored</li>
            <li><span class="check">&#10003;</span><strong>Active Defense</strong> + auto-heal vault</li>
            <li><span class="check">&#10003;</span><strong>Ransomware killswitch</strong></li>
            <li><span class="check">&#10003;</span><strong>USB DLP</strong> device control</li>
            <li><span class="check">&#10003;</span><strong>Google Drive</strong> cloud backup</li>
            <li><span class="check">&#10003;</span><strong>Forensic vault</strong> + snapshots</li>
            <li><span class="check">&#10003;</span>Email security alerts</li>
          </ul>
          <button class="btn btn-blue" onclick="startPayment('pro_monthly')">
            Buy Monthly &#x2014; &#x20B9;499
          </button>
        </div>
        <div class="card featured">
          <div class="badge">BEST VALUE &#x2014; SAVE &#x20B9;1,989</div>
          <p class="plan">PRO ANNUAL</p>
          <div class="price">&#x20B9;4,999<span>/yr</span></div>
          <p class="period">&#x20B9;416/mo billed annually <span class="savings">&#x2714; 2 months free</span></p>
          <div class="email-row">
            <label>EMAIL — KEY WILL BE SENT HERE</label>
            <input type="email" id="email-annual" placeholder="you@example.com">
          </div>
          <ul>
            <li><span class="check">&#10003;</span><strong>Everything</strong> in Monthly</li>
            <li><span class="check">&#10003;</span><strong>Priority</strong> email support</li>
            <li><span class="check">&#10003;</span><strong>Early access</strong> to new features</li>
            <li><span class="check">&#10003;</span>Invoice for business use</li>
            <li><span class="check">&#10003;</span>Extended offline grace period</li>
            <li><span class="check">&#10003;</span>2 months free vs monthly</li>
            <li><span class="check">&#10003;</span>Feature request priority</li>
          </ul>
          <button class="btn btn-green" onclick="startPayment('pro_annual')">
            Buy Annual &#x2014; &#x20B9;4,999
          </button>
        </div>
      </div>
      <p class="note">
        Payments secured by Razorpay &bull; UPI, Net Banking, Cards, Wallets accepted<br>
        One license per device &bull; Transfer to new device on request
      </p>
    </main>
    <footer>FMSecure v2.0 &bull; Enterprise Endpoint Detection &amp; Response &bull; Made in India</footer>
    <script>
    async function startPayment(tier) {{
      const eid   = tier === 'pro_monthly' ? 'email-monthly' : 'email-annual';
      const email = document.getElementById(eid).value.trim();
      if (!email || !email.includes('@') || !email.includes('.')) {{
        alert('Please enter a valid email.\\nYour license key will be sent there.');
        document.getElementById(eid).focus();
        return;
      }}
      let od;
      try {{
        const r = await fetch('{base}/payment/create-order', {{
          method: 'POST',
          headers: {{'Content-Type': 'application/json'}},
          body: JSON.stringify({{tier, email}})
        }});
        od = await r.json();
      }} catch(e) {{
        alert('Could not reach payment server. Please try again.');
        return;
      }}
      if (od.error) {{ alert('Error: ' + od.error); return; }}
      new Razorpay({{
        key: '{rzpkey}',
        amount: od.amount,
        currency: od.currency,
        name: 'FMSecure',
        description: od.description,
        order_id: od.order_id,
        prefill: {{email}},
        theme: {{color: '#2f81f7'}},
        handler: async function(res) {{
          let result;
          try {{
            const vr = await fetch('{base}/payment/verify', {{
              method: 'POST',
              headers: {{'Content-Type': 'application/json'}},
              body: JSON.stringify({{
                razorpay_order_id:  res.razorpay_order_id,
                razorpay_payment_id: res.razorpay_payment_id,
                razorpay_signature:  res.razorpay_signature,
                email,
                tier
              }})
            }});
            result = await vr.json();
          }} catch(e) {{
            alert('Verification error. Contact support. Payment ID: ' + res.razorpay_payment_id);
            return;
          }}
          if (result.success) {{
            window.location.href = '{base}/payment/success?key='
              + encodeURIComponent(result.license_key)
              + '&email=' + encodeURIComponent(email)
              + '&tier='  + encodeURIComponent(tier);
          }} else {{
            alert('Payment verification failed.\\nPayment ID: ' + res.razorpay_payment_id);
          }}
        }},
        modal: {{ondismiss: function(){{}}}}
      }}).open();
    }}
    </script>
    </body></html>"""
