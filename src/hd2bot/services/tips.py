"""Locally curated loading-screen tips, with attributed Chinese translations."""

from __future__ import annotations

import json
import math
import random
import re
import time
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

from hd2bot.hd2.errors import CommandError
from hd2bot.presentation import ChatContext, CommandButton, CommandReply

DEFAULT_PATH = Path(__file__).resolve().parents[1] / "assets" / "loading_tips.json"
_MAX_BYTES = 1_048_576
_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,47}\Z")
_TRANSLATION_STATUS = {"reference_translation", "verified_zh_cn"}
_FIELDS = {"id", "text", "english", "source_url", "translation_status"}
_NEXT_BUTTON = ((CommandButton("再来一条", "小贴士"),),)


@dataclass(frozen=True)
class LoadingTip:
    id: str
    text: str
    english: str
    source_url: str
    translation_status: str


def _plain_text(value: object, *, maximum: int) -> bool:
    return (isinstance(value, str) and 1 <= len(value) <= maximum
            and value == value.strip()
            and not any(unicodedata.category(char).startswith("C") for char in value))


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate loading tips JSON key")
        result[key] = value
    return result


def _valid_source(value: object) -> bool:
    if not _plain_text(value, maximum=500) or any(char.isspace() for char in value):
        return False
    try:
        url = urlsplit(value)
        return (url.scheme == "https" and bool(url.hostname) and url.username is None
                and url.password is None)
    except ValueError:
        return False


def _validate_date(value: object) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("invalid loading tips collection date")
    date.fromisoformat(value)


def load_tips(path: Path | None = None) -> tuple[LoadingTip, ...]:
    """Validate the complete catalogue before returning any usable tip.

    Translation status is explicit per entry: a translated wiki quote cannot
    silently become a verified quote from the game's Simplified Chinese UI.
    """
    with (path if path is not None else DEFAULT_PATH).open("rb") as stream:
        raw = stream.read(_MAX_BYTES + 1)
    if len(raw) > _MAX_BYTES:
        raise ValueError("loading tips catalogue too large")
    payload = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique_object)
    if (not isinstance(payload, dict)
            or not {"schema", "items"}.issubset(payload)
            or set(payload) - {"schema", "items", "collected_at", "sources", "attribution", "license"}
            or type(payload["schema"]) is not int or payload["schema"] != 1):
        raise ValueError("invalid loading tips schema")
    if "collected_at" in payload:
        _validate_date(payload["collected_at"])
    for field in ("attribution", "license"):
        if field in payload and not _plain_text(payload[field], maximum=1000):
            raise ValueError("invalid loading tips attribution")
    if "sources" in payload:
        sources = payload["sources"]
        if not isinstance(sources, list) or not 1 <= len(sources) <= 100:
            raise ValueError("invalid loading tips sources")
        for source in sources:
            if (not isinstance(source, dict)
                    or not {"name", "url", "retrieved_at"}.issubset(source)
                    or set(source) - {"name", "url", "retrieved_at", "revision"}
                    or not _plain_text(source["name"], maximum=160)
                    or not _valid_source(source["url"])):
                raise ValueError("invalid loading tips source metadata")
            _validate_date(source["retrieved_at"])
            if "revision" in source and not _plain_text(source["revision"], maximum=100):
                raise ValueError("invalid loading tips source revision")
    items = payload["items"]
    if not isinstance(items, list) or not 1 <= len(items) <= 1000:
        raise ValueError("invalid loading tips items")
    result = []
    seen = set()
    for item in items:
        if not isinstance(item, dict) or set(item) != _FIELDS:
            raise ValueError("invalid loading tip fields")
        tip_id = item["id"]
        if not isinstance(tip_id, str) or not _ID.fullmatch(tip_id) or tip_id in seen:
            raise ValueError("invalid or duplicate loading tip ID")
        if (not _plain_text(item["text"], maximum=600)
                or not re.search(r"[\u3400-\u9fff]", item["text"])
                or not _plain_text(item["english"], maximum=1200)):
            raise ValueError("invalid loading tip text")
        status = item["translation_status"]
        if not isinstance(status, str) or status not in _TRANSLATION_STATUS:
            raise ValueError("invalid loading tip translation status")
        if not _valid_source(item["source_url"]):
            raise ValueError("invalid loading tip source")
        seen.add(tip_id)
        result.append(LoadingTip(**item))
    return tuple(result)


