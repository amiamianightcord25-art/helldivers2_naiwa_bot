"""Offline catalogue usability, snapshot recovery and isolated numbered choices."""

import json
import os
import socket

import pytest
from PIL import Image

from hd2bot.presentation import ChatContext
from hd2bot.rendering.renderer import HtmlRenderer
from hd2bot.wiki.service import CatalogService


def equipment(index=1, **changes):
    value = {
        "id": f"weapons:primary:item-{index}", "category": "weapons",
        "name": f"Sample Rifle {index}", "english_name": f"Sample Rifle {index}",
        "code": f"AR-{index}", "aliases": [f"步枪{index}"],
        "subcategory": "Primary / Assault Rifle", "summary": "A local equipment summary.",
        "fields": [["伤害", "70"], ["容量", "45"]],
        "source_url": f"https://helldivers.wiki.gg/wiki/Sample_Rifle_{index}",
        "revision": str(100 + index),
    }
    value.update(changes)
    return value


def write_snapshot(path, entries=None, **changes):
    payload = {
        "schema_version": 1, "synced_at": "2026-09-16T12:00:00Z",
        "source": {"name": "Helldivers Wiki", "url": "https://helldivers.wiki.gg/",
                   "license": "CC BY-NC-SA 4.0",
                   "license_url": "https://creativecommons.org/licenses/by-nc-sa/4.0/"},
        "entries": [equipment()] if entries is None else entries,
    }
    payload.update(changes)
    path.parent.mkdir(parents=True, exist_ok=True)
    previous = path.stat().st_mtime_ns if path.exists() else 0
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf8")
    if previous:
        os.utime(path, ns=(previous + 1_000_000, previous + 1_000_000))
    return payload


def service_for(tmp_path, entries=None, **kwargs):
    path = tmp_path / "catalog.json"
    write_snapshot(path, entries)
    service = CatalogService(path, **kwargs)
    service.bundled_path = tmp_path / "missing-bundled.json"
    return service


def test_home_guides_users_who_do_not_know_equipment_names(tmp_path):
    reply = service_for(tmp_path).handle("百科")
    assert reply.card is None
    assert "武器 列表" in reply.text
    assert "型号、中文别名、英文片段" in reply.text
    assert "选择 1" in reply.text


def test_simple_detail_is_local_text_with_source_and_license(tmp_path, monkeypatch):
    service = service_for(tmp_path)

    def no_network(*args, **kwargs):
        raise AssertionError("Catalogue queries must not use the network")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    reply = service.handle("武器", "ar 1")
    assert reply.card is None
    assert "伤害：70" in reply.text
    assert "Sample_Rifle_1" in reply.text
    assert "2026-09-16" in reply.text
    assert "CC BY-NC-SA 4.0" in reply.text


def test_stable_id_and_alias_queries_can_select_directly(tmp_path):
    service = service_for(tmp_path)
    for query in ("weapons:primary:item-1", "步枪1", "AR-1"):
        assert "伤害：70" in service.handle("百科", query).text


def test_directory_pages_number_current_page_and_choose_correct_item(tmp_path):
    service = service_for(tmp_path, [equipment(index) for index in range(1, 19)])
    first = service.handle("武器")
    assert "第 1/3 页" in first.text
    assert "8. 步枪8 · AR-8" in first.text
    assert first.card is None
    second = service.handle("下一页")
    assert "第 2/3 页" in second.text
    assert "1. 步枪9 · AR-9" in second.text
    selected = service.handle("选择", "1")
    assert "步枪9 · AR-9" in selected.text
    assert "没有可用的候选" in service.handle("选择", "1").text


def test_explicit_pages_bounds_and_previous_page(tmp_path):
    service = service_for(tmp_path, [equipment(index) for index in range(1, 10)])
    assert "页码超出" in service.handle("武器", "列表 0").text
    assert "页码超出" in service.handle("武器", "列表 3").text
    assert "第 2/2 页" in service.handle("武器", "列表 2").text
    assert "最后一页" in service.handle("下一页").text
    assert "第 1/2 页" in service.handle("上一页").text
    assert "第一页" in service.handle("上一页").text
    assert "1–8" in service.handle("选择", "9").text
    assert "用法" in service.handle("选择", "hello").text


