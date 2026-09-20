"""DSS reports with a proportional semicircle and intact station/action panels."""

from __future__ import annotations

import math
from collections.abc import Sequence

from hd2bot import galactic_features as g
from hd2bot.hd2.models import DSSElection, DSSVoteOption, SpaceStation, TacticalAction
from hd2bot.hd2.parsing import number
from hd2bot.hd2.service import DataResult
from hd2bot.presentation import _metadata
from hd2bot.rendering.models import (
    CardRow,
    CardSection,
    QueryCard,
    VoteChart,
    VoteDot,
    VoteLegend,
)

MAX_DOTS = 360
_VOTE_PALETTE = (
    "#4DA8FF", "#F5D547", "#5DCF74", "#EF72AE", "#B299FF", "#FF9656",
    "#44D9D0", "#FA5266", "#D4E5F5", "#B3BE43", "#AC8763", "#8289A6",
)


def _apportion(weights: Sequence[int], count: int) -> list[int]:
    """Hamilton allocation using integer remainders, with stable input-order ties."""
    total = sum(weights)
    if not total or count <= 0:
        return [0] * len(weights)
    divisions = [divmod(weight * count, total) for weight in weights]
    assigned = [quotient for quotient, _ in divisions]
    priority = sorted(range(len(weights)), key=lambda i: (-divisions[i][1], i))
    for index in priority[:count - sum(assigned)]:
        assigned[index] += 1
    return assigned


def _valid_options(election: DSSElection) -> list[DSSVoteOption]:
    return sorted((option for option in election.options
                   if type(option.meta_id) is int and option.meta_id >= 0
                   and type(option.count) is int and option.count >= 0),
                  key=lambda option: (-option.count, option.meta_id, option.text or ""))


def _planet_colors(options: list[DSSVoteOption]) -> dict[int, str]:
    # Allocate a distinct palette by ID, never by vote rank. A new candidate set
    # may change colors; within one election, vote changes never do. Hashing IDs
    # directly to hues made real candidates 158 and 268 almost identical green.
    colors = {}
    for index, planet_id in enumerate(sorted({option.meta_id for option in options})):
        if index < len(_VOTE_PALETTE):
            colors[planet_id] = _VOTE_PALETTE[index]
        else:
            hue = (index * 137_508) % 360_000 / 1000
            colors[planet_id] = f"hsl({hue:.3f}, 72%, 63%)"
    return colors


