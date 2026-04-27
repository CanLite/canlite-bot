from __future__ import annotations

import time

import discord
from discord import app_commands
from discord.ext import commands

from .catalog import catalog_store
from .config import CANLITE_ACCOUNT_URL, DISCORD_GUILD_ID, DISCORD_TOKEN, LEVEL_UP_CREDIT_REWARD, LINKED_ROLE_ID
from .database import (
    add_private_link_member,
    claim_link_code,
    create_pool,
    create_route_pool,
    get_credit_balance_for_discord_user,
    list_private_links_for_owner,
    grant_level_up_credit,
    remove_private_link_member,
    save_private_link_for_owner,
)
from .dispense_usage import ensure_dispense_usage_store, reset_guild_dispense, reset_user_dispense
from .models import SiteEntry
from .utils import parse_tags, slugify
from .views import SiteDispenserView, build_dispenser_embed
from .xp import XP_COOLDOWN_SECONDS, apply_message_xp, ensure_xp_store, load_xp_store, save_xp_store, xp_needed_for_level


def is_catalog_admin(interaction: discord.Interaction) -> bool:
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        return False
    permissions = interaction.user.guild_permissions
    return permissions.administrator or permissions.manage_guild


intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
bot.db_pool = None
bot.route_db_pool = None
bot.xp_cooldowns: dict[str, float] = {}


def build_private_link_dm_embed(result: dict[str, object]) -> discord.Embed:
    domain = str(result.get("link_domain") or "Private Link")
    embed = discord.Embed(
        title="Private Link Access Granted",
        description=f"You were added to `{domain}`.",
        color=discord.Color.blue(),
    )
    embed.add_field(name="Domain", value="https://" + str(domain), inline=False)
    embed.add_field(name="Cover Site", value=str(result.get("cover_url") or "Not set"), inline=False)
    embed.add_field(name="Login Path", value="https://" + str(domain) + str(result.get("login_path") or "Not set"), inline=False)
    embed.add_field(name="Cost", value=f"{float(result.get('monthly_cost_credits') or 0):.2f} credits/month", inline=False)
    return embed


def build_private_link_owner_dm_embed(result: dict[str, object]) -> discord.Embed:
    domain = str(result.get("domain") or result.get("url") or "").strip()
    login_path = str(result.get("login_path") or "").strip()
    cover_url = str(result.get("cover_url") or "").strip()
    action = str(result.get("action") or "").strip().title() or "Saved"
    embed = discord.Embed(
        title="Your Private Link Is Ready",
        description=f"{action} your private link setup.",
        color=discord.Color.green(),
    )
    if domain:
        embed.add_field(name="Private Domain", value=f"https://{domain}", inline=False)
    if login_path and domain:
        embed.add_field(name="Login URL", value=f"https://{domain}{login_path}", inline=False)
    if cover_url:
        embed.add_field(name="Cover Site", value=cover_url, inline=False)
    embed.add_field(
        name="How It Works",
        value="People see the cover site first. You use the login URL when you want the private CanLite page.",
        inline=False,
    )
    return embed


async def try_send_private_link_dm(result: dict[str, object]) -> str | None:
    target_discord_user_id = result.get("target_discord_user_id")
    if not target_discord_user_id:
        return None

    try:
        user = bot.get_user(int(target_discord_user_id)) or await bot.fetch_user(int(target_discord_user_id))
    except (ValueError, discord.HTTPException):
        return "Invite added, but I could not fetch the linked Discord user to DM them."

    try:
        await user.send(embed=build_private_link_dm_embed(result))
    except discord.HTTPException:
        return "Invite added, but I could not deliver the DM to the linked Discord account."

    return None


async def try_send_owner_private_link_dm(user: discord.abc.User, result: dict[str, object]) -> str | None:
    try:
        await user.send(embed=build_private_link_owner_dm_embed(result))
    except discord.HTTPException:
        return "I could not deliver the private link by DM. Check your DM settings and try again."
    return None