def test_excessively_long_page_number_reports_range_instead_of_crashing(tmp_path):
    service = service_for(tmp_path)
    reply = service.handle("武器", "列表 " + "9" * 5000)
    assert "页码超出范围：共 1 页" in reply.text
    assert "第 1/1 页" in service.handle("武器", "列表 1").text


def test_c2c_and_group_members_have_separate_choices(tmp_path):
    service = service_for(tmp_path, [equipment(index) for index in range(1, 10)])
    group_a = ChatContext("group", "group-1", "user-a")
    group_b = ChatContext("group", "group-1", "user-b")
    other_group = ChatContext("group", "group-2", "user-a")
    direct = ChatContext("c2c", "user-a")
    service.handle("武器", context=group_a)
    service.handle("武器", "列表 2", context=group_b)
    service.handle("武器", context=direct)
    assert "没有可用的候选" in service.handle("选择", "1", context=other_group).text
    assert "步枪9 · AR-9" in service.handle("选择", "1", context=group_b).text
    assert "步枪1 · AR-1" in service.handle("选择", "1", context=group_a).text
    assert "步枪1 · AR-1" in service.handle("选择", "1", context=direct).text
    assert "没有可用的候选" in service.handle("选择", "1").text


def test_group_without_member_identity_never_shares_choices(tmp_path):
    service = service_for(tmp_path)
    context = ChatContext("group", "group-1")
    reply = service.handle("武器", context=context)
    assert "未保存选择" in reply.text
    assert "不能使用编号选择" in service.handle("选择", "1", context=context).text
    assert not service._choices
    assert "伤害：70" in service.handle("武器", "AR-1", context=context).text


def test_selection_and_pages_expire_without_saving_any_identity_to_disk(tmp_path):
    now = [0.0]
    service = service_for(tmp_path, clock=lambda: now[0], selection_ttl=30)
    files_before = sorted(tmp_path.rglob("*"))
    context = ChatContext("c2c", "private-openid")
    service.handle("武器", context=context)
    now[0] = 30
    assert "已过期" in service.handle("下一页", context=context).text
    assert not service._choices
    assert sorted(tmp_path.rglob("*")) == files_before
    assert "private-openid" not in service.snapshot_path.read_text(encoding="utf8")


def test_capacity_eviction_keeps_recent_context(tmp_path):
    service = service_for(tmp_path, capacity=2)
    for identifier in ("old", "middle", "new"):
        service.handle("武器", context=ChatContext("c2c", identifier))
    assert len(service._choices) == 2
    assert "已过期" in service.handle("选择", "1", context=ChatContext("c2c", "old")).text
    assert "伤害：70" in service.handle("选择", "1", context=ChatContext("c2c", "new")).text


def test_changed_snapshot_invalidates_choices_before_selection(tmp_path):
    service = service_for(tmp_path, [equipment(1), equipment(2)])
    service.handle("武器")
    write_snapshot(service.snapshot_path, [equipment(2), equipment(1, summary="已更新的中文说明")])
    reply = service.handle("选择", "1")
    assert "资料已更新" in reply.text
    assert "伤害：" not in reply.text
    assert "已更新的中文说明" in service.handle("武器", "AR-1").text


def test_identical_snapshot_rewrite_does_not_invalidate_choices(tmp_path):
    service = service_for(tmp_path)
    service.handle("武器")
    write_snapshot(service.snapshot_path)
    assert "伤害：70" in service.handle("选择", "1").text


def test_bad_new_snapshot_preserves_last_good_and_recovers(tmp_path):
    service = service_for(tmp_path)
    assert "伤害：70" in service.handle("武器", "AR-1").text
    service.snapshot_path.write_text("{partial update", encoding="utf8")
    reply = service.handle("武器", "AR-1")
    assert "伤害：70" in reply.text
    assert "最近有效快照" in reply.text
    write_snapshot(service.snapshot_path, [equipment(2)])
    assert "Sample Rifle 2" in service.handle("武器", "AR-2").text
    assert "最近有效快照" not in service.handle("资料状态").text


