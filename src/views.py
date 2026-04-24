import random

import discord

from .catalog import catalog_store
from .utils import titleize


def build_dispenser_embed(selected_site: str | None, selected_filter: str | None) -> discord.Embed:
    site_count = len(catalog_store.get_site_names())
    filter_count = len(catalog_store.get_filters_for_site(selected_site)) if selected_site else 0

    embed = discord.Embed(
        title="CanLite Link Dispenser",
        description="Pick a site name, then pick a filter. The bot will send you one matching URL privately.",
        color=discord.Color.from_rgb(116, 217, 182),
    )
    embed.add_field(name="Site", value=selected_site or "Not selected", inline=True)
    embed.add_field(name="Filter", value=selected_filter or "Not selected", inline=True)
    embed.add_field(name="Catalog", value=f"{site_count} sites, {filter_count} filters for current site", inline=False)

    return embed


class SiteSelect(discord.ui.Select):
    def __init__(self, selected_site: str | None) -> None:
        options = [
            discord.SelectOption(label=site_name[:100], value=site_name, default=site_name == selected_site)
            for site_name in catalog_store.get_site_names()[:25]
        ] or [discord.SelectOption(label="No sites loaded", value="__none__")]

        super().__init__(placeholder="Choose a site", options=options, row=0, custom_id="canlite:site-select")

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, DispenserView):
            await interaction.response.send_message("This dispenser is no longer active. Open a new one from /dispense.", ephemeral=True)
            return
        view.selected_site = self.values[0] if self.values[0] != "__none__" else None
        view.selected_filter = None
        view.refresh_items()
        await interaction.response.edit_message(
            embed=build_dispenser_embed(view.selected_site, view.selected_filter),
            view=view,
        )


class FilterSelect(discord.ui.Select):
    def __init__(self, selected_site: str | None, selected_filter: str | None) -> None:
        filters = catalog_store.get_filters_for_site(selected_site) if selected_site else []
        options = [
            discord.SelectOption(label=titleize(filter_name)[:100], value=filter_name, default=filter_name == selected_filter)
            for filter_name in filters[:25]
        ] or [discord.SelectOption(label="Choose a site first", value="__none__")]

        super().__init__(
            placeholder="Choose a filter",
            options=options,
            row=1,
            disabled=not bool(filters),
            custom_id="canlite:filter-select",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, DispenserView):
            await interaction.response.send_message("This dispenser is no longer active. Open a new one from /dispense.", ephemeral=True)
            return
        view.selected_filter = self.values[0] if self.values[0] != "__none__" else None
        await interaction.response.edit_message(
            embed=build_dispenser_embed(view.selected_site, view.selected_filter),
            view=view,
        )


class GenerateButton(discord.ui.Button):
    def __init__(self, enabled: bool) -> None:
        super().__init__(
            label="Generate URL",
            style=discord.ButtonStyle.success,
            disabled=not enabled,
            row=2,
            custom_id="canlite:generate-url",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, DispenserView):
            await interaction.response.send_message("This dispenser is no longer active. Open a new one from /dispense.", ephemeral=True)
            return
        matches = catalog_store.get_matching_entries(view.selected_site, view.selected_filter)
        if not matches:
            await interaction.response.send_message("No URLs match that site and filter.", ephemeral=True)
            return

        chosen = random.choice(matches)
        await interaction.response.send_message(chosen.url, ephemeral=True)


class DispenserView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)
        self.selected_site: str | None = None
        self.selected_filter: str | None = None
        self.refresh_items()

    def refresh_items(self) -> None:
        self.clear_items()
        self.add_item(SiteSelect(self.selected_site))
        self.add_item(FilterSelect(self.selected_site, self.selected_filter))
        self.add_item(GenerateButton(enabled=bool(self.selected_site and self.selected_filter)))