async def assign_linked_role(interaction: discord.Interaction) -> tuple[bool, str | None]:
    guild = interaction.guild
    if guild is None and DISCORD_GUILD_ID:
        guild = bot.get_guild(int(DISCORD_GUILD_ID))
        if guild is None:
            try:
                guild = await bot.fetch_guild(int(DISCORD_GUILD_ID))
            except discord.HTTPException:
                guild = None

    if guild is None:
        return False, "Linked successfully, but no guild context was available for the role."

    role = guild.get_role(LINKED_ROLE_ID)
    if role is None:
        try:
            role = await guild.fetch_role(LINKED_ROLE_ID)
        except discord.HTTPException:
            return False, "Linked successfully, but the linked-account role could not be found."

    member = interaction.user if isinstance(interaction.user, discord.Member) else guild.get_member(interaction.user.id)
    if member is None:
        try:
            member = await guild.fetch_member(interaction.user.id)
        except discord.HTTPException:
            return False, "Linked successfully, but I could not find your server member to grant the role."

    try:
        await member.add_roles(role, reason="Linked CanLite account")
    except discord.HTTPException:
        return False, "Linked successfully, but I could not grant the linked-account role."

    return True, None


@bot.event
async def setup_hook() -> None:
    bot.db_pool = await create_pool()
    bot.route_db_pool = await create_route_pool()
    ensure_xp_store()
    ensure_dispense_usage_store()
    bot.add_view(SiteDispenserView())

    if DISCORD_GUILD_ID:
        guild = discord.Object(id=int(DISCORD_GUILD_ID))
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
    else:
        await bot.tree.sync()


@bot.event
async def on_ready() -> None:
    if bot.user:
        print(f"CanLite bot ready as {bot.user} ({bot.user.id})")


@bot.event
async def on_message(message: discord.Message) -> None:
    if message.author.bot or message.guild is None:
        return

    cooldown_key = f"{message.guild.id}:{message.author.id}"
    now = time.time()
    if now - bot.xp_cooldowns.get(cooldown_key, 0) < XP_COOLDOWN_SECONDS:
        await bot.process_commands(message)
        return

    bot.xp_cooldowns[cooldown_key] = now

    xp_store = load_xp_store()
    progress = apply_message_xp(xp_store, message.guild.id, message.author.id)
    save_xp_store(xp_store)

    if progress["current_level"] > progress["previous_level"]:
        credit_note = ""
        granted, balance = await grant_level_up_credit(bot.db_pool, message.author.id)
        if granted and balance is not None:
            credit_note = f" They also got {LEVEL_UP_CREDIT_REWARD} CanLite credit and now have {balance} credits."
        else:
            credit_note = f" Link your CanLite account to receive the {LEVEL_UP_CREDIT_REWARD}-credit level reward."

        await message.channel.send(
            f"{message.author.mention} reached level {progress['current_level']} and now has {progress['xp']} XP.{credit_note}"
        )

    await bot.process_commands(message)


@bot.tree.command(name="link", description="Link your Discord account to your CanLite account.")
@app_commands.describe(code="The 8-character code from your CanLite account page.")
async def link_command(interaction: discord.Interaction, code: str) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    ok, result = await claim_link_code(
        bot.db_pool,
        code=code,
        discord_user_id=interaction.user.id,
        discord_username=interaction.user.name,
        discord_global_name=getattr(interaction.user, "global_name", None),
    )
    if not ok:
        await interaction.followup.send(f"{result}\n\nGenerate a fresh code from {CANLITE_ACCOUNT_URL}.", ephemeral=True)
        return

    role_ok, role_note = await assign_linked_role(interaction)
    message = f"Discord account linked to CanLite user #{result['user_id']}."
    if result.get("reward_granted"):
        message += f" You got 5 credits for linking for the first time and now have {result['reward_balance']} credits."
    if role_ok:
        message += " The linked-account role was granted."
    elif role_note:
        message += f" {role_note}"
    message += f" Manage it from {CANLITE_ACCOUNT_URL}."
    await interaction.followup.send(message, ephemeral=True)


@bot.tree.command(name="dispense", description="Pick a site and filter, then get one URL privately.")
async def dispense_command(interaction: discord.Interaction) -> None:
    await interaction.response.send_message(
        embed=build_dispenser_embed(),
        view=SiteDispenserView(),
    )


