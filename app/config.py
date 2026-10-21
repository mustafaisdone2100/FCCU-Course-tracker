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