def test_invalid_override_falls_back_to_bundle_and_later_uses_override(tmp_path):
    path, bundle = tmp_path / "new.json", tmp_path / "bundled.json"
    path.write_text("{}", encoding="utf8")
    write_snapshot(bundle)
    service = CatalogService(path)
    service.bundled_path = bundle
    assert "随附快照" in service.handle("武器", "AR-1").text
    write_snapshot(path, [equipment(2)])
    assert "Sample Rifle 2" in service.handle("武器", "AR-2").text


def test_missing_catalogue_and_empty_category_are_honest(tmp_path):
    service = CatalogService(tmp_path / "missing.json")
    service.bundled_path = tmp_path / "missing-bundle.json"
    assert "尚无可用" in service.handle("百科").text
    assert "尚无可用" in service.handle("武器").text
    service = service_for(tmp_path)
    assert "尚未收录" in service.handle("装饰").text
    assert "不表示整个 Wiki 已完整镜像" in service.handle("资料状态").text


@pytest.mark.parametrize("changes", [
    {"schema_version": True}, {"schema_version": 2}, {"entries": [{}]},
    {"entries": [equipment(), equipment()]}, {"synced_at": "2026-09-16"},
    {"source": {"name": "fake", "url": "javascript:alert(1)", "license": "CC BY-NC-SA 4.0"}},
    {"entries": [equipment(source_url="https://helldivers.wiki.gg.evil.invalid/wiki/Test")]},
    {"entries": [equipment(source_url="https://helldivers.wiki.gg@evil.invalid/wiki/Test")]},
    {"entries": [equipment(fields=["not a field pair"])]},
    {"entries": [equipment(category="unknown")]},
])
def test_invalid_snapshots_fail_closed_without_crashing(tmp_path, changes):
    service = service_for(tmp_path)
    write_snapshot(service.snapshot_path, **changes)
    assert "尚无可用" in service.handle("武器").text


def test_long_detail_card_keeps_fields_source_license_and_html_escaped(tmp_path):
    entry = equipment(summary='这是一段来自中文来源的装备说明，用于检查危险标记会正确转义：'
                              '<script>alert("bad")</script>',
                      fields=[[f"Field {number}", f"Value {number}"] for number in range(19)])
    service = service_for(tmp_path, [entry])
    reply = service.handle("武器", "AR-1")
    assert reply.card is not None
    assert "Field 18：待译：Value 18" in reply.text
    html = HtmlRenderer().render_html(reply.card)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "Sample_Rifle_1" in html
    assert "CC BY-NC-SA 4.0" in html
    assert "Value 18" in html


def test_subcategory_keywords_work_without_any_equipment_name(tmp_path):
    service = service_for(tmp_path, [equipment(), equipment(2, subcategory="Support Weapons")])
    assert "主武器 / 突击步枪" in service.handle("武器", "分类").text
    for query in ("突击步枪", "分类 突击步枪", "Assault Rifle", "主武器 / 突击步枪"):
        reply = service.handle("武器", query)
        assert "共 1 项" in reply.text
        assert "Sample Rifle 1" in reply.text
    assert "Sample Rifle 2" in service.handle("武器", "支援武器").text


def test_cross_category_duplicate_names_offer_choices_and_typo_is_not_auto_selected(tmp_path):
    other = equipment(2, id="stratagems:support:liberator", category="stratagems",
                      name="Liberator", english_name="Liberator", code="", aliases=[])
    first = equipment(name="Liberator", english_name="Liberator", aliases=[])
    service = service_for(tmp_path, [first, other])
    assert "共 2 项" in service.handle("百科", "Liberator").text
    assert "[战略配备]" in service.handle("百科", "Liberator").text
    assert "伤害：70" in service.handle("武器", "Liberator").text
    reply = service.handle("武器", "Liberatr")
    assert "候选" in reply.text and "共 1 项" in reply.text
    assert "伤害：70" not in reply.text


