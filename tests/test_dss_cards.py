"""Vote pictures preserve exact counts while scaling only the decorative dots."""

import math
from collections import Counter
from datetime import UTC, datetime
from itertools import combinations

import pytest

from hd2bot.dss_cards import MAX_DOTS, dss_station_card, dss_votes_card
from hd2bot.hd2.models import (
    DSSElection,
    DSSVoteOption,
    SpaceStation,
    TacticalAction,
    TacticalCost,
)
from hd2bot.hd2.service import DataResult
from hd2bot.rendering.renderer import HtmlRenderer

NOW = datetime(2026, 9, 19, 12, 30, tzinfo=UTC)


def result(value, *, stale=False, source="captured"):
    return [DataResult(value, source, NOW, stale)]


def election(*counts):
    return DSSElection("hidden-election-uuid", options=[
        DSSVoteOption(index, count, f"候选星球{index}")
        for index, count in enumerate(counts)
    ])


@pytest.mark.parametrize("counts", [
    (5396, 648, 292, 203, 57, 45, 25, 20),
    (1,), (0,), (1, 1, 1), (121, 120, 120), (10**20, 1, 0),
    tuple(range(50)),
])
def test_dot_allocation_is_bounded_exact_and_proportional(counts):
    data = [election(*counts)]
    chart = dss_votes_card(data, result(data)).vote_charts[0]
    total = sum(counts)
    expected = min(total, MAX_DOTS)
    assert chart.total == total
    assert len(chart.dots) == expected
    assert sum(entry.dots for entry in chart.legend) == expected
    assert sum(entry.votes for entry in chart.legend) == total
    for entry in chart.legend:
        if total:
            numerator = entry.votes * expected
            low, remainder = divmod(numerator, total)
            assert entry.dots in {low, low + bool(remainder)}
        else:
            assert entry.dots == 0 and entry.percentage == "0.00%"
    assert Counter(dot.color for dot in chart.dots) == Counter({
        entry.color: entry.dots for entry in chart.legend if entry.dots
    })
    for dot in chart.dots:
        assert dot.radius <= 8
        assert dot.radius <= dot.x <= 900 - dot.radius
        assert dot.radius <= dot.y <= 455 - dot.radius
        assert math.isfinite(dot.x) and math.isfinite(dot.y)


def test_equal_votes_remainders_and_reordered_input_are_deterministic():
    data = election(*([1000] * 7))
    original = dss_votes_card([data], []).vote_charts[0]
    assert [item.dots for item in original.legend] == [52, 52, 52, 51, 51, 51, 51]
    data.options.reverse()
    reordered = dss_votes_card([data], []).vote_charts[0]
    assert original == reordered


def test_planet_colors_survive_vote_order_changes_and_tiny_candidates_stay_in_legend():
    before = dss_votes_card([election(9999999, 1, 0)], []).vote_charts[0]
    after = dss_votes_card([election(1, 9999999, 0)], []).vote_charts[0]
    assert len(before.legend) == len(after.legend) == 3
    assert before.legend[1].dots == 0 and before.legend[1].votes == 1
    assert {item.planet_id: item.color for item in before.legend} == {
        item.planet_id: item.color for item in after.legend
    }
    assert "按票数比例缩放" in before.note
    assert "1 票" in HtmlRenderer().render_html(dss_votes_card([election(9999999, 1, 0)], []))


@pytest.mark.parametrize("ids", [
    (158, 261, 268, 140, 269, 76, 217, 79),  # The live eight-candidate election.
    tuple(range(12)),
])
def test_candidate_palette_has_no_similar_color_collision_and_matches_dots(ids):
    data = DSSElection("current", options=[
        DSSVoteOption(planet_id, (len(ids) - rank) * 100)
        for rank, planet_id in enumerate(ids)
    ])
    chart = dss_votes_card([data], []).vote_charts[0]
    colors = {entry.planet_id: entry.color for entry in chart.legend}
    assert len(set(colors.values())) == len(ids)
    rgb = [tuple(int(color[i:i + 2], 16) for i in (1, 3, 5)) for color in colors.values()]
    # Detect the former nearly identical hues as well as exact color collisions.
    assert all(math.dist(left, right) >= 60 for left, right in combinations(rgb, 2))
    assert Counter(dot.color for dot in chart.dots) == Counter({
        entry.color: entry.dots for entry in chart.legend
    })
    changed = DSSElection("current", options=[
        DSSVoteOption(option.meta_id, 1500 - option.count) for option in reversed(data.options)
    ])
    updated = dss_votes_card([changed], []).vote_charts[0]
    assert colors == {entry.planet_id: entry.color for entry in updated.legend}


