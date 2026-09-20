"""Resolve certain names directly and leave ambiguous or approximate names to users.

The small traditional-character map covers common equipment vocabulary. It is
deliberately not a claim to provide complete Chinese language conversion; known
regional names should be recorded as explicit aliases in the catalogue.
"""

import re
import unicodedata
from difflib import SequenceMatcher

from hd2bot.wiki.models import CatalogEntry, CatalogSearchResult

_TRADITIONAL = str.maketrans({
    "戰": "战", "備": "备", "護": "护", "強": "强", "資": "资", "裝": "装",
    "飾": "饰", "機": "机", "槍": "枪", "懲": "惩", "罰": "罚", "愛": "爱",
    "國": "国", "參": "参", "議": "议", "員": "员", "鐵": "铁", "衝": "冲",
    "鋒": "锋", "極": "极", "壞": "坏", "衛": "卫", "飛": "飞", "彈": "弹",
    "毀": "毁", "滅": "灭", "軌": "轨", "鐳": "镭", "電": "电", "離": "离",
    "榴": "榴", "發": "发", "煙": "烟", "霧": "雾", "燃": "燃", "燒": "烧",
    "體": "体", "輕": "轻", "類": "类", "軍": "军", "無": "无", "畏": "畏",
    "遠": "远", "偵": "侦", "敵": "敌", "導": "导", "隱": "隐", "獵": "猎",
    "殺": "杀", "鷹": "鹰", "鷲": "鹫", "龍": "龙", "騎": "骑", "士": "士",
    "馬": "马", "鋼": "钢", "錘": "锤", "錐": "锥", "鋸": "锯", "鋰": "锂",
    "錫": "锡", "銀": "银", "閃": "闪", "擊": "击", "爆": "爆", "擲": "掷",
    "礦": "矿", "擴": "扩", "優": "优", "壓": "压", "應": "应", "醫": "医",
    "療": "疗", "傷": "伤", "維": "维", "斷": "断", "絲": "丝", "繩": "绳",
    "綠": "绿", "紅": "红", "藍": "蓝", "黃": "黄", "黑": "黑", "白": "白",
    "號": "号", "絕": "绝", "終": "终", "審": "审", "決": "决", "墜": "坠",
    "襲": "袭", "奪": "夺", "進": "进", "連": "连", "迴": "回", "迸": "迸",
    "開": "开", "關": "关", "閉": "闭", "鎖": "锁", "鏈": "链", "網": "网",
    "輔": "辅", "助": "助", "補": "补", "給": "给", "標": "标", "誌": "志",
    "榮": "荣", "譽": "誉", "頌": "颂", "領": "领", "靈": "灵", "魂": "魂",
    "聖": "圣", "詠": "咏", "誓": "誓", "頑": "顽", "復": "复", "仇": "仇",
    "勳": "勋", "績": "绩", "獎": "奖", "賞": "赏", "鬥": "斗", "競": "竞",
    "隊": "队", "師": "师", "將": "将", "帥": "帅", "尉": "尉", "職": "职",
    "學": "学", "術": "术", "實": "实", "驗": "验", "測": "测", "試": "试",
    "噴": "喷", "濺": "溅", "漿": "浆", "濃": "浓", "凍": "冻", "熱": "热",
    "溫": "温", "適": "适", "節": "节", "約": "约", "劑": "剂", "膚": "肤",
    "織": "织", "紋": "纹", "繡": "绣", "縫": "缝", "棉": "棉", "襯": "衬",
    "頭": "头", "盔": "盔", "靴": "靴", "臂": "臂", "肢": "肢", "殘": "残",
    "盡": "尽", "動": "动", "轉": "转", "驅": "驱", "載": "载", "駕": "驾",
    "駛": "驶", "攜": "携", "帶": "带", "運": "运", "輸": "输", "艙": "舱",
    "殲": "歼", "滲": "渗", "透": "透", "尋": "寻", "響": "响", "聲": "声",
    "錨": "锚", "貫": "贯", "徑": "径", "劍": "剑", "斧": "斧", "刃": "刃",
    "傑": "杰", "爾": "尔", "達": "达", "羅": "罗", "爛": "烂", "剋": "克",
})
_MODEL_CODE = re.compile(r"[a-z]{1,8}\d+[a-z]*\Z")
_LATIN_WORD = re.compile(r"[a-zA-Z]+")


