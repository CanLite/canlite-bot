import random

import discord

from .catalog import catalog_store
from .utils import titleize


def build_dispenser_embed(selected_site: str | None, selected_filter: str | None, generated_url: str | None = None) -> discord.Embed:
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

    if generated_url:
        embed.add_field(name="Your URL", value=generated_url, inline=False)

    return embed


class SiteSelect(discord.ui.Select):
    def __init__(self, selected_site: str | None) -> None:
        options = [
            discord.SelectOption(label=site_name[:100], value=site_name, default=site_name == selected_site)
            for site_name in catalog_store.get_site_names()[:25]
        ] or [discord.SelectOption(label="No sites loaded", value="__none__")]

        super().__init__(placeholder="Choose a site", options=options, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        assert isinstance(self.view, DispenserView)
        self.view.selected_site = self.values[0] if self.values[0] != "__none__" else None
        self.view.selected_filter = None
        self.view.generated_url = None
        self.view.refresh_items()
        await interaction.response.edit_message(
            embed=build_dispenser_embed(self.view.selected_site, self.view.selected_filter),
            view=self.view,
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
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        assert isinstance(self.view, DispenserView)
        self.view.selected_filter = self.values[0] if self.values[0] != "__none__" else None
        self.view.generated_url = None
        await interaction.response.edit_message(
            embed=build_dispenser_embed(self.view.selected_site, self.view.selected_filter),
            view=self.view,
        )


class GenerateButton(discord.ui.Button):
    def __init__(self, enabled: bool) -> None:
        super().__init__(label="Generate URL", style=discord.ButtonStyle.success, disabled=not enabled, row=2)

    async def callback(self, interaction: discord.Interaction) -> None:
        assert isinstance(self.view, DispenserView)
        matches = catalog_store.get_matching_entries(self.view.selected_site, self.view.selected_filter)
        if not matches:
            await interaction.response.send_message("No URLs match that site and filter.", ephemeral=True)
            return

        chosen = random.choice(matches)
        self.view.generated_url = chosen.url
        await interaction.response.edit_message(
            embed=build_dispenser_embed(self.view.selected_site, self.view.selected_filter, chosen.url),
            view=self.view,
        )


class DispenserView(discord.ui.View):
    def __init__(self, owner_id: int) -> None:
        super().__init__(timeout=1800)
        self.owner_id = owner_id
        self.selected_site: str | None = None
        self.selected_filter: str | None = None
        self.generated_url: str | None = None
        self.refresh_items()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Only the person who opened this dispenser can use it.", ephemeral=True)
            return False
        return True

    def refresh_items(self) -> None:
        self.clear_items()
        self.add_item(SiteSelect(self.selected_site))
        self.add_item(FilterSelect(self.selected_site, self.selected_filter))
        self.add_item(GenerateButton(enabled=bool(self.selected_site and self.selected_filter)))

    async def on_timeout(self) -> None:
        self.clear_items()
