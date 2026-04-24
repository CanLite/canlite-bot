import re


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def titleize(value: str) -> str:
    return " ".join(part.capitalize() for part in value.replace("_", "-").split("-") if part)


def parse_tags(raw_tags: str | None) -> list[str]:
    if not raw_tags:
        return []
    return [slugify(tag) for tag in raw_tags.split(",") if slugify(tag)]


def parse_identifier_to_discord_id(identifier: str) -> str | None:
    raw = identifier.strip()
    mention_match = re.fullmatch(r"<@!?(\d+)>", raw)
    if mention_match:
        return mention_match.group(1)
    if raw.isdigit():
        return raw
    return None
