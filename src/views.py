import asyncio
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

import discord

from .config import DISPENSE_LIMIT, DISPENSE_LOG_CHANNEL_ID
from .dispense_usage import get_remaining_count, record_dispense
from .utils import slugify, titleize

DISPENSER_SITE_TYPES = {
    "CanLite": "canlite",
    "BrunysIXL": "brunysixl",
}

DISPENSER_FILTERS = [
    "blocksi",
    "cisco",
    "iboss",
    "lanschool",
    "lightspeed",
    "linewize",
    "senso",
]

GENERATOR_BASE_URL = "http://127.0.0.1:8080/generate"
GENERATOR_IP = "104.36.85.249"
GENERATOR_REQUEST_TIMEOUT = 180

def build_dispenser_embed() -> discord.Embed:
    site_count = len(DISPENSER_SITE_TYPES)

    embed = discord.Embed(
        title="CanLite Link Dispenser",
        description="Choose a site first, then choose a filter. Your link will be sent by DM.",
        color=discord.Color.from_rgb(116, 217, 182),
    )
    embed.add_field(name="Catalog", value=f"{site_count} sites available", inline=True)
    embed.add_field(name="Limit", value=f"{DISPENSE_LIMIT} links per member", inline=True)
    return embed


def build_private_dispenser_embed(selected_site: str, selected_filter: str | None, remaining_uses: int) -> discord.Embed:
    filter_count = len(DISPENSER_FILTERS)

    embed = discord.Embed(
        title="CanLite Link Dispenser",
        description="Choose a filter for this site.",
        color=discord.Color.from_rgb(116, 217, 182),
    )
    embed.add_field(name="Site", value=selected_site, inline=True)
    embed.add_field(name="Filter", value=selected_filter or "Not selected", inline=True)
    embed.add_field(name="Remaining", value=f"{remaining_uses}/{DISPENSE_LIMIT}", inline=True)
    embed.add_field(name="Catalog", value=f"{filter_count} filters for this site", inline=False)
    return embed