@bot.tree.command(name="private-add", description="Add a CanLite user to one of your private links.")
@app_commands.describe(domain="The private-link domain you own.", identifier="Their CanLite email, Discord @name, mention, or Discord ID.")
async def private_add_command(interaction: discord.Interaction, domain: str, identifier: str) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    ok, result = await add_private_link_member(bot.db_pool, interaction.user.id, domain, identifier)
    if not ok:
        await interaction.followup.send(str(result), ephemeral=True)
        return

    payload = result if isinstance(result, dict) else {"message": str(result)}
    dm_note = await try_send_private_link_dm(payload)
    message = str(payload.get("message") or "Private link member added.")
    if dm_note:
        message = f"{message} {dm_note}"
    elif payload.get("target_discord_user_id"):
        message = f"{message} The linked Discord user was notified by DM."
    await interaction.followup.send(message, ephemeral=True)


@bot.tree.command(name="private-remove", description="Remove a CanLite user from one of your private links.")
@app_commands.describe(domain="The private-link domain you own.", identifier="Their CanLite email, Discord @name, mention, or Discord ID.")
async def private_remove_command(interaction: discord.Interaction, domain: str, identifier: str) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    ok, message = await remove_private_link_member(bot.db_pool, interaction.user.id, domain, identifier)
    await interaction.followup.send(message, ephemeral=True)


@bot.tree.command(name="private-list", description="List the private links on your linked CanLite account.")
async def private_list_command(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    ok, result = await list_private_links_for_owner(bot.db_pool, interaction.user.id)
    if not ok:
        await interaction.followup.send(str(result), ephemeral=True)
        return

    links = result if isinstance(result, list) else []
    if not links:
        await interaction.followup.send(
            "No private links yet.\n\nStart with `/private-create` and fill in:\n- a brand-new domain\n- a secret login path\n- a normal-looking cover site",
            ephemeral=True,
        )
        return

    lines = [f"`{item['domain']}` - `{item['login_path']}` - {item['member_count']} invited member(s)" for item in links[:20]]
    embed = discord.Embed(
        title="Your Private Links",
        description="\n".join(lines),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Setup Reminder",
        value="Use a brand-new domain, choose a secret login path, and pick a normal-looking cover site.",
        inline=False,
    )
    if len(links) > 20:
        embed.add_field(name="More", value=f"{len(links) - 20} additional link(s) not shown.", inline=False)
    await interaction.followup.send(embed=embed, ephemeral=True)


@bot.tree.command(name="private-create", description="Create or update one of your real CanLite private links.")
@app_commands.describe(
    domain="A brand-new domain or subdomain you control, like study.example.com.",
    login_path="A secret path like /school or /notes that opens your CanLite login page.",
    cover_url="The normal site people should see before they use your login path.",
)
async def private_create_command(
    interaction: discord.Interaction,
    domain: str,
    login_path: str,
    cover_url: str,
) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)

    linked_user_id = await bot.db_pool.fetchval(
        "SELECT user_id FROM discord_account_links WHERE discord_user_id = $1",
        str(interaction.user.id),
    )
    if not linked_user_id:
        await interaction.followup.send(
            f"Link your Discord account first from {CANLITE_ACCOUNT_URL}, then run `/private-create` again.",
            ephemeral=True,
        )
        return

    try:
        ok, result = await save_private_link_for_owner(
            bot.db_pool,
            bot.route_db_pool,
            interaction.user.id,
            domain=domain,
            cover_url=cover_url,
            login_path=login_path,
        )
    except Exception as exc:
        await interaction.followup.send(f"Could not save your private link right now: {exc}", ephemeral=True)
        return

    if not ok:
        await interaction.followup.send(str(result), ephemeral=True)
        return

    payload = result if isinstance(result, dict) else {"action": "saved"}

    saved_domain = str(payload.get("domain") or "").strip()
    saved_login_path = str(payload.get("login_path") or "").strip()
    dm_note = await try_send_owner_private_link_dm(interaction.user, payload)
    message = (
        f"Private link saved for `https://{saved_domain}`.\n\n"
        f"Login URL: `https://{saved_domain}{saved_login_path}`\n"
        f"Cover site: `{payload.get('cover_url')}`\n\n"
        "The login URL is the secret one you use to open the private CanLite page.\n"
        "Anyone who visits the main domain normally will see the cover site instead."
    )
    if dm_note:
        message += f"\n\n{dm_note}"
    else:
        message += "\n\nI also sent the setup details to your DMs."
    await interaction.followup.send(message, ephemeral=True)