def test_search_candidates_are_not_truncated_at_search_page_limit(tmp_path):
    service = service_for(tmp_path, [equipment(index) for index in range(1, 62)])
    reply = service.handle("武器", "Sample")
    assert "共 61 项" in reply.text
    for _ in range(7):
        reply = service.handle("下一页")
    assert "第 8/8 页" in reply.text
    assert "1." in reply.text and "5." in reply.text and "6." not in reply.text


def test_valid_local_image_creates_card_without_remote_fetch(tmp_path, monkeypatch):
    service = service_for(tmp_path, [equipment(image_path="abc.png",
        image_url="https://helldivers.wiki.gg/images/test.png",
        image_credit="https://helldivers.wiki.gg/wiki/File:Test.png")])
    image_dir = tmp_path / "wiki_images"
    image_dir.mkdir()
    Image.new("RGB", (32, 24), "red").save(image_dir / "abc.png")
    monkeypatch.setattr(socket.socket, "connect", lambda *args: pytest.fail("network request"))
    reply = service.handle("武器", "AR-1")
    assert reply.card.image_data.startswith("data:image/png;base64,")
    assert reply.card.image_caption == ""
    assert service.entries[0].image_credit.endswith("File:Test.png")
    assert "data:image/png;base64," in HtmlRenderer().render_html(reply.card)


@pytest.mark.parametrize("image_path", ["../outside.png", "/tmp/outside.png",
                                         "C:\\outside.png", "subdir/picture.png", "evil.svg"])
def test_unsafe_image_paths_only_disable_image(tmp_path, image_path):
    service = service_for(tmp_path, [equipment(image_path=image_path)])
    reply = service.handle("武器", "AR-1")
    assert reply.card is None
    assert "伤害：70" in reply.text
    assert image_path not in reply.text


@pytest.mark.parametrize("mode", ["corrupt", "oversized-bytes", "oversized-pixels"])
def test_unusable_local_images_fall_back_without_losing_equipment(tmp_path, mode):
    service = service_for(tmp_path, [equipment(image_path="bad.png")])
    image_dir = tmp_path / "wiki_images"
    image_dir.mkdir()
    path = image_dir / "bad.png"
    if mode == "corrupt":
        path.write_bytes(b"<svg>not a png</svg>")
    elif mode == "oversized-bytes":
        path.write_bytes(b"x" * (5 * 1024 * 1024 + 1))
    else:
        Image.new("1", (5000, 5000)).save(path)
    reply = service.handle("武器", "AR-1")
    assert reply.card is None
    assert "伤害：70" in reply.text


def test_symlink_image_escape_is_not_read(tmp_path):
    service = service_for(tmp_path, [equipment(image_path="link.png")])
    image_dir = tmp_path / "wiki_images"
    image_dir.mkdir()
    outside = tmp_path / "outside.png"
    Image.new("RGB", (20, 20)).save(outside)
    try:
        (image_dir / "link.png").symlink_to(outside)
    except OSError:
        pytest.skip("Windows symlink privilege is unavailable")
    assert service.handle("武器", "AR-1").card is None


def test_snapshot_images_follow_last_good_source_directory(tmp_path):
    old_dir = tmp_path / "bundle"
    bundle = old_dir / "catalog.json"
    write_snapshot(bundle, [equipment(image_path="bundle.png")])
    (old_dir / "wiki_images").mkdir()
    Image.new("RGB", (10, 10)).save(old_dir / "wiki_images" / "bundle.png")
    service = CatalogService(tmp_path / "missing.json")
    service.bundled_path = bundle
    assert service.handle("武器", "AR-1").card.image_data.startswith("data:image/png")
    service.snapshot_path.write_text("bad update", encoding="utf8")
    assert service.handle("武器", "AR-1").card.image_data.startswith("data:image/png")


def test_empty_catalogue_is_rejected_and_never_replaces_last_good(tmp_path):
    service = service_for(tmp_path, [])
    assert "尚无可用" in service.handle("武器").text
    write_snapshot(service.snapshot_path)
    assert "伤害：70" in service.handle("武器", "AR-1").text
    write_snapshot(service.snapshot_path, [])
    reply = service.handle("武器", "AR-1")
    assert "伤害：70" in reply.text
    assert "最近有效快照" in reply.text


