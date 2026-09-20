"""Chinese equipment presentation preserves facts and exposes translation gaps."""

import json
import os
import re

import pytest

from hd2bot.wiki import localization as zh
from hd2bot.wiki.models import CatalogEntry
from hd2bot.wiki.service import CatalogService


def write_texts(path, texts):
    old = path.stat().st_mtime_ns if path.exists() else 0
    path.write_text(json.dumps({"schema_version": 1, "texts": texts}, ensure_ascii=False),
                    encoding="utf8")
    if old:
        os.utime(path, ns=(old + 1_000_000, old + 1_000_000))


@pytest.mark.parametrize(("label", "value", "expected"), [
    ("Weapon Category", "Primary Weapons", "主要武器"),
    ("Firing Modes", "Auto • Semi • Burst", "全自动 • 半自动 • 点射"),
    ("Fire Rate", "640 rpm", "640 发/分钟"),
    ("Fire Rate", "160rpm • 240rpm • 320rpm", "160发/分钟 • 240发/分钟 • 320发/分钟"),
    ("Standard Damage", "90 Ballistic", "90 实弹"),
    ("Reload Time", "4s 3.6s (Upgraded)", "4秒 3.6秒 (升级后)"),
    ("Cost", "7000 Requisition Slips", "7000 申购单"),
    ("Cost", "200 Super Credits", "200 超级货币"),
    ("Source", "Starter Equipment", "初始装备"),
    ("Source", "Castellan’s Creed P2", "卡斯特兰信条 第2页"),
    ("Source", "Exo Experts P1", "外骨骼装甲专家 第1页"),
    ("Fuse Time", "Impact", "碰撞引爆"),
    ("Standard Damage", "230 Impact", "230 撞击"),
    ("Cookable", "Yes", "是"), ("Cookable", "No", "否"),
    ("Fire Rate", "N/A", "不适用"), ("Fire Rate", "Not applicable", "不适用"),
    ("Firing Modes", "None", "无"),
])
def test_reviewed_stats_units_and_sources_are_chinese(label, value, expected):
    assert zh.localize_value(label, value) == expected


def test_ac8_composite_damage_keeps_all_values_and_translates_all_terms():
    original = "325 Projectile (APHET) 150 Explosion 110 Ballistic (Flak x30) 190 Explosion"
    result = zh.localize_value("Standard Damage", original)
    assert re.findall(r"\d+", result) == re.findall(r"\d+", original)
    assert not re.search(r"[A-Za-z]", result)
    assert "弹体" in result and "近炸弹 ×30" in result


def test_localized_copy_preserves_source_and_english_reference_and_original_record():
    entry = CatalogEntry("weapons:test", "weapons", "AR-23 Liberator", code="AR-23",
        aliases=("解放者",), subcategory="Primary / Assault Rifle",
        fields=(("Standard Damage", "90 Ballistic"), ("Fire Rate", "640 rpm"),
                ("Capacity", "45")), source_url="https://helldivers.wiki.gg/wiki/AR-23_Liberator",
        revision="133890")
    result = zh.localize_entry(entry)
    assert result.name == "解放者" and result.english_name == "AR-23 Liberator"
    assert result.fields == (("基础伤害", "90 实弹"), ("射速", "640 发/分钟"), ("容量", "45"))
    assert result.source_url == entry.source_url and result.revision == entry.revision
    assert entry.name == "AR-23 Liberator" and entry.fields[0][1] == "90 Ballistic"
    assert result.subcategory == "主武器 / 突击步枪"


def test_native_chinese_fields_summary_and_name_win_over_old_aliases():
    entry = CatalogEntry("weapons:test", "weapons", "中文原名", english_name="Original Name",
                         aliases=("较旧译名",), summary="中文原页面说明，90 点实弹伤害。",
                         fields=(("装甲穿透等级", "轻型装甲"), ("射速", "640 发/分钟")))
    result = zh.localize_entry(entry)
    assert result.name == "中文原名"
    assert result.summary == entry.summary and result.fields == entry.fields


def test_new_or_changed_prose_never_silently_reuses_an_old_translation(tmp_path, monkeypatch):
    path = tmp_path / "texts.json"
    write_texts(path, {"Deals 10 damage.": "造成 10 点伤害。"})
    monkeypatch.setattr(zh, "_TEXTS_PATH", path)
    assert zh.localize_summary("Deals 10 damage.") == "造成 10 点伤害。"
    assert zh.localize_summary("Deals 20 damage.") == zh.PENDING_DESCRIPTION
    assert zh.localize_value("Description", "A newly discovered long English paragraph.") \
        == zh.PENDING_DESCRIPTION
    write_texts(path, {"Deals 20 damage.": "造成 20 点伤害。"})
    assert zh.localize_summary("Deals 20 damage.") == "造成 20 点伤害。"


def test_english_sentence_with_one_chinese_weapon_name_is_not_mistaken_for_chinese():
    value = "This powerful 机枪 provides continuous fire against large groups of enemy infantry."
    assert zh.localize_summary(value, texts={}) == zh.PENDING_DESCRIPTION
    chinese = "超级地球武装部队（SEAF）的 AR-23 制式步枪，提供可靠的持续火力。"
    assert zh.localize_summary(chinese, texts={}) == chinese


def test_unknown_short_terms_are_flagged_without_guessing_a_translation():
    value = zh.localize_value("Source", "Unverified Proper Noun P2")
    assert value == "待译：Unverified Proper Noun 第2页"
    # Matching a known term must not alter characters inside an unfamiliar word.
    assert zh.localize_value("Firing Modes", "Semiotic") == "待译：Semiotic"


def test_project_names_use_concise_chinese_in_queries():
    result = zh.localize_value("Source", "Righteous Revenants P2")
    assert result == "正义亡魂 第2页"
    assert "待译" not in result
    assert zh.localize_value("获取方式", "Station-81 | ❌") == \
        "81号站 | ❌"


def test_missing_or_invalid_translation_assets_do_not_break_queries(tmp_path, monkeypatch):
    monkeypatch.setattr(zh, "_TEXTS_PATH", tmp_path / "missing.json")
    assert zh.localize_summary("Some English prose.") == zh.PENDING_DESCRIPTION
    broken = tmp_path / "terms.json"
    broken.write_text("{invalid", encoding="utf8")
    monkeypatch.setattr(zh, "_TERMS_PATH", broken)
    assert zh.localize_value("Damage", "90 Ballistic") == "待译：90 Ballistic"


def test_real_ar23_card_has_chinese_facts_and_keeps_original_numbers():
    catalog = CatalogService()
    candidates = catalog.handle("武器", "AR23")
    assert "候选" in candidates.text
    reply = catalog.handle("选择", "1")
    assert reply.card is not None
    body = "\n".join(row.label + "：" + row.value
                     for section in reply.card.sections for row in section.rows)
    assert "Primary Weapons" not in body and "Light Armor Penetrating" not in body
    assert "90 实弹" in body and "640 发/分钟" in body and "容量：45" in body
    assert "AR-23 Liberator" in reply.card.subtitle
    assert any("helldivers.wiki.gg" in line for line in reply.card.footer)
