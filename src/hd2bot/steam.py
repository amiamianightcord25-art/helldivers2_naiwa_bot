"""Read-only Steam announcements and Steam-only online counts for HELLDIVERS 2."""

import asyncio
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urlsplit

import aiohttp

from hd2bot.hd2.errors import HD2APIError, HD2SchemaError, HD2UnavailableError
from hd2bot.hd2.http import JSONHTTPClient
from hd2bot.hd2.service import DataResult
from hd2bot.presentation import CommandReply
from hd2bot.rendering.models import CardRow, CardSection, QueryCard
from hd2bot.services.cache import CacheResult, TTLCache

APP_ID = 553850
STEAM_BASE = "https://api.steampowered.com"
NEWS_PATH = (
    "/ISteamNews/GetNewsForApp/v2/?appid=553850&count=50&maxlength=0"
    "&format=json&feeds=steam_community_announcements"
)
PLAYERS_PATH = "/ISteamUserStats/GetNumberOfCurrentPlayers/v1/?appid=553850"
LOCAL_TZ = timezone(timedelta(hours=8))
_GID = re.compile(r"[0-9]{1,24}\Z")
_PATCH_TITLE = re.compile(r"\b(?:patch(?:\s+notes)?|hotfix)\b|补丁|热修复", re.I)
_SOURCE_PATH = re.compile(
    r"/news/externalpost/steam_community_announcements/[0-9]+/?\Z"
    r"|/news/app/553850/view/[0-9]+/?\Z"
)
_BLOCKS = {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "tr"}
_HIDDEN = {"script", "style", "iframe", "object"}


class _PlainHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in _HIDDEN:
            self.hidden += 1
        elif not self.hidden and tag in _BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _HIDDEN:
            self.hidden = max(0, self.hidden - 1)
        elif not self.hidden and tag in _BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def clean_steam_text(value: str) -> str:
    """Keep readable prose, omitting embedded media and HTML/BBCode markup."""
    value = re.sub(
        r"\[(img|previewyoutube|previewyoutubevideo|video|previewimg|youtube)(?:=[^\]]*)?\]"
        r".*?\[/\1\]", "\n", value, flags=re.I | re.S,
    )
    value = re.sub(r"\[\*\]", "\n• ", value)
    value = re.sub(r"\[/?(?:p|h[1-6]|list|olist|quote|table|tr)(?:=[^\]]*)?\]",
                   "\n", value, flags=re.I)
    value = re.sub(r"\[/?[a-z][a-z0-9_]*(?:[= ][^\]]*)?\]", "", value, flags=re.I)
    parser = _PlainHTML()
    parser.feed(value)
    parser.close()
    text = unescape("".join(parser.parts)).replace("\r", "")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def official_source_url(value: str) -> str | None:
    """Accept known Steam announcement routes, including Steam's CDN alias."""
    try:
        url = urlsplit(value)
        if url.scheme not in {"https", "http"} or url.username or url.password:
            return None
        if url.port not in {None, 80, 443} or url.query or url.fragment:
            return None
        if url.hostname in {"store.steampowered.com", "steamstore-a.akamaihd.net"}:
            if _SOURCE_PATH.fullmatch(url.path):
                return "https://store.steampowered.com" + url.path
        if url.hostname == "steamcommunity.com" and re.fullmatch(
            r"/games/553850/announcements/detail/[0-9]+/?", url.path,
        ):
            return "https://steamcommunity.com" + url.path
    except ValueError:
        pass
    return None


@dataclass(frozen=True)
class SteamNews:
    gid: str
    title: str
    contents: str
    url: str
    published_at: datetime
    patch: bool = False


