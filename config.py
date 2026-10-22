import os
from dotenv import load_dotenv

load_dotenv()


def _list(name):
    return [x.strip() for x in os.getenv(name, "").split(",") if x.strip()]


TG_TOKEN = os.getenv("TG_BOT_TOKEN", "")
WA_ENABLED = os.getenv("WA_ENABLED", "1") == "1"

PANEL_MODE = os.getenv("PANEL_MODE", "mock")          # mock | real
PANEL_URL = os.getenv("PANEL_URL", "").rstrip("/")
PANEL_ADMIN_KEY = os.getenv("PANEL_ADMIN_KEY", "")

STAFF_TARGETS = _list("STAFF_TARGETS")                 # e.g. wa:9199..., tg:1234

SECRET = os.getenv("SECRET", "change-me")
DB_PATH = os.getenv("DB_PATH", "bot.db")
PROVIDERS_FILE = os.getenv("PROVIDERS_FILE", "providers.json")

OTP_TTL = 600            # seconds the code stays valid
MAX_TRIES = 3            # wrong username / wrong code tries
LOCK_SECONDS = 600       # lock after too many wrong tries
DUP_WINDOW = 6 * 3600    # same order + same action is blocked for 6 hours

GROUP_COOLDOWN = int(os.getenv("GROUP_COOLDOWN", "3"))   # seconds between commands per group
USER_MAX_PER_MIN = int(os.getenv("USER_MAX_PER_MIN", "6"))  # commands per person per minute
WA_SEND_GAP = 1.5        # seconds between WhatsApp messages (safer for the number)
TG_SEND_GAP = 0.4

# Telegram helper account (talks to provider bots such as Persian's)
TGU_API_ID = os.getenv("TGU_API_ID", "")
TGU_API_HASH = os.getenv("TGU_API_HASH", "")
TGU_SESSION = os.getenv("TGU_SESSION", "tg_user")