def normalize_catalog_name(value: str) -> str:
    """Ignore case, width, punctuation and whitespace in equipment names."""
    if not isinstance(value, str):
        return ""
    normalized = unicodedata.normalize("NFKC", value).casefold().translate(_TRADITIONAL)
    return "".join(character for character in normalized if character.isalnum())


def _names(entry: CatalogEntry) -> set[str]:
    code = normalize_catalog_name(entry.code)
    names = {
        name for value in (entry.name, entry.english_name, entry.code, *entry.aliases)
        if (name := normalize_catalog_name(value))
    }
    # The wiki commonly embeds the model code in the page title. Its actual name
    # must still be exact-searchable, e.g. "Liberator" in "AR-23 Liberator".
    if code:
        names |= {name[len(code):] for name in names if name.startswith(code) and name != code}
    return names


def _acronyms(entry: CatalogEntry) -> set[str]:
    result = set()
    code = normalize_catalog_name(entry.code)
    for value in (entry.english_name, entry.name):
        if not value:
            continue
        # Exclude the weapon code rather than turning "APW-1 Anti-Materiel Rifle"
        # into the unhelpful abbreviation "AAMR".
        if code and normalize_catalog_name(value).startswith(code):
            consumed = 0
            for index, character in enumerate(unicodedata.normalize("NFKC", value)):
                consumed += bool(character.isalnum())
                if consumed == len(code):
                    value = unicodedata.normalize("NFKC", value)[index + 1:]
                    break
        words = _LATIN_WORD.findall(value)
        if 2 <= len(words) <= 6 and all(len(word) >= 2 for word in words):
            result.add("".join(word[0] for word in words).casefold())
    return result


def _tie_breaker(entry: CatalogEntry) -> tuple[str, str, str, str]:
    return (entry.category, normalize_catalog_name(entry.name),
            normalize_catalog_name(entry.code), entry.id)


def _result(
    entries: list[CatalogEntry], *, exact: bool, auto_select: bool, limit: int, offset: int,
) -> CatalogSearchResult:
    if len(entries) == 1 and auto_select:
        return CatalogSearchResult(match=entries[0], exact=exact, matches_total=1)
    return CatalogSearchResult(
        candidates=entries[offset:offset + limit], exact=exact, matches_total=len(entries),
    )


