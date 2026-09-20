"""Offline Chinese presentation without modifying wiki facts or their revisions."""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import replace
from functools import lru_cache
from pathlib import Path

from hd2bot.wiki.models import CatalogEntry

_ASSETS = Path(__file__).resolve().parents[1] / "assets"
_TERMS_PATH = _ASSETS / "wiki_zh_terms.json"
_TEXTS_PATH = _ASSETS / "wiki_zh_texts.json"
_CJK = re.compile(r"[\u3400-\u9fff]")
_LATIN = re.compile(r"[A-Za-z]")
_LONG_FIELDS = {"description", "summary", "说明", "简介"}
PENDING_DESCRIPTION = "新说明待中文整理，请查看来源页面。"


def _key(value: str) -> str:
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", value).casefold())


def _chinese_prose(value: str) -> bool:
    chinese_count = len(_CJK.findall(value))
    if not chinese_count:
        return False
    # A translated weapon name alone does not make an otherwise English sentence Chinese.
    without_codes = re.sub(r"\b(?:[A-Z]{1,5}-\d+[A-Z]*|SEAF|DPS|EMS)\b", "", value)
    latin_count = len(_LATIN.findall(without_codes))
    return chinese_count >= latin_count


@lru_cache(maxsize=8)
def _read_json(path: str, modified: int, size: int) -> dict:
    del modified  # The stat values form the cache key, so edited translations reload locally.
    if size > 4 * 1024 * 1024:
        return {}
    try:
        data = json.loads(Path(path).read_text(encoding="utf8"))
        if not isinstance(data, dict) or data.get("schema_version") != 1:
            return {}
        return data
    except (OSError, ValueError):
        return {}


def _resource(path: Path) -> dict:
    try:
        stat = path.stat()
    except OSError:
        return {}
    return _read_json(str(path), stat.st_mtime_ns, stat.st_size)


def _strings(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {key: text for key, text in value.items()
            if isinstance(key, str) and key and isinstance(text, str) and text.strip()}


@lru_cache(maxsize=4)
def _term_pattern(items: tuple[tuple[str, str], ...]) -> tuple[re.Pattern, dict[str, str]]:
    names = sorted((key for key, _ in items), key=len, reverse=True)
    # Letter boundaries protect unknown words while allowing units adjacent to numbers.
    pattern = re.compile(r"(?<![A-Za-z])(?:" + "|".join(re.escape(key) for key in names)
                         + r")(?![A-Za-z])", re.IGNORECASE) if names else re.compile(r"(?!)")
    return pattern, {key.casefold(): value for key, value in items}


def _resources() -> tuple[dict, dict[str, str], re.Pattern, dict[str, str]]:
    terms = _resource(_TERMS_PATH)
    texts = _strings(_resource(_TEXTS_PATH).get("texts"))
    pattern, mapping = _term_pattern(tuple(_strings(terms.get("terms")).items()))
    return terms, texts, pattern, mapping


def localize_summary(value: str, *, texts: dict[str, str] | None = None) -> str:
    """Only exact source prose can use a stored translation; revisions never reuse old prose."""
    if not value or _chinese_prose(value) or not _LATIN.search(value):
        return value
    if texts is None:
        texts = _strings(_resource(_TEXTS_PATH).get("texts"))
    return texts.get(value, PENDING_DESCRIPTION)


def _translate_value(label: str, value: str, texts: dict[str, str],
                     pattern: re.Pattern, mapping: dict[str, str]) -> str:
    if label.startswith(("详参·", "配件·")):
        return value  # The structured importer supplies Chinese keys and original SI units.
    if re.fullmatch(r"\?\s*Medals?", value, flags=re.IGNORECASE):
        return "奖章数量未公布"
    if value in texts:
        return texts[value]
    if _key(label) in _LONG_FIELDS:
        return localize_summary(value, texts=texts)
    if not _LATIN.search(value):
        return value
    # Preserve the meaning of impact in the fuse field, instead of treating it as damage.
    if _key(label) in {"fusetime", "引信时间"} and value.casefold() == "impact":
        return "碰撞引爆"
    translated = pattern.sub(lambda match: mapping[match.group().casefold()], value)
    units = {"rpm": "发/分钟", "mm": "毫米", "s": "秒", "m": "米"}
    translated = re.sub(r"(?P<number>\d)(?P<gap>\s*)(?P<unit>rpm|mm|s|m)\b",
                        lambda match: match["number"] + match["gap"]
                        + units[match["unit"].casefold()], translated, flags=re.IGNORECASE)
    translated = re.sub(r"/s\b", "/秒", translated)
    translated = re.sub(r"(?<![A-Za-z])x(?=\d)", "×", translated)
    translated = re.sub(r"\bP(\d+)\b", r"第\1页", translated)
    # Known identifiers are useful references, not missing prose translations.
    residual = pattern.sub("", translated)  # Reviewed names may retain their English reference.
    residual = re.sub(r"\b(?:[A-Z]{1,5}-\d+[A-Z]*|[A-Z]\d+(?:-\d+)+)\b", "", residual)
    if _LATIN.search(residual):
        if len(value) > 120 and not _chinese_prose(value):
            return PENDING_DESCRIPTION
        return "待译：" + translated
    return translated


def localize_value(label: str, value: str) -> str:
    """Translate reviewed terms and units while retaining every original numeric value."""
    _, texts, pattern, mapping = _resources()
    return _translate_value(label, value, texts, pattern, mapping)


def localize_entry(entry: CatalogEntry) -> CatalogEntry:
    """Return a display copy; search records, source URLs and revisions stay untouched."""
    terms, texts, pattern, mapping = _resources()
    labels = _strings(terms.get("field_labels"))
    subcategories = _strings(terms.get("subcategories"))
    name = entry.name
    english_name = entry.english_name
    if not _CJK.search(name):
        english_name = english_name or name
        name = next((alias for alias in entry.aliases if _CJK.search(alias)), name)
    return replace(
        entry, name=name, english_name=english_name,
        subcategory=" / ".join(subcategories.get(part.strip(), part.strip())
                               for part in entry.subcategory.split("/")),
        summary=localize_summary(entry.summary, texts=texts),
        fields=tuple((labels.get(_key(label), label),
                      _translate_value(label, value, texts, pattern, mapping))
                     for label, value in entry.fields),
    )
