from dataclasses import FrozenInstanceError

import pytest

from hd2bot.wiki import CatalogEntry, normalize_catalog_name, search_catalog


@pytest.fixture
def entries():
    return [
        CatalogEntry("liberator", "weapons", "解放者", "AR-23 Liberator", "AR-23",
                     aliases=("解放", "初始步枪")),
        CatalogEntry("penetrator", "weapons", "解放者穿透型", "AR-23P Liberator Penetrator",
                     "AR-23P", aliases=("穿甲解放者",)),
        CatalogEntry("punisher", "weapons", "惩罚者", "SG-8 Punisher", "SG-8",
                     aliases=("喷子", "泵动霰弹枪")),
        CatalogEntry("slugger", "weapons", "重炮手", "SG-8S Slugger", "SG-8S", aliases=("喷子",)),
        CatalogEntry("amr", "stratagems", "反器材步枪", "APW-1 Anti-Materiel Rifle", "APW-1"),
        CatalogEntry("hero", "armor", "联邦英雄", "DP-40 Hero of the Federation", "DP-40"),
        CatalogEntry("vitality", "boosters", "活力强化", "Vitality Enhancement"),
        CatalogEntry("honor", "cosmetics", "荣誉披风", "Honor Cape"),
    ]


def test_catalog_entry_is_immutable(entries):
    with pytest.raises(FrozenInstanceError):
        entries[0].name = "changed"


@pytest.mark.parametrize("query", [
    "AR-23", "ar23", "ＡＲ－２３", "a r — 2 3", "ar.23", "Liberator", "LIBERATOR",
    "ＡＲ－２３ Ｌｉｂｅｒａｔｏｒ", "解放者", "解放", "初始步枪", "初始步槍",
])
def test_exact_name_code_alias_and_unicode_width(query, entries):
    result = search_catalog(query, entries)
    assert result.match is entries[0]
    assert result.exact is True
    assert result.matches_total == 1
    assert result.candidates == []


@pytest.mark.parametrize("query", ["惩罚者", "懲罰者", "泵動霰彈槍"])
def test_common_traditional_characters(query, entries):
    assert search_catalog(query, entries).match is entries[2]


def test_unique_meaningful_fragment_is_selected(entries):
    result = search_catalog("穿透", entries)
    assert result.match is entries[1]
    assert result.exact is False


def test_shared_alias_requires_selection(entries):
    result = search_catalog("喷子", entries)
    assert result.match is None
    assert {entry.id for entry in result.candidates} == {"punisher", "slugger"}
    assert result.exact is True


def test_shared_names_across_categories_require_selection(entries):
    cape = CatalogEntry("hero-cape", "cosmetics", "联邦英雄", "Hero of the Federation")
    result = search_catalog("联邦英雄", entries + [cape])
    assert result.match is None
    assert {entry.id for entry in result.candidates} == {"hero", "hero-cape"}
    assert search_catalog("联邦英雄", entries + [cape], "armor").match is entries[5]


def test_category_filter_also_applies_to_aliases_and_suggestions(entries):
    assert search_catalog("喷子", entries, "armor").matches_total == 0
    assert search_catalog("Liberatr", entries, "armor").candidates == []


def test_exact_code_wins_over_other_equipment_alias(entries):
    misleading_alias = CatalogEntry("different", "armor", "Other", aliases=("ar23",))
    assert search_catalog("ar23", entries + [misleading_alias]).match is entries[0]


@pytest.mark.parametrize("query", ["AR23P", "ar-23p"])
def test_derivative_code_is_exact_and_independent(query, entries):
    assert search_catalog(query, entries).match is entries[1]


def test_missing_base_model_does_not_automatically_select_derivative(entries):
    result = search_catalog("AR23", [entries[1]])
    assert result.match is None
    assert result.candidates == [entries[1]]
    assert result.exact is False


def test_partial_model_code_needs_choice_even_for_one_match(entries):
    result = search_catalog("APW", entries)
    assert result.match is entries[4]
    incomplete = search_catalog("APW1A", entries)
    assert incomplete.match is None
    assert incomplete.candidates == []
    result = search_catalog("AR2", [entries[0]])
    assert result.match is None
    assert result.candidates == [entries[0]]


@pytest.mark.parametrize("query", ["AR32", "SG9", "23", "２３", "900", "x99"])
def test_wrong_model_number_does_not_guess(query, entries):
    result = search_catalog(query, entries)
    assert result.match is None
    assert result.candidates == []


def test_acronym_suggestions_require_selection(entries):
    result = search_catalog("AMR", entries)
    assert result.match is None
    assert result.candidates == [entries[4]]
    assert result.exact is False


def test_curated_acronym_alias_can_select_directly():
    entry = CatalogEntry("amr", "stratagems", "反器材步枪", aliases=("AMR",))
    assert search_catalog("amr", [entry]).match is entry


def test_short_latin_partial_is_only_a_suggestion(entries):
    result = search_catalog("pu", entries)
    assert result.match is None
    assert result.candidates == [entries[2]]


@pytest.mark.parametrize("query", ["Liberatr", "Liberaotr", "Punihser"])
def test_typo_requires_selection_even_when_only_one_suggestion(query, entries):
    result = search_catalog(query, entries)
    assert result.match is None
    assert result.candidates
    assert result.candidates[0].id == ("punisher" if query == "Punihser" else "liberator")
    assert result.exact is False


@pytest.mark.parametrize("query", ["", " ", "!!!", "a", "枪", "甲", "天气", "hello", "xx", None])
def test_empty_short_or_unrelated_query_has_no_loose_matches(query, entries):
    result = search_catalog(query, entries)
    assert result.match is None
    assert result.candidates == []
    assert result.matches_total == 0


def test_single_character_explicit_alias_is_supported():
    entry = CatalogEntry("commando", "stratagems", "突击兵", aliases=("筒",))
    assert search_catalog("筒", [entry]).match is entry


def test_deduplication_preserves_first_record_identity(entries):
    duplicate = CatalogEntry("liberator", "weapons", "解放者", code="AR-23")
    result = search_catalog("解放者", [entries[0], duplicate])
    assert result.match is entries[0]
    assert result.matches_total == 1


def test_candidates_are_ranked_stably_and_paginated():
    records = [CatalogEntry(str(i), "weapons", f"共同名称{i:02d}") for i in range(25)]
    first = search_catalog("共同", list(reversed(records)), limit=8)
    second = search_catalog("共同", records, limit=8, offset=8)
    assert first.matches_total == second.matches_total == 25
    assert first.candidates == records[:8]
    assert second.candidates == records[8:16]
    assert search_catalog("共同", records, offset=100).candidates == []


def test_page_size_is_bounded():
    records = [CatalogEntry(str(i), "armor", f"共同名称{i:02d}") for i in range(75)]
    result = search_catalog("共同", records, limit=10000)
    assert result.matches_total == 75
    assert len(result.candidates) == 50


def test_partial_prefers_shorter_more_specific_name():
    records = [
        CatalogEntry("long", "armor", "超级荣誉装甲"),
        CatalogEntry("short", "armor", "荣誉装甲"),
    ]
    assert search_catalog("荣誉", records).candidates == list(reversed(records))


def test_duplicate_exact_names_and_ids_have_stable_order():
    records = [CatalogEntry("b", "weapons", "喷子"), CatalogEntry("a", "weapons", "喷子")]
    assert [entry.id for entry in search_catalog("喷子", records).candidates] == ["a", "b"]


def test_normalization_preserves_meaningful_letters_and_digits():
    assert normalize_catalog_name("  ＡＲ—２３ · 解放者  ") == "ar23解放者"
