import csv
import io
import json
from collections import defaultdict

from .config import CATALOG_PATH
from .models import CatalogImportResult, SiteEntry
from .utils import parse_tags, slugify


def _normalize_entry(item: dict) -> SiteEntry:
    site = str(item.get("site") or item.get("label") or "").strip()
    filter_name = str(item.get("filter") or item.get("filterName") or "default").strip()
    url = str(item.get("url") or "").strip()
    category = slugify(str(item.get("category") or "general").strip())
    host_type = slugify(str(item.get("hostType") or item.get("host_type") or "custom-domain").strip())
    status = slugify(str(item.get("status") or "stable").strip())
    tags = item.get("tags", [])

    if isinstance(tags, str):
        parsed_tags = parse_tags(tags)
    else:
        parsed_tags = [slugify(str(tag)) for tag in tags if slugify(str(tag))]

    entry_id = slugify(str(item.get("id") or f"{site}-{filter_name}-{url}"))
    return SiteEntry(
        id=entry_id,
        site=site,
        filter_name=filter_name,
        url=url,
        category=category,
        host_type=host_type,
        status=status,
        tags=parsed_tags,
    )


class CatalogStore:
    def __init__(self) -> None:
        self.entries: list[SiteEntry] = []
        self.reload()

    def reload(self) -> None:
        raw_entries = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        self.entries = [_normalize_entry(item) for item in raw_entries]

    def save(self) -> None:
        CATALOG_PATH.write_text(
            json.dumps([entry.to_storage() for entry in self.entries], indent=2) + "\n",
            encoding="utf-8",
        )

    def get_site_names(self) -> list[str]:
        return sorted({entry.site for entry in self.entries})

    def get_filters_for_site(self, site_name: str) -> list[str]:
        return sorted({entry.filter_name for entry in self.entries if entry.site == site_name})

    def get_matching_entries(self, site_name: str, filter_name: str) -> list[SiteEntry]:
        return [
            entry
            for entry in self.entries
            if entry.site == site_name and entry.filter_name == filter_name
        ]

    def get_grouped_summary(self) -> dict[str, list[SiteEntry]]:
        grouped: dict[str, list[SiteEntry]] = defaultdict(list)
        for entry in self.entries:
            grouped[entry.site].append(entry)
        return dict(sorted(grouped.items()))

    def add_entry(self, entry: SiteEntry) -> str:
        existing = next((item for item in self.entries if item.id == entry.id), None)
        if existing:
            existing.site = entry.site
            existing.filter_name = entry.filter_name
            existing.url = entry.url
            existing.category = entry.category
            existing.host_type = entry.host_type
            existing.status = entry.status
            existing.tags = entry.tags
            self.save()
            return "updated"

        self.entries.append(entry)
        self.save()
        return "added"

    def remove_entry(self, entry_id: str) -> bool:
        before = len(self.entries)
        self.entries = [entry for entry in self.entries if entry.id != slugify(entry_id)]
        changed = len(self.entries) != before
        if changed:
            self.save()
        return changed

    def import_entries(self, rows: list[dict]) -> CatalogImportResult:
        added = 0
        updated = 0
        skipped = 0

        for row in rows:
            try:
                entry = _normalize_entry(row)
            except Exception:
                skipped += 1
                continue

            if not entry.site or not entry.url:
                skipped += 1
                continue

            result = self.add_entry(entry)
            if result == "added":
                added += 1
            else:
                updated += 1

        return CatalogImportResult(added=added, updated=updated, skipped=skipped)

    def parse_import_payload(self, payload: str) -> list[dict]:
        text = payload.strip()
        if not text:
            return []

        if text.startswith("["):
            parsed = json.loads(text)
            if not isinstance(parsed, list):
                raise ValueError("JSON import payload must be an array.")
            return parsed

        reader = csv.DictReader(io.StringIO(text))
        return [row for row in reader]


catalog_store = CatalogStore()