def test_invalid_counts_and_ids_are_excluded_and_explained():
    data = DSSElection("not-shown", options=[
        DSSVoteOption(0, 20, "有效星球"), DSSVoteOption(1, -1),
        DSSVoteOption(2, True), DSSVoteOption(3, 1.5), DSSVoteOption(-1, 8),
        DSSVoteOption(True, 5), DSSVoteOption("7", 9), DSSVoteOption(5, float("nan")),
    ])
    card = dss_votes_card([data], result([data]))
    assert card.vote_charts[0].total == 20
    assert [entry.planet_id for entry in card.vote_charts[0].legend] == [0]
    assert any("仅统计有效数据" in note for note in card.notices)


def test_empty_unavailable_and_zero_votes_have_distinct_honest_states():
    empty = dss_votes_card([], []).vote_charts[0]
    unavailable = dss_votes_card([election()], []).vote_charts[0]
    zero = dss_votes_card([election(0, 0)], []).vote_charts[0]
    assert "暂无公开" in empty.empty_message and "暂无公开" in unavailable.empty_message
    assert "暂无票数" in zero.empty_message
    assert len(zero.legend) == 2 and not zero.dots
    html = HtmlRenderer().render_html(dss_votes_card([election(0, 0)], []))
    assert html.count('class="vote-entry"') == 2
    assert "0.00%" in html and "0 票" in html
    assert '<circle ' not in html


def test_multiple_elections_get_separate_totals_without_technical_ids():
    data = [election(10, 20), election(60, 40)]
    card = dss_votes_card(data, result(data))
    assert [chart.total for chart in card.vote_charts] == [30, 100]
    assert [chart.title for chart in card.vote_charts] == ["迁移投票 · 第 1 组", "迁移投票 · 第 2 组"]
    html = HtmlRenderer().render_html(card)
    assert html.count('<svg ') == 2
    assert "hidden-election-uuid" not in html and "ElectionV2" not in html


def test_render_contains_complete_self_contained_semicircle_and_vote_legend():
    data = [election(5396, 648, 292, 203, 57, 45, 25, 20)]
    html = HtmlRenderer().render_html(dss_votes_card(data, result(data)))
    assert html.count('<circle ') == 360
    assert html.count('class="vote-entry"') == 8
    assert 'viewBox="0 0 900 455"' in html
    assert "6,686" in html and "5,396 票" in html and "80.71%" in html
    assert "点阵按票数比例缩放" in html
    assert 'src="https://' not in html and 'href="https://' not in html
    assert "来源：官方战局 API" in html and "2026-09-19" in html


def test_stale_and_mock_metadata_are_kept_above_chart():
    data = [election(1, 2)]
    card = dss_votes_card(data, result(data, stale=True, source="mock"))
    assert any("最近缓存" in notice for notice in card.notices)
    assert any("模拟" in notice for notice in card.notices)
    html = HtmlRenderer().render_html(card)
    assert html.index("最近缓存") < html.index('<section class="vote-panel"')


def test_station_preserves_vote_status_deadline_and_complete_action_sections():
    station = SpaceStation(749875195, planet_index=0, flags=1, election_ends_at=NOW,
                           election=election(100, 50), tactical_actions=[
        TacticalAction(1, "飞鹰风暴", 1, NOW, strategic_description="清除星球上的敌人。",
                       costs=[TacticalCost(3992382197, 500, 1000, 75, 86400)]),
        TacticalAction(2, "轨道轰炸", 3, NOW, description="轨道炮火支援。"),
    ])
    card = dss_station_card([station], result([station]))
    assert len(card.vote_charts) == 1
    assert card.vote_charts[0].total == 150
    assert "超级地球" in card.vote_charts[0].subtitle
    assert "2026-09-19 20:30 UTC+8" in card.vote_charts[0].subtitle
    assert card.columns == 1 and all(section.full_width for section in card.sections)
    assert [section.title for section in card.sections] == ["飞鹰风暴", "轨道轰炸"]
    assert any(row.progress == 50 for row in card.sections[0].rows)
    html = HtmlRenderer().render_html(card)
    for value in ("运行中", "准备中", "冷却中", "普通样本筹备", "50.00%"):
        assert value in html
    assert "75 / 86,400 秒" in card.sections[0].rows[-1].detail
    assert html.index('<section class="vote-panel"') < html.index('飞鹰风暴')


def test_station_missing_votes_and_no_stations_are_renderable():
    card = dss_station_card([SpaceStation(749875195, flags=0)], [])
    assert not card.vote_charts[0].dots
    html = HtmlRenderer().render_html(card)
    assert "当前不可用" in html and "暂无公开迁移投票" in html and "暂无公开战术行动" in html
    empty = HtmlRenderer().render_html(dss_station_card([], []))
    assert "当前公开数据中没有空间站" in empty


def test_labels_are_escaped_and_long_names_are_retained():
    name = "名字很长的候选星球 & 引号\" <陌生文本>"
    data = [DSSElection("uuid", options=[DSSVoteOption(1, 10, name)])]
    html = HtmlRenderer().render_html(dss_votes_card(data, []))
    assert "名字很长的候选星球 &amp; 引号&#34; &lt;陌生文本&gt;" in html
    assert '<陌生文本>' not in html
