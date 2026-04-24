import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


BASE_DIR = Path(__file__).resolve().parent.parent
DISCORD_TOKEN = require_env("DISCORD_TOKEN")
DATABASE_URL = require_env("DATABASE_URL")
CANLITE_ACCOUNT_URL = os.getenv("CANLITE_ACCOUNT_URL", "https://canlite.org/account").strip()
DISCORD_GUILD_ID = os.getenv("DISCORD_GUILD_ID", "").strip()
DATABASE_SSL = os.getenv("DATABASE_SSL", "false").lower() == "true"
LINKED_ROLE_ID = int(os.getenv("LINKED_ROLE_ID", "1497028527273541722"))

CATALOG_PATH = BASE_DIR / "proxy-sites.json"
XP_PATH = BASE_DIR / "xp-data.json"

XP_COOLDOWN_SECONDS = 45
XP_MIN_GAIN = 15
XP_MAX_GAIN = 25
LEVEL_UP_CREDIT_REWARD = 1
FIRST_LINK_CREDIT_REWARD = 5
