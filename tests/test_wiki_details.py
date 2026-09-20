"""Detailed attack components and attachment prices remain distinct source facts."""

from html import escape

import pytest

from hd2bot.wiki_details import extract_weapon_attachments, extract_weapon_details


def _page(*parts):
    return '<div class="mw-parser-output">' + "".join(parts) + "</div>"


def _table(kind, title, *parts):
    rows = [f'<tr><th colspan="2">{escape(title)}</th></tr>']
    for part in parts:
        if isinstance(part, str):
            rows.append(f'<tr><th colspan="2">{escape(part)}</th></tr>')
        else:
            rows.append("<tr>" + "".join(f"<td>{escape(cell)}</td>" for cell in part) + "</tr>")
    return ('<table class="wikitable table-weapon-stats attack-data-table-' + kind + '">'
            + "".join(rows) + "</table>")


def test_no_detailed_tables_does_not_invent_stats_or_read_history():
    html = _page(
        '<div class="druid-row"><div class="druid-label">Recoil</div>'
        '<div class="druid-data">14</div></div>',
        '<h2>Change History</h2><ul><li>Durable damage increased to 22</li></ul>',
        '<table class="wikitable"><tr><td>Damage</td><td>9000</td></tr></table>',
    )
    assert extract_weapon_details(html) == []
    assert extract_weapon_attachments(html) == []


def test_unrelated_malformed_tables_are_not_parsed_as_attachments():
    html = _page('<table class="wikitable"><tr><td rowspan="not-a-number">'
                 'Historical note</td></tr></table>')
    assert extract_weapon_attachments(html) == []


def test_current_detailed_stats_keep_precision_and_do_not_override_infobox():
    html = _page(
        '<div class="druid-row">Recoil 14</div>',
        _table("weapon", "AR-23 LIBERATOR", ("Recoil", "10.5"),
               ("Starting Magazines", "6"), ("Mags from Supply", "8")),
        _table("projectile", "5.5x50mm FULL METAL JACKET P", "Projectile",
               ("Initial Velocity", "900 m/s"), ("Lifetime", "0.899999976 sec"),
               "Damage", ("Standard", "90 Ballistic"), ("vs. Durable", "22 Ballistic")),
    )
    fields = dict(extract_weapon_details(html))
    assert fields["详参·武器本体·基础"] == "后坐力：10.5\n初始弹匣：6\n补给箱补充弹匣：8"
    damage = fields["详参·5.5x50mm 全金属被甲弹 弹体·伤害"]
    assert damage == "基础伤害：90 实弹\n耐久伤害：22 实弹"
    projectile = fields["详参·5.5x50mm 全金属被甲弹 弹体·弹体参数"]
    assert "初速：900 m/s" in projectile
    assert "存在时间：0.899999976 秒" in projectile
    assert all(key.startswith("详参·") for key in fields)
    assert all("14" not in value for value in fields.values())


def test_attack_modes_damage_falloff_and_status_fields_do_not_collapse():
    html = _page(
        _table("weapon", "AC-8 AUTOCANNON", "Attacks",
               ("* 20mm APHET ROUNDS P", "Projectile"),
               ("* 20mm FLAK ROUNDS P", "Projectile"),
               ("** 20mm FLAK ROUNDS P IE", "Explosion")),
        _table("projectile", "20mm APHET ROUNDS P", "Damage",
               ("Standard", "325 Ballistic"), ("vs. Durable", "260 Ballistic")),
        _table("projectile", "20mm FLAK ROUNDS P", "Damage",
               ("Standard", "150 Ballistic"), ("vs. Durable", "150 Ballistic")),
        _table("explosion", "20mm FLAK ROUNDS P IE", "Area of Effect",
               ("Inner Radius", "2 m"), ("Outer Radius", "7 m"),
               "Damage", ("Inner Radius", "190 Explosion"),
               ("Outer Radius", "189 - 0"), ("Inner Durable", "190 Explosion")),
        _table("status", "Fire", "Status", ("Status Duration", "3 sec"),
               "Damage", ("Standard", "100 Fire"), ("vs. Durable", "100 Fire")),
    )
    fields = dict(extract_weapon_details(html))
    assert fields["详参·武器本体·攻击链"].splitlines() == [
        "* 20mm 穿甲高爆曳光弹 弹体：弹体", "* 20mm 近炸弹 弹体：弹体",
        "** 20mm 近炸弹 弹体 命中爆炸：爆炸",
    ]
    assert "耐久伤害：260 实弹" in fields["详参·20mm 穿甲高爆曳光弹 弹体·伤害"]
    assert "基础伤害：150 实弹" in fields["详参·20mm 近炸弹 弹体·伤害"]
    assert fields["详参·20mm 近炸弹 弹体 命中爆炸·作用范围"] == "内圈半径：2 m\n外圈半径：7 m"
    assert fields["详参·20mm 近炸弹 弹体 命中爆炸·伤害"] == (
        "内圈伤害：190 爆炸\n外圈伤害：189 - 0\n内圈耐久伤害：190 爆炸"
    )
    assert fields["详参·火焰·状态"] == "状态持续时间：3 秒"
    assert fields["详参·火焰·伤害"] == "基础伤害：100 火焰\n耐久伤害：100 火焰"