@bot.tree.command(name="private-help", description="Show a simple guide for setting up a real CanLite private link.")
async def private_help_command(interaction: discord.Interaction) -> None:
    embed = discord.Embed(title="Private Link Setup", color=discord.Color.blurple())
    embed.description = "A private link is your own domain that hides CanLite behind a normal-looking cover site."
    embed.add_field(
        name="Step 1",
        value=f"Link your Discord account from {CANLITE_ACCOUNT_URL}.",
        inline=False,
    )
    embed.add_field(
        name="Step 2",
        value="Point a brand-new domain or subdomain to `104.36.85.249`.",
        inline=False,
    )
    embed.add_field(
        name="Step 3",
        value="Run `/private-create` with your domain, a secret login path, and a cover site.",
        inline=False,
    )
    embed.add_field(
        name="Example",
        value="Domain: `study.example.com`\nLogin path: `/notes`\nCover site: `https://google.com`",
        inline=False,
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="rank", description="Show your XP, level, and message count.")
async def rank_command(interaction: discord.Interaction) -> None:
    xp_store = load_xp_store()
    guild_bucket = xp_store.get(str(interaction.guild_id), {}) if interaction.guild_id else {}
    user_bucket = guild_bucket.get(str(interaction.user.id), {"xp": 0, "level": 0, "messages": 0})
    next_level = int(user_bucket["level"]) + 1
    next_level_goal = xp_needed_for_level(next_level)

    embed = discord.Embed(title=f"{interaction.user.display_name}'s Rank", color=discord.Color.blurple())
    embed.add_field(name="Level", value=str(user_bucket["level"]))
    embed.add_field(name="XP", value=str(user_bucket["xp"]))
    embed.add_field(name="Messages", value=str(user_bucket["messages"]))
    embed.add_field(name="Next Level At", value=f"{next_level_goal} XP", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="credits", description="Show your linked CanLite credit balance.")
async def credits_command(interaction: discord.Interaction) -> None:
    linked, balance = await get_credit_balance_for_discord_user(bot.db_pool, interaction.user.id)
    if not linked or balance is None:
        await interaction.response.send_message(
            f"Link your Discord account first from {CANLITE_ACCOUNT_URL} to view your credits here.",
            ephemeral=True,
        )
        return

    embed = discord.Embed(title="CanLite Credits", color=discord.Color.green())
    embed.add_field(name="Member", value=interaction.user.mention, inline=True)
    embed.add_field(name="Balance", value=str(balance), inline=True)
    embed.set_footer(text="Credit balance is shown publicly in this channel.")
    await interaction.response.send_message(
        embed=embed,
    )


@bot.tree.command(name="leaderboard", description="Show the server XP leaderboard.")
async def leaderboard_command(interaction: discord.Interaction) -> None:
    if interaction.guild_id is None:
        await interaction.response.send_message("This command only works in a server.", ephemeral=True)
        return

    xp_store = load_xp_store()
    guild_bucket = xp_store.get(str(interaction.guild_id), {})
    rows = sorted(guild_bucket.items(), key=lambda item: (item[1].get("xp", 0), item[1].get("messages", 0)), reverse=True)[:10]
    if not rows:
        await interaction.response.send_message("No XP data yet.", ephemeral=True)
        return

    lines = []
    for index, (user_id, data) in enumerate(rows, start=1):
        lines.append(f"{index}. <@{user_id}> - Level {data.get('level', 0)} - {data.get('xp', 0)} XP")

    embed = discord.Embed(title="XP Leaderboard", description="\n".join(lines), color=discord.Color.gold())
    embed.set_footer(text=f"Showing top {len(lines)} members in this server.")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="reset-dispense-user", description="Reset one member's dispenser limit.")
@app_commands.check(is_catalog_admin)
@app_commands.describe(member="The member whose 3-link dispenser limit should be reset.")
async def reset_dispense_user_command(interaction: discord.Interaction, member: discord.Member) -> None:
    changed = reset_user_dispense(interaction.guild_id, member.id)
    message = f"Reset dispenser usage for {member.mention}."
    if not changed:
        message = f"{member.mention} was already at 0 used links."
    await interaction.response.send_message(message)


