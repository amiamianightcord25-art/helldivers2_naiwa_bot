"""Structured reports preserve the data-to-row binding and complete prose."""

from datetime import UTC, datetime

from hd2bot.feature_cards import episode_card, region_card
from hd2bot.hd2.models import (
    Episode,
    EpisodePhase,
    EpisodeReward,
    Faction,
    PlanetRegion,
)
from hd2bot.hd2.service import DataResult
from hd2bot.rendering.renderer import HtmlRenderer

NOW = datetime(2026, 9, 16, 9, 30, tzinfo=UTC)


def results(value, *, stale=False, source="captured"):
    return [DataResult(value, source, NOW, stale)]


def test_all_41_region_records_keep_their_values_in_one_row_without_truncation():
    regions = [PlanetRegion(1000 + index // 4, index % 4,
                            Faction.TERMINIDS, health=index, max_health=100,
                            players=10000 + index, available=index % 2 == 0,
                            damage_multiplier=index + 0.5) for index in range(41)]
    card = region_card(regions, results(regions))
    rows = [row for section in card.sections for row in section.rows]
    assert len(rows) == 41
    assert all(len(section.rows) <= 12 for section in card.sections)
    for index, row in enumerate(rows):
        assert f"星球 #{1000 + index // 4}" in row.label
        assert f"区域 #{index % 4}" in row.label
        assert row.value == f"{10000 + index:,} 人"
        assert f"{100 - index:.2f}%" in row.detail
        assert f"区域伤害倍率 {index + 0.5}" in row.detail
    html = HtmlRenderer().render_html(card)
    assert html.count('class="row"') == 41
    assert "10,040 人" in html


def test_region_filter_missing_values_and_human_control_are_not_fabricated():
    regions = [PlanetRegion(1000, 1, Faction.HUMANS, players=0),
               PlanetRegion(1000, 2, Faction.UNKNOWN),
               PlanetRegion(1001, 1, Faction.TERMINIDS)]
    card = region_card(regions, results(regions), 1000)
    rows = [row for section in card.sections for row in section.rows]
    assert len(rows) == 2
    assert rows[0].value == "0 人"
    assert "超级地球已控制" in rows[0].detail
    assert "可用性暂无数据" in rows[0].detail
    assert rows[1].value == "暂无数据"
    assert "未知阵营" in rows[1].detail
    assert "解放进度" not in rows[1].detail
    assert region_card(regions, results(regions), 999) is None
    assert region_card([], results([])) is None


def sample_episodes():
    return [Episode(1, "PREVIOUS CAMPAIGN", status=2),
            Episode(2, "CONTAINMENT", "Briefing paragraph.",
                    intro_message="Separate opening.", outro_message="Overall ending.",
                    status=0, faction=Faction.AUTOMATONS, starts_at=NOW,
                    phases=[EpisodePhase(21, 2, "FIRST PHASE", "Past objective.",
                                         "CONCLUDED", "Past result.", [EpisodeReward(897894480, 5)]),
                            EpisodePhase(22, 0, "CURRENT OBJECTIVE", "Current task.\nNext line.")],
                    rewards=[EpisodeReward(897894480, 10)])]


def test_episode_overview_keeps_directory_and_current_briefing_separate():
    episodes = sample_episodes()
    card = episode_card(episodes, results(episodes))
    directory, current = card.sections
    assert directory.title == "战役目录"
    assert len(directory.rows) == 2
    assert directory.rows[0].label == "#2 CONTAINMENT"
    assert current.title == "当前战役 · #2"
    text = "\n".join(f"{row.label}\n{row.value}" for row in current.rows)
    for expected in ("CONTAINMENT", "Briefing paragraph.", "Separate opening.",
                     "Overall ending.", "Current task.\nNext line.", "阶段 1", "奖章 × 10",
                     "2026-09-16 17:30 UTC+8"):
        assert expected in text
    assert "Past objective." not in text  # Same default-view semantics as the text formatter.
    assert "文本尚未本地化" not in text


def test_episode_detail_keeps_all_phase_paragraphs_rewards_and_markup_escaped():
    episodes = sample_episodes()
    episodes[1].phases[0].intro_message = "A complete paragraph.\n" * 100 + "LAST LINE"
    episodes[1].description = '<img src="https://invalid.example">Actual prose.'
    card = episode_card(episodes, results(episodes), 2)
    assert len(card.sections) == 1
    assert card.sections[0].title == "战役详情 · #2"
    text = "\n".join(f"{row.label}\n{row.value}" for row in card.sections[0].rows)
    assert text.count("A complete paragraph.") == 100
    assert "LAST LINE" in text and "Past result." in text
    assert "阶段奖励：奖章 × 5" in text
    html = HtmlRenderer().render_html(card)
    assert '<img src="https://invalid.example">' not in html
    assert "invalid.example" not in html
    assert "Actual prose." in html
    assert "CONTAINMENT" in html
    assert "LAST LINE" in html


def test_episode_recent_fallback_empty_and_no_match():
    episodes = sample_episodes()
    episodes[1].status = 3
    card = episode_card(episodes, results(episodes))
    assert card.sections[1].title == "最近战役 · #2"
    assert episode_card(episodes, results(episodes), 99) is None
    assert episode_card([], results([])) is None


def test_cards_keep_stale_mock_notices_and_original_source_time():
    regions = [PlanetRegion(1000, 1)]
    for card in (region_card(regions, results(regions, stale=True, source="mock")),
                 episode_card(sample_episodes(), results([], stale=True, source="mock"))):
        assert any("模拟" in line for line in card.notices)
        assert any("最近缓存" in line for line in card.notices)
        assert any("2026-09-16" in line for line in card.footer)
