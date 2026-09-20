"""Readable dispatches and supply-line listings, independent of any chat adapter."""

import re
from datetime import UTC, timedelta, timezone

from hd2bot.hd2.models import Dispatch, Planet
from hd2bot.services.localization import clean_text, faction_name

DISPLAY_ZONE = timezone(timedelta(hours=8))
NEWS_HISTORY_LIMIT = 25
NEWS_PAGE_SIZE = 5


def parse_dispatch_argument(argument: str) -> tuple[str, int]:
    """Return ``("page", page)`` or ``("detail", dispatch_id)`` for 新闻."""
    value = argument.strip()
    if not value or value == "列表":
        return "page", 1
    page = re.fullmatch(r"列表\s+([1-9]\d{0,19})", value)
    if page is not None:
        return "page", int(page.group(1))
    if re.fullmatch(r"\d{1,20}", value):
        return "detail", int(value)
    raise ValueError("新闻用法：新闻（最近列表）、新闻 列表 [页码]、新闻 <战报ID>。")


def _published_text(item: Dispatch) -> str:
    if item.published_at is None:
        return "发布时间未知"
    stamp = item.published_at
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return stamp.astimezone(DISPLAY_ZONE).strftime("%Y-%m-%d %H:%M UTC+8")


def _ordered_dispatches(items: list[Dispatch]) -> list[Dispatch]:
    return sorted(items, key=lambda item: item.id, reverse=True)


def format_dispatches(items: list[Dispatch], *, page: int = 1, page_size: int = 3,
                      history_limit: int = NEWS_HISTORY_LIMIT) -> str:
    if not items:
        return "【银河新闻】\n暂无近期银河新闻。"
    page = max(1, page)
    page_size = max(1, page_size)
    history_limit = max(1, history_limit)
    ordered = _ordered_dispatches(items)[:history_limit]
    pages = max(1, (len(ordered) + page_size - 1) // page_size)
    if page > pages:
        return (f"【银河新闻】最近 {len(ordered)} 条 · 第 {page}/{pages} 页\n"
                "没有这一页；请发送 新闻 列表 查看有效页码。")
    start = (page - 1) * page_size
    shown = ordered[start:start + page_size]
    if page_size == 3 and page == 1:
        heading = f"最新 {len(shown)} 条"
    else:
        heading = f"最近 {len(ordered)} 条 · 第 {page}/{pages} 页（每页 {page_size} 条）"
    lines = [f"【银河新闻】{heading}"]
    for item in shown:
        lines.extend(["", f"战报 #{item.id} · {_published_text(item)}", clean_text(item.message)])
    return "\n".join(lines)


def format_dispatch_detail(items: list[Dispatch], dispatch_id: int) -> str | None:
    """Format one already-fetched dispatch, returning ``None`` when absent."""
    selected = next((item for item in items if item.id == dispatch_id), None)
    if selected is None:
        return None
    return "\n".join([
        "【银河新闻 · 战报详情】",
        f"战报 #{selected.id} · {_published_text(selected)}",
        "",
        clean_text(selected.message),
    ])


def format_supply_lines(planet: Planet, all_planets: list[Planet]) -> str:
    index = {item.index: item for item in all_planets}
    outgoing = set(planet.waypoints) - {planet.index}
    incoming = {item.index for item in all_planets
                if item.index != planet.index and planet.index in item.waypoints}
    targets = sorted(outgoing | incoming)
    lines = [f"【补给线 · {clean_text(planet.name)}】"]
    for target in targets:
        direction = ("双向记录" if target in outgoing and target in incoming
                     else "本星 → 邻星" if target in outgoing else "邻星 → 本星")
        connected = index.get(target)
        if connected is None:
            lines.append(f"星球 #{target} | {direction} | 详情暂无数据")
        else:
            players = f"{connected.players:,}" if connected.players is not None else "暂无数据"
            lines.append(f"{clean_text(connected.name)}（#{target}） | {direction}"
                         f" | {faction_name(connected.owner)} | 在线 {players}")
    if not targets:
        lines.append("当前数据未提供相邻连接。")
    lines.append("方向仅表示 API 的 waypoint 记录；联通不代表当前可进攻或可部署。")
    return "\n".join(lines)