def parse_news(payload) -> tuple[SteamNews, ...]:
    if not isinstance(payload, dict) or not isinstance(payload.get("appnews"), dict):
        raise HD2SchemaError("Steam news envelope is missing")
    envelope = payload["appnews"]
    rows = envelope.get("newsitems")
    if envelope.get("appid") != APP_ID or not isinstance(rows, list):
        raise HD2SchemaError("Steam news envelope is invalid")
    result: dict[str, SteamNews] = {}
    for row in rows:
        if not isinstance(row, dict) or row.get("feedname") != "steam_community_announcements":
            continue
        if row.get("appid") != APP_ID:
            continue
        gid = str(row.get("gid", ""))
        title, contents, url = row.get("title"), row.get("contents"), row.get("url")
        if not _GID.fullmatch(gid) or not all(isinstance(v, str) for v in (title, contents, url)):
            continue
        source = official_source_url(url)
        if source is None:
            continue
        stamp = row.get("date")
        if isinstance(stamp, bool) or not isinstance(stamp, (int, float)):
            continue
        try:
            published = datetime.fromtimestamp(stamp, UTC)
        except (ValueError, OverflowError, OSError):
            continue
        title = clean_steam_text(title).replace("\n", " ").strip()
        if not title:
            continue
        tags = row.get("tags")
        patch = (isinstance(tags, list) and "patchnotes" in tags) or bool(_PATCH_TITLE.search(title))
        result[gid] = SteamNews(gid, title, clean_steam_text(contents), source, published, patch)
    return tuple(sorted(result.values(), key=lambda item: item.published_at, reverse=True))


def _time(value: datetime) -> str:
    return value.astimezone(LOCAL_TZ).strftime("%Y-%m-%d %H:%M UTC+8")


