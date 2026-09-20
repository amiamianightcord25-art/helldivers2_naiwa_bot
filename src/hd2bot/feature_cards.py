"""Structured cards that keep each region and each campaign narrative together."""

from hd2bot import galactic_features as g
from hd2bot.hd2.models import Episode, Faction, PlanetRegion
from hd2bot.hd2.parsing import progress
from hd2bot.hd2.service import DataResult
from hd2bot.presentation import _metadata
from hd2bot.rendering.models import CardMetric, CardRow, CardSection, QueryCard
from hd2bot.services.localization import faction_name


def region_card(regions: list[PlanetRegion], results: list[DataResult],
                planet_index: int | None = None) -> QueryCard | None:
    """Render one indivisible row per region, including every matching record."""
    selected = sorted((region for region in regions
                       if planet_index is None or region.planet_index == planet_index),
                      key=lambda region: (region.planet_index, region.index))
    if not selected:
        return None
    rows = []
    for region in selected:
        available = {True: "可进入", False: "不可进入", None: "可用性暂无数据"}[region.available]
        detail = [f"{faction_name(region.faction)} · {available}"]
        if region.faction == Faction.HUMANS:
            detail.append("超级地球已控制")
        elif region.faction != Faction.UNKNOWN:
            liberated = progress(region.health, region.max_health)
            detail.append(f"解放进度 {liberated:.2f}%" if liberated is not None
                          else "解放进度 暂无数据")
        if region.damage_multiplier is not None:
            detail.append(f"区域伤害倍率 {g._number(region.damage_multiplier)}")
        players = f"{g._number(region.players)} 人" if region.players is not None else "暂无数据"
        rows.append(CardRow(f"{g._planet(region.planet_index)} · 区域 #{region.index}",
                            players, "\n".join(detail)))
    # The shared template has up to three columns. Balance complete grid rows
    # instead of leaving a lone short panel beneath three much taller panels.
    section_count = (len(rows) + 11) // 12
    if section_count > 3:
        section_count = (section_count + 2) // 3 * 3
    per_section = (len(rows) + section_count - 1) // section_count
    sections = tuple(CardSection(
        f"区域 {start + 1}–{min(start + per_section, len(rows))}",
        tuple(rows[start:start + per_section]),
    ) for start in range(0, len(rows), per_section))
    metadata = _metadata(results)
    metadata["footer"] += ("区域编号来自公开接口；未公开名称的区域保留编号。",
                           "区域 <星球名称或编号> 查看指定星球",)
    return QueryCard(
        "星球区域战况", "所有公开区域" if planet_index is None else g._planet(planet_index),
        "HELLDIVERS 2 / PLANET REGIONS",
        metrics=(CardMetric("区域记录", str(len(selected))),
                 CardMetric("涉及星球", str(len({region.planet_index for region in selected})))),
        sections=sections, **metadata,
    )


def _episode_section(episode: Episode, *, detailed: bool, heading: str) -> CardSection:
    rows = [CardRow("", g._game_text(episode.title)),
            CardRow("战役状态", g.episode_status(episode.status)),
            CardRow("作战阵营", faction_name(episode.faction)),
            CardRow("时间", f"开始：{g._date(episode.starts_at)}\n结束：{g._date(episode.ends_at)}"),
            CardRow("战役简报", g._game_text(episode.description))]
    if episode.intro_message and episode.intro_message != episode.description:
        rows.append(CardRow("战役序言", g._game_text(episode.intro_message)))
    if episode.outro_message:
        rows.append(CardRow("战役结果", g._game_text(episode.outro_message)))
    for index, phase in enumerate(episode.phases, 1):
        label = f"阶段 {index} · {g._game_text(phase.intro_title, f'#{phase.id}')}"
        status = g.episode_status(phase.status)
        paragraphs = []
        if detailed or phase.status == 0:
            if phase.intro_message:
                paragraphs.append(g._game_text(phase.intro_message))
            if phase.outro_message:
                paragraphs.append(f"{g._game_text(phase.outro_title, '阶段结果')}：\n"
                                  f"{g._game_text(phase.outro_message)}")
            if phase.rewards:
                paragraphs.append("阶段奖励：" + g._rewards(phase.rewards))
        if paragraphs:
            rows.append(CardRow(f"{label} · {status}", "\n\n".join(paragraphs)))
        else:
            rows.append(CardRow(label, status))
    if episode.rewards:
        rows.append(CardRow("战役奖励", g._rewards(episode.rewards)))
    return CardSection(f"{heading} · #{episode.id}", tuple(rows))


def episode_card(episodes: list[Episode], results: list[DataResult],
                 episode_id: int | None = None) -> QueryCard | None:
    """Use separate directory/briefing panels; never split prose by line count."""
    if not episodes:
        return None
    sections = []
    if episode_id is None:
        sections.append(CardSection("战役目录", tuple(
            CardRow(f"#{episode.id} {g._game_text(episode.title)}",
                    g.episode_status(episode.status)) for episode in reversed(episodes)
        )))
        selected = [episode for episode in episodes if episode.status == 0] or episodes[-1:]
        heading = "当前战役" if any(episode.status == 0 for episode in selected) else "最近战役"
    else:
        selected = [episode for episode in episodes if episode.id == episode_id]
        if not selected:
            return None
        heading = "战役详情"
    sections.extend(_episode_section(episode, detailed=episode_id is not None, heading=heading)
                    for episode in selected)
    metadata = _metadata(results)
    metadata["footer"] += ("部分战役正文由上游提供英文原文。",)
    if episode_id is None:
        metadata["footer"] += ("控制中心 <战役编号> 查看全部阶段与结果。",)
    subtitle = ("战役目录 · 当前行动与阶段目标" if episode_id is None
                else f"#{episode_id} · {g._game_text(selected[0].title)}")
    return QueryCard("银河控制中心", subtitle, "HELLDIVERS 2 / CONTROL CENTRE",
                     sections=tuple(sections), **metadata)
