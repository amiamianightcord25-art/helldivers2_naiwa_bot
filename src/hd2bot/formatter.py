"""Compact Chinese replies shared by the CLI and official QQ adapter."""

from datetime import UTC, datetime

from hd2bot.hd2.models import (
    Campaign,
    Faction,
    GlobalStatistics,
    MajorOrder,
    Planet,
    WarStatus,
    utcnow,
)
from hd2bot.hd2.parsing import number, progress
from hd2bot.hd2.service import DataResult
from hd2bot.services.localization import clean_text, faction_name, metadata_for, term

UNKNOWN = "暂无数据"


def _number(value, digits: int = 0) -> str:
    if isinstance(value, int) and not isinstance(value, bool) and digits == 0:
        return f"{value:,}"
    parsed = number(value)
    if parsed is None:
        return UNKNOWN
    return f"{parsed:,.{digits}f}"


def _percent(value) -> str:
    parsed = number(value)
    return f"{max(0, min(100, parsed)):.2f}%" if parsed is not None else UNKNOWN


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _remaining(value: datetime | None) -> str:
    if value is None:
        return UNKNOWN
    seconds = (_aware(value) - utcnow()).total_seconds()
    if seconds <= 0:
        return "已结束"
    seconds = int(seconds)
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes = seconds // 60
    if days:
        return f"{days}天{hours}小时{minutes}分"
    if hours:
        return f"{hours}小时{minutes}分"
    return f"{minutes}分" if minutes else "不足1分钟"


def _name(planet: Planet, english: bool = False) -> str:
    name = clean_text(planet.name, f"星球 #{planet.index}")
    if english and planet.english_name and planet.english_name.casefold() != name.casefold():
        name += f"（{clean_text(planet.english_name)}）"
    return name


def _active_defense(planet: Planet) -> bool:
    event = planet.event
    now = utcnow()
    return bool(event is not None and event.event_type == 1 and not planet.disabled
                and (event.starts_at is None or _aware(event.starts_at) <= now)
                and (event.ends_at is None or _aware(event.ends_at) > now))


def _defense_progress(planet: Planet) -> float | None:
    event = planet.event
    if event is None:
        return None
    # Event HP has its own maximum; never borrow the planet's health scale.
    return progress(event.health, event.max_health)


def _planet_state(planet: Planet) -> str:
    if planet.disabled:
        return "当前不可部署"
    if _active_defense(planet):
        return f"防守中 {_percent(_defense_progress(planet))}"
    if planet.owner == Faction.HUMANS:
        return "超级地球已控制"
    if planet.owner == Faction.UNKNOWN:
        return "归属暂无数据"
    return f"解放进度 {_percent(planet.liberation)}"


def _target_indices(orders: list[MajorOrder] | None) -> set[int]:
    return {task.planet_index for order in orders or []
            if order.expires_at is None or _aware(order.expires_at) > utcnow()
            for task in order.tasks if task.planet_index is not None}


def _attacks(campaigns: list[Campaign], orders: list[MajorOrder] | None = None) -> list[Campaign]:
    targets = _target_indices(orders)
    active = [campaign for campaign in campaigns
              if campaign.planet.owner not in {Faction.HUMANS, Faction.UNKNOWN}
              and not campaign.planet.disabled and not _active_defense(campaign.planet)]
    return sorted(active, key=lambda campaign: (
        campaign.planet.index not in targets,
        -(campaign.planet.players if campaign.planet.players is not None else -1),
        -(campaign.planet.liberation if campaign.planet.liberation is not None else -1),
        campaign.planet.index, campaign.id,
    ))


