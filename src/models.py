from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class SiteEntry:
    id: str
    site: str
    filter_name: str
    url: str
    category: str
    host_type: str
    status: str
    tags: list[str] = field(default_factory=list)

    def to_storage(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["filter"] = payload.pop("filter_name")
        payload["hostType"] = payload.pop("host_type")
        return payload


@dataclass
class CatalogImportResult:
    added: int
    updated: int
    skipped: int
