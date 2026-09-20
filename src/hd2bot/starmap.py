"""Project public planet coordinates into a static, queryable galactic map."""

from __future__ import annotations

import math

from hd2bot import formatter as f
from hd2bot.hd2.models import Campaign, MajorOrder, Planet
from hd2bot.hd2.service import DataResult
from hd2bot.presentation import _metadata
from hd2bot.rendering.models import (
    CardMetric,
    CardRow,
    CardSection,
    MapLink,
    MapPlanet,
    QueryCard,
    StarMap,
)
from hd2bot.services.localization import faction_name


def _position(planet: Planet) -> tuple[float, float] | None:
    position = planet.position
    if position is None or len(position) != 2:
        return None
    if any(type(value) not in (int, float) or not math.isfinite(value)
           or abs(value) > 1 for value in position):
        return None
    return position


def _marker_offsets(featured: list[Planet], points: dict[int, tuple[float, float]]) -> dict:
    """Place a small number of numbered labels away from nodes and earlier labels."""
    boxes = []
    result = {}
    offsets = [(dx, dy) for dy in (-39, 9, -75, 45, -111, 81)
               for dx in (20, -58, 60, -98)]
    for planet in featured:
        x, y = points[planet.index]
        candidates = []
        for dx, dy in offsets:
            box = (x + dx, y + dy, x + dx + 38, y + dy + 31)
            if box[0] < 32 or box[1] < 32 or box[2] > 968 or box[3] > 968:
                continue
            overlaps = sum(box[0] < b[2] + 5 and box[2] + 5 > b[0]
                           and box[1] < b[3] + 5 and box[3] + 5 > b[1] for b in boxes)
            covers = sum(box[0] - 9 < px < box[2] + 9 and box[1] - 9 < py < box[3] + 9
                         for px, py in points.values())
            score = overlaps * 10000 + covers * 100 + math.hypot(dx + 19, dy + 15.5)
            candidates.append((score, dx, dy, box))
        _, dx, dy, box = min(candidates)
        result[planet.index] = (dx, dy)
        boxes.append(box)
    return result


