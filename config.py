"""
Cloud Worker Configuration.
Loads credentials and preferences for 24/7 cloud email monitoring and notifications.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv()
load_dotenv(dotenv_path=BASE_DIR / ".env")
load_dotenv(dotenv_path=BASE_DIR.parent / ".env")

# AI Credentials
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

# Telegram Credentials
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_ADMIN_CHAT_ID = os.getenv("TELEGRAM_ADMIN_CHAT_ID", "").strip()

# Email Credentials (Set via .env)
EMAIL_USER = os.getenv("EMAIL_USER", "").strip()
EMAIL_PASS = os.getenv("EMAIL_PASS", "").strip()  # App Password
EMAIL_PROVIDER = os.getenv("EMAIL_PROVIDER", "gmail").lower().strip()
EMAIL_IMAP_SERVER = os.getenv("EMAIL_IMAP_SERVER", "").strip()
EMAIL_IMAP_PORT = int(os.getenv("EMAIL_IMAP_PORT", "993"))

# Operational Settings
CHECK_INTERVAL_SECONDS = int(os.getenv("EMAIL_CHECK_INTERVAL_SECONDS", "600"))  # 10 minutes
EMAIL_DRY_RUN = os.getenv("EMAIL_DRY_RUN", "true").lower() in ("true", "1", "yes")  # Safe mode by default
REQUIRE_CONFIRMATION = os.getenv("EMAIL_REQUIRE_CONFIRMATION", "true").lower() in ("true", "1", "yes")

# T-Invest (Tinkoff Investments) Credentials & Security
TINKOFF_INVEST_TOKEN = os.getenv("TINKOFF_INVEST_TOKEN", "").strip()
TINKOFF_ACCOUNT_ID = os.getenv("TINKOFF_ACCOUNT_ID", "").strip()
TINKOFF_READ_ONLY_STRICT = os.getenv("TINKOFF_READ_ONLY_STRICT", "true").lower() in ("true", "1", "yes")

# Known IMAP presets
IMAP_PRESETS = {
    "gmail": "imap.gmail.com",
    "yandex": "imap.yandex.ru",
    "mailru": "imap.mail.ru",
    "mail.ru": "imap.mail.ru",
    "outlook": "outlook.office365.com",
    "hotmail": "outlook.office365.com",
}

if not EMAIL_IMAP_SERVER and EMAIL_PROVIDER in IMAP_PRESETS:
    EMAIL_IMAP_SERVER = IMAP_PRESETS[EMAIL_PROVIDER]
