"""Read the wiki's detailed weapon tables without conflating attack components.

Values stay textual: the source's precision, ranges, and separate attack components
must survive import. These fields supplement the infobox rather than replacing it.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hd2bot.wiki_sync import Node


_LABELS = {
    "Attacks": "攻击链", "Projectile": "弹体参数", "Damage": "伤害",
    "Penetration": "穿透", "Special Effects": "特殊效果", "Status": "状态",
    "Area of Effect": "作用范围", "AoE Effect": "范围效果", "Heat Data": "热量",
    "Beam": "光束参数", "Arc": "电弧参数", "Charge": "蓄力",
    "Fire Rate": "射速", "Recoil": "后坐力", "Horizontal Recoil": "水平后坐力",
    "Vertical Recoil": "垂直后坐力", "Spread": "散布", "Sway": "晃动",
    "Ergonomics": "操控性", "Capacity": "容量", "Spare Magazines": "备用弹匣",
    "Starting Magazines": "初始弹匣", "Mags from Supply": "补给箱补充弹匣",
    "Mags from Ammo Box": "弹药箱补充弹匣", "Mass": "质量", "Initial Velocity": "初速",
    "Drag Factor": "阻力系数", "Gravity Factor": "重力系数",
    "Penetration Slowdown": "穿透减速", "Standard": "基础伤害",
    "vs. Durable": "耐久伤害", "Direct": "正面", "Slight Angle": "小角度",
    "Large Angle": "大角度", "Extreme Angle": "极端角度",
    "Demolition Force": "拆除能力", "Stagger Force": "硬直强度", "Push Force": "推力",
    "Status Strength": "状态强度", "Status Duration": "状态持续时间", "Element": "伤害类型",
    "Spare Rounds": "备用弹药", "Starting Rounds": "初始弹药",
    "Rounds from Supply": "补给箱补充弹药", "Rounds from Ammo Box": "弹药箱补充弹药",
    "Explosion On Impact": "命中爆炸", "Explode After": "延时引爆",
    "Inner Radius": "内圈半径", "Outer Radius": "外圈半径",
    "Shockwave Radius": "冲击波半径", "Damage Element": "伤害类型",
    "Inner Durable": "内圈耐久伤害", "Outer Durable": "外圈耐久伤害",
    "Second Status": "第二状态", "Third Status": "第三状态", "Pellets": "弹丸数",
    "Barrels": "枪管数", "Lifetime": "存在时间", "Shrapnel Count": "弹片数量",
    "Shrapnel": "弹片", "Overheats at": "过热温度", "Warmup": "预热时间",
    "Heat Per Second": "每秒热量", "Cool Per Sec": "每秒冷却",
    "Cooldown After Overheat": "过热后冷却", "Beam Fire Rate": "光束频率",
    "Beam Range": "光束射程", "Heat Per Shot": "每发热量", "Arc Range": "电弧距离",
    "Arc Velocity": "电弧速度", "Speed Mult Range": "速度倍率范围",
    "Distance Mult Range": "距离倍率范围", "Damage Mult Range": "伤害倍率范围",
    "Beams": "光束数量", "Reload Time": "换弹时间", "Tactical Reload": "战术换弹",
    "Throwables from Supply": "补给箱补充投掷物", "Max Rounds": "最大弹药",
    "AoE Duration": "范围持续时间", "Main Health": "主体生命", "Main Armor": "主体装甲",
    "Shield Capacity": "护盾容量", "Shield Regen": "护盾恢复",
    "Shield Regen Delay": "护盾恢复延迟", "Shield Broken Delay": "破盾恢复延迟",
    "Shield Radius": "护盾半径", "Cooldown": "冷却时间", "Uses": "使用次数",
}

_TERMS = {
    "FULL METAL JACKET": "全金属被甲弹", "HIGH VELOCITY": "高速弹",
    "HOLLOW POINT": "空尖弹", "PENETRATOR": "穿甲弹", "SUBSONIC": "亚音速弹",
    "APHET ROUNDS": "穿甲高爆曳光弹", "FLAK ROUNDS": "近炸弹",
    "HIGH-EXPLOSIVE GRENADE": "高爆榴弹", "HEAT GRENADE": "高爆反坦克榴弹",
    "HE GRENADE": "高爆榴弹", "HIGH EXPLOSIVE": "高爆弹", "EXPLOSIVE": "爆炸弹",
    "STANDARD ROCKET": "标准火箭弹", "HOMING GROUND MISSILE": "对地制导导弹",
    "CLUSTER BOMB": "集束炸弹", "RAILGUN ROUND": "磁轨炮弹",
    "MEDIUM PLASMA BOLT": "中型等离子弹", "LARGE PLASMA BOLT": "大型等离子弹",
    "SMALL PLASMA BOLT": "小型等离子弹", "BCHP RIFLE ROUNDS": "BCHP 步枪弹",
    "EIT ROUNDS": "EIT 弹", "STUN ROUNDS": "眩晕弹", "RIFLED SLUGS": "独头弹",
    "LIBERTY FIRE": "自由之火弹", "FLECHETTES": "箭形弹", "BUCKSHOT": "鹿弹",
    "BIRDSHOT": "鸟弹", "MAGNUM": "马格南弹", "SHRAPNEL": "弹片",
    "MISSILE": "导弹", "LASER": "激光", "FLAMEWALL": "火墙",
    "Fire Panic": "燃烧恐慌", "FlamerSlowed": "火焰减速", "BurningHeavy": "重度燃烧",
    "Gas Confusion": "毒气混乱", "Stun Medium": "中型眩晕", "Stun Large": "大型眩晕",
    "Stun Small": "小型眩晕", "Thermite": "铝热燃烧", "Fire": "火焰", "Gas": "毒气",
    "Non-Guided": "非制导", "Guided": "制导", "Artillery": "炮击", "Burst": "齐射",
    "CHARGED": "已蓄力", "Overcharge": "过载", "Underbarrel": "下挂",
    "DEFAULT": "默认", "RELOAD NEEDED": "需要换弹", "Var2": "变体2",
    "Bayonet Melee": "刺刀近战", "Railgun Max Charge": "磁轨炮最大蓄力",
    "Stun Lance": "眩晕长矛", "Stun Baton": "眩晕短棍", "Combat Hatchet": "战斧",
    "Saber": "军刀", "Machete": "砍刀", "Entrenchment Tool": "工兵铲",
    "One True Flag": "唯一真旗", "Breaching Hammer Impact": "破门锤冲击",
    "Breaching Hammer": "破门锤", "Defolation Tool": "除叶工具",
    "Defoliation Tool": "除叶工具", "SOLO SILO": "单兵发射井", "SWP": "特种武器",
    "Ballistic": "实弹", "Explosion": "爆炸", "Melee": "近战", "Damage": "伤害",
    "Projectile": "弹体", "Spray": "喷射", "Beam": "光束", "Arc": "电弧",
    "Status": "状态", "None": "无", "Very Light": "极轻甲", "Light": "轻甲",
    "Medium": "中甲", "Heavy": "重甲", "Unarmored": "无甲",
    "Anti-Tank III": "反坦克 III", "Anti-Tank II": "反坦克 II",
    "Anti-Tank V": "反坦克 V", "Anti-Tank I": "反坦克 I", "dmg": "伤害",
    "Optics": "瞄具", "Magazine": "弹匣", "Muzzle": "枪口",
    "Tube Red Dot": "管式红点瞄具", "Sniper Scope": "狙击镜", "Combat Scope": "战斗瞄具",
    "Angled Foregrip": "斜角前握把", "Compensator": "补偿器", "Drum Magazine": "弹鼓",
    "Duckbill": "鸭嘴收束器", "Extended Magazine": "扩容弹匣", "Flash Hider": "消焰器",
    "Flashlight Vertical Foregrip": "手电垂直前握把", "Full Choke": "全收束器",
    "Half Choke": "半收束器", "High Capacity Heatsink": "高容量散热器",
    "High Dissipation Heatsink": "高散热散热器", "Holographic Sight": "全息瞄具",
    "Iron Sight": "机械瞄具", "Laser Sight": "激光瞄具",
    "Laser Sight Angled Foregrip": "激光斜角前握把",
    "Laser Sight W/ Flashlight": "激光瞄具与手电", "Muzzle Brake": "制退器",
    "No Attachment": "无配件", "No Choke": "无收束器", "No Optics": "无瞄具",
    "No Underbarrel": "无下挂配件", "Reflex Sight Mk2": "反射瞄具 Mk2",
    "Reflex Sight": "反射瞄具", "Short Magazine": "短弹匣", "Standard Heatsink": "标准散热器",
    "Vertical Foregrip": "垂直前握把", "Weapon Level": "武器等级",
    "Requisition Slips": "申购点", "CAPACITY": "容量", "START MAGS": "初始弹匣",
    "MAX MAGS": "最大弹匣", "FULL RELOAD": "空仓换弹", "PARTIAL RELOAD": "战术换弹",
    "ERGONOMICS": "操控性", "SWAY": "晃动", "ZOOM": "瞄准距离",
    "VERTICAL RECOIL": "垂直后坐力", "HORIZONTAL RECOIL": "水平后坐力",
    "VERTICAL SPREAD": "垂直散布", "HORIZONTAL SPREAD": "水平散布",
    "OVERHEATS AT": "过热温度", "COOLDOWN RATE": "冷却速率", "ROUNDS": "发",
}

_SUFFIXES = {"P": "弹体", "IE": "命中爆炸", "E": "爆炸", "EImpact": "冲击爆炸",
             "B": "光束", "A": "电弧", "S": "喷射", "dm": "伤害"}


@lru_cache(maxsize=1)
def _translation_pattern() -> tuple[re.Pattern, dict[str, str]]:
    # Existing reviewed weapon names also identify attack components (FLAM-66, etc.).
    path = Path(__file__).parent / "assets" / "wiki_catalog.json"
    try:
        entries = json.loads(path.read_text("utf8")).get("entries", [])
    except (OSError, ValueError, AttributeError):
        entries = []
    terms = {}
    for entry in entries:
        name = entry.get("name", "")
        original = entry.get("english_name", "")
        if original and re.search(r"[\u3400-\u9fff]", name):
            terms[original.casefold()] = name
    terms.update({key.casefold(): value for key, value in _TERMS.items()})
    pattern = re.compile(
        r"(?<![A-Za-z])(?:" + "|".join(re.escape(key) for key in
                                        sorted(terms, key=len, reverse=True)) + r")(?![A-Za-z])",
        re.IGNORECASE,
    )
    return pattern, terms


def _translate(value: str) -> str:
    """Translate known terms; retain unknown identifiers and every numeric lexeme."""
    # Underscores are reference separators in the wiki's linked attack identifiers.
    value = value.replace("_", " ")
    pattern, terms = _translation_pattern()
    value = pattern.sub(lambda match: terms[match.group().casefold()], value)
    # These suffixes label the source's component IDs. Keep their variant numbers.
    value = re.sub(r"(?<!\S)(EImpact|IE|P|E|B|A|S|dm)(\d*)(?!\S)",
                   lambda m: _SUFFIXES[m[1]] + m[2], value)
    value = re.sub(r"(?<=\d)(\s*)rpm\b", r"\1发/分钟", value, flags=re.IGNORECASE)
    value = re.sub(r"\bshield/s\b", "护盾/秒", value)
    value = re.sub(r"(?<=\d)(\s*)(?:sec|s)\b", r"\1秒", value)
    value = value.replace("°C/s", "°C/秒")
    return value


def _label(value: str, section: str) -> str:
    # The explosion table reuses radius headings for damage falloff, not distances.
    if section == "Damage" and value in {"Inner Radius", "Outer Radius"}:
        return {"Inner Radius": "内圈伤害", "Outer Radius": "外圈伤害"}[value]
    charge = re.fullmatch(r"at (\([^)]*\))s", value)
    if charge:
        return "蓄力至 " + charge[1] + "秒"
    return _LABELS.get(value, _translate(value))


def _table_groups(table: Node) -> list[tuple[str, str]]:
    output: list[tuple[str, str]] = []
    title = ""
    section = "基础"
    subweapon = ""
    pairs: list[str] = []

    def flush() -> None:
        if pairs:
            context = "·".join(part for part in (title, subweapon, _label(section, "")) if part)
            output.append(("详参·" + context, "\n".join(pairs)))
            pairs.clear()

    for row in table.walk():
        if row.tag != "tr":
            continue
        cells = [node for node in row.children if not isinstance(node, str)
                 and node.tag in {"td", "th"}]
        if not cells:
            continue
        if cells[0].tag == "th":
            flush()
            heading = cells[0].text()
            if not title:
                title = "武器本体" if table.has("attack-data-table-weapon") else _translate(heading)
            elif heading.startswith("Underbarrel "):
                subweapon = _translate(heading)
                section = "基础"
            else:
                section = heading
            continue
        if len(cells) != 2:
            raise ValueError("Weapon detail table has an unsupported row layout")
        label, value = cells[0].text(), cells[1].text()
        if label and value:
            pairs.append(_label(label, section) + "：" + _translate(value))
    flush()
    return output


def extract_weapon_details(html: str) -> list[tuple[str, str]]:
    """Return compact Chinese groups from the current detailed-statistics tables.

    Pages without these tables return an empty list. History, infoboxes, attachment
    prices and prose are deliberately outside this source of numeric statistics.
    """
    # Lazy import allows wiki_sync.enrich_weapon to call this module without a cycle.
    from hd2bot.wiki_sync import article

    output = []
    for table in article(html).walk():
        if table.tag == "table" and table.has("table-weapon-stats"):
            output.extend(_table_groups(table))
    return output


def extract_weapon_attachments(html: str) -> list[tuple[str, str]]:
    """Retain each attachment's weapon-level requirement, own price and effects."""
    from hd2bot.wiki_sync import article, rows

    output = []
    required = {"Category", "Attachment Name", "Unlock Level", "Unlock Cost", "Effect"}
    for table in article(html).walk():
        if table.tag != "table" or not table.has("wikitable"):
            continue
        if not any(node.tag == "th" and node.text() == "Attachment Name"
                   for node in table.walk()):
            continue
        matrix = rows(table)
        if not matrix:
            continue
        headers = [cell.text() for cell in matrix[0]]
        if not required.issubset(headers):
            continue
        for row in matrix[1:]:
            if len(row) != len(headers):
                raise ValueError("Weapon attachment table has an unsupported row layout")
            data = {key: cell.text() for key, cell in zip(headers, row, strict=True)}
            if not data["Attachment Name"]:
                continue
            title = "配件·" + _translate(data["Category"]) + "·" + _translate(data["Attachment Name"])
            values = []
            for key, label in (("Unlock Level", "解锁条件"), ("Unlock Cost", "配件费用"),
                               ("Effect", "效果")):
                if data[key]:
                    values.append(label + "：" + _translate(data[key]))
            output.append((title, "\n".join(values)))
    return output
