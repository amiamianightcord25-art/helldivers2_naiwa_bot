"""Compact strategic overview assembled from the public HD2 data service.

The dashboard deliberately uses the same public models as the individual
commands.  It has no separate endpoint or authenticated data dependency;
when an optional feed is unavailable the remaining sections still render.
"""

from __future__ import annotations

from collections.abc import Awaitable
from typing import TypeVar

from hd2bot import formatter
from hd2bot.hd2.errors import HD2APIError
from hd2bot.hd2.models import Campaign, Faction, MajorOrder, Planet
from hd2bot.hd2.service import DataResult, HD2Service
from hd2bot.presentation import CommandReply, _metadata
from hd2bot.rendering.models import CardMetric, CardRow, CardSection, QueryCard
from hd2bot.services.concurrency import gather_cancel_on_error
from hd2bot.services.localization import clean_text, faction_name

T = TypeVar("T")


async def _optional(awaitable: Awaitable[DataResult[T]]) -> DataResult[T] | None:
    """Keep a partial dashboard useful when one public feed is unavailable."""
    try:
        return await awaitable
    except HD2APIError:
        return None


def _planet_name(planet: Planet | None, planets: list[Planet] | None = None) -> str:
    if planet is not None:
        return clean_text(planet.name, f"星球 #{planet.index}")
    return "暂无数据"


def _state(planet: Planet) -> str:
    if planet.disabled:
        return "不可部署"
    if formatter._active_defense(planet):
        return "防守中 " + formatter._percent(formatter._defense_progress(planet))
    if planet.owner == Faction.HUMANS:
        return "超级地球已控制"
    if planet.owner == Faction.UNKNOWN:
        return "归属暂无数据"
    return "解放 " + formatter._percent(planet.liberation)


def _campaign_rows(campaigns: list[Campaign], orders: list[MajorOrder] | None,
                  limit: int = 5) -> tuple[list[Campaign], int]:
    active = formatter._attacks(campaigns, orders)
    return active[:limit], len(active)


def _defense_rows(planets: list[Planet], limit: int = 5) -> tuple[list[Planet], int]:
    active = [planet for planet in planets if formatter._active_defense(planet)]
    active.sort(key=lambda planet: (
        formatter._aware(planet.event.ends_at).timestamp()
        if planet.event and planet.event.ends_at is not None else float("inf"),
        -(planet.players if planet.players is not None else -1),
        planet.index,
    ))
    return active[:limit], len(active)


def _order_line(order: MajorOrder, planets: list[Planet] | None) -> list[str]:
    lines = [clean_text(order.title, f"主要指令 #{order.id}")]
    if order.expires_at is not None:
        lines.append("剩余 " + formatter._remaining(order.expires_at))
    for task in order.tasks[:3]:
        label = clean_text(task.description, "任务")
        if task.planet_index is not None:
            planet = next((item for item in planets or [] if item.index == task.planet_index), None)
            label = clean_text(task.description, _planet_name(planet) if planet else
                               f"星球 #{task.planet_index}")
        progress = formatter._number(task.progress)
        if task.target is not None:
            progress += " / " + formatter._number(task.target)
        lines.append(f"{label}：{progress}")
    if len(order.tasks) > 3:
        lines.append(f"另有 {len(order.tasks) - 3} 项任务")
    return lines


def _text_report(war: DataResult, planets: DataResult | None,
                 campaigns: DataResult | None, orders: DataResult | None,
                 stations: DataResult | None, events: DataResult | None,
                 missing: list[str]) -> str:
    war_value = war.value
    lines = ["【战略看板】", f"战争状态：{clean_text(war_value.status)}",
             f"全服在线：{formatter._number(war_value.players)}"]

    if planets is not None:
        earth = next((planet for planet in planets.value if planet.index == 0), None)
        lines.append("超级地球：" + (_state(earth) if earth else "暂无数据"))
    else:
        lines.append("超级地球：暂无数据")

    if orders is not None:
        active_orders = [order for order in orders.value
                         if order.expires_at is None or formatter._aware(order.expires_at)
                         > formatter.utcnow()]
        if active_orders:
            lines.append("主线：" + " | ".join(_order_line(active_orders[0],
                                                       planets.value if planets else None)))
        else:
            lines.append("主线：暂无当前主线")
    else:
        lines.append("主线：暂无数据")

    if campaigns is not None:
        top, total = _campaign_rows(campaigns.value, orders.value if orders else None)
        lines.append(f"进攻战区：{total} 个")
        lines.extend(
            f"  {index}. {_planet_name(item.planet)} · {faction_name(item.planet.owner)}"
            f" · 在线 {formatter._number(item.planet.players)} · {_state(item.planet)}"
            for index, item in enumerate(top, 1)
        )
        if total > len(top):
            lines.append(f"  其余 {total - len(top)} 个请发送：进攻")
    else:
        lines.append("进攻战区：暂无数据")

    if planets is not None:
        defenses, total = _defense_rows(planets.value)
        lines.append(f"防守战区：{total} 个")
        lines.extend(
            f"  {index}. {_planet_name(item)} · {faction_name(item.event.faction)}"
            f" · {_state(item)} · 在线 {formatter._number(item.players)}"
            for index, item in enumerate(defenses, 1) if item.event is not None
        )
        if total > len(defenses):
            lines.append(f"  其余 {total - len(defenses)} 个请发送：防守")
    else:
        lines.append("防守战区：暂无数据")

    if stations is not None:
        actions = [action for station in stations.value for action in station.tactical_actions]
        lines.append("DSS：" + ("、".join(clean_text(action.name, f"行动 #{action.id}")
                               for action in actions[:3]) if actions else "暂无公开行动"))
    else:
        lines.append("DSS：暂无数据")

    if events is not None:
        active_events = [event for event in events.value
                         if event.expires_at is None or formatter._aware(event.expires_at)
                         > formatter.utcnow()]
        if active_events:
            lines.append("银河事件：" + "；".join(
                f"#{event.id} {clean_text(event.title, '未命名事件')}"
                for event in active_events[:3]
            ))
        else:
            lines.append("银河事件：当前没有公开事件")
    else:
        lines.append("银河事件：暂无数据")

    if missing:
        lines.append("暂不可用：" + "、".join(missing))
    return "\n".join(lines)