def search_catalog(
    query: str,
    entries: list[CatalogEntry],
    category: str | None = None,
    *,
    limit: int = 8,
    offset: int = 0,
    expand_families: bool = False,
) -> CatalogSearchResult:
    """Search names and aliases, preserving uncertainty for a numbered choice.

    A unique explicit code, full name, alias, or meaningful name fragment can
    resolve directly. Model-code prefixes, derived acronyms and spelling
    approximations are suggestions even when only one candidate remains.
    Pagination applies to candidates; ``matches_total`` counts all matches.
    """
    key = normalize_catalog_name(query)
    if not key:
        return CatalogSearchResult()
    limit = max(1, min(limit, 50))
    offset = max(0, offset)
    unique: dict[str, CatalogEntry] = {}
    for entry in entries:
        if category is None or entry.category == category:
            unique.setdefault(entry.id, entry)
    records = sorted(unique.values(), key=_tie_breaker)

    # Copyable stable IDs also disambiguate entries without model codes.
    identifiers = [entry for entry in records if ":" in query
                   and entry.id.casefold() == query.strip().casefold()]
    if identifiers:
        return _result(identifiers, exact=True, auto_select=True, limit=limit, offset=offset)

    # In family browsing mode a base code includes its letter-suffixed variants.
    # AR23 lists AR23/AR23A/AR23C/AR23P, while AR230 is a different model number.
    codes = [entry for entry in records if normalize_catalog_name(entry.code) == key]
    if codes:
        if expand_families and _MODEL_CODE.fullmatch(key):
            family = []
            for entry in records:
                candidate = normalize_catalog_name(entry.code)
                suffix = candidate[len(key):] if candidate.startswith(key) else ""
                if candidate == key or (candidate.startswith(key) and suffix.isalpha()):
                    family.append(entry)
            if len(family) > len(codes):
                family.sort(key=lambda entry: (normalize_catalog_name(entry.code) != key,
                                              normalize_catalog_name(entry.code), _tie_breaker(entry)))
                return _result(family, exact=False, auto_select=False, limit=limit, offset=offset)
        return _result(codes, exact=True, auto_select=True, limit=limit, offset=offset)

    indexed = [(entry, _names(entry)) for entry in records]
    exact = [entry for entry, names in indexed if key in names]
    if exact:
        if expand_families and not any(character.isdigit() for character in key):
            # A bare family name may also be the base model's exact name. Keep
            # all matching variants visible; fully specified names and variant codes remain usable.
            family = [entry for entry, names in indexed
                      if any(name.startswith(key) for name in names)]
            if len(family) > len(exact):
                exact_ids = {entry.id for entry in exact}
                family.sort(key=lambda entry: (entry.id not in exact_ids, _tie_breaker(entry)))
                return _result(family, exact=False, auto_select=False, limit=limit, offset=offset)
        return _result(exact, exact=True, auto_select=True, limit=limit, offset=offset)

    # One character is only useful as an explicitly curated alias. Numerals
    # alone must not accidentally select a model with an unrelated code.
    if len(key) < 2 or key.isdecimal():
        return CatalogSearchResult()
    is_model = bool(_MODEL_CODE.fullmatch(key))
    if is_model:
        family = [entry for entry in records if normalize_catalog_name(entry.code).startswith(key)]
        family.sort(key=lambda entry: (len(normalize_catalog_name(entry.code)), _tie_breaker(entry)))
        return _result(family, exact=False, auto_select=False, limit=limit, offset=offset)

    partial = []
    for entry, names in indexed:
        scores = [len(key) / len(name) + (0.1 if name.startswith(key) else 0)
                  for name in names if key in name]
        if scores:
            partial.append((entry, max(scores)))
    if partial:
        partial.sort(key=lambda item: (-item[1], _tie_breaker(item[0])))
        # Two Latin letters are useful for browsing a code family, but are too
        # short to treat a sole substring match as the user's certain intent.
        confident = not (key.isascii() and len(key) < 3)
        return _result([entry for entry, _ in partial], exact=False, auto_select=confident,
                       limit=limit, offset=offset)

    acronym = [entry for entry in records if key in _acronyms(entry)]
    if acronym:
        return _result(acronym, exact=False, auto_select=False, limit=limit, offset=offset)

    # Fuzzy suggestions are conservative and never auto-selected. Very short
    # strings and differing model numbers otherwise produce unrelated matches.
    if len(key) < (4 if key.isascii() else 3) or any(character.isdigit() for character in key):
        return CatalogSearchResult()
    cutoff = 0.78 if key.isascii() else 0.74
    suggestions = []
    for entry, names in indexed:
        eligible = [name for name in names if not any(character.isdigit() for character in name)]
        score = max((SequenceMatcher(None, key, name, autojunk=False).ratio()
                     for name in eligible), default=0)
        if score >= cutoff:
            suggestions.append((entry, score))
    suggestions.sort(key=lambda item: (-item[1], _tie_breaker(item[0])))
    return _result([entry for entry, _ in suggestions], exact=False, auto_select=False,
                   limit=limit, offset=offset)