def build_generation_pending_embed(selected_site: str, selected_filter: str, remaining_uses: int) -> discord.Embed:
    embed = discord.Embed(
        title="Generating Link",
        description="Your link is being generated now. This might take a bit.",
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Site", value=selected_site, inline=True)
    embed.add_field(name="Filter", value=titleize(selected_filter), inline=True)
    embed.add_field(name="Remaining", value=f"{remaining_uses}/{DISPENSE_LIMIT}", inline=True)
    return embed


def build_generation_result_embed(selected_site: str, selected_filter: str, generated_url: str, remaining_uses: int) -> discord.Embed:
    embed = discord.Embed(
        title="Link Sent",
        description="Your generated link was delivered by DM.",
        color=discord.Color.green(),
    )
    embed.add_field(name="Site", value=selected_site, inline=True)
    embed.add_field(name="Filter", value=titleize(selected_filter), inline=True)
    embed.add_field(name="Remaining", value=f"{remaining_uses}/{DISPENSE_LIMIT}", inline=True)
    return embed


def build_generation_dm_embed(selected_site: str, selected_filter: str, generated_url: str, remaining_uses: int) -> discord.Embed:
    embed = discord.Embed(
        title="Your Generated Link",
        description=generated_url,
        color=discord.Color.green(),
    )
    embed.add_field(name="Site", value=selected_site, inline=True)
    embed.add_field(name="Filter", value=titleize(selected_filter), inline=True)
    embed.add_field(name="Remaining After This", value=f"{remaining_uses}/{DISPENSE_LIMIT}", inline=True)
    return embed


def build_generation_error_embed(selected_site: str, selected_filter: str, remaining_uses: int, message: str) -> discord.Embed:
    embed = discord.Embed(
        title="Generation Failed",
        description=message,
        color=discord.Color.red(),
    )
    embed.add_field(name="Site", value=selected_site, inline=True)
    embed.add_field(name="Filter", value=titleize(selected_filter), inline=True)
    embed.add_field(name="Remaining", value=f"{remaining_uses}/{DISPENSE_LIMIT}", inline=True)
    return embed


def _generate_link_sync(selected_site: str, selected_filter: str) -> str:
    link_type = DISPENSER_SITE_TYPES.get(selected_site)
    if not link_type:
        raise ValueError("That site is not configured for link generation.")

    query = urlencode(
        {
            "ip": GENERATOR_IP,
            "blocker": selected_filter,
            "linktype": link_type,
        }
    )
    request_url = f"{GENERATOR_BASE_URL}?{query}"
    try:
        with urlopen(request_url, timeout=GENERATOR_REQUEST_TIMEOUT) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        if exc.code == 504:
            raise ValueError("The generator took too long to finish. Try again in a moment.") from exc
        raise ValueError(f"Generator request failed with HTTP {exc.code}.") from exc
    except URLError as exc:
        raise ValueError("Could not reach the local generator") from exc

    generated_url = str(payload.get("url") or "").strip()
    if not generated_url:
        raise ValueError("Generator returned an invalid response.")
    return generated_url


def create_private_link_payload(url: str | None = None, site: str | None = None, filter_name: str | None = None) -> dict[str, str]:
    provided_url = (url or "").strip()
    if provided_url:
        return {
            "url": provided_url,
            "source": "provided_url",
            "site": "CanLite",
            "filter_name": "",
        }

    selected_site = "CanLite"
    selected_filter = (filter_name or "").strip().lower()
    if not selected_filter:
        raise ValueError("Provide either a specific private-link URL or choose a filter.")

    generated_url = _generate_link_sync(selected_site, selected_filter)

    return {
        "url": generated_url,
        "source": "generator",
        "site": selected_site,
        "filter_name": selected_filter,
    }


async def generate_link(selected_site: str, selected_filter: str) -> str:
    return await asyncio.to_thread(_generate_link_sync, selected_site, selected_filter)


async def create_private_link(url: str | None = None, site: str | None = None, filter_name: str | None = None) -> dict[str, str]:
    return await asyncio.to_thread(create_private_link_payload, url, site, filter_name)


async def send_dispense_log(
    interaction: discord.Interaction,
    selected_site: str,
    selected_filter: str,
    generated_url: str,
    remaining_uses: int,
) -> None:
    channel = interaction.client.get_channel(DISPENSE_LOG_CHANNEL_ID)
    if channel is None:
        try:
            channel = await interaction.client.fetch_channel(DISPENSE_LOG_CHANNEL_ID)
        except discord.HTTPException:
            return

    if not isinstance(channel, discord.abc.Messageable):
        return

    guild_name = interaction.guild.name if interaction.guild else "Direct Messages"
    embed = discord.Embed(
        title="Dispenser Log",
        color=discord.Color.orange(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Member", value=f"{interaction.user.mention} (`{interaction.user.id}`)", inline=False)
    embed.add_field(name="Server", value=guild_name, inline=True)
    embed.add_field(name="Site", value=selected_site, inline=True)
    embed.add_field(name="Filter", value=titleize(selected_filter), inline=True)
    embed.add_field(name="Remaining Uses", value=f"{remaining_uses}/{DISPENSE_LIMIT}", inline=True)
    embed.add_field(name="URL", value=generated_url, inline=False)
    await channel.send(embed=embed)


class SiteButton(discord.ui.Button):
    def __init__(self, site_name: str, row: int) -> None:
        super().__init__(
            label=site_name[:80],
            style=discord.ButtonStyle.primary,
            row=row,
            custom_id=f"canlite:site:{slugify(site_name)}",
        )
        self.site_name = site_name

    async def callback(self, interaction: discord.Interaction) -> None:
        filters = DISPENSER_FILTERS
        if not filters:
            await interaction.response.send_message("That site has no filters configured yet.", ephemeral=True)
            return

        remaining_uses = get_remaining_count(interaction.guild_id, interaction.user.id)
        view = PrivateDispenserView(selected_site=self.site_name)
        await interaction.response.send_message(
            embed=build_private_dispenser_embed(self.site_name, None, remaining_uses),
            view=view,
            ephemeral=True,
        )


class FilterSelect(discord.ui.Select):
    def __init__(self, selected_site: str, selected_filter: str | None) -> None:
        filters = DISPENSER_FILTERS
        options = [
            discord.SelectOption(label=titleize(filter_name)[:100], value=filter_name, default=filter_name == selected_filter)
            for filter_name in filters[:25]
        ] or [discord.SelectOption(label="No filters loaded", value="__none__")]

        super().__init__(
            placeholder="Choose a filter",
            options=options,
            row=0,
            disabled=not bool(filters),
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, PrivateDispenserView):
            await interaction.response.send_message("This private dispenser is no longer active. Use /dispense again.", ephemeral=True)
            return
        view.selected_filter = self.values[0] if self.values[0] != "__none__" else None
        view.refresh_items()
        remaining_uses = get_remaining_count(interaction.guild_id, interaction.user.id)
        await interaction.response.edit_message(
            embed=build_private_dispenser_embed(view.selected_site, view.selected_filter, remaining_uses),
            view=view,
        )


class GenerateButton(discord.ui.Button):
    def __init__(self, enabled: bool) -> None:
        super().__init__(
            label="Generate Link",
            style=discord.ButtonStyle.success,
            disabled=not enabled,
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, PrivateDispenserView):
            await interaction.response.send_message("This private dispenser is no longer active. Use /dispense again.", ephemeral=True)
            return
        remaining_before = get_remaining_count(interaction.guild_id, interaction.user.id)
        if remaining_before <= 0:
            await interaction.response.send_message(
                "You have used all of your dispenser links. Ask a moderator to reset your limit.",
                ephemeral=True,
            )
            return
        selected_filter = view.selected_filter or ""
        await interaction.response.send_message(
            embed=build_generation_pending_embed(view.selected_site, selected_filter, remaining_before),
            ephemeral=True,
        )

        try:
            generated_url = await generate_link(view.selected_site, selected_filter)
        except Exception as exc:
            await interaction.edit_original_response(
                embed=build_generation_error_embed(
                    view.selected_site,
                    selected_filter,
                    remaining_before,
                    f"Could not generate a link right now: {exc}",
                )
            )
            return

        try:
            await interaction.user.send(
                embed=build_generation_dm_embed(view.selected_site, selected_filter, generated_url, remaining_before - 1)
            )
        except discord.HTTPException:
            await interaction.edit_original_response(
                embed=build_generation_error_embed(
                    view.selected_site,
                    selected_filter,
                    remaining_before,
                    "I generated the link, but could not DM it to you. Check your DM settings and try again.",
                )
            )
            return

        usage = record_dispense(interaction.guild_id, interaction.user.id)
        await interaction.edit_original_response(
            embed=build_generation_result_embed(view.selected_site, selected_filter, generated_url, usage["remaining"])
        )
        await send_dispense_log(interaction, view.selected_site, selected_filter, generated_url, usage["remaining"])


class PrivateDispenserView(discord.ui.View):
    def __init__(self, selected_site: str) -> None:
        super().__init__(timeout=1800)
        self.selected_site = selected_site
        self.selected_filter: str | None = None
        self.refresh_items()

    def refresh_items(self) -> None:
        self.clear_items()
        self.add_item(FilterSelect(self.selected_site, self.selected_filter))
        self.add_item(GenerateButton(enabled=bool(self.selected_filter)))


class SiteDispenserView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)
        for index, site_name in enumerate(list(DISPENSER_SITE_TYPES)[:25]):
            self.add_item(SiteButton(site_name, row=index // 5))
