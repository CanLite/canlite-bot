import json

import asyncpg
import discord

from .config import CANLITE_ACCOUNT_URL, DATABASE_SSL, DATABASE_URL, FIRST_LINK_CREDIT_REWARD, LEVEL_UP_CREDIT_REWARD
from .utils import parse_identifier_to_discord_id


def parse_credit_balance(raw_data) -> float:
    payload = raw_data or "{}"
    if isinstance(payload, str):
        parsed = json.loads(payload or "{}")
    else:
        parsed = dict(payload)
    return round(float(parsed.get("credits", 0)), 2)


def serialize_credit_balance(raw_data, credits: float) -> str:
    payload = raw_data or "{}"
    if isinstance(payload, str):
        parsed = json.loads(payload or "{}")
    else:
        parsed = dict(payload)
    parsed["credits"] = round(float(credits), 2)
    return json.dumps(parsed)


async def create_pool() -> asyncpg.Pool:
    return await asyncpg.create_pool(DATABASE_URL, ssl=True if DATABASE_SSL else None)


async def claim_link_code(
    pool: asyncpg.Pool,
    code: str,
    discord_user_id: int,
    discord_username: str,
    discord_global_name: str | None,
) -> tuple[bool, dict | str]:
    normalized_code = (code or "").strip().upper()
    if not normalized_code:
        return False, "Enter a valid CanLite link code."

    async with pool.acquire() as conn:
        async with conn.transaction():
            code_row = await conn.fetchrow(
                """
                SELECT code, user_id, expires_at, claimed_at
                FROM discord_link_codes
                WHERE code = $1
                FOR UPDATE
                """,
                normalized_code,
            )
            if not code_row:
                return False, "That CanLite link code does not exist."
            if code_row["claimed_at"] is not None:
                return False, "That CanLite link code has already been used."
            if code_row["expires_at"] <= discord.utils.utcnow():
                return False, "That CanLite link code has expired."

            existing_link = await conn.fetchrow(
                """
                SELECT user_id
                FROM discord_account_links
                WHERE discord_user_id = $1
                FOR UPDATE
                """,
                str(discord_user_id),
            )
            if existing_link and int(existing_link["user_id"]) != int(code_row["user_id"]):
                return False, "Your Discord account is already linked to another CanLite account."

            existing_user_link = await conn.fetchrow(
                """
                SELECT discord_user_id
                FROM discord_account_links
                WHERE user_id = $1
                FOR UPDATE
                """,
                code_row["user_id"],
            )
            first_link_for_account = existing_user_link is None

            await conn.execute(
                """
                INSERT INTO discord_account_links (
                    user_id,
                    discord_user_id,
                    discord_username,
                    discord_global_name,
                    linked_at,
                    updated_at
                )
                VALUES ($1, $2, $3, $4, NOW(), NOW())
                ON CONFLICT (user_id) DO UPDATE
                SET discord_user_id = EXCLUDED.discord_user_id,
                    discord_username = EXCLUDED.discord_username,
                    discord_global_name = EXCLUDED.discord_global_name,
                    updated_at = NOW()
                """,
                code_row["user_id"],
                str(discord_user_id),
                discord_username,
                discord_global_name,
            )

            await conn.execute(
                """
                UPDATE discord_link_codes
                SET claimed_at = NOW()
                WHERE code = $1
                """,
                normalized_code,
            )

            await conn.execute(
                """
                DELETE FROM discord_link_codes
                WHERE user_id = $1
                  AND code <> $2
                """,
                code_row["user_id"],
                normalized_code,
            )

            reward_granted = False
            reward_balance = None
            if first_link_for_account:
                user_row = await conn.fetchrow(
                    """
                    SELECT data
                    FROM users
                    WHERE id = $1
                    FOR UPDATE
                    """,
                    code_row["user_id"],
                )
                if user_row:
                    current_credits = parse_credit_balance(user_row["data"])
                    new_credits = round(current_credits + FIRST_LINK_CREDIT_REWARD, 2)
                    await conn.execute(
                        """
                        UPDATE users
                        SET data = $1
                        WHERE id = $2
                        """,
                        serialize_credit_balance(user_row["data"], new_credits),
                        code_row["user_id"],
                    )
                    reward_granted = True
                    reward_balance = new_credits

            return True, {
                "user_id": str(code_row["user_id"]),
                "reward_granted": reward_granted,
                "reward_balance": reward_balance,
            }


