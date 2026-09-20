"""Public Galactic-Wide-Web-inspired reports for text and HTML/JPEG cards.

Public field identifiers and status meanings were cross-checked against the
upstream API wrapper; no authenticated endpoints or upstream code are reused.
"""

import re
from datetime import UTC, datetime, timedelta, timezone

from hd2bot.hd2.models import (
    DSSElection,
    DSSVoteOption,
    Episode,
    EpisodeReward,
    Faction,
    GlobalEvent,
    PlanetRegion,
    SpaceStation,
    SpecialUnit,
)
from hd2bot.hd2.parsing import number, progress
from hd2bot.services.localization import faction_name, metadata_for

_HKT = timezone(timedelta(hours=8))
_DONATION_ITEMS = {3992382197: "普通样本", 2985106497: "稀有样本", 3608481516: "申购单"}


def _game_text(value: str | None, fallback: str = "暂无数据") -> str:
    # Episode titles such as CONTAINMENT are real uppercase text, not locale keys.
    if not value:
        return fallback
    return re.sub(r"</?[a-zA-Z][^>]*>", "", value).strip() or fallback


def tactical_status(status: int | None) -> str:
    return {0: "未启用", 1: "准备中", 2: "已启用", 3: "冷却中"}.get(
        status, "暂无数据" if status is None else f"未知状态（{status}）",
    )


def episode_status(status: int | None) -> str:
    return {0: "进行中", 2: "成功", 3: "失败"}.get(
        status, "暂无数据" if status is None else f"未知状态（{status}）",
    )


def _planet(index: int | None) -> str:
    if index is None:
        return "暂无数据"
    return _game_text(metadata_for(index).get("name"), f"星球 #{index}")


def _date(value: datetime | None) -> str:
    if value is None:
        return "暂无数据"
    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value
    return aware.astimezone(_HKT).strftime("%Y-%m-%d %H:%M UTC+8")


def _number(value) -> str:
    parsed = number(value)
    return f"{parsed:,.2f}".rstrip("0").rstrip(".") if parsed is not None else "暂无数据"


def _percent(current, target) -> str:
    current, target = number(current), number(target)
    if current is None or target is None or current < 0 or target <= 0:
        return "暂无数据"
    return f"{max(0, min(100, current / target * 100)):.2f}%"


def _vote_label(option: DSSVoteOption) -> str:
    return _game_text(option.text, _planet(option.meta_id))


def _vote_lines(election: DSSElection) -> list[str]:
    options = [option for option in election.options
               if type(option.meta_id) is int and option.meta_id >= 0
               and type(option.count) is int and option.count >= 0]
    if not options:
        return ["迁移投票：暂无公开选项。"]
    total = sum(option.count for option in options)
    lines = [f"迁移投票总数：{total:,}"]
    for index, option in enumerate(sorted(options, key=lambda item: (-item.count, item.meta_id)), 1):
        ratio = f"（{option.count / total * 100:.2f}%）" if total else "（0.00%）"
        lines.append(f"{index}. {_vote_label(option)}：{option.count:,} 票 {ratio}")
    return lines


def format_dss_votes(elections: list[DSSElection]) -> str:
    """Render the public ElectionV2 vote counts without authenticated data."""
    lines = ["【DSS 迁移投票】"]
    if not elections:
        return "\n".join(lines + ["当前没有可用的公开迁移投票。"])
    for election in elections:
        lines.append(f"选举：{_game_text(election.id, '当前选举')}")
        lines.extend(_vote_lines(election))
    lines.append("数据来自官方公开 ElectionV2 接口。")
    return "\n".join(lines)


def format_space_stations(stations: list[SpaceStation]) -> str:
    lines = ["【民主空间站 DSS】"]
    if not stations:
        return "\n".join(lines + ["当前公开数据中没有空间站。"])
    for station in stations:
        name = "民主空间站 DSS" if station.id == 749875195 else f"未知空间站 #{station.id}"
        lines += [name, f"驻留星球：{_planet(station.planet_index)}"]
        if station.flags in {0, 2}:
            lines.append("当前不可用")
        elif station.flags != 1:
            lines.append(f"运行标记：{_number(station.flags)}（含义未知）")
        lines.append(f"本轮迁移投票截止：{_date(station.election_ends_at)}")
        if station.election is not None:
            lines.extend(_vote_lines(station.election))
        for action in station.tactical_actions:
            lines += ["", f"{_game_text(action.name, f'战术行动 #{action.id}')}"
                      f"：{tactical_status(action.status)}"]
            if action.strategic_description:
                lines.append(_game_text(action.strategic_description))
            elif action.description:
                lines.append(_game_text(action.description))
            lines.append(f"当前状态截止：{_date(action.expires_at)}")
            if action.status == 1:
                for cost in action.costs:
                    item = _DONATION_ITEMS.get(cost.item_id, f"未知物资 #{cost.item_id}")
                    lines.append(f"{item}筹备进度：{_percent(cost.current, cost.target)}")
                    if cost.donation_limit is not None and cost.donation_period_seconds is not None:
                        lines.append(f"单人捐献上限：{cost.donation_limit:,} / "
                                     f"{cost.donation_period_seconds:,} 秒")
        if not station.tactical_actions:
            lines.append("暂无公开战术行动。")
    if not any(station.election is not None for station in stations):
        lines.append("\n公开数据不包含迁移投票票数；截止时间不代表已确认的迁移目的地。")
    else:
        lines.append("\n票数来自官方公开 ElectionV2；截止时间不代表已确认的迁移目的地。")
    return "\n".join(lines)