@bot.tree.command(name="reset-dispense-server", description="Reset dispenser limits for the whole server.")
@app_commands.check(is_catalog_admin)
async def reset_dispense_server_command(interaction: discord.Interaction) -> None:
    affected = reset_guild_dispense(interaction.guild_id)
    await interaction.response.send_message(f"Reset dispenser usage for this server. Cleared {affected} member record(s).")


@bot.tree.command(name="add-link", description="Add one URL entry to the dispenser catalog.")
@app_commands.check(is_catalog_admin)
@app_commands.describe(
    site="Site name users pick from.",
    filter_name="Filter name users pick after choosing the site.",
    url="The URL to send.",
    category="Optional category.",
    host_type="Optional host type.",
    status="Optional status.",
    tags="Optional comma-separated tags."
)
async def add_link_command(
    interaction: discord.Interaction,
    site: str,
    filter_name: str,
    url: str,
    category: str = "general",
    host_type: str = "custom-domain",
    status: str = "stable",
    tags: str | None = None,
) -> None:
    entry = SiteEntry(
        id=slugify(f"{site}-{filter_name}-{url}"),
        site=site.strip(),
        filter_name=filter_name.strip(),
        url=url.strip(),
        category=slugify(category),
        host_type=slugify(host_type),
        status=slugify(status),
        tags=parse_tags(tags),
    )
    result = catalog_store.add_entry(entry)
    await interaction.response.send_message(
        f"{result.title()} `{entry.id}` for site `{entry.site}` and filter `{entry.filter_name}`.",
        ephemeral=True,
    )


@bot.tree.command(name="bulk-add-links", description="Bulk import many site/filter/url entries from JSON or CSV.")
@app_commands.check(is_catalog_admin)
@app_commands.describe(
    payload="JSON array or CSV text. CSV headers: site,filter,url,category,hostType,status,tags",
    attachment="Optional JSON or CSV file attachment."
)
async def bulk_add_links_command(
    interaction: discord.Interaction,
    payload: str | None = None,
    attachment: discord.Attachment | None = None,
) -> None:
    if not payload and not attachment:
        await interaction.response.send_message("Provide either `payload` text or an `attachment`.", ephemeral=True)
        return

    source_text = payload or ""
    if attachment is not None:
        source_text = (await attachment.read()).decode("utf-8")

    try:
        rows = catalog_store.parse_import_payload(source_text)
    except Exception as error:
        await interaction.response.send_message(f"Import failed to parse: {error}", ephemeral=True)
        return

    result = catalog_store.import_entries(rows)
    await interaction.response.send_message(
        f"Import finished. Added: {result.added}. Updated: {result.updated}. Skipped: {result.skipped}.",
        ephemeral=True,
    )


@bot.tree.command(name="remove-link", description="Remove one URL entry from the dispenser catalog.")
@app_commands.check(is_catalog_admin)
@app_commands.describe(link_id="The catalog entry ID to remove.")
async def remove_link_command(interaction: discord.Interaction, link_id: str) -> None:
    changed = catalog_store.remove_entry(link_id)
    if not changed:
        await interaction.response.send_message("That link ID was not found.", ephemeral=True)
        return
    await interaction.response.send_message(f"Removed `{slugify(link_id)}`.", ephemeral=True)


@bot.tree.command(name="list-links", description="List the current dispenser catalog grouped by site.")
@app_commands.check(is_catalog_admin)
async def list_links_command(interaction: discord.Interaction) -> None:
    site_names = catalog_store.get_site_names()
    if not site_names:
        await interaction.response.send_message("The catalog is empty.", ephemeral=True)
        return

    lines = []
    for site_name in site_names[:20]:
        filter_names = catalog_store.get_filters_for_site(site_name)
        url_count = catalog_store.get_entry_count_for_site(site_name)
        lines.append(f"{site_name} - {url_count} urls - filters: {', '.join(filter_names[:6])}")

    embed = discord.Embed(title="Dispenser Catalog", description="\n".join(lines), color=discord.Color.green())
    await interaction.response.send_message(embed=embed, ephemeral=True)


@add_link_command.error
@bulk_add_links_command.error
@remove_link_command.error
@list_links_command.error
@reset_dispense_user_command.error
@reset_dispense_server_command.error
async def admin_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
    if isinstance(error, app_commands.CheckFailure):
        message = "You need `Manage Server` or `Administrator` to manage the catalog."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
        return
    raise error


def run() -> None:
    bot.run(DISCORD_TOKEN)