async def get_linked_canlite_user_id(pool: asyncpg.Pool, discord_user_id: int) -> int | None:
    row = await pool.fetchrow(
        """
        SELECT user_id
        FROM discord_account_links
        WHERE discord_user_id = $1
        """,
        str(discord_user_id),
    )
    return int(row["user_id"]) if row else None


async def get_credit_balance_for_discord_user(pool: asyncpg.Pool, discord_user_id: int) -> tuple[bool, float | None]:
    user_id = await get_linked_canlite_user_id(pool, discord_user_id)
    if user_id is None:
        return False, None

    row = await pool.fetchrow(
        """
        SELECT data
        FROM users
        WHERE id = $1
        """,
        user_id,
    )
    if not row:
        return False, None

    return True, parse_credit_balance(row["data"])


async def resolve_user_identifier(conn: asyncpg.Connection, identifier: str) -> asyncpg.Record | None:
    normalized = identifier.strip()
    if not normalized:
        return None

    normalized_email = normalized.lower()
    discord_id = parse_identifier_to_discord_id(normalized)
    discord_name = normalized.lower().removeprefix("@")

    return await conn.fetchrow(
        """
        SELECT users.id,
               users.email,
               discord_account_links.discord_user_id,
               discord_account_links.discord_username,
               discord_account_links.discord_global_name
        FROM users
        LEFT JOIN discord_account_links ON discord_account_links.user_id = users.id
        WHERE lower(users.email) = $1
           OR discord_account_links.discord_user_id = $2
           OR lower(coalesce(discord_account_links.discord_username, '')) = $3
           OR lower(coalesce(discord_account_links.discord_global_name, '')) = $3
        ORDER BY CASE
            WHEN lower(users.email) = $1 THEN 0
            WHEN discord_account_links.discord_user_id = $2 THEN 1
            WHEN lower(coalesce(discord_account_links.discord_username, '')) = $3 THEN 2
            WHEN lower(coalesce(discord_account_links.discord_global_name, '')) = $3 THEN 3
            ELSE 4
        END
        LIMIT 1
        """,
        normalized_email,
        discord_id,
        discord_name,
    )


async def add_private_link_member(
    pool: asyncpg.Pool,
    owner_discord_user_id: int,
    domain: str,
    identifier: str,
) -> tuple[bool, dict | str]:
    owner_user_id = await get_linked_canlite_user_id(pool, owner_discord_user_id)
    if owner_user_id is None:
        return False, f"Link your Discord account first from {CANLITE_ACCOUNT_URL}."

    async with pool.acquire() as conn:
        async with conn.transaction():
            link = await conn.fetchrow(
                """
                SELECT id,
                       domain,
                       cover_url,
                       login_path,
                       monthly_cost_credits
                FROM private_links
                WHERE owner_user_id = $1
                  AND lower(domain) = $2
                FOR UPDATE
                """,
                owner_user_id,
                domain.strip().lower(),
            )
            if not link:
                return False, "That private link was not found on your account."

            target_user = await resolve_user_identifier(conn, identifier)
            if not target_user:
                return False, "No CanLite account matched that email or linked Discord."
            if int(target_user["id"]) == owner_user_id:
                return False, "You already own that private link."

            member_count = await conn.fetchval(
                "SELECT COUNT(*) FROM private_link_members WHERE link_id = $1",
                link["id"],
            )
            if int(member_count) >= 19:
                return False, "That private link already has the maximum number of invited users."

            existing_member = await conn.fetchval(
                """
                SELECT 1
                FROM private_link_members
                WHERE link_id = $1 AND user_id = $2
                """,
                link["id"],
                target_user["id"],
            )
            if existing_member:
                return False, "That account already has access."

            await conn.execute(
                """
                INSERT INTO private_link_members (link_id, user_id, invited_by_user_id)
                VALUES ($1, $2, $3)
                """,
                link["id"],
                target_user["id"],
                owner_user_id,
            )
            return True, {
                "message": f"Added {target_user['email']} to {link['domain']}.",
                "target_email": str(target_user["email"]),
                "target_discord_user_id": str(target_user["discord_user_id"] or "").strip() or None,
                "link_domain": str(link["domain"]),
                "cover_url": str(link["cover_url"]),
                "login_path": str(link["login_path"]),
                "monthly_cost_credits": float(link["monthly_cost_credits"]),
            }


