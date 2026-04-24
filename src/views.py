import random

import discord

from .catalog import catalog_store
from .utils import slugify, titleize


def build_dispenser_embed() -> discord.Embed:
    site_count = len(catalog_store.get_site_names())

    embed = discord.Embed(
        title="CanLite Link Dispenser",
        description="Choose a site button below. The filter picker will open privately for you.",
        color=discord.Color.from_rgb(116, 217, 182),
    )
    embed.add_field(name="Catalog", value=f"{site_count} sites available", inline=False)
    return embed


def build_private_dispenser_embed(selected_site: str, selected_filter: str | None) -> discord.Embed:
    filter_count = len(catalog_store.get_filters_for_site(selected_site))

    embed = discord.Embed(
        title="CanLite Link Dispenser",
        description="Choose a filter, then generate one matching URL privately.",
        color=discord.Color.from_rgb(116, 217, 182),
    )
    embed.add_field(name="Site", value=selected_site, inline=True)
    embed.add_field(name="Filter", value=selected_filter or "Not selected", inline=True)
    embed.add_field(name="Catalog", value=f"{filter_count} filters for this site", inline=False)
    return embed


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

        view = PrivateDispenserView(selected_site=self.site_name)
        await interaction.response.send_message(
            embed=build_private_dispenser_embed(self.site_name, None),
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
        await interaction.response.edit_message(
            embed=build_private_dispenser_embed(view.selected_site, view.selected_filter),
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
        matches = catalog_store.get_matching_entries(view.selected_site, view.selected_filter)
        if not matches:
            await interaction.response.send_message("No URLs match that site and filter.", ephemeral=True)
            return

        chosen = random.choice(matches)
        await interaction.response.send_message(chosen.url, ephemeral=True)


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