def format_war_status(war: WarStatus, planets: list[Planet] | None,
                      campaigns: list[Campaign] | None) -> str:
    status = war.status
    now = utcnow()
    if war.ended_at is not None and _aware(war.ended_at) <= now:
        status = "已结束"
    elif war.started_at is not None and _aware(war.started_at) > now:
        status = "尚未开始"
    lines = ["【银河战况】", f"战争状态：{clean_text(status)}",
             f"全服在线：{_number(war.players)}"]
    earth = next((planet for planet in planets or [] if planet.index == 0), None)
    lines.append(f"超级地球：{_planet_state(earth) if earth is not None else UNKNOWN}")
    if campaigns is None:
        lines.append("活跃战区 / 战役：暂无数据")
    else:
        active = [campaign for campaign in campaigns if not campaign.planet.disabled
                  and (campaign.planet.owner not in {Faction.HUMANS, Faction.UNKNOWN}
                       or _active_defense(campaign.planet))]
        lines.append(f"活跃战区：{len({campaign.planet.index for campaign in active})}"
                     f" | 战役：{len(active)}")
        if active:
            lines.append("热门战区（按在线人数）：")
            top = sorted(active, key=lambda item: (
                -(item.planet.players if item.planet.players is not None else -1), item.id,
            ))[:5]
            lines.extend(f"{index}. {_name(item.planet)} | {_planet_state(item.planet)}"
                         f" | 在线 {_number(item.planet.players)}"
                         for index, item in enumerate(top, 1))
    if planets is not None:
        lines.append(f"进行中的防守：{sum(_active_defense(planet) for planet in planets)}")
    if war.events:
        lines.append("银河公告：")
        for message in war.events[:3]:
            plain = clean_text(message)
            lines.append(plain if len(plain) <= 200 else plain[:197] + "…")
        if len(war.events) > 3:
            lines.append(f"另有 {len(war.events) - 3} 条公告")
    return "\n".join(lines)


def format_major_order(orders: list[MajorOrder], planets: list[Planet] | None = None) -> str:
    if not orders:
        return "【主要指令】\n暂无当前主线。"
    names = {planet.index: _name(planet) for planet in planets or []}
    lines = ["【主要指令】"]
    for position, order in enumerate(orders):
        if position:
            lines.append("")
        lines.append(clean_text(order.title, f"主要指令 #{order.id}"))
        shown = set()
        for value in (order.briefing, order.description):
            if value:
                message = clean_text(value)
                if message not in shown:
                    lines.append(message)
                    shown.add(message)
        lines.append(f"剩余时间：{_remaining(order.expires_at)}")
        status = order.status or (
            "已结束" if order.expires_at and _aware(order.expires_at) <= utcnow()
            else "进行中" if order.expires_at else UNKNOWN
        )
        lines.append(f"状态：{clean_text(status)}")
        if not order.tasks:
            lines.append("任务详情：暂无数据")
        for index, task in enumerate(order.tasks, 1):
            if task.type == 11 and task.planet_index is not None:
                name = names.get(task.planet_index)
                if not name:
                    name = clean_text(metadata_for(task.planet_index).get("name"),
                                      f"星球 #{task.planet_index}")
                label = f"解放 {name}"
                if task.description:
                    label = clean_text(task.description)
            else:
                label = (clean_text(task.description) if task.description
                         else f"任务类型 {_number(task.type)}")
            measured = _number(task.progress, 0 if task.progress is None
                               or float(task.progress).is_integer() else 2)
            if task.target is not None:
                measured += " / " + _number(task.target)
            lines.append(f"{index}. {label} | 进度 {measured}")
        if order.rewards:
            lines.append("奖励数量：" + "、".join(_number(reward.amount) for reward in order.rewards))
        else:
            lines.append("奖励：暂无数据")
    return "\n".join(lines)


def format_planet(planet: Planet) -> str:
    lines = [f"【{_name(planet, english=True)}】", f"星球编号：{planet.index}",
             f"星区：{term(planet.sector)}", f"归属：{faction_name(planet.owner)}",
             f"状态：{_planet_state(planet)}", f"在线玩家：{_number(planet.players)}",
             f"星球生命：{_number(planet.health)} / {_number(planet.max_health)}"]
    if _active_defense(planet):
        event = planet.event
        lines.extend([f"入侵方：{faction_name(event.faction)}",
                      f"防守进度：{_percent(_defense_progress(planet))}",
                      f"敌方事件生命：{_number(event.health)} / {_number(event.max_health)}",
                      f"防守剩余：{_remaining(event.ends_at)}"])
    regeneration = f"{_number(planet.regen_rate, 2)} HP/秒" if planet.regen_rate is not None else UNKNOWN
    if (planet.regen_rate is not None and planet.max_health is not None
            and planet.max_health > 0):
        regeneration += f"（每小时恢复星球最大生命的 {planet.regen_rate * 360000 / planet.max_health:.2f}%）"
    lines.append("星球生命恢复：" + regeneration)
    lines.append(f"环境：{term(planet.biome)}")
    lines.append("环境影响：" + ("、".join(term(hazard) for hazard in planet.hazards)
                                if planet.hazards else UNKNOWN))
    return "\n".join(lines)