def _dot_positions(count: int) -> list[tuple[float, float, float]]:
    """Equal-density concentric rows, sorted angularly for continuous color wedges."""
    if count <= 0:
        return []
    rows = min(8, max(1, math.ceil(math.sqrt(count / 6))))
    radii = [300] if rows == 1 else [180 + 210 * row // (rows - 1) for row in range(rows)]
    per_row = _apportion(radii, count)
    positions = []
    dot_radius = min(8, 0.34 * (210 / max(1, rows - 1)))
    for radius, row_count in zip(radii, per_row, strict=True):
        for index in range(row_count):
            angle = math.pi * (index + 0.5) / row_count
            positions.append((angle, radius,
                              round(450 + radius * math.cos(angle), 3),
                              round(415 - radius * math.sin(angle), 3)))
    positions.sort(key=lambda position: (-position[0], -position[1]))
    return [(x, y, round(dot_radius, 3)) for _, _, x, y in positions]


def _chart(election: DSSElection | None, title: str, subtitle: str = "") -> VoteChart:
    options = _valid_options(election) if election is not None else []
    total = sum(option.count for option in options)
    allocated = _apportion([option.count for option in options], min(total, MAX_DOTS))
    colors = _planet_colors(options)
    legend = tuple(VoteLegend(
        option.meta_id, g._vote_label(option), option.count,
        f"{option.count * 100 / total:.2f}%" if total else "0.00%",
        colors[option.meta_id], dots,
    ) for option, dots in zip(options, allocated, strict=True))
    positions = iter(_dot_positions(sum(allocated)))
    dots = []
    for item in legend:
        for _ in range(item.dots):
            x, y, radius = next(positions)
            dots.append(VoteDot(x, y, item.color, radius))
    empty = "" if total else ("候选星球暂无票数" if options else "当前暂无公开迁移投票")
    return VoteChart(title, total, tuple(dots), legend, subtitle, empty)


def _card_metadata(results: list[DataResult], *, invalid: bool = False) -> dict:
    metadata = _metadata(results) if results else {"notices": (), "footer": ()}
    if invalid:
        metadata["notices"] += ("部分选项票数异常，图表仅统计有效数据。",)
    metadata["footer"] += ("投票结果会随时间变化；领先星球不代表最终迁移目的地。",)
    return metadata


def dss_votes_card(elections: list[DSSElection], results: list[DataResult]) -> QueryCard:
    charts = tuple(_chart(election, "迁移投票" if len(elections) == 1
                          else f"迁移投票 · 第 {index} 组")
                   for index, election in enumerate(elections, 1))
    invalid = any(len(_valid_options(election)) != len(election.options) for election in elections)
    return QueryCard(
        "DSS 迁移投票", "每种颜色代表一颗候选星球", "HELLDIVERS 2 / DEMOCRACY SPACE STATION",
        columns=1, vote_charts=charts or (_chart(None, "迁移投票"),),
        **_card_metadata(results, invalid=invalid),
    )


def _action_section(action: TacticalAction, station_label: str = "") -> CardSection:
    rows = [CardRow("当前状态", g.tactical_status(action.status)),
            CardRow("状态截止", g._date(action.expires_at))]
    description = action.strategic_description or action.description
    if description:
        rows.append(CardRow("行动效果", g._game_text(description)))
    if action.status == 1:
        for cost in action.costs:
            item = g._DONATION_ITEMS.get(cost.item_id, "其他物资")
            current, target = number(cost.current), number(cost.target)
            ratio = (max(0, min(100, current / target * 100))
                     if current is not None and current >= 0
                     and target is not None and target > 0 else None)
            detail = f"已筹备 {g._number(cost.current)} / {g._number(cost.target)}"
            if cost.donation_limit is not None and cost.donation_period_seconds is not None:
                detail += (f"\n单人捐献上限 {cost.donation_limit:,} / "
                           f"{cost.donation_period_seconds:,} 秒")
            rows.append(CardRow(f"{item}筹备", g._percent(cost.current, cost.target),
                                detail, ratio))
    return CardSection(station_label + g._game_text(action.name, "战术行动"), tuple(rows),
                       full_width=True)


def dss_station_card(stations: list[SpaceStation], results: list[DataResult]) -> QueryCard:
    sections = []
    charts = []
    invalid = False
    for index, station in enumerate(stations, 1):
        label = "民主空间站" if len(stations) == 1 else f"空间站 {index}"
        status = "运行中" if station.flags == 1 else (
            "当前不可用" if station.flags in {0, 2} else "状态暂无数据")
        election = station.election
        if election is not None:
            invalid |= len(_valid_options(election)) != len(election.options)
        charts.append(_chart(election, f"{label} · 迁移投票",
                             f"驻留：{g._planet(station.planet_index)} · {status}\n"
                             f"本轮投票截止：{g._date(station.election_ends_at)}"))
        prefix = f"{label} · " if len(stations) > 1 else ""
        sections.extend(_action_section(action, prefix) for action in station.tactical_actions)
        if not station.tactical_actions:
            sections.append(CardSection(prefix + "战术行动", (CardRow("", "暂无公开战术行动"),),
                                        full_width=True))
    if not stations:
        sections.append(CardSection("空间站状态", (CardRow("", "当前公开数据中没有空间站"),),
                                    full_width=True))
    return QueryCard(
        "民主空间站 DSS", "迁移投票 · 当前驻留 · 战术行动",
        "HELLDIVERS 2 / DEMOCRACY SPACE STATION", sections=tuple(sections),
        columns=1, vote_charts=tuple(charts), **_card_metadata(results, invalid=invalid),
    )
