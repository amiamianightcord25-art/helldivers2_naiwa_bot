"""Offline equipment catalogue, contextual choices and small text-first replies."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import re
import threading
import time
import unicodedata
import warnings
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit

from PIL import Image, UnidentifiedImageError

from hd2bot.presentation import ChatContext, CommandReply
from hd2bot.rendering.models import CardRow, CardSection, CardTile, QueryCard
from hd2bot.wiki.localization import localize_entry, localize_value
from hd2bot.wiki.models import CatalogEntry
from hd2bot.wiki.search import search_catalog

CATEGORY_LABELS = {
    "weapons": "武器", "stratagems": "战略配备", "armor": "盔甲",
    "boosters": "强化资源", "cosmetics": "装饰",
}
_CATEGORIES = {label: key for key, label in CATEGORY_LABELS.items()}
_SUBCATEGORY_ALIASES = {
    "突击步枪": ("assault rifle",), "狙击": ("sniper", "marksman"),
    "精确射手": ("marksman",), "冲锋枪": ("submachine", "smg"),
    "霰弹枪": ("shotgun",), "散弹枪": ("shotgun",), "能量": ("energy",),
    "爆炸": ("explosive",), "特殊": ("special",), "近战": ("melee",),
    "主武器": ("primary",), "副武器": ("secondary",), "手枪": ("pistol",),
    "投掷物": ("throwable", "grenade"), "手雷": ("grenade", "throwable"),
    "支援武器": ("support weapon",), "支援": ("support",),
    "轨道": ("orbital",), "飞鹰": ("eagle",), "背包": ("backpack",),
    "防御": ("defensive", "sentry", "fortification"), "炮塔": ("sentry",),
    "载具": ("vehicle",), "轻甲": ("light",), "中甲": ("medium",),
    "重甲": ("heavy",), "头盔": ("helmet",), "披风": ("cape",),
    "名片": ("card",), "表情": ("emote",), "胜利姿势": ("victory pose",),
    "称号": ("title",), "皮肤": ("skin", "pattern"),
}
_SUBCATEGORY_LABELS = {
    "Primary": "主武器", "Secondary": "副武器", "Throwable": "投掷物",
    "Assault Rifle": "突击步枪", "Marksman Rifle": "精确射手步枪",
    "Submachine Gun": "冲锋枪", "Shotgun": "霰弹枪", "Explosive": "爆炸武器",
    "Energy-Based": "能量武器", "Special": "特殊", "Melee": "近战",
    "Pistol": "手枪", "Standard": "标准", "Support Weapons": "支援武器",
    "Supply Permit": "补给配备", "Offensive Permit": "进攻配备",
    "Defensive Permit": "防御配备", "Orbital Strikes": "轨道打击",
    "Eagle Strikes": "飞鹰空袭", "Backpacks": "背包", "Vehicles": "载具",
    "Sentries": "哨戒炮", "Emplacements": "固定阵地", "Other": "其他",
    "General": "通用", "Mission": "任务", "Unavailable": "暂不可用",
    "Light": "轻甲", "Medium": "中甲", "Heavy": "重甲", "Helmets": "头盔",
    "Capes": "披风", "Player Cards": "玩家名片", "Emotes and Poses": "表情与姿势",
    "Vehicle Patterns": "载具涂装", "Weapon Patterns": "武器涂装",
    "Titles": "称号", "Boosters": "强化资源",
}
PAGE_SIZE = 8
_MAX_BYTES = 32 * 1024 * 1024
_LICENSE = "CC BY-NC-SA 4.0"
_LICENSE_URL = "https://creativecommons.org/licenses/by-nc-sa/4.0/"
_ZH_LICENSE_URL = "https://creativecommons.org/licenses/by-sa/4.0/deed.zh-hans"
_SOURCE_URL = "https://helldivers.wiki.gg/"
_WARBONDS_URL = "https://helldivers.wiki.gg/wiki/Warbonds"
_WARBOND_NOTICE = "本地 Wiki 资料快照，不代表实时轮换、商店状态或账号解锁进度。"
_FIELD_LABELS = {
    "weaponcategory": "武器分类", "weapontype": "类型", "firingmodes": "射击模式",
    "traits": "特性", "standarddamage": "基础伤害", "armorpenetration": "穿甲",
    "firerate": "射速", "capacity": "容量", "sparemags": "备用弹匣",
    "ergonomics": "操控性", "recoil": "后坐力", "reloadtime": "换弹时间",
    "source": "获取方式", "acquisition": "获取方式", "damage": "伤害",
    "dps": "每秒伤害", "armor": "护甲", "penetration": "穿透力",
    "speed": "速度", "stamina": "耐力", "passive": "被动效果",
    "description": "说明", "price": "价格", "cost": "费用", "warbond": "战争债券",
    "permittype": "配备分类", "stratagemcode": "配备指令", "basecooldown": "基础冷却",
    "unlocklevel": "解锁等级", "unlockcost": "解锁费用", "levelneeded": "所需等级",
    "levelearned": "获得等级", "sparerounds": "备用弹药", "spareshells": "备用霰弹",
    "supplyboxrefill": "补给箱补充", "ammoboxrefill": "弹药箱补充",
    "tacticalreloadtime": "战术换弹时间", "roundsreloadfulltime": "完全换弹时间",
    "reloadtimeforfirstround": "首发换弹时间", "reloadtimeforoneround": "单发换弹时间",
    "reloadtimefor1round": "后续弹药换弹时间", "cookable": "可预热引信",
    "fusetime": "引信时间", "outerradius": "外部半径", "emote": "表情动作",
    "victorypose": "胜利姿势", "exosuitcost": "机甲涂装费用",
    "hellpodcost": "空投舱涂装费用", "shuttlecost": "运输机涂装费用",
    "vehiclecost": "载具涂装费用",
}


@dataclass(frozen=True)
class _Snapshot:
    entries: tuple[CatalogEntry, ...]
    synced_at: str
    source_name: str
    source_url: str
    notices: tuple[str, ...]
    digest: str


@dataclass(frozen=True)
class _Choice:
    ids: tuple[str, ...]
    title: str
    page: int
    generation: int
    expires_at: float
    view: str = "概览"
    kind: str = "entry"
    subject: str = ""


@dataclass(frozen=True)
class _Warbond:
    key: str
    name: str
    english_name: str
    entries: tuple[CatalogEntry, ...]


def _plain(value: object, *, maximum: int = 16000, empty: bool = True) -> str:
    if not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip()):
        raise ValueError("Invalid catalogue text")
    if any(ord(char) < 32 and char not in "\n\r\t" for char in value):
        raise ValueError("Invalid catalogue control character")
    return value.strip()


def _wiki_url(value: object, *, empty: bool = False) -> str:
    value = _plain(value, maximum=2048, empty=empty)
    if empty and not value:
        return ""
    parsed = urlsplit(value)
    host = parsed.hostname or ""
    if (parsed.scheme != "https" or parsed.username or parsed.password
            or parsed.port not in {None, 443}
            or not (host == "helldivers.wiki.gg" or host.endswith(".helldivers.wiki.gg"))):
        raise ValueError("Invalid catalogue source URL")
    return value


def _norm(value: str) -> str:
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", value).casefold())


def _chinese_alias(entry: CatalogEntry) -> str:
    return next((alias for alias in entry.aliases if re.search(r"[\u3400-\u9fff]", alias)), "")


def _display_name(entry: CatalogEntry) -> str:
    # A real Chinese source name always wins over the manually maintained aliases.
    if re.search(r"[\u3400-\u9fff]", entry.name):
        name = entry.name
    else:
        name = _chinese_alias(entry) or entry.name
    if entry.code and _norm(entry.code) not in _norm(name):
        name += " · " + entry.code
    return name


def _english_name(entry: CatalogEntry) -> str:
    value = entry.english_name or entry.name
    return value if not re.search(r"[\u3400-\u9fff]", value) else ""


def _subcategory_label(value: str) -> str:
    return " / ".join(_SUBCATEGORY_LABELS.get(part.strip(), part.strip())
                      for part in value.split("/"))


def _field_label(label: str) -> str:
    return _FIELD_LABELS.get(_norm(label), label)


def _entry_fields(entry: CatalogEntry) -> dict[str, str]:
    return dict(entry.fields)


def _warbond_source(value: str) -> tuple[str, str]:
    """Return a stable English/source name and its localized display name."""
    source = re.sub(r"\s+P\d+$", "", value, flags=re.IGNORECASE)
    source = re.sub(r"\s+Premium Warbond$", "", source, flags=re.IGNORECASE)
    source = re.sub(r"\s+第[一二三四五六七八九十百\d]+页$", "", source)
    source = source.replace("Castellan’s", "Castellan's")
    if source.rstrip("!") == "Helldivers Mobilize":
        source = "Helldivers Mobilize!"
    display = localize_value("Source", source).removeprefix("待译：")
    return source, display


def _warbond_page(entry: CatalogEntry) -> int:
    value = _entry_fields(entry).get("战争债券页码", "")
    match = re.search(r"\d+", value)
    return int(match.group()) if match else 0


def _read_snapshot(path: Path) -> _Snapshot:
    # A size bound also prevents an accidentally copied full wiki dump blocking commands.
    with path.open("rb") as stream:
        content = stream.read(_MAX_BYTES + 1)
    if len(content) > _MAX_BYTES:
        raise ValueError("Catalogue exceeds size limit")
    payload = json.loads(content)
    if (not isinstance(payload, dict) or type(payload.get("schema_version")) is not int
            or payload.get("schema_version") != 1):
        raise ValueError("Unsupported catalogue schema")
    stamp = _plain(payload.get("synced_at"), maximum=100, empty=False)
    if datetime.fromisoformat(stamp.replace("Z", "+00:00")).utcoffset() is None:
        raise ValueError("Catalogue date needs a timezone")
    source = payload.get("source")
    if not isinstance(source, dict):
        raise ValueError("Missing catalogue attribution")
    source_name = _plain(source.get("name"), maximum=200, empty=False)
    source_url = _wiki_url(source.get("url"))
    license_name = _plain(source.get("license"), maximum=100, empty=False)
    if _norm(license_name) != _norm(_LICENSE):
        raise ValueError("Unsupported catalogue license")
    if (_plain(source.get("license_url", _LICENSE_URL), maximum=2048).rstrip("/")
            != _LICENSE_URL.rstrip("/")):
        raise ValueError("Invalid catalogue license URL")
    raw_entries = payload.get("entries")
    if not isinstance(raw_entries, list) or not raw_entries or len(raw_entries) > 20000:
        raise ValueError("Invalid catalogue entries")
    entries, ids = [], set()
    for item in raw_entries:
        if not isinstance(item, dict):
            raise ValueError("Invalid catalogue entry")
        identifier = _plain(item.get("id"), maximum=512, empty=False)
        if identifier in ids:
            raise ValueError("Duplicate catalogue id")
        ids.add(identifier)
        category = _plain(item.get("category"), maximum=100, empty=False)
        category = _CATEGORIES.get(category, category)
        if category not in CATEGORY_LABELS:
            raise ValueError("Unknown catalogue category")
        aliases = item.get("aliases", [])
        if not isinstance(aliases, list) or len(aliases) > 100:
            raise ValueError("Invalid catalogue aliases")
        fields = item.get("fields", [])
        if not isinstance(fields, list) or len(fields) > 100:
            raise ValueError("Invalid catalogue fields")
        pairs = []
        for pair in fields:
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                raise ValueError("Invalid catalogue field")
            pairs.append((_plain(pair[0], maximum=200, empty=False), _plain(pair[1])))
        chinese_source = item.get("chinese_detail_source", {})
        if not isinstance(chinese_source, dict):
            raise ValueError("Invalid Chinese catalogue source")
        chinese_fields = chinese_source.get("fields", [])
        source_urls = item.get("source_urls", [])
        if not isinstance(source_urls, list) or len(source_urls) > 30:
            raise ValueError("Invalid additional catalogue sources")
        if (not isinstance(chinese_fields, list) or len(chinese_fields) > 100
                or type(chinese_source.get("summary", False)) is not bool):
            raise ValueError("Invalid Chinese catalogue attribution")
        entries.append(CatalogEntry(
            id=identifier, category=category,
            name=_plain(item.get("name"), maximum=500, empty=False),
            english_name=_plain(item.get("english_name", ""), maximum=500),
            code=_plain(item.get("code", ""), maximum=100),
            aliases=tuple(_plain(alias, maximum=500, empty=False) for alias in aliases),
            subcategory=_plain(item.get("subcategory", ""), maximum=200),
            summary=_plain(item.get("summary", "")), fields=tuple(pairs),
            source_url=_wiki_url(item.get("source_url", ""), empty=True),
            revision=_plain(str(item.get("revision", "")), maximum=100),
            image_url=_wiki_url(item.get("image_url", ""), empty=True),
            image_path=_plain(item.get("image_path", ""), maximum=512),
            image_credit=_wiki_url(item.get("image_credit", ""), empty=True),
            revision_source_url=_wiki_url(item.get("revision_source_url", ""), empty=True),
            name_source_url=_wiki_url(item.get("name_source_url", ""), empty=True),
            name_revision=_plain(str(item.get("name_revision", "")), maximum=100),
            chinese_detail_source_url=_wiki_url(chinese_source.get("url", ""), empty=True),
            chinese_detail_revision=_plain(str(chinese_source.get("revision", "")), maximum=100),
            chinese_detail_fields=tuple(_plain(label, maximum=200, empty=False)
                                        for label in chinese_fields),
            chinese_detail_summary=chinese_source.get("summary", False),
            source_urls=tuple(_wiki_url(url) for url in source_urls),
        ))
    notices = payload.get("notices", [])
    if not isinstance(notices, list) or len(notices) > 30:
        raise ValueError("Invalid catalogue notices")
    return _Snapshot(tuple(entries), stamp, source_name, source_url,
                     tuple(_plain(item, maximum=2000) for item in notices),
                     hashlib.sha256(content).hexdigest())


class CatalogService:
    """Read local snapshots only; the independent updater owns all HTTP requests."""

    def __init__(self, snapshot_path: Path | None = None, *, clock=time.monotonic,
                 selection_ttl: float = 300, capacity: int = 512):
        if selection_ttl <= 0 or capacity < 1:
            raise ValueError("Selection TTL and capacity must be positive")
        self.snapshot_path = Path(snapshot_path) if snapshot_path is not None else None
        self.bundled_path = Path(__file__).resolve().parents[1] / "assets" / "wiki_catalog.json"
        self.clock, self.selection_ttl, self.capacity = clock, selection_ttl, capacity
        self._snapshot: _Snapshot | None = None
        self._by_id: dict[str, CatalogEntry] = {}
        self._generation = 0
        self._signature: tuple | None = None
        self._warning = ""
        self._loaded_path: Path | None = None
        self._choices: OrderedDict[tuple[str, ...], _Choice] = OrderedDict()
        self._lock = threading.RLock()

    @property
    def entries(self) -> tuple[CatalogEntry, ...]:
        with self._lock:
            self._refresh()
            return self._snapshot.entries if self._snapshot else ()

    @staticmethod
    def _stat(path: Path | None) -> tuple | None:
        if path is None:
            return None
        try:
            stat = path.stat()
            return (str(path), stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size)
        except OSError:
            return (str(path), None)

    def _refresh(self) -> None:
        signature = (self._stat(self.snapshot_path), self._stat(self.bundled_path))
        if signature == self._signature:
            return
        self._signature = signature
        paths = [self.snapshot_path] if self.snapshot_path is not None else []
        paths.append(self.bundled_path)
        failed_override = False
        for path in dict.fromkeys(paths):
            try:
                snapshot = _read_snapshot(path)
            except (OSError, ValueError, TypeError, OverflowError):
                if path == self.snapshot_path:
                    failed_override = True
                    # Keep the newest known-good data, rather than reverting to an older bundle.
                    if self._snapshot is not None:
                        self._warning = "本地更新文件暂不可用，继续使用最近有效快照。"
                        return
                continue
            if self._snapshot is None or self._snapshot.digest != snapshot.digest:
                self._generation += 1
                self._snapshot = snapshot
                self._by_id = {entry.id: entry for entry in snapshot.entries}
            self._loaded_path = path
            self._warning = "本地更新文件暂不可用，当前使用随附快照。" if failed_override else ""
            return
        if self._snapshot is not None:
            self._warning = "资料文件暂不可用，继续使用最近有效快照。"
        else:
            self._warning = "尚无可用的本地装备资料；维护者完成 Wiki 目录同步后即可查询。"

    def _context_key(self, context: ChatContext | None) -> tuple[str, ...] | None:
        if context is None:
            return ("cli",)
        if context.scope == "dms" and context.target_id and context.user_id:
            return ("dms", context.target_id, context.user_id)
        if context.scope == "c2c" and context.target_id:
            return ("c2c", context.target_id)
        user_id = getattr(context, "user_id", None)
        if context.scope in {"group", "channel"} and context.target_id and user_id:
            return (context.scope, context.target_id, user_id)
        return None

    def _prune(self) -> None:
        now = self.clock()
        for key in tuple(self._choices):
            if self._choices[key].expires_at <= now:
                del self._choices[key]

    def _remember(self, key: tuple[str, ...] | None, choice: _Choice) -> bool:
        if key is None:
            return False
        self._prune()
        self._choices[key] = choice
        self._choices.move_to_end(key)
        while len(self._choices) > self.capacity:
            self._choices.popitem(last=False)
        return True

    def handle(self, name: str, argument: str = "", *,
               context: ChatContext | None = None) -> CommandReply:
        with self._lock:
            self._refresh()
            self._prune()
            argument = unicodedata.normalize("NFKC", argument).strip()
            key = self._context_key(context)
            if name in {"选择", "下一页", "上一页"}:
                return self._continue(name, argument, key)
            if name == "资料状态":
                return self._status()
            if name == "百科" and not argument:
                return self._home()
            if not self._snapshot or not self._snapshot.entries:
                return CommandReply(self._warning or "本地装备目录为空，等待下一次资料同步。")
            if name == "战争债券":
                return self._warbonds(argument, key)
            category = _CATEGORIES.get(name)
            if name != "百科" and category is None:
                return CommandReply("可查询：百科、武器、战略配备、盔甲、强化资源、装饰。")
            if category is None:
                nested = argument.split(maxsplit=1)
                if nested and nested[0] in _CATEGORIES:
                    category = _CATEGORIES[nested[0]]
                    argument = nested[1] if len(nested) > 1 else ""
            entries = tuple(entry for entry in self._snapshot.entries
                            if category is None or entry.category == category)
            label = CATEGORY_LABELS.get(category, "装备百科")
            if not entries:
                return CommandReply(f"{label}目录尚未收录；可发送 资料状态 查看现有覆盖范围。")
            view = "概览"
            match = re.fullmatch(r"(.*?)(?:\s*)([详詳](?:[细細][参參][数數]|[参參])|配件)", argument)
            if match:
                argument = match[1].strip()
                view = "配件" if match[2] == "配件" else "详参"
            if argument in {"分类", "子类"}:
                return self._subcategories(entries, label)
            list_match = re.fullmatch(r"(?:列表|目录)(?:\s*(\d+))?", argument)
            if not argument or list_match:
                try:
                    page = int(list_match[1]) if list_match and list_match[1] else 1
                except ValueError:
                    # Python bounds decimal conversion; excessive user input is
                    # an invalid page, not a failed catalogue command.
                    return CommandReply(f"页码超出范围：共 {math.ceil(len(entries) / PAGE_SIZE)} 页。")
                return self._show_list(entries, label + "目录", key, page=page, view=view)
            # Subcategory keywords make browsing possible without knowing any weapon names.
            subcategory_query = re.sub(r"^(?:分类|子类)\s+", "", argument)
            sub_entries = self._filter_subcategory(subcategory_query, entries)
            found = search_catalog(argument, entries, category, limit=50, expand_families=True)
            if found.match is not None:
                if key is not None:
                    self._choices.pop(key, None)
                return self._detail(found.match, view=view)
            if sub_entries:
                return self._show_list(sub_entries, label + " · " + subcategory_query, key, view=view)
            if found.candidates:
                candidates = list(found.candidates)
                for offset in range(50, found.matches_total, 50):
                    candidates.extend(search_catalog(argument, entries, category,
                                                     limit=50, offset=offset, expand_families=True).candidates)
                return self._show_list(tuple(candidates), f"“{argument}”的候选", key, view=view)
            if key is not None:
                self._choices.pop(key, None)
            return CommandReply(f"没有找到“{argument}”。可用中文别名、英文片段或型号查询，"
                                f"也可发送 {label if category else '武器'} 列表 浏览名称。")

    def _warbond_groups(self) -> tuple[_Warbond, ...]:
        grouped: dict[str, dict[str, object]] = {}
        for entry in self._snapshot.entries if self._snapshot else ():
            fields = _entry_fields(entry)
            if "战争债券页码" not in fields:
                continue
            source = fields.get("Source", "") or fields.get("获取方式", "")
            if not source:
                continue
            english, name = _warbond_source(source)
            key = _norm(name)
            group = grouped.setdefault(key, {"name": name, "english": english, "entries": []})
            if re.search(r"[\u3400-\u9fff]", str(group["english"])) and not re.search(
                    r"[\u3400-\u9fff]", english):
                group["english"] = english
            group["entries"].append(entry)
        bonds = []
        for key, group in grouped.items():
            entries = tuple(sorted(group["entries"], key=lambda item: (
                _warbond_page(item), CATEGORY_LABELS[item.category], _display_name(item))))
            bonds.append(_Warbond(key, str(group["name"]), str(group["english"]), entries))
        return tuple(sorted(bonds, key=lambda bond: (
            0 if bond.english_name.rstrip("!") == "Helldivers Mobilize" else 1,
            bond.name.casefold(), bond.english_name.casefold())))

    def _warbonds(self, argument: str, key: tuple[str, ...] | None) -> CommandReply:
        bonds = self._warbond_groups()
        if not bonds:
            return CommandReply("当前本地 Wiki 快照尚未收录战争债券资料。")
        list_match = re.fullmatch(r"(?:列表|目录)(?:\s*(\d+))?", argument)
        if not argument or list_match:
            try:
                page = int(list_match[1]) if list_match and list_match[1] else 1
            except ValueError:
                page = 0
            return self._show_warbond_index(bonds, key, page)
        query = _norm(argument)
        exact = tuple(bond for bond in bonds if query in {
            _norm(bond.name), _norm(bond.english_name),
            _norm(bond.english_name.rstrip("!")),
        })
        matches = exact or tuple(bond for bond in bonds
                                 if query in _norm(bond.name) or query in _norm(bond.english_name))
        if len(matches) == 1:
            return self._warbond_detail(matches[0], key)
        if matches:
            return self._show_warbond_index(matches, key, 1,
                                            title=f"“{argument}”的战争债券候选")
        return CommandReply(f"没有找到战争债券“{argument}”。可发送 战争债券 浏览本地目录。\n"
                            + _WARBOND_NOTICE)

    def _show_warbond_index(self, bonds: tuple[_Warbond, ...], key: tuple[str, ...] | None,
                            page: int, *, title: str = "战争债券目录") -> CommandReply:
        pages = math.ceil(len(bonds) / PAGE_SIZE)
        if page < 1 or page > pages:
            return CommandReply(f"页码超出范围：共 {pages} 页。")
        choice = _Choice(tuple(bond.key for bond in bonds), title, page,
                         self._generation, self.clock() + self.selection_ttl,
                         kind="warbond_index")
        remembered = self._remember(key, choice)
        return self._list_reply(choice, remembered)

    def _warbond_detail(self, bond: _Warbond, key: tuple[str, ...] | None) -> CommandReply:
        choice = _Choice(tuple(entry.id for entry in bond.entries), bond.name + "战争债券", 1,
                         self._generation, self.clock() + self.selection_ttl,
                         kind="warbond_items", subject=bond.key)
        remembered = self._remember(key, choice)
        return self._list_reply(choice, remembered)

    def _warbond_list_reply(self, choice: _Choice, remembered: bool) -> CommandReply:
        bonds = {bond.key: bond for bond in self._warbond_groups()}
        pages = math.ceil(len(choice.ids) / PAGE_SIZE)
        start = (choice.page - 1) * PAGE_SIZE
        selected = choice.ids[start:start + PAGE_SIZE]
        if choice.kind == "warbond_index":
            page_bonds = [bonds[identifier] for identifier in selected if identifier in bonds]
            lines = [f"{choice.title} · 第 {choice.page}/{pages} 页 · 共 {len(choice.ids)} 项",
                     _WARBOND_NOTICE]
            rows = []
            for index, bond in enumerate(page_bonds, 1):
                english = ("（" + bond.english_name + "）"
                           if _norm(bond.english_name) != _norm(bond.name) else "")
                lines.append(f"{index}. {bond.name}{english} · 收录 {len(bond.entries)} 项")
                rows.append(CardRow(f"{index}. {bond.name}",
                                    f"{bond.english_name} · 收录 {len(bond.entries)} 项"))
            instruction = ("发送 选择 <本页序号> 查看关联装备；下一页 / 上一页 翻页。"
                           if remembered else "请用“战争债券 <名称>”查看关联装备。")
            lines.append(instruction)
            card = QueryCard(choice.title,
                             f"第 {choice.page}/{pages} 页 · 共 {len(choice.ids)} 项",
                             "HELLDIVERS 2 / WARBONDS",
                             sections=(CardSection("本地战争债券目录", tuple(rows), True),),
                             notices=(_WARBOND_NOTICE,),
                             footer=(instruction, f"资料快照：{self._snapshot.synced_at}",
                                     "来源：" + _WARBONDS_URL,
                                     "Helldivers Wiki 贡献者 · CC BY-NC-SA 4.0"))
            return CommandReply("\n".join(lines), card)

        bond = bonds.get(choice.subject)
        if bond is None:
            return CommandReply("战争债券资料已更新，请重新查询。")
        page_entries = [self._by_id[identifier] for identifier in selected]
        costs = list(dict.fromkeys(_entry_fields(entry).get("战争债券费用", "")
                                   for entry in bond.entries))
        costs = [localize_value("战争债券费用", value) for value in costs if value]
        max_page = max((_warbond_page(entry) for entry in bond.entries), default=0)
        english = ("（" + bond.english_name + "）"
                   if _norm(bond.english_name) != _norm(bond.name) else "")
        lines = [bond.name + english, _WARBOND_NOTICE,
                 "债券费用：" + (" / ".join(costs) or "Wiki 快照未记录"),
                 f"债券页数：{max_page or 'Wiki 快照未记录'}",
                 f"关联条目 · 第 {choice.page}/{pages} 页 · 共 {len(choice.ids)} 项"]
        rows = []
        for index, entry in enumerate(page_entries, 1):
            page = _warbond_page(entry)
            kind = " / ".join(filter(None, (
                CATEGORY_LABELS[entry.category], _subcategory_label(entry.subcategory))))
            detail = kind + (f" · 债券第 {page} 页" if page else "")
            lines.append(f"{index}. {_display_name(entry)} [{detail}]")
            rows.append(CardRow(f"{index}. {_display_name(entry)}", detail))
        instruction = ("发送 选择 <本页序号> 查看装备详情；下一页 / 上一页 翻页。"
                       if remembered else f"请用“战争债券 {bond.name}”重新打开可选择列表。")
        lines.append(instruction)
        overview = (CardRow("债券费用", " / ".join(costs) or "Wiki 快照未记录"),
                    CardRow("债券页数", str(max_page) if max_page else "Wiki 快照未记录"),
                    CardRow("本地关联条目", str(len(bond.entries))))
        card = QueryCard(bond.name, bond.english_name,
                         "HELLDIVERS 2 / WARBONDS",
                         sections=(CardSection("快照资料", overview, True),
                                   CardSection(f"关联装备 · 第 {choice.page}/{pages} 页",
                                               tuple(rows), True)),
                         notices=(_WARBOND_NOTICE,),
                         footer=(instruction, f"资料快照：{self._snapshot.synced_at}",
                                 "来源：" + _WARBONDS_URL,
                                 "Helldivers Wiki 贡献者 · CC BY-NC-SA 4.0"))
        return CommandReply("\n".join(lines), card)

    @staticmethod
    def _filter_subcategory(query: str, entries: tuple[CatalogEntry, ...]):
        normalized = _norm(query)
        if not normalized:
            return ()
        terms = (normalized,) + tuple(_norm(value)
                                     for value in _SUBCATEGORY_ALIASES.get(query, ()))
        return tuple(entry for entry in entries
                     if any(term in _norm(entry.subcategory)
                            or term in _norm(_subcategory_label(entry.subcategory))
                            for term in terms))

    def _home(self) -> CommandReply:
        counts = {key: 0 for key in CATEGORY_LABELS}
        for entry in self._snapshot.entries if self._snapshot else ():
            counts[entry.category] += 1
        lines = ["HELLDIVERS 2 装备百科", " · ".join(
            f"{label} {counts[key]}" for key, label in CATEGORY_LABELS.items()),
            "先发送 武器 列表 查看名称；武器 分类 查看子类。",
            "可以用型号、中文别名、英文片段或资料 ID 搜索，不用记全名。",
            "例如：武器 AR-23、武器 解放者、战略配备 飞鹰。",
            "战争债券：浏览本地 Wiki 快照中的债券及关联装备。",
            "重名或近似匹配会列出候选，发送 选择 1；下一页 / 上一页 翻页。",
            "资料状态：查看快照日期、来源与收录范围。"]
        if self._warning:
            lines.append(self._warning)
        return CommandReply("\n".join(lines))

    def _status(self) -> CommandReply:
        if self._snapshot is None:
            return CommandReply(self._warning)
        counts = {key: 0 for key in CATEGORY_LABELS}
        for entry in self._snapshot.entries:
            counts[entry.category] += 1
        lines = ["本地百科资料状态", f"已收录 {len(self._snapshot.entries)} 条装备资料",
                 " · ".join(f"{label} {counts[key]}" for key, label in CATEGORY_LABELS.items()),
                 f"静态战争债券 {len(self._warbond_groups())} 项",
                 "范围：以上装备目录；不表示整个 Wiki 已完整镜像。",
                 "查询直接读取本地快照，可离线使用。", *self._attribution(),
                 *self._snapshot.notices]
        if self._warning:
            lines.append(self._warning)
        return CommandReply("\n".join(lines))

    def _subcategories(self, entries: tuple[CatalogEntry, ...], label: str) -> CommandReply:
        counts: dict[str, int] = {}
        for entry in entries:
            subcategory = _subcategory_label(entry.subcategory) or "未分类"
            counts[subcategory] = counts.get(subcategory, 0) + 1
        lines = [f"{label}分类", *(f"• {key}（{value}）" for key, value in counts.items()),
                 f"发送 {label} <分类名或关键词> 查看；例如：武器 突击步枪。"]
        return CommandReply("\n".join(lines))

    def _show_list(self, entries: tuple[CatalogEntry, ...], title: str,
                   key: tuple[str, ...] | None, *, page: int = 1, view: str = "概览") -> CommandReply:
        if page < 1 or page > math.ceil(len(entries) / PAGE_SIZE):
            return CommandReply(f"页码超出范围：共 {math.ceil(len(entries) / PAGE_SIZE)} 页。")
        choice = _Choice(tuple(entry.id for entry in entries), title, page,
                         self._generation, self.clock() + self.selection_ttl, view)
        remembered = self._remember(key, choice)
        return self._list_reply(choice, remembered)

    def _list_reply(self, choice: _Choice, remembered: bool) -> CommandReply:
        if choice.kind in {"warbond_index", "warbond_items"}:
            return self._warbond_list_reply(choice, remembered)
        pages = math.ceil(len(choice.ids) / PAGE_SIZE)
        start = (choice.page - 1) * PAGE_SIZE
        view_label = "" if choice.view == "概览" else " · " + choice.view
        lines = [f"{choice.title}{view_label} · 第 {choice.page}/{pages} 页 · 共 {len(choice.ids)} 项"]
        page_entries = [self._by_id[identifier] for identifier in choice.ids[start:start + PAGE_SIZE]]
        for index, identifier in enumerate(choice.ids[start:start + PAGE_SIZE], 1):
            entry = self._by_id[identifier]
            names = _display_name(entry)
            english_name = _english_name(entry)
            if english_name and _norm(english_name) != _norm(names):
                names += "（" + english_name + "）"
            lines.append(f"{index}. {names} [{CATEGORY_LABELS[entry.category]}]"
                         + (f"\n   ID：{entry.id}" if not entry.code else ""))
        if remembered:
            lines.append("发送 选择 <本页序号> 查看详情；下一页 / 上一页 翻页。")
            lines.append(f"选择在 {self.selection_ttl / 60:g} 分钟后过期。")
        else:
            lines.append("当前会话无法确认发消息者，未保存选择；请用“百科 <型号或ID>”直接查询。")
        if page_entries and all(entry.category == "cosmetics" for entry in page_entries):
            tiles = tuple(CardTile(index, _display_name(entry),
                                   _subcategory_label(entry.subcategory), self._image_data(entry))
                          for index, entry in enumerate(page_entries, 1))
            instruction = ("回复 选择 <本页序号> 查看；下一页 / 上一页 翻页。"
                           if remembered else "请使用条目名称或 ID 查询。")
            card = QueryCard(choice.title + view_label,
                             f"第 {choice.page}/{pages} 页 · 共 {len(choice.ids)} 项", "HELLDIVERS 2 / COSMETICS",
                             gallery=tiles, footer=(instruction, f"候选保留 {self.selection_ttl / 60:g} 分钟。",
                                                     *self._detail_sources(page_entries[0])))
            return CommandReply("\n".join(lines), card)
        return CommandReply("\n".join(lines))

    def _continue(self, name: str, argument: str,
                  key: tuple[str, ...] | None) -> CommandReply:
        if key is None:
            return CommandReply("当前会话无法确认发消息者，不能使用编号选择；请直接查询资料名称或ID。")
        choice = self._choices.get(key)
        if choice is None:
            return CommandReply("没有可用的候选列表，或列表已过期。请重新发送 武器 列表 或搜索名称。")
        if choice.generation != self._generation:
            self._choices.pop(key, None)
            return CommandReply("装备资料已更新，旧候选列表已失效。请重新查询后选择，避免选错条目。")
        if name == "选择":
            if not re.fullmatch(r"[0-9]{1,2}", argument):
                return CommandReply("用法：选择 <本页序号>，例如 选择 1。")
            number = int(argument)
            start = (choice.page - 1) * PAGE_SIZE
            page_ids = choice.ids[start:start + PAGE_SIZE]
            if not 1 <= number <= len(page_ids):
                return CommandReply(f"请选择本页的 1–{len(page_ids)} 号。")
            if choice.kind == "warbond_index":
                bond = next((item for item in self._warbond_groups()
                             if item.key == page_ids[number - 1]), None)
                if bond is None:
                    self._choices.pop(key, None)
                    return CommandReply("战争债券资料已更新，请重新查询。")
                return self._warbond_detail(bond, key)
            self._choices.pop(key, None)
            return self._detail(self._by_id[page_ids[number - 1]], view=choice.view)
        if argument:
            return CommandReply(f"请直接发送 {name}，无需额外参数。")
        page = choice.page + (1 if name == "下一页" else -1)
        if page < 1 or page > math.ceil(len(choice.ids) / PAGE_SIZE):
            return CommandReply("已经是第一页。" if page < 1 else "已经是最后一页。")
        updated = _Choice(choice.ids, choice.title, page, choice.generation,
                          self.clock() + self.selection_ttl, choice.view,
                          choice.kind, choice.subject)
        self._remember(key, updated)
        return self._list_reply(updated, True)

    def _attribution(self, entry: CatalogEntry | None = None) -> tuple[str, ...]:
        snapshot = self._snapshot
        assert snapshot is not None
        origin = ((entry.revision_source_url or entry.source_url)
                  if entry else snapshot.source_url)
        result = (f"来源：{snapshot.source_name} 贡献者 · {origin or snapshot.source_url}",
                  f"资料快照：{snapshot.synced_at}")
        if "/zh/" not in origin:
            result += (f"英文Wiki资料及其中文整理 · {_LICENSE} · {_LICENSE_URL}",)
        has_chinese = (bool(entry.name_source_url or entry.chinese_detail_source_url)
                       or "/zh/" in origin) if entry else any(
                           item.name_source_url or item.chinese_detail_source_url
                           for item in snapshot.entries)
        if has_chinese:
            result += (f"中文Wiki原文及名称 · CC BY-SA 4.0 · {_ZH_LICENSE_URL}",)
        if entry and entry.source_url and entry.source_url != origin:
            result += ("装备页面：" + entry.source_url,)
        if entry and entry.name_source_url:
            result += ("中文名称来源：" + entry.name_source_url
                       + (" · 修订 " + entry.name_revision if entry.name_revision else ""),)
        if entry and entry.chinese_detail_source_url:
            translated_fields = "、".join(_field_label(label) for label in entry.chinese_detail_fields)
            if entry.chinese_detail_summary:
                translated_fields = "简介" + ("、" + translated_fields if translated_fields else "")
            result += ("中文原文来源：" + entry.chinese_detail_source_url
                       + (" · 修订 " + entry.chinese_detail_revision
                          if entry.chinese_detail_revision else "")
                       + ("（" + translated_fields + "）" if translated_fields else ""),)
        return result

    def _detail(self, entry: CatalogEntry, *, view: str = "概览") -> CommandReply:
        entry = localize_entry(entry)
        category = CATEGORY_LABELS[entry.category]
        title = _display_name(entry)
        english_name = _english_name(entry)
        subtitle = " · ".join(value for value in
                              (english_name if _norm(english_name) != _norm(title) else "",
                               category, _subcategory_label(entry.subcategory))
                              if value)
        lines = [title, subtitle]
        if entry.summary and view == "概览":
            lines.append(entry.summary)
        # Keep source diagnostics in the catalogue/status view, outside equipment facts.
        fields = [(label, value) for label, value in entry.fields if label != "Wiki 提醒"]
        more = []
        for prefix in ("详参", "配件"):
            if any(label.startswith(prefix + "·") for label, value in fields):
                more.append(f"{category} {entry.code or entry.id} {prefix}")
        if view != "概览":
            fields = [(k.removeprefix(view + "·"), v) for k, v in fields
                      if k.startswith(view + "·")]
            if not fields:
                return CommandReply(f"{title}\nWiki 暂无该装备的{view}记录。")
            title += " · " + view
            lines = [title, subtitle]
        else:
            fields = [(k, v) for k, v in fields if not k.startswith(("详参·", "配件·"))]
        acquisition_labels = {"获取方式", "解锁费用", "费用", "价格", "解锁等级", "所需等级",
                              "战争债券", "债券价格", "债券页码", "页解锁门槛", "获得条件",
                              "战争债券费用", "战争债券页码", "解锁页累计花费"}
        acquisition = [(label, value) for label, value in fields if label in acquisition_labels]
        acquisition = list(dict(acquisition).items())
        order = {label: index for index, label in enumerate(("解锁费用", "费用", "价格", "获取方式",
                 "解锁等级", "战争债券费用", "战争债券页码", "解锁页累计花费"))}
        acquisition.sort(key=lambda pair: order.get(pair[0], len(order)))
        statistics = [(label, value) for label, value in fields if label not in acquisition_labels]
        lines.extend(f"{_field_label(label)}：{value}" for label, value in [*acquisition, *statistics])
        footer = self._detail_sources(entry)
        if more and view == "概览":
            footer = (" · ".join(more), *footer)
        if self._warning:
            lines.append(self._warning)
        content = "\n".join([*lines, *footer])
        image_data = self._image_data(entry)
        if entry.category != "cosmetics" and not image_data and len(entry.fields) < 5 and len("\n".join(lines)) < 700:
            return CommandReply(content)
        sections = []
        if entry.category == "cosmetics" and not image_data:
            sections.append(CardSection("示意图", (CardRow("", "当前来源暂无可用示意图；以下保留真实资料。"),), full_width=True))
        if acquisition:
            sections.append(CardSection("获取与解锁", tuple(CardRow(k, v) for k, v in acquisition),
                                        full_width=True))
        if entry.summary and view == "概览":
            sections.append(CardSection("简介", (CardRow("", entry.summary),), full_width=True))
        section_count = (len(statistics) + 11) // 12
        if section_count > 2:
            section_count = (section_count + 1) // 2 * 2
        per_section = math.ceil(len(statistics) / section_count) if section_count else 12
        for start in range(0, len(statistics), per_section):
            section_title = "装备资料" if view == "概览" else view
            sections.append(CardSection(section_title if start == 0 else section_title + " · 续",
                                        tuple(CardRow(_field_label(label), value)
                                              for label, value in statistics[start:start + per_section]),
                                        full_width=section_count == 1))
        card = QueryCard(title, subtitle, "HELLDIVERS 2 / WIKI", sections=tuple(sections), columns=2,
                         notices=(self._warning,) if self._warning else (),
                         footer=footer,
                         image_data=image_data)
        return CommandReply(content, card)

    def _detail_sources(self, entry: CatalogEntry) -> tuple[str, ...]:
        """Compact attribution; full revisions, file credits and notices remain in JSON."""
        english = entry.revision_source_url or entry.source_url
        sources = list(dict.fromkeys(url for url in (
            english, entry.name_source_url, entry.chinese_detail_source_url, *entry.source_urls,
        ) if url))
        licenses = []
        if any("/zh/" not in url for url in sources):
            licenses.append("英文/译文 CC BY-NC-SA 4.0")
        if any("/zh/" in url for url in sources):
            licenses.append("中文 CC BY-SA 4.0")
        date = datetime.fromisoformat(self._snapshot.synced_at.replace("Z", "+00:00"))
        return (f"Helldivers Wiki 贡献者 · {' / '.join(licenses)} · {date:%Y-%m-%d}",
                *("来源：" + unquote(url) for url in sources))

    def _image_data(self, entry: CatalogEntry) -> str:
        """Never fetch image URLs; validate bounded snapshot assets before embedding."""
        if not entry.image_path or self._loaded_path is None:
            return ""
        name = entry.image_path
        if (not re.fullmatch(r"[A-Za-z0-9_-]+\.(?:png|jpe?g|webp|svg)", name, re.IGNORECASE)
                or Path(name).name != name):
            return ""
        directory = self._loaded_path.parent / "wiki_images"
        path = directory / name
        try:
            # resolve checks Windows junctions as well as symlinks, including a linked directory.
            if (directory.resolve() != directory.parent.resolve() / directory.name
                    or path.resolve().parent != directory.resolve() or path.is_symlink()):
                return ""
            with path.open("rb") as stream:
                content = stream.read(5 * 1024 * 1024 + 1)
            if len(content) > 5 * 1024 * 1024:
                return ""
            if path.suffix.lower() == ".svg":
                from hd2bot.wiki.images import validated_svg
                content = validated_svg(content)
                return "data:image/svg+xml;base64," + base64.b64encode(content).decode("ascii")
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(content)) as picture:
                    mime = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}.get(
                        picture.format)
                    if (mime is None or max(picture.size) > 8192
                            or picture.width * picture.height > 20_000_000):
                        return ""
                    picture.verify()
                with Image.open(io.BytesIO(content)) as picture:
                    picture.load()
            return "data:" + mime + ";base64," + base64.b64encode(content).decode("ascii")
        except (OSError, ValueError, SyntaxError, EOFError, UnidentifiedImageError,
                Image.DecompressionBombError, Image.DecompressionBombWarning):
            return ""