def test_underbarrel_charge_and_repeated_status_strength_retain_context():
    html = _page(_table(
        "weapon", "Test Weapon", ("Capacity", "30"), "Underbarrel AR/GL-21 ONE-TWO 0",
        ("Capacity", "1"), "Attacks", ("* 40mm HEAT GRENADE P", "Projectile"),
        "Charge", ("at (0.10)s", "PLAS-101_P × 0.50 dmg"),
        ("at (1.00)s", "PLAS-101_Charged_P × 1.00 dmg"),
        "Special Effects", ("Status", "Fire"), ("Status Strength", "2"),
        ("Second Status", "Fire_Panic"), ("Status Strength", "3"),
    ))
    fields = extract_weapon_details(html)
    assert fields[0] == ("详参·武器本体·基础", "容量：30")
    assert "下挂" in fields[1][0] and fields[1][1] == "容量：1"
    assert all("下挂" in label for label, _ in fields[1:])
    charge = next(value for key, value in fields if key.endswith("·蓄力"))
    assert charge == (
        "蓄力至 (0.10)秒：PLAS-101 弹体 × 0.50 伤害\n"
        "蓄力至 (1.00)秒：PLAS-101 已蓄力 弹体 × 1.00 伤害"
    )
    status = fields[-1][1]
    assert status == "状态：火焰\n状态强度：2\n第二状态：燃烧恐慌\n状态强度：3"


def test_unfamiliar_values_are_preserved_instead_of_guessed_or_dropped():
    html = _page(_table("weapon", "Experimental", "Heat Data",
                        ("Heat Per Shot", "1.14999998 °C"),
                        ("Cool Per Sec", "12 - 8 - 6"),
                        ("Unknown Property", "EXPERIMENTAL_MODE 3.14159265")))
    fields = extract_weapon_details(html)
    assert fields == [("详参·武器本体·热量",
                       "每发热量：1.14999998 °C\n每秒冷却：12 - 8 - 6\n"
                       "Unknown Property：EXPERIMENTAL MODE 3.14159265")]


def test_unsupported_detailed_row_layout_fails_instead_of_losing_cells():
    html = _page(_table("projectile", "Example", ("Damage", "100", "200")))
    with pytest.raises(ValueError, match="unsupported row layout"):
        extract_weapon_details(html)


def test_attachment_rowspans_keep_weapon_level_and_price_separate_from_effect():
    html = _page('''
        <table class="wikitable sticky-header">
          <tr><th>Weapon</th><th>Category</th><th>Attachment Name</th>
              <th>Unlock Level</th><th>Unlock Cost</th><th>Effect</th></tr>
          <tr><td rowspan="2">Liberator</td><td rowspan="2">Optics</td>
              <td>Reflex Sight</td><td>Weapon Level 4</td><td>Requisition Slips 5,000</td>
              <td>ZOOM 25m</td></tr>
          <tr><td>4x Combat Scope</td><td>Weapon Level 21</td><td>Requisition Slips 25,000</td>
              <td>ERGONOMICS -2, ZOOM 25m • 75m • 150m</td></tr>
          <tr><td>Liberator</td><td>Muzzle</td><td>No Attachment</td>
              <td>Weapon Level 1</td><td>Requisition Slips 0</td><td></td></tr>
        </table>
    ''')
    attachments = extract_weapon_attachments(html)
    assert attachments == [
        ("配件·瞄具·反射瞄具", "解锁条件：武器等级 4\n配件费用：申购点 5,000\n效果：瞄准距离 25m"),
        ("配件·瞄具·4x 战斗瞄具", "解锁条件：武器等级 21\n配件费用：申购点 25,000\n"
         "效果：操控性 -2, 瞄准距离 25m • 75m • 150m"),
        ("配件·枪口·无配件", "解锁条件：武器等级 1\n配件费用：申购点 0"),
    ]


def test_heatsink_attachment_keeps_rates_capacity_and_reload_values():
    html = _page('''
        <table class="wikitable">
          <tr><th>Category</th><th>Attachment Name</th><th>Unlock Level</th>
              <th>Unlock Cost</th><th>Effect</th></tr>
          <tr><td>Magazine</td><td>High Capacity Heatsink</td><td>Weapon Level 24</td>
              <td>Requisition Slips 25,000</td>
              <td>ERGONOMICS -10, OVERHEATS AT 150°C, COOLDOWN RATE 8.5°C/s,
                  2 START MAGS, 3 MAX MAGS, 2.25s FULL RELOAD, 3.6s PARTIAL RELOAD</td></tr>
        </table>
    ''')
    title, value = extract_weapon_attachments(html)[0]
    assert title == "配件·弹匣·高容量散热器"
    assert "过热温度 150°C, 冷却速率 8.5°C/秒" in value
    assert "2 初始弹匣, 3 最大弹匣, 2.25秒 空仓换弹, 3.6秒 战术换弹" in value