class TipsService:
    """Serve tips without network access or an unbounded conversation history.

    Public conversations share a last-seen tip across members. Private chats
    include the user ID, so a shared guild DMS target cannot mix two users.
    Synchronous selection and bookkeeping are atomic within the bot event loop.
    """

    def __init__(self, path: Path | None = None, *, rng=None, clock=time.monotonic,
                 max_sessions: int = 4096, session_ttl: float = 3600):
        if type(max_sessions) is not int or max_sessions < 1:
            raise ValueError("max_sessions must be a positive integer")
        if (isinstance(session_ttl, bool) or not isinstance(session_ttl, (int, float))
                or not math.isfinite(session_ttl) or session_ttl <= 0):
            raise ValueError("session_ttl must be positive and finite")
        self.tips = load_tips(path)
        self.rng = rng if rng is not None else random.SystemRandom()
        self.clock = clock
        self.max_sessions = max_sessions
        self.session_ttl = session_ttl
        self._by_id = {tip.id: index for index, tip in enumerate(self.tips)}
        self._recent: OrderedDict[tuple[str, str, str], tuple[str, float]] = OrderedDict()

    @staticmethod
    def _session_key(context: ChatContext | None) -> tuple[str, str, str]:
        if context is None:
            return "cli", "", ""
        if (context.scope not in {"c2c", "group", "dms", "channel"}
                or not isinstance(context.target_id, str) or not context.target_id.strip()):
            raise CommandError("无法识别当前会话，请重新发送“小贴士”。")
        return (context.scope, context.target_id,
                (context.user_id or "") if context.is_private else "")

    def _prune(self, now: float) -> None:
        while self._recent:
            _, (_, seen_at) = next(iter(self._recent.items()))
            if now - seen_at < self.session_ttl:
                break
            self._recent.popitem(last=False)

    def _sources_reply(self) -> CommandReply:
        reference_count = sum(tip.translation_status == "reference_translation" for tip in self.tips)
        lines = [f"🦅 加载小贴士来源 · 共 {len(self.tips)} 条",
                 "内容摘自网上收录的 HELLDIVERS 2 加载画面提示，按条目保留英文原文和来源。"]
        if reference_count:
            lines.append(f"其中 {reference_count} 条中文为参考翻译，尚未核实为游戏内官方简体原文。")
        if reference_count < len(self.tips):
            lines.append(f"其中 {len(self.tips) - reference_count} 条标注为已核对游戏内简体中文。")
        sources = list(dict.fromkeys(tip.source_url for tip in self.tips))
        lines.extend(sources[:5])
        if len(sources) > 5:
            lines.append("更多来源可通过“小贴士 编号”查看对应条目。")
        lines.extend(("部分提示是游戏世界观中的幽默宣传语。",
                      f"发送 小贴士 随机查看，或 小贴士 1～{len(self.tips)} 指定编号。"))
        return CommandReply("\n".join(lines), keyboard=_NEXT_BUTTON)

    def reply(self, argument: str = "", *, context: ChatContext | None = None) -> CommandReply:
        if not isinstance(argument, str) or len(argument) > 64:
            raise CommandError("用法：小贴士 / 小贴士 编号 / 小贴士 来源。")
        argument = unicodedata.normalize("NFKC", argument).strip().casefold()
        if argument in {"来源", "source", "sources"}:
            return self._sources_reply()
        index = None
        if argument:
            number = argument.removeprefix("#")
            if re.fullmatch(r"[0-9]{1,4}", number):
                if not 1 <= int(number) <= len(self.tips):
                    raise CommandError(f"小贴士编号范围是 1～{len(self.tips)}。例如：小贴士 1")
                index = int(number) - 1
            elif argument in self._by_id:
                index = self._by_id[argument]
            else:
                raise CommandError("用法：小贴士 / 小贴士 编号 / 小贴士 来源。")
        key = self._session_key(context)
        now = self.clock()
        self._prune(now)
        if index is None:
            previous_id = self._recent.get(key, (None, 0))[0]
            choices = tuple(i for i, tip in enumerate(self.tips) if tip.id != previous_id)
            index = self.rng.choice(choices or (0,))
        tip = self.tips[index]
        self._recent[key] = (tip.id, now)
        self._recent.move_to_end(key)
        while len(self._recent) > self.max_sessions:
            self._recent.popitem(last=False)
        translation = ("中文为参考翻译。"
                       if tip.translation_status == "reference_translation"
                       else "中文已核对游戏内简体原文。")
        lines = [f"🦅 加载小贴士 · {index + 1}/{len(self.tips)}", "", tip.text,
                 "", translation,
                 "出处：小贴士 来源 · 点击“再来一条”继续。"]
        return CommandReply("\n".join(lines), keyboard=_NEXT_BUTTON)