def format_campaigns(campaigns: list[Campaign], orders: list[MajorOrder] | None = None) -> str:
    active = _attacks(campaigns, orders)
    if not active:
        return "【进攻战役】\n当前没有可识别的进行中进攻战役。"
    targets = _target_indices(orders)
    lines = [f"【进攻战役】共 {len(active)} 个", "排序：主线目标优先，其次在线人数、解放进度"]
    for index, campaign in enumerate(active, 1):
        planet = campaign.planet
        priority = "[主线] " if planet.index in targets else ""
        lines.append(f"{index}. {priority}{_name(planet)} | {faction_name(planet.owner)}"
                     f" | 解放 {_percent(planet.liberation)} | 在线 {_number(planet.players)}")
    return "\n".join(lines)


def format_defenses(planets: list[Planet]) -> str:
    active = sorted((planet for planet in planets if _active_defense(planet)), key=lambda planet: (
        _aware(planet.event.ends_at).timestamp() if planet.event.ends_at is not None else float("inf"),
        -(planet.players if planet.players is not None else -1), planet.index,
    ))
    if not active:
        return "【防守战役】\n当前没有进行中的防守战役。"
    lines = [f"【防守战役】共 {len(active)} 个"]
    for index, planet in enumerate(active, 1):
        event = planet.event
        lines.append(f"{index}. {_name(planet)} | {faction_name(event.faction)}入侵"
                     f" | 防守 {_percent(_defense_progress(planet))}"
                     f" | 在线 {_number(planet.players)} | 剩余 {_remaining(event.ends_at)}")
    return "\n".join(lines)


def format_players(stats: GlobalStatistics) -> str:
    lines = ["【全服玩家与累计统计】", f"全服在线：{_number(stats.players)}", "按战线分布："]
    for faction in (Faction.TERMINIDS, Faction.AUTOMATONS, Faction.ILLUMINATE, Faction.HUMANS):
        label = "超级地球控制区" if faction == Faction.HUMANS else faction_name(faction) + "战线"
        lines.append(f"{label}：{_number(stats.faction_players.get(faction))}")
    if stats.faction_players.get(Faction.UNKNOWN) is not None:
        lines.append(f"未知战线：{_number(stats.faction_players[Faction.UNKNOWN])}")
    lines.extend([f"累计任务：胜利 {_number(stats.missions_won)} / 失败 {_number(stats.missions_lost)}",
                  f"累计击杀：终结族 {_number(stats.terminid_kills)}"
                  f" / 机器人 {_number(stats.automaton_kills)} / 光能者 {_number(stats.illuminate_kills)}",
                  f"累计阵亡：{_number(stats.deaths)}", "统计范围：全服及星球汇总。",
                  "已配置测试账号的个人生涯数据请发送：战绩。"])
    return "\n".join(lines)


def finish_response(body: str, results: list[DataResult]) -> str:
    banners = []
    if any(result.stale for result in results):
        banners.append("数据暂时无法更新，以下为最近缓存")
    if any(result.source == "mock" for result in results):
        banners.append("【模拟数据 · 非实时战况】")
    labels = {"captured": "官方战局 API", "community": "社区战局 API", "mock": "本地模拟"}
    sources = list(dict.fromkeys(labels.get(result.source, clean_text(result.source))
                                 for result in results))
    footer = ["来源：" + (" / ".join(sources) if sources else UNKNOWN)]
    if results:
        oldest = min(_aware(result.fetched_at) for result in results)
        footer.append("采样时间（本地，最早）：" + oldest.astimezone().isoformat(timespec="seconds"))
    else:
        footer.append("采样时间：暂无数据")
    return "\n".join([*banners, body, "", *footer])