def _build_card(war: DataResult, planets: DataResult | None,
                campaigns: DataResult | None, orders: DataResult | None,
                stations: DataResult | None, events: DataResult | None,
                missing: list[str], results: list[DataResult]) -> QueryCard:
    war_value = war.value
    metrics = [CardMetric("全服在线", formatter._number(war_value.players))]
    if campaigns is not None:
        _, attack_count = _campaign_rows(campaigns.value, orders.value if orders else None)
        metrics.append(CardMetric("进攻战区", str(attack_count)))
    if planets is not None:
        _, defense_count = _defense_rows(planets.value)
        metrics.append(CardMetric("防守战区", str(defense_count)))
    if stations is not None:
        metrics.append(CardMetric("DSS行动", str(sum(len(s.tactical_actions)
                                                     for s in stations.value))))

    sections: list[CardSection] = [CardSection("战争概览", (
        CardRow("状态", clean_text(war_value.status)),
        CardRow("战争编号", str(war_value.war_id) if war_value.war_id is not None else "暂无数据"),
    ))]
    if orders is not None:
        current = [order for order in orders.value
                   if order.expires_at is None or formatter._aware(order.expires_at)
                   > formatter.utcnow()]
        if current:
            order = current[0]
            sections.append(CardSection("当前主线", (
                CardRow("标题", clean_text(order.title, f"主要指令 #{order.id}")),
                CardRow("进度", clean_text(order.description or order.briefing, "暂无描述")),
                CardRow("剩余", formatter._remaining(order.expires_at)),
            )))
    if campaigns is not None:
        top, total = _campaign_rows(campaigns.value, orders.value if orders else None)
        rows = [CardRow(_planet_name(item.planet),
                        formatter._number(item.planet.players) + " 人",
                        faction_name(item.planet.owner) + " · " + _state(item.planet))
                for item in top]
        if total > len(top):
            rows.append(CardRow("其他战区", f"{total - len(top)} 个", "发送 进攻 查看全部"))
        sections.append(CardSection("重点进攻战区", tuple(
            rows or [CardRow("当前战区", "暂无数据")],
        )))
    if planets is not None:
        defenses, total = _defense_rows(planets.value)
        rows = [CardRow(_planet_name(item), formatter._number(item.players) + " 人",
                        faction_name(item.event.faction) if item.event else "暂无阵营")
                for item in defenses]
        if total > len(defenses):
            rows.append(CardRow("其他防守", f"{total - len(defenses)} 个", "发送 防守 查看全部"))
        sections.append(CardSection("重点防守战区", tuple(
            rows or [CardRow("当前防守", "暂无数据")],
        )))
    if stations is not None:
        rows = [CardRow(clean_text(action.name, f"行动 #{action.id}"),
                        {0: "未启用", 1: "准备中", 2: "已启用", 3: "冷却中"}.get(
                            action.status, "暂无状态"))
                for station in stations.value for action in station.tactical_actions[:3]]
        sections.append(CardSection("DSS", tuple(rows or [CardRow("公开行动", "暂无数据")])))
    if events is not None:
        rows = [CardRow(f"#{event.id} {clean_text(event.title, '未命名事件')}",
                        clean_text(event.message, "暂无正文")) for event in events.value[:3]]
        sections.append(CardSection("银河事件", tuple(rows or [CardRow("公开事件", "暂无数据")])))

    metadata = _metadata(results)
    if missing:
        metadata["notices"] += ("部分公开数据暂不可用：" + "、".join(missing),)
    metadata["footer"] += ("发送 看板 / dashboard 刷新此总览；详细数据可分别发送进攻、防守、DSS、公告。",)
    return QueryCard("战略看板", "公开银河战况总览", "HELLDIVERS 2 / STRATEGIC DASHBOARD",
                     metrics=tuple(metrics), sections=tuple(sections), **metadata)


async def dashboard_reply(service: HD2Service) -> CommandReply:
    """Fetch the public feeds concurrently and return text plus an optional card."""
    war, planets, campaigns, orders, stations, events = await gather_cancel_on_error(
        service.get_war(),
        _optional(service.get_planets()),
        _optional(service.get_campaigns()),
        _optional(service.get_major_order()),
        _optional(service.get_space_stations()),
        _optional(service.get_global_events()),
    )
    feeds = (("星球", planets), ("进攻战区", campaigns), ("主线", orders),
             ("DSS", stations), ("银河事件", events))
    missing = [label for label, result in feeds if result is None]
    results = [result for result in (war, planets, campaigns, orders, stations, events)
               if result is not None]
    body = _text_report(war, planets, campaigns, orders, stations, events, missing)
    card = _build_card(war, planets, campaigns, orders, stations, events, missing, results)
    return CommandReply(formatter.finish_response(body, results), card)


__all__ = ["dashboard_reply"]
