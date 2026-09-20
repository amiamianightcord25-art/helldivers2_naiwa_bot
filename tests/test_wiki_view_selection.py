import pytest
from test_wiki_service import equipment, service_for

from hd2bot.presentation import ChatContext
from hd2bot.wiki.models import CatalogEntry
from hd2bot.wiki.search import search_catalog


@pytest.mark.parametrize(
    "command,category",
    [
        ("武器", "weapons"),
        ("战略配备", "stratagems"),
        ("盔甲", "armor"),
        ("强化资源", "boosters"),
        ("装饰", "cosmetics"),
        ("百科", "weapons"),
    ],
)
@pytest.mark.parametrize(
    "suffix,view",
    [
        ("详参", "详参"),
        ("详细参数", "详参"),
        ("詳參", "详参"),
        ("详參", "详参"),
        ("詳細参数", "详参"),
        ("配件", "配件"),
    ],
)
@pytest.mark.parametrize("separator", [" ", ""])
def test_ambiguous_names_with_views_across_categories(
    tmp_path, command, category, suffix, view, separator
):
    entries = [
        equipment(
            i,
            category=category,
            aliases=["共同别名"],
            fields=[["详参·本体", f"参数唯一值{i}"], ["配件·瞄具", f"配件唯一值{i}"]],
        )
        for i in (1, 2)
    ]
    service = service_for(tmp_path, entries)
    context = ChatContext("group", "same-group", "same-author")
    found = service.handle(command, "共同别名" + separator + suffix, context=context)
    assert "共 2 项" in found.text and view in found.text
    chosen = service.handle("选择", "2", context=context)
    expected = "参数唯一值2" if view == "详参" else "配件唯一值2"
    excluded = "配件唯一值2" if view == "详参" else "参数唯一值2"
    assert expected in chosen.text and excluded not in chosen.text


@pytest.mark.parametrize("argument", ["列表 详参", "详参", "列表2详参", "列表 2 詳細參數"])
def test_page_and_selection_keep_view(tmp_path, argument):
    entries = [
        equipment(i, fields=[["详参·本体", f"参数{i}"], ["配件·瞄具", f"配件{i}"]])
        for i in range(1, 19)
    ]
    service = service_for(tmp_path, entries)
    listed = service.handle("武器", argument)
    assert "详参" in listed.text and "共 18 项" in listed.text
    if "2" not in argument:
        assert "详参" in service.handle("下一页").text
    selected = service.handle("选择", "1")
    assert "参数" in selected.text and "配件" not in selected.text


@pytest.mark.parametrize("view", ["详参", "配件"])
def test_nested_category_view_and_context_isolation(tmp_path, view):
    entries = [
        equipment(i, aliases=["重名"], fields=[["详参·本体", "参数"], ["配件·瞄具", "配件详情"]])
        for i in (1, 2)
    ]
    service = service_for(tmp_path, entries)
    a, b = ChatContext("group", "g", "a"), ChatContext("group", "g", "b")
    assert "共 2 项" in service.handle("百科", "武器 重名 " + view, context=a).text
    assert "没有可用的候选" in service.handle("选择", "1", context=b).text
    result = service.handle("选择", "1", context=a)
    assert ("参数" if view == "详参" else "配件详情") in result.text


def test_family_name_and_base_model_show_variants():
    records = [
        CatalogEntry("base", "weapons", "解放者", "AR-23 Liberator", "AR-23"),
        CatalogEntry("variant", "weapons", "解放者穿透型", "AR-23P Liberator Penetrator", "AR-23P"),
    ]
    family = search_catalog("解放者", records, expand_families=True)
    assert family.match is None and family.matches_total == 2
    assert search_catalog("AR23", records, expand_families=True).matches_total == 2
    assert search_catalog("AR23", records, expand_families=True).match is None
    assert search_catalog("AR23P", records, expand_families=True).match.id == "variant"


def test_ambiguous_family_all_pages_are_reachable(tmp_path):
    entries = [
        equipment(
            i,
            name="测试枪" if i == 1 else f"测试枪型号{i}",
            aliases=[],
            fields=[["详参·本体", f"参数{i}"]],
        )
        for i in range(1, 61)
    ]
    service = service_for(tmp_path, entries)
    assert "共 60 项" in service.handle("武器", "测试枪详参").text
    for _ in range(7):
        service.handle("下一页")
    assert "参数" in service.handle("选择", "4").text


@pytest.mark.parametrize("query", ["ar23", "AR-23", "ＡＲ－２３", "a r — 2 3"])
@pytest.mark.parametrize("suffix", ["", " 详参", "配件", " 詳細參數"])
def test_ar23_family_choice_preserves_requested_view(tmp_path, query, suffix):
    records = [equipment(i, name="Series variant " + code, english_name="Series variant " + code,
                         code=code, aliases=[], fields=[["详参·本体", "参数" + code], ["配件·瞄具", "配件" + code]])
               for i, code in enumerate(("AR-23", "AR-23A", "AR-23C", "AR-23P", "AR-230"), 1)]
    service = service_for(tmp_path, records)
    menu = service.handle("武器", query + suffix)
    assert "共 4 项" in menu.text and "AR-230" not in menu.text
    for code in ("AR-23A", "AR-23C", "AR-23P"):
        assert code in menu.text
    chosen = service.handle("选择", "4")
    assert "AR-23P" in chosen.text
    if "配件" in suffix:
        assert "配件AR-23P" in chosen.text and "参数AR-23P" not in chosen.text
    elif suffix:
        assert "参数AR-23P" in chosen.text and "配件AR-23P" not in chosen.text


def test_concrete_variant_and_full_name_still_resolve_directly(tmp_path):
    records = [equipment(1, code="AR-23", name="AR-23 基础型", aliases=[]),
               equipment(2, code="AR-23P", name="AR-23P 穿透型", aliases=[])]
    service = service_for(tmp_path, records)
    assert "共 2 项" in service.handle("武器", "AR23").text
    assert "共 2 项" not in service.handle("武器", "AR23P").text
    assert "共 2 项" not in service.handle("武器", "AR-23 基础型").text