async def remove_private_link_member(
    pool: asyncpg.Pool,
    owner_discord_user_id: int,
    domain: str,
    identifier: str,
) -> tuple[bool, str]:
    owner_user_id = await get_linked_canlite_user_id(pool, owner_discord_user_id)
    if owner_user_id is None:
        return False, f"Link your Discord account first from {CANLITE_ACCOUNT_URL}."

    async with pool.acquire() as conn:
        async with conn.transaction():
            link = await conn.fetchrow(
                """
                SELECT id, domain
                FROM private_links
                WHERE owner_user_id = $1
                  AND lower(domain) = $2
                FOR UPDATE
                """,
                owner_user_id,
                domain.strip().lower(),
            )
            if not link:
                return False, "That private link was not found on your account."

            target_user = await resolve_user_identifier(conn, identifier)
            if not target_user:
                return False, "No CanLite account matched that email or linked Discord."

            deleted = await conn.execute(
                """
                DELETE FROM private_link_members
                WHERE link_id = $1 AND user_id = $2
                """,
                link["id"],
                target_user["id"],
            )
            if deleted.endswith("0"):
                return False, "That account did not have access to the private link."

            return True, f"Removed {target_user['email']} from {link['domain']}."


async def list_private_links_for_owner(pool: asyncpg.Pool, owner_discord_user_id: int) -> tuple[bool, list[dict] | str]:
    owner_user_id = await get_linked_canlite_user_id(pool, owner_discord_user_id)
    if owner_user_id is None:
        return False, f"Link your Discord account first from {CANLITE_ACCOUNT_URL}."

    rows = await pool.fetch(
        """
        SELECT private_links.domain,
               COUNT(private_link_members.user_id) AS member_count
        FROM private_links
        LEFT JOIN private_link_members ON private_link_members.link_id = private_links.id
        WHERE private_links.owner_user_id = $1
        GROUP BY private_links.id, private_links.domain
        ORDER BY lower(private_links.domain)
        """,
        owner_user_id,
    )
    return True, [
        {
            "domain": str(row["domain"]),
            "member_count": int(row["member_count"] or 0),
        }
        for row in rows
    ]


async def grant_level_up_credit(pool: asyncpg.Pool, discord_user_id: int) -> tuple[bool, float | None]:
    user_id = await get_linked_canlite_user_id(pool, discord_user_id)
    if user_id is None:
        return False, None

    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                SELECT data
                FROM users
                WHERE id = $1
                FOR UPDATE
                """,
                user_id,
            )
            if not row:
                return False, None

            current_credits = parse_credit_balance(row["data"])
            new_credits = round(current_credits + LEVEL_UP_CREDIT_REWARD, 2)

            await conn.execute(
                """
                UPDATE users
                SET data = $1
                WHERE id = $2
                """,
                serialize_credit_balance(row["data"], new_credits),
                user_id,
            )
            return True, new_credits