class SteamService:
    """An optional service with its own lazy session, or a caller-owned session."""

    def __init__(self, settings, *, session: aiohttp.ClientSession | None = None):
        self.settings = settings
        self._session = session
        self._owns_session = session is None
        self._client: JSONHTTPClient | None = None
        self._cache = TTLCache()
        self._closed = False

    def _http(self) -> JSONHTTPClient:
        if self._closed:
            raise HD2UnavailableError("Steam service is closed")
        if self._client is None:
            if self._session is None:
                self._session = aiohttp.ClientSession()
            self._client = JSONHTTPClient(
                self._session, STEAM_BASE, provider="steam", timeout=self.settings.timeout,
                retries=self.settings.retries, headers={"User-Agent": "HD2-QQ-Bot/0.2"},
            )
        return self._client

    async def _load_news(self) -> tuple[SteamNews, ...]:
        if self.settings.provider == "mock":
            return (
                SteamNews("10003", "模拟补丁：武器平衡调整", "此为本地演示补丁，不是真实游戏更新。",
                          "https://store.steampowered.com/news/app/553850",
                          datetime(2026, 9, 16, 4, tzinfo=UTC), True),
                SteamNews("10002", "模拟官方公告：超级地球通讯", "此为本地演示公告，不是真实新闻。",
                          "https://store.steampowered.com/news/app/553850",
                          datetime(2026, 9, 15, 4, tzinfo=UTC)),
                SteamNews("10001", "模拟热修复：稳定性改进", "此为本地演示补丁，不是真实游戏更新。",
                          "https://store.steampowered.com/news/app/553850",
                          datetime(2026, 9, 14, 4, tzinfo=UTC), True),
            )
        return parse_news(await self._http().get(NEWS_PATH, ttl=0))

    async def _load_players(self) -> int:
        if self.settings.provider == "mock":
            return 12345
        payload = await self._http().get(PLAYERS_PATH, ttl=0)
        response = payload.get("response") if isinstance(payload, dict) else None
        count = response.get("player_count") if isinstance(response, dict) else None
        if (not isinstance(response, dict) or response.get("result") != 1
                or isinstance(count, bool) or not isinstance(count, int) or count < 0):
            raise HD2SchemaError("Steam player count is invalid")
        return count

    def _metadata(self, result: CacheResult) -> tuple[str, ...]:
        notes = ["来源：Steam 官方公告" if self.settings.provider != "mock" else "模拟数据 · 非真实新闻或在线人数"]
        if result.stale:
            notes.append("数据暂时无法更新，以下为最近缓存")
        notes.append("查询时间：" + _time(result.fetched_at))
        return tuple(notes)

    async def get_patch_notes(self) -> DataResult[tuple[SteamNews, ...]]:
        """Expose verified patches with the shared news cache's original sample time.

        Notification callers need to distinguish stale data from a genuine empty
        patch feed. Failures propagate to their existing retry policy.
        """
        if self._closed:
            raise HD2UnavailableError("Steam service is closed")
        async with asyncio.timeout(min(25, self.settings.timeout + 2)):
            result = await self._cache.get(
                "news", self._load_news, max(60, self.settings.order_ttl), self.settings.stale_ttl,
            )
        return DataResult(tuple(item for item in result.value if item.patch),
                          "mock" if self.settings.provider == "mock" else "steam",
                          result.fetched_at, result.stale)

    async def query(self, argument: str = "", *, patches_only: bool = False) -> CommandReply:
        """List three recent official posts, or show one of the latest 50 by news ID."""
        argument = argument.strip()
        if argument and not _GID.fullmatch(argument):
            return CommandReply("用法：更新（最新公告）· 补丁（更新补丁）· 更新 <新闻ID>（详情）。")
        if self._closed:
            return CommandReply("Steam 新闻服务暂时不可用，请稍后再试。")
        try:
            async with asyncio.timeout(min(25, self.settings.timeout + 2)):
                result = await self._cache.get(
                    "news", self._load_news, max(60, self.settings.order_ttl), self.settings.stale_ttl,
                )
        except (HD2APIError, TimeoutError):
            return CommandReply("Steam 新闻暂时无法取得，请稍后再试。")
        metadata = self._metadata(result)
        items = result.value
        if argument:
            selected = next((item for item in items if item.gid == argument), None)
            if selected is None:
                return CommandReply("未找到该新闻 ID；目前可查询最近 50 条官方公告。发送 更新 查看最新列表。")
            body = selected.contents or "此公告没有可显示的正文，请查看官方原文。"
            if len(body) > 32000:
                body = body[:32000] + "\n\n正文较长，以上为前 32,000 字；完整内容请查看官方原文。"
            category = "补丁 / 热修复" if selected.patch else "官方公告"
            text = (f"📰 {selected.title}\n类型：{category}\n发布时间：{_time(selected.published_at)}"
                    f"\n新闻 ID：{selected.gid}\n\n{body}\n\n官方原文：{selected.url}\n"
                    + "\n".join(metadata))
            card = None
            if len(body) > 600:
                card = QueryCard(
                    "Steam · " + category, selected.title, "HELLDIVERS 2 / STEAM",
                    sections=(CardSection("公告正文", (CardRow("", body),)),),
                    notices=tuple(note for note in metadata if "模拟" in note or "缓存" in note),
                    footer=(f"发布时间：{_time(selected.published_at)}", f"新闻 ID：{selected.gid}",
                            f"官方原文：{selected.url}", *metadata),
                )
            return CommandReply(text, card)
        if patches_only:
            items = tuple(item for item in items if item.patch)
        title = "Steam 补丁 / 热修复" if patches_only else "Steam 官方公告"
        if not items:
            return CommandReply(f"最近 50 条官方公告中暂无可用的{'补丁' if patches_only else '新闻'}。\n"
                                + "\n".join(metadata))
        lines = ["📰 " + title]
        for item in items[:3]:
            category = "补丁" if item.patch else "公告"
            lines.extend((f"\n[{category}] {item.title}", f"发布：{_time(item.published_at)}",
                          f"详情：更新 {item.gid}", f"原文：{item.url}"))
        lines.extend(("\n补丁按 Steam patchnotes 标签或明确的补丁标题识别。", *metadata))
        return CommandReply("\n".join(lines))

    async def players(self) -> CommandReply:
        """Steam counts are explicitly distinct from cross-platform war totals."""
        if self._closed:
            return CommandReply("Steam 在线人数暂时不可用，请稍后再试。")
        try:
            async with asyncio.timeout(min(25, self.settings.timeout + 2)):
                result = await self._cache.get(
                    "players", self._load_players, max(30, self.settings.cache_ttl),
                    self.settings.stale_ttl,
                )
        except (HD2APIError, TimeoutError):
            return CommandReply("Steam 在线人数暂时无法取得，请稍后再试。")
        notes = list(self._metadata(result))
        if self.settings.provider != "mock":
            notes[0] = "来源：Steam GetNumberOfCurrentPlayers（553850）"
        return CommandReply(f"👥 Steam 当前在线：{result.value:,} 人\n"
                            "仅含 Steam 平台，不代表全平台总人数。\n" + "\n".join(notes))

    async def close(self) -> None:
        self._closed = True
        self._cache.clear()
        if self._owns_session and self._session is not None:
            await self._session.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.close()