def format_global_events(events: list[GlobalEvent], event_id: int | None = None) -> str:
    lines = ["【银河全球事件】"]
    selected = [event for event in events if event_id is None or event.id == event_id]
    if not selected:
        return "\n".join(lines + ["未找到该事件。" if event_id is not None else "当前没有公开全球事件。"])
    for event in selected:
        lines += ["", f"#{event.id} {_game_text(event.title, '未命名事件')}",
                  f"阵营：{faction_name(event.faction)}", _game_text(event.message, "暂无事件正文"),
                  f"结束时间：{_date(event.expires_at)}"]
        if event.planet_indices:
            lines.append("涉及星球：" + "、".join(_planet(index) for index in event.planet_indices))
        if event.assignment_id:
            lines.append(f"关联主要指令：#{event.assignment_id}")
        if event.effect_ids:
            lines.append("效果编号：" + "、".join(map(str, event.effect_ids)))
    return "\n".join(lines)


def _rewards(rewards: list[EpisodeReward]) -> str:
    # Only the medal identifier has been cross-checked. Never invent item names.
    return "、".join(f"{'奖章' if reward.item_id == 897894480 else f'物品 #{reward.item_id}'}"
                    f" × {_number(reward.amount)}" for reward in rewards)


def format_episodes(episodes: list[Episode], episode_id: int | None = None) -> str:
    lines = ["【银河控制中心】"]
    if episode_id is None:
        if not episodes:
            return "\n".join(lines + ["当前没有公开战役记录。"])
        lines.append("战役目录：")
        for episode in reversed(episodes):
            lines.append(f"#{episode.id} {_game_text(episode.title)} · {episode_status(episode.status)}")
        selected = [episode for episode in episodes if episode.status == 0] or episodes[-1:]
        lines.append("\n当前战役简报：" if any(e.status == 0 for e in selected) else "\n最近战役简报：")
    else:
        selected = [episode for episode in episodes if episode.id == episode_id]
        if not selected:
            return "\n".join(lines + ["未找到该战役。使用「控制中心」查看战役编号。"])
    for episode in selected:
        lines += [f"#{episode.id} {_game_text(episode.title)} · {episode_status(episode.status)}",
                  f"阵营：{faction_name(episode.faction)}", _game_text(episode.description),
                  f"开始时间：{_date(episode.starts_at)}", f"结束时间：{_date(episode.ends_at)}"]
        if episode.intro_message and episode.intro_message != episode.description:
            lines.append(_game_text(episode.intro_message))
        if episode.outro_message:
            lines.append(_game_text(episode.outro_message))
        for index, phase in enumerate(episode.phases, 1):
            lines.append(f"\n阶段 {index}：{_game_text(phase.intro_title, f'#{phase.id}')}"
                         f" · {episode_status(phase.status)}")
            # The default view includes the current objective. Explicit episode
            # queries preserve all phase narratives, without truncating API text.
            if episode_id is not None or phase.status == 0:
                if phase.intro_message:
                    lines.append(_game_text(phase.intro_message))
                if phase.outro_message:
                    lines.append(f"{_game_text(phase.outro_title, '阶段结果')}："
                                 f"{_game_text(phase.outro_message)}")
                if phase.rewards:
                    lines.append("阶段奖励：" + _rewards(phase.rewards))
        if episode.rewards:
            lines.append("战役奖励：" + _rewards(episode.rewards))
    if episode_id is None:
        lines.append("\n发送「控制中心 战役编号」可查看该战役全部阶段与结果。")
    lines.append("部分战役正文由上游提供英文原文。")
    return "\n".join(lines)


def format_planet_regions(regions: list[PlanetRegion], planet_index: int | None = None) -> str:
    lines = ["【星球区域战况】"]
    selected = sorted((region for region in regions
                       if planet_index is None or region.planet_index == planet_index),
                      key=lambda region: (region.planet_index, region.index))
    if not selected:
        return "\n".join(lines + ["当前公开数据中没有对应的区域战况。"])
    for region in selected:
        available = {True: "可进入", False: "不可进入", None: "可用性暂无数据"}[region.available]
        lines += ["", f"{_planet(region.planet_index)} · 区域 #{region.index}",
                  f"归属：{faction_name(region.faction)} | {available}",
                  f"在线人数：{_number(region.players)}"]
        if region.faction == Faction.HUMANS:
            lines.append("超级地球已控制")
        elif region.faction != Faction.UNKNOWN:
            liberated = progress(region.health, region.max_health)
            lines.append(f"解放进度：{liberated:.2f}%" if liberated is not None else "解放进度：暂无数据")
        if region.damage_multiplier is not None:
            lines.append(f"区域伤害倍率：{_number(region.damage_multiplier)}")
    lines.append("\n区域编号来自公开接口；未公开名称的区域保留编号。")
    return "\n".join(lines)


def format_special_units(units: list[SpecialUnit]) -> str:
    lines = ["【特殊部队动向】"]
    if not units:
        return "\n".join(lines + ["当前公开战场效果中没有识别到特殊部队。"])
    for unit in units:
        # Original faction/unit names are facts, not localization lookup keys.
        lines += ["", f"{unit.name} · {faction_name(unit.faction)}",
                  "所在星球：" + "、".join(_planet(index) for index in unit.planet_indices),
                  "战场效果编号：" + "、".join(map(str, unit.effect_ids))]
    lines.append("\n位置依据公开战场效果；不代表实时兵力或单位数量。")
    return "\n".join(lines)
