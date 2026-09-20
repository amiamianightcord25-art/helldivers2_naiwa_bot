"""Faction warfront summaries from the same public battle/planet snapshot."""

from hd2bot import formatter as f
from hd2bot.hd2.errors import CommandError
from hd2bot.hd2.models import Campaign, Faction, MajorOrder, Planet, utcnow
from hd2bot.services.localization import faction_name

FACTIONS = {
    "虫族": Faction.TERMINIDS, "终结族": Faction.TERMINIDS, "terminids": Faction.TERMINIDS,
    "机器人": Faction.AUTOMATONS, "automaton": Faction.AUTOMATONS,
    "automatons": Faction.AUTOMATONS, "光能族": Faction.ILLUMINATE,
    "光能者": Faction.ILLUMINATE,
    "illuminate": Faction.ILLUMINATE,
}


def parse_front(argument: str) -> Faction | None:
    if not argument:
        return None
    faction = FACTIONS.get(argument.casefold())
    if faction is None:
        raise CommandError("用法：战线 虫族 / 战线 机器人 / 战线 光能族；发送 战线 查看全部阵营。")
    return faction


def format_front(planets: list[Planet], campaigns: list[Campaign],
                 orders: list[MajorOrder] | None, faction: Faction | None) -> str:
    sides = [faction] if faction is not None else [
        Faction.TERMINIDS, Faction.AUTOMATONS, Faction.ILLUMINATE,
    ]
    lines = ["【阵营战线】"]
    targets = f._target_indices(orders)
    current = {p.index: p for p in planets}

    def urgent(p):
        return bool(p.event and p.event.event_type == 0 and not p.disabled
                    and (p.event.ends_at is None or f._aware(p.event.ends_at) > utcnow()))

    for side in sides:
        owned = [p for p in planets if p.owner == side]
        defenses = [p for p in planets if f._active_defense(p) and p.event.faction == side]
        attacks = {p.index: p for c in campaigns
                   if (p := current.get(c.planet.index)) is not None
                   and p.owner == side and not p.disabled and not f._active_defense(p)}
        fronts = {p.index: p for p in [*attacks.values(), *defenses]}
        known = [p.players for p in fronts.values() if p.players is not None]
        players = f._number(sum(known)) if len(known) == len(fronts) else "暂无完整数据"
        lines.extend(["", f"{faction_name(side)}：控制 {len(owned)} 个星球",
                      f"进攻 {len(attacks)} 处 · 防守 {len(defenses)} 处 · 战区在线 {players}"])
        ordered = sorted(fronts.values(), key=lambda p: (
            not urgent(p), not f._active_defense(p), p.index not in targets, -(p.players or 0), p.index,
        ))
        for p in ordered:
            tags = "主线 · " if p.index in targets else ""
            if urgent(p):
                tags += f"紧急解放（{f._remaining(p.event.ends_at)}） · "
            lines.append(f"• {f._name(p)}（#{p.index}） | {tags}{f._planet_state(p)}"
                         f" | {f._number(p.players)} 人")
        if not ordered:
            lines.append("暂无进行中的该阵营战役。")
        remaining = sorted((p for p in owned if p.index not in fronts), key=lambda p: p.index)
        if remaining:
            lines.append("其他控制星球：" + "、".join(
                f"{f._name(p)} #{p.index}" + ("（不可部署）" if p.disabled else "")
                for p in remaining
            ))
    if orders is None:
        lines.append("主线数据暂时不可用，目标标记可能不完整。")
    lines.append("发送 星图 查看银河分布；星球 <名称> 查看单星详情。")
    return "\n".join(lines)
