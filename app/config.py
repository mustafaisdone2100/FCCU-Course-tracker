import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# On Vercel serverless, filesystem outside /tmp is read-only
if os.environ.get("VERCEL"):
    DATA_DIR = Path("/tmp")
    DB_PATH = Path("/tmp/tracker.db")
else:
    DATA_DIR = BASE_DIR / "data"
    DATA_DIR.mkdir(exist_ok=True)
    DB_PATH = DATA_DIR / "tracker.db"

# Empower SIS Settings
EMPOWER_BASE_URL = "https://mysis-fccollege.empower-xl.com"
EMPOWER_CATALOG_URL = f"{EMPOWER_BASE_URL}/fusebox.cfm?fuseaction=CourseCatalog&rpt=1"
EMPOWER_GET_LIST_URL = f"{EMPOWER_BASE_URL}/cfcs/courseCatalog.cfc?method=GetList"

DEFAULT_TERM = "2027SP"
SUPPORTED_TERMS = [
    {"id": "2027SP", "name": "Spring 2027 (Undergraduate)", "is_primary": True},
    {"id": "2026FA", "name": "Fall 2026 (Test Mode)", "is_primary": False},
]

# Polling configuration
DEFAULT_POLL_INTERVAL_SECONDS = 30
REQUEST_TIMEOUT_SECONDS = 35

# Server config
SERVER_HOST = os.environ.get("HOST", "0.0.0.0" if os.environ.get("PORT") else "127.0.0.1")
SERVER_PORT = int(os.environ.get("PORT", "8000"))

# External Notifications (Mobile alerts when computer is closed)
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# Email Alert Configuration (Gmail SMTP, Resend API, Brevo API)
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com").strip()
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587").strip()) if os.environ.get("SMTP_PORT") else 587
SMTP_USER = os.environ.get("SMTP_USER", "").strip()
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "").replace(" ", "").strip()
SMTP_FROM_NAME = os.environ.get("SMTP_FROM_NAME", "FCCU Course Tracker").strip()
RESEND_API_KEY = (
    os.environ.get("RESEND_API_KEY")
    or os.environ.get("resend_api_key")
    or os.environ.get("RESEND_KEY")
    or os.environ.get("RESEND_TOKEN")
    or os.environ.get("RESEND_API_TOKEN")
    or ""
).strip().strip("'\"")
RESEND_FROM_EMAIL = (os.environ.get("RESEND_FROM_EMAIL") or "onboarding@resend.dev").strip().strip("'\"")
BREVO_API_KEY = (
    os.environ.get("BREVO_API_KEY")
    or os.environ.get("brevo_api_key")
    or os.environ.get("BRAVO_API_KEY")
    or os.environ.get("bravo_api_key")
    or os.environ.get("BREVO_KEY")
    or os.environ.get("BRAVO_KEY")
    or os.environ.get("BREVO_TOKEN")
    or os.environ.get("BRAVO_TOKEN")
    or os.environ.get("BREVO_API_TOKEN")
    or os.environ.get("BRAVO_API_TOKEN")
    or ""
).strip().strip("'\"")
BREVO_SENDER_EMAIL = (
    os.environ.get("BREVO_SENDER_EMAIL")
    or os.environ.get("BRAVO_SENDER_EMAIL")
    or os.environ.get("brevo_sender_email")
    or os.environ.get("bravo_sender_email")
    or ""
).strip().strip("'\"") or SMTP_USER
IS_RENDER = bool(os.environ.get("RENDER"))

# Auto-detect if user entered Resend API key into SMTP_PASSWORD field
if not RESEND_API_KEY and SMTP_PASSWORD.startswith("re_"):
    RESEND_API_KEY = SMTP_PASSWORD
    SMTP_PASSWORD = ""

# Auto-detect if user entered Brevo API key into SMTP_PASSWORD field
if not BREVO_API_KEY and SMTP_PASSWORD.startswith("xkeysib-"):
    BREVO_API_KEY = SMTP_PASSWORD
    SMTP_PASSWORD = ""