def map_reply(planets: list[Planet], campaigns: list[Campaign] | None,
              orders: list[MajorOrder] | None, results: list[DataResult],
              focus: Planet | None = None) -> tuple[str, QueryCard | None]:
    """Keep geometry and meaning in typed data; the HTML template only draws it.

    Positive API Y points upwards. Each undirected waypoint is drawn once, and
    only when both endpoints have known coordinates in the current viewport.
    """
    positions = {p.index: _position(p) for p in planets}
    known = {p.index: p for p in planets if positions[p.index] is not None}
    if focus is not None and focus.index not in known:
        return f"【星图 · {f._name(focus)}】\n该星球坐标暂缺或无效，暂时无法绘制局部星图。", None
    if not known:
        return "【银河星图】\n当前数据未提供有效星球坐标，暂时无法绘制星图。", None

    center_x, center_y, half_span = 0.0, 0.0, 1.0
    if focus is not None:
        center_x, center_y = positions[focus.index]
        neighbors = set(focus.waypoints) | {
            p.index for p in planets if focus.index in p.waypoints
        }
        extent = max((max(abs(positions[index][0] - center_x),
                          abs(positions[index][1] - center_y))
                      for index in neighbors if index in known), default=0)
        half_span = max(0.16, extent * 1.18)

    visible = {index: p for index, p in known.items()
               if abs(positions[index][0] - center_x) <= half_span
               and abs(positions[index][1] - center_y) <= half_span}

    def project(index: int) -> tuple[float, float]:
        x, y = positions[index]
        return (round(500 + (x - center_x) * 440 / half_span, 3),
                round(500 - (y - center_y) * 440 / half_span, 3))

    current = {p.index: p for p in planets}
    active = {c.planet.index for c in campaigns or []
              if (p := current.get(c.planet.index)) is not None and not p.disabled
              and (p.owner.value not in {"Humans", "Unknown"} or f._active_defense(p))}
    defense = {p.index for p in planets if f._active_defense(p)}
    major = f._target_indices(orders)
    # Numbered keys stay compact on crowded fronts. Full names are listed below.
    priorities = sorted(visible.values(), key=lambda p: (
        not (focus is not None and p.index == focus.index), p.index != 0,
        p.index not in major, p.index not in defense, p.index not in active,
        -(p.players or 0), p.index,
    ))
    featured = [p for p in priorities if focus is not None or p.index == 0
                or p.index in major | defense | active][:12]
    markers = {p.index: str(number) for number, p in enumerate(featured, 1)}
    points = {index: project(index) for index in visible}
    offsets = _marker_offsets(featured, points)
    nodes = tuple(MapPlanet(
        index=p.index, name=f._name(p), x=project(p.index)[0], y=project(p.index)[1],
        faction=p.owner.value, active=p.index in active, defense=p.index in defense,
        major=p.index in major, disabled=p.disabled,
        focused=focus is not None and p.index == focus.index, marker=markers.get(p.index, ""),
        marker_dx=offsets.get(p.index, (20, -39))[0],
        marker_dy=offsets.get(p.index, (20, -39))[1],
    ) for p in sorted(visible.values(), key=lambda p: p.index))
    pairs = sorted({tuple(sorted((p.index, neighbor)))
                    for p in visible.values() for neighbor in p.waypoints
                    if neighbor in visible and neighbor != p.index})
    links = tuple(MapLink(*project(a), *project(b)) for a, b in pairs)

    title = "银河星图" if focus is None else f"局部星图 · {f._name(focus)}"
    lines = [f"【{title}】", f"可绘制星球：{len(visible)} · 补给连线：{len(links)}"]
    sections = []
    rows = []
    for number, planet in enumerate(featured, 1):
        tags = []
        if planet.index in major:
            tags.append("主线")
        if planet.index in defense:
            tags.append("防守")
        elif planet.index in active:
            tags.append("进攻")
        if planet.disabled:
            tags.append("不可部署")
        detail = " · ".join([faction_name(planet.owner), *tags, f"#{planet.index}"])
        rows.append(CardRow(f"{number:02d}  {f._name(planet)}", f"{f._number(planet.players)} 人", detail))
        lines.append(f"{number:02d}. {f._name(planet)} · {detail} · 在线 {f._number(planet.players)}")
    if rows:
        for start in range(0, len(rows), 4):
            sections.append(CardSection("图中标记" if start == 0 else "图中标记（续）",
                                        tuple(rows[start:start + 4])))
    metadata = _metadata(results)
    notices = list(metadata["notices"])
    missing = len(positions) - len(known)
    if missing:
        notices.append(f"{missing} 个星球坐标缺失或无效，未绘入地图")
    if campaigns is None:
        notices.append("战役数据暂时不可用，进攻标记可能不完整")
    if orders is None:
        notices.append("主线数据暂时不可用，未标记主线目标")
    lines.extend(notices)
    lines.extend(["补给线表示相邻连接，不代表当前可进攻或可部署。",
                  "发送 星图 <星球名称或编号> 查看局部，发送 星球 <名称> 查看详情。"])
    metadata["notices"] = tuple(notices)
    metadata["footer"] += ("星图 <星球> 查看局部 · 星球 <名称> 查看详情",)
    return "\n".join(lines), QueryCard(
        title, "星球控制方 · 战役态势 · 补给连接", "HELLDIVERS 2 / GALACTIC MAP",
        metrics=(CardMetric("图中星球", str(len(visible))),
                 CardMetric("补给连线", str(len(links))),
                 CardMetric("图中战区", str(len((active | defense) & visible.keys()))
                            if campaigns is not None else "暂无数据"),
                 CardMetric("主线目标", str(len(major & visible.keys()))
                            if orders is not None else "暂无数据")),
        sections=tuple(sections), star_map=StarMap(nodes, links, f._name(focus) if focus else ""),
        **metadata,
    )
