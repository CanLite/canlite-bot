import random

import discord

from .catalog import catalog_store
from .config import DISPENSE_LIMIT, DISPENSE_LOG_CHANNEL_ID
from .dispense_usage import get_remaining_count, record_dispense
from .utils import slugify, titleize


def build_dispenser_embed() -> discord.Embed:
    site_count = len(catalog_store.get_site_names())

    embed = discord.Embed(
        title="CanLite Link Dispenser",
        description="Choose a site below. The filter picker opens privately, and each member gets 3 links until a moderator resets them.",
        color=discord.Color.from_rgb(116, 217, 182),
    )
    embed.add_field(name="Catalog", value=f"{site_count} sites available", inline=True)
    embed.add_field(name="Limit", value=f"{DISPENSE_LIMIT} links per member", inline=True)
    embed.set_footer(text="Links are delivered privately. Moderator resets are supported.")
    return embed


def build_private_dispenser_embed(selected_site: str, selected_filter: str | None, remaining_uses: int) -> discord.Embed:
    filter_count = len(catalog_store.get_filters_for_site(selected_site))

    embed = discord.Embed(
        title="CanLite Link Dispenser",
        description="Choose a filter, then generate one matching URL privately.",
        color=discord.Color.from_rgb(116, 217, 182),
    )
    embed.add_field(name="Site", value=selected_site, inline=True)
    embed.add_field(name="Filter", value=selected_filter or "Not selected", inline=True)
    embed.add_field(name="Remaining", value=f"{remaining_uses}/{DISPENSE_LIMIT}", inline=True)
    embed.add_field(name="Catalog", value=f"{filter_count} filters for this site", inline=False)
    embed.set_footer(text="Your selection and generated link stay visible only to you.")
    return embed


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
        filters = catalog_store.get_filters_for_site(self.site_name)
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
        filters = catalog_store.get_filters_for_site(selected_site)
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
            label="Generate URL",
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
                "You have used all 3 dispenser links. Ask a moderator to reset your limit.",
                ephemeral=True,
            )
            return
        matches = catalog_store.get_matching_entries(view.selected_site, view.selected_filter)
        if not matches:
            await interaction.response.send_message("No URLs match that site and filter.", ephemeral=True)
            return

        chosen = random.choice(matches)
        usage = record_dispense(interaction.guild_id, interaction.user.id)
        result_embed = discord.Embed(
            title="Your Link",
            description=chosen.url,
            color=discord.Color.green(),
        )
        result_embed.add_field(name="Site", value=view.selected_site, inline=True)
        result_embed.add_field(name="Filter", value=titleize(view.selected_filter or "not-selected"), inline=True)
        result_embed.add_field(name="Remaining", value=f"{usage['remaining']}/{DISPENSE_LIMIT}", inline=True)
        result_embed.set_footer(text="This message is only visible to you.")
        await interaction.response.send_message(embed=result_embed, ephemeral=True)
        await send_dispense_log(interaction, view.selected_site, view.selected_filter or "", chosen.url, usage["remaining"])


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
        for index, site_name in enumerate(catalog_store.get_site_names()[:25]):
            self.add_item(SiteButton(site_name, row=index // 5))
