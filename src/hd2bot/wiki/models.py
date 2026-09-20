"""Stable records for the bundled, offline equipment catalogue."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CatalogEntry:
    id: str
    category: str
    name: str
    english_name: str = ""
    code: str = ""
    aliases: tuple[str, ...] = ()
    subcategory: str = ""
    summary: str = ""
    fields: tuple[tuple[str, str], ...] = ()
    source_url: str = ""
    revision: str = ""
    image_url: str = ""
    image_path: str = ""
    image_credit: str = ""
    revision_source_url: str = ""
    name_source_url: str = ""
    name_revision: str = ""
    chinese_detail_source_url: str = ""
    chinese_detail_revision: str = ""
    chinese_detail_fields: tuple[str, ...] = ()
    chinese_detail_summary: bool = False
    source_urls: tuple[str, ...] = ()


@dataclass
class CatalogSearchResult:
    match: CatalogEntry | None = None
    candidates: list[CatalogEntry] = field(default_factory=list)
    exact: bool = False
    matches_total: int = 0