def test_constructor_rejects_unbounded_or_immediately_expiring_state(tmp_path):
    with pytest.raises(ValueError):
        CatalogService(tmp_path / "none.json", capacity=0)
    with pytest.raises(ValueError):
        CatalogService(tmp_path / "none.json", selection_ttl=0)


def test_directory_shows_first_chinese_alias_without_changing_source_name(tmp_path):
    entry = equipment(name="SG-225 Breaker", english_name="SG-225 Breaker", code="SG-225",
                      aliases=["SG-225", "Breaker", "破裂者", "喷子"])
    service = service_for(tmp_path, [entry])
    listed = service.handle("武器")
    assert "破裂者 · SG-225（SG-225 Breaker）" in listed.text
    assert " · 喷子" not in listed.text
    assert listed.card is None
    assert service.entries[0].name == "SG-225 Breaker"


def test_detail_translates_known_field_labels_but_preserves_values_and_unknown_labels(tmp_path):
    fields = [["Weapon Category", "Primary Weapons"], ["FiringModes", "Auto • Semi"],
              ["Fire Rate", "640 rpm"], ["Reload Time", "3s"],
              ["Unrecognized New Stat", "42 units"], ["Source", "Starter Equipment"]]
    entry = equipment(aliases=["AR-1", "解放者", "步枪"], fields=fields,
                      summary="Original English summary, unchanged.")
    reply = service_for(tmp_path, [entry]).handle("武器", "AR-1")
    assert "解放者 · AR-1" == reply.card.title
    assert "Sample Rifle 1" in reply.card.subtitle
    assert "中文名称采用本项目维护译名" not in reply.text
    assert "武器分类：主要武器" in reply.text
    assert "射击模式：全自动 • 半自动" in reply.text
    assert "射速：640 发/分钟" in reply.text
    assert "换弹时间：3秒" in reply.text
    assert "Unrecognized New Stat：待译：42 units" in reply.text
    assert "获取方式：初始装备" in reply.text
    rows = [row for section in reply.card.sections for row in section.rows]
    assert any(row.label == "射速" and row.value == "640 发/分钟" for row in rows)
    assert any(row.label == "Unrecognized New Stat" and row.value == "待译：42 units" for row in rows)
    assert "新说明待中文整理" in reply.text
    assert "Original English summary, unchanged." not in reply.text


def test_chinese_wiki_name_and_fields_take_priority_over_community_aliases(tmp_path):
    entry = equipment(name="解放者", english_name="AR-23 Liberator", code="AR-23",
                      aliases=["老别名", "AR-23"], subcategory="主武器 / 突击步枪",
                      summary="超级地球武装部队制式突击步枪。",
                      fields=[["武器类型", "突击步枪"], ["开火模式", "全自动 • 半自动 • 点射"],
                              ["伤害", "90 实弹"], ["容量", "45"], ["射速", "640 发/分钟"]],
                      source_url="https://helldivers.wiki.gg/zh/wiki/AR-23解放者")
    service = service_for(tmp_path, [entry])
    reply = service.handle("武器", "AR23")
    assert reply.card.title == "解放者 · AR-23"
    assert "老别名" not in reply.card.title
    assert "中文名称采用本项目维护译名" not in reply.text
    assert "AR-23 Liberator" in reply.card.subtitle
    assert "武器类型：突击步枪" in reply.text
    assert "开火模式：全自动 • 半自动 • 点射" in reply.text
    assert "伤害：90 实弹" in reply.text
    assert "射速：640 发/分钟" in reply.text
    assert "超级地球武装部队制式突击步枪。" in reply.text
    assert "/zh/wiki/" in reply.text
    listed = service.handle("武器")
    assert "解放者 · AR-23（AR-23 Liberator）" in listed.text
    assert "老别名" not in listed.text
    assert "主武器 / 突击步枪" in service.handle("武器", "分类").text


