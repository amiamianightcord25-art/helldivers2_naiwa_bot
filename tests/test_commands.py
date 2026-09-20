import pytest

from hd2bot.commands import Command, parse_command, search_planets
from hd2bot.hd2.errors import CommandError
from hd2bot.hd2.models import Planet


@pytest.mark.parametrize("name", ["战况", "主线", "进攻", "防守", "玩家", "帮助"])
def test_plain_commands(name):
    assert parse_command(name) == Command(name)


@pytest.mark.parametrize("text", [
    "  战况  ", "/战况", "／战况", "／  战况", "<@123> 战况",
    "<@!123>/战况", "<@123><@!456> /战况", "@机器人 战况", "＠机器人／战况",
    "@机器人战况", "/<@123>战况", "\u3000<@123>\u3000／\u3000战况\u3000",
])
def test_prefixes_and_unicode_whitespace(text):
    assert parse_command(text) == Command("战况")


def test_planet_preserves_english_case_and_collapses_extra_spaces():
    assert parse_command("<@!123> ／星球  Angel's   Venture  ") == Command(
        "星球", "Angel's Venture",
    )
    assert parse_command("星球 MeRiDiA").argument == "MeRiDiA"
    assert parse_command("星球 ６４").argument == "64"


@pytest.mark.parametrize("text", ["", " \t\n", "/", "<@123>", "@机器人 /", None])
def test_empty_input_has_friendly_error(text):
    with pytest.raises(CommandError, match="请输入"):
        parse_command(text)


@pytest.mark.parametrize("text", ["unknown", "天气", "//战况", "<@wrong> 战况", "星球Meridia"])
def test_unknown_commands_are_rejected(text):
    with pytest.raises(CommandError, match="未识别"):
        parse_command(text)


@pytest.mark.parametrize("text", ["星球", "/星球   ", "<@123> 星球\t"])
def test_planet_requires_an_argument(text):
    with pytest.raises(CommandError, match="星球名称或编号"):
        parse_command(text)


@pytest.mark.parametrize("text", ["战况 now", "主线 1", "进攻 星球", "防守 now", "玩家 2", "帮助 all"])
def test_other_commands_reject_extra_arguments(text):
    with pytest.raises(CommandError, match="不需要额外参数"):
        parse_command(text)


def test_mentions_inside_query_are_not_removed():
    assert parse_command("星球 Example <@123>").argument == "Example <@123>"


@pytest.fixture
def planets():
    return [
        Planet(index=64, name="梅里迪亚", english_name="Meridia", aliases=("梅里迪安",)),
        Planet(index=127, name="天使投资", english_name="Angel's Venture"),
        Planet(index=9991, name="阿尔法一号", english_name="Alpha One", aliases=("共享别名",)),
        Planet(index=9992, name="阿尔法二号", english_name="Alpha Two", aliases=("共享别名",)),
    ]


@pytest.mark.parametrize("query", ["Meridia", "meridia", "MERIDIA", "Ｍｅｒｉｄｉａ",
                                  "梅里迪亚", "梅里迪安", "64", "#64", "６４", "00064"])
def test_exact_english_chinese_alias_and_index_queries(query, planets):
    result = search_planets(query, planets)
    assert result.match is planets[0]
    assert result.candidates == []


@pytest.mark.parametrize("query", ["天使之愿", "Angel’s Venture", "angelsventure", "Angel's Venture"])
def test_catalog_aliases_and_punctuation_normalization(query, planets):
    assert search_planets(query, planets).match is planets[1]


def test_unique_substring_selects_the_only_match(planets):
    assert search_planets("Merid", planets).match is planets[0]


@pytest.mark.parametrize("query", ["Alpha", "阿尔法", "共享别名"])
def test_ambiguous_query_returns_candidates_without_selecting(query, planets):
    result = search_planets(query, planets)
    assert result.match is None
    assert {p.index for p in result.candidates} == {9991, 9992}


def test_typo_is_a_suggestion_even_if_it_has_only_one_candidate(planets):
    result = search_planets("Merida", planets)
    assert result.match is None
    assert planets[0] in result.candidates


@pytest.mark.parametrize("query", ["阿尔法", "Alphx"])
def test_suggestions_are_bounded(query):
    planets = [Planet(index=10000 + i, name=f"阿尔法{i}", english_name=f"Alpha {i}") for i in range(20)]
    result = search_planets(query, planets)
    assert result.match is None
    assert len(result.candidates) == 5


def test_numeric_lookup_does_not_fuzzily_match_other_indices(planets):
    result = search_planets("65", planets)
    assert result.match is None
    assert result.candidates == []


def test_totally_unrelated_query_and_empty_planet_list(planets):
    for result in (search_planets("完全不存在的星球", planets), search_planets("Meridia", [])):
        assert result.match is None
        assert result.candidates == []


@pytest.mark.parametrize("query", ["", "  ", "---", None])
def test_empty_search_is_not_a_match_for_every_planet(query, planets):
    with pytest.raises(CommandError, match="星球名称或编号"):
        search_planets(query, planets)


def test_provider_alias_takes_priority_over_catalog_alias(monkeypatch):
    planets = [Planet(9000, "本地星球", aliases=("独特名字",)), Planet(9001, "另一星球")]
    monkeypatch.setattr("hd2bot.commands.search.metadata_for", lambda index: {
        "aliases": ["独特名字"] if index == 9001 else [],
    })
    assert search_planets("独特名字", planets).match is planets[0]


def test_mismatched_catalog_identity_does_not_pollute_mock_names(monkeypatch):
    planet = Planet(8, "模拟天使", english_name="Angel's Venture", aliases=("天使之愿",))
    monkeypatch.setattr("hd2bot.commands.search.metadata_for", lambda index: {
        "english_name": "DARROWSPORT", "aliases": ["别的星球"],
    })
    assert search_planets("天使之愿", [planet]).match is planet
    assert search_planets("别的星球", [planet]).match is None


def test_duplicate_planet_id_is_not_ambiguous(planets):
    assert search_planets("Meridia", [planets[0], planets[0]]).match is planets[0]
