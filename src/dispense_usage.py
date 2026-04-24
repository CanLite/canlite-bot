import json
from typing import Any

from .config import DISPENSE_LIMIT, DISPENSE_USAGE_PATH


def ensure_dispense_usage_store() -> None:
    if not DISPENSE_USAGE_PATH.exists():
        DISPENSE_USAGE_PATH.write_text("{}\n", encoding="utf-8")


def load_dispense_usage_store() -> dict[str, Any]:
    ensure_dispense_usage_store()
    return json.loads(DISPENSE_USAGE_PATH.read_text(encoding="utf-8"))


def save_dispense_usage_store(payload: dict[str, Any]) -> None:
    DISPENSE_USAGE_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def get_usage_count(guild_id: int | None, user_id: int) -> int:
    payload = load_dispense_usage_store()
    guild_bucket = payload.get(str(guild_id or 0), {})
    return int(guild_bucket.get(str(user_id), 0))


def get_remaining_count(guild_id: int | None, user_id: int) -> int:
    return max(DISPENSE_LIMIT - get_usage_count(guild_id, user_id), 0)


def record_dispense(guild_id: int | None, user_id: int) -> dict[str, int]:
    payload = load_dispense_usage_store()
    guild_key = str(guild_id or 0)
    user_key = str(user_id)
    guild_bucket = payload.setdefault(guild_key, {})
    guild_bucket[user_key] = int(guild_bucket.get(user_key, 0)) + 1
    save_dispense_usage_store(payload)
    used = int(guild_bucket[user_key])
    return {"used": used, "remaining": max(DISPENSE_LIMIT - used, 0)}


def reset_user_dispense(guild_id: int | None, user_id: int) -> bool:
    payload = load_dispense_usage_store()
    guild_bucket = payload.get(str(guild_id or 0), {})
    removed = str(user_id) in guild_bucket
    guild_bucket.pop(str(user_id), None)
    if str(guild_id or 0) in payload:
        payload[str(guild_id or 0)] = guild_bucket
    save_dispense_usage_store(payload)
    return removed


def reset_guild_dispense(guild_id: int | None) -> int:
    payload = load_dispense_usage_store()
    guild_key = str(guild_id or 0)
    guild_bucket = payload.get(guild_key, {})
    affected = len(guild_bucket)
    payload[guild_key] = {}
    save_dispense_usage_store(payload)
    return affected