def test_mixed_chinese_name_and_field_sources_keep_separate_attribution(tmp_path):
    entry = equipment(name="机炮", english_name="AC-8 Autocannon", code="AC-8",
                      source_url="https://helldivers.wiki.gg/wiki/AC-8_Autocannon",
                      name_source_url="https://helldivers.wiki.gg/zh/wiki/武器", name_revision="4545",
                      chinese_detail_source={"url": "https://helldivers.wiki.gg/zh/wiki/AC-8_机炮",
                                             "revision": "5452", "fields": ["Standard Damage"],
                                             "summary": False},
                      fields=[["Standard Damage", "325 实弹"], ["Capacity", "10"],
                              ["Fire Rate", "190 rpm"], ["Reload Time", "4s"], ["Spare Rounds", "50"]])
    reply = service_for(tmp_path, [entry]).handle("武器", "AC8")
    for text in (reply.text, "\n".join(reply.card.footer)):
        assert "https://helldivers.wiki.gg/wiki/AC-8_Autocannon" in text
        assert "https://helldivers.wiki.gg/zh/wiki/武器" in text
        assert "https://helldivers.wiki.gg/zh/wiki/AC-8_机炮" in text
        assert "英文/译文 CC BY-NC-SA 4.0" in text
        assert "中文 CC BY-SA 4.0" in text
        assert "修订" not in text


def test_unlock_facts_are_first_and_diagnostics_stay_out_of_equipment_details(tmp_path):
    fields = [["Weapon Category", "Primary Weapons"], ["Capacity", "45"],
              ["Unlock Cost", "20 Medals"], ["Source", "Steeled Veterans P1"],
              ["战争债券费用", "1000 超级货币"], ["Wiki 提醒", "过时模板声明"]]
    service = service_for(tmp_path, [equipment(fields=fields)])
    reply = service.handle("武器", "AR-1")
    assert reply.card.sections[0].title == "获取与解锁"
    assert {(r.label, r.value) for r in reply.card.sections[0].rows} == {
        ("解锁费用", "20 奖章"), ("获取方式", "铁血老兵 第1页"), ("战争债券费用", "1000 超级货币"),
    }
    display = reply.text + "\n".join(r.value for s in reply.card.sections for r in s.rows)
    for removed in ("资料ID", "Wiki 修订", "过时模板声明", "检索别名", "本项目维护译名"):
        assert removed not in display
    assert "CC BY-NC-SA 4.0" in reply.text and "helldivers.wiki.gg" in reply.text
    assert ["Wiki 提醒", "过时模板声明"] in json.loads(
        service.snapshot_path.read_text(encoding="utf8"))["entries"][0]["fields"]


def test_weapon_detailed_statistics_and_attachments_are_accessible_without_overloading_overview(tmp_path):
    fields = [["Unlock Cost", "20 Medals"], ["Capacity", "45"],
              ["详参·弹体·伤害", "基础伤害：90 实弹\n耐久伤害：22 实弹"],
              ["配件·瞄具·反射瞄具", "武器等级：4\n费用：5,000 申购点"]]
    service = service_for(tmp_path, [equipment(fields=fields)])
    overview = service.handle("武器", "AR-1")
    assert "20 奖章" in overview.text and "耐久伤害" not in overview.text
    assert "武器 AR-1 详参" in overview.text and "武器 AR-1 配件" in overview.text
    advanced = service.handle("武器", "AR-1 详参")
    assert "耐久伤害：22 实弹" in advanced.text and "20 奖章" not in advanced.text
    assert "反射瞄具" in service.handle("武器", "AR-1 配件").text
    assert len(service.entries[0].fields) == 4


def test_fuzzy_selection_keeps_requested_details_view(tmp_path):
    entries = [equipment(i, aliases=["公共别名"], fields=[
        ["详参·本体", f"耐久伤害：{i * 22}"], ["配件·瞄具", f"所需等级：{i}"],
    ]) for i in (1, 2)]
    service = service_for(tmp_path, entries)
    assert "共 2 项" in service.handle("武器", "公共别名 详参").text
    reply = service.handle("选择", "2")
    assert "耐久伤害：44" in reply.text and "所需等级" not in reply.text
