import json
import random
from typing import Any

from .config import XP_COOLDOWN_SECONDS, XP_MAX_GAIN, XP_MIN_GAIN, XP_PATH


def ensure_xp_store() -> None:
    if not XP_PATH.exists():
        XP_PATH.write_text("{}\n", encoding="utf-8")


def load_xp_store() -> dict[str, Any]:
    ensure_xp_store()
    return json.loads(XP_PATH.read_text(encoding="utf-8"))


def save_xp_store(payload: dict[str, Any]) -> None:
    XP_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def xp_needed_for_level(level: int) -> int:
    return 100 * level * level


def level_from_xp(xp: int) -> int:
    level = 0
    while xp >= xp_needed_for_level(level + 1):
        level += 1
    return level


def apply_message_xp(store: dict[str, Any], guild_id: int, user_id: int) -> dict[str, Any]:
    guild_bucket = store.setdefault(str(guild_id), {})
    user_bucket = guild_bucket.setdefault(str(user_id), {"xp": 0, "level": 0, "messages": 0})

    previous_level = int(user_bucket.get("level", 0))
    gained = random.randint(XP_MIN_GAIN, XP_MAX_GAIN)
    user_bucket["xp"] = int(user_bucket.get("xp", 0)) + gained
    user_bucket["messages"] = int(user_bucket.get("messages", 0)) + 1
    user_bucket["level"] = level_from_xp(user_bucket["xp"])

    return {
        "previous_level": previous_level,
        "current_level": int(user_bucket["level"]),
        "xp": int(user_bucket["xp"]),
        "messages": int(user_bucket["messages"]),
        "cooldown_seconds": XP_COOLDOWN_SECONDS,
    }
