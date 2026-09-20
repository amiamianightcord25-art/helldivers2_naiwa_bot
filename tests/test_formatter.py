from datetime import UTC, datetime, timedelta

import pytest

from hd2bot import formatter
from hd2bot.hd2.models import (
    Campaign,
    Faction,
    GlobalStatistics,
    MajorOrder,
    OrderTask,
    Planet,
    PlanetEvent,
    Reward,
    WarStatus,
)
from hd2bot.hd2.service import DataResult

NOW = datetime(2026, 9, 11, 0, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr(formatter, "utcnow", lambda: NOW)


def world(index=1, **kwargs):
    return Planet(index=index, name=f"测试星球{index}", **kwargs)


def defense(**kwargs):
    return PlanetEvent(event_type=1, faction=Faction.TERMINIDS, **kwargs)


def test_missing_data_remains_unknown_while_real_zero_is_displayed():
    reply = formatter.format_players(GlobalStatistics(players=0))
    assert "全服在线：0" in reply
    assert "终结族战线：暂无数据" in reply
    assert "累计阵亡：暂无数据" in reply
    assert "已配置测试账号" in reply and "请发送：战绩" in reply
    assert "活跃战区 / 战役：暂无数据" in formatter.format_war_status(WarStatus(), None, None)


def test_chinese_and_english_name_and_human_control_are_preserved():
    reply = formatter.format_planet(Planet(index=64, name="梅里迪亚", english_name="Meridia",
                                           faction=Faction.HUMANS, health=100, max_health=100))
    assert "梅里迪亚（Meridia）" in reply
    assert "超级地球已控制" in reply
    assert "解放进度 0" not in reply


def test_defense_progress_uses_event_hp_not_planet_hp_or_cached_wrong_progress():
    planet = world(faction=Faction.HUMANS, health=900, max_health=1000,
                   event=defense(health=3000, max_health=4000, progress=90,
                                 ends_at=NOW + timedelta(hours=1)))
    reply = formatter.format_planet(planet)
    assert "防守进度：25.00%" in reply
    assert "敌方事件生命：3,000 / 4,000" in reply
    assert "防守剩余：1小时0分" in reply
    planet.event.max_health = None
    assert "防守进度：暂无数据" in formatter.format_planet(planet)


def test_defense_filter_rejects_expired_other_events_and_disabled_planets():
    active = world(1, event=defense(ends_at=NOW + timedelta(minutes=15)))
    unknown_end = world(2, event=defense())
    expired = world(3, event=defense(ends_at=NOW))
    other = world(4, event=PlanetEvent(event_type=2, ends_at=NOW + timedelta(hours=1)))
    disabled = world(5, disabled=True, event=defense())
    reply = formatter.format_defenses([disabled, expired, other, unknown_end, active])
    assert "共 2 个" in reply
    assert "测试星球1" in reply and "测试星球2" in reply
    assert all(f"测试星球{index}" not in reply for index in (3, 4, 5))
    assert "剩余 暂无数据" in reply


def test_attacks_keep_all_rows_and_prioritize_order_players_then_progress():
    campaigns = [Campaign(index, world(index, faction=Faction.AUTOMATONS,
                                       players=index, liberation=index * 10))
                 for index in range(1, 9)]
    campaigns.extend([
        Campaign(10, world(10, faction=Faction.HUMANS)),
        Campaign(11, world(11, faction=Faction.UNKNOWN)),
        Campaign(12, world(12, faction=Faction.TERMINIDS, disabled=True)),
        Campaign(13, world(13, faction=Faction.AUTOMATONS, event=defense())),
    ])
    order = MajorOrder(1, tasks=[OrderTask(type=11, planet_index=1)])
    reply = formatter.format_campaigns(campaigns, [order])
    assert "共 8 个" in reply
    assert "1. [主线] 测试星球1" in reply
    assert "2. 测试星球8" in reply
    assert "8. 测试星球2" in reply
    assert all(f"测试星球{index}" not in reply for index in (10, 11, 12, 13))


def test_attack_tiebreak_and_expired_order_priority():
    campaigns = [Campaign(1, world(1, faction=Faction.TERMINIDS, players=10, liberation=20)),
                 Campaign(2, world(2, faction=Faction.TERMINIDS, players=10, liberation=60))]
    order = MajorOrder(1, tasks=[OrderTask(type=11, planet_index=1)], expires_at=NOW)
    reply = formatter.format_campaigns(campaigns, [order])
    assert "1. 测试星球2" in reply
    assert "[主线]" not in reply


def test_major_order_cleans_text_maps_planets_and_preserves_unknown_tasks(monkeypatch):
    monkeypatch.setattr(formatter, "metadata_for", lambda index: {"name": "目录星球"})
    order = MajorOrder(4, title="<i=1>解放行动</i>", briefing="<b>完成任务</b>",
                       tasks=[OrderTask(type=11, planet_index=64, progress=0, target=1),
                              OrderTask(type=11, planet_index=99, progress=None),
                              OrderTask(type=321, progress=17)],
                       rewards=[Reward(type=1, amount=45)],
                       expires_at=NOW + timedelta(days=1, hours=2, minutes=3))
    reply = formatter.format_major_order([order], [Planet(64, "梅里迪亚", english_name="Meridia")])
    assert "<i" not in reply and "<b>" not in reply
    assert "解放 梅里迪亚 | 进度 0 / 1" in reply
    assert "解放 目录星球 | 进度 暂无数据" in reply
    assert "任务类型 321 | 进度 17" in reply
    assert "剩余时间：1天2小时3分" in reply
    assert "奖励数量：45" in reply
    assert "勋章" not in reply
    assert "暂无当前主线" in formatter.format_major_order([])


def test_localization_keys_and_expired_order_are_readable():
    reply = formatter.format_major_order([MajorOrder(2, title="LOC_ORDER_TITLE", expires_at=NOW)])
    assert "文本尚未本地化" in reply
    assert "剩余时间：已结束" in reply
    assert "LOC_ORDER_TITLE" not in reply


def test_regeneration_is_explained_as_hp_recovery():
    reply = formatter.format_planet(world(faction=Faction.AUTOMATONS, max_health=1000000,
                                          regen_rate=5))
    assert "5.00 HP/秒" in reply
    assert "每小时恢复星球最大生命的 1.80%" in reply
    assert "净进度" not in reply and "预计解放" not in reply


def test_war_summary_shows_earth_defense_and_limits_only_summary_top_list():
    planets = [world(0, faction=Faction.HUMANS)]
    campaigns = [Campaign(index, world(index, faction=Faction.AUTOMATONS, players=index))
                 for index in range(1, 8)]
    reply = formatter.format_war_status(WarStatus(players=28, events=("<b>银河公告</b>",)),
                                        planets, campaigns)
    assert "超级地球：超级地球已控制" in reply
    assert "活跃战区：7 | 战役：7" in reply
    assert "测试星球7" in reply and "测试星球3" in reply
    assert "测试星球2" not in reply
    assert "银河公告" in reply and "<b>" not in reply


def test_finish_response_marks_stale_mock_and_actual_oldest_local_timestamp():
    old = NOW - timedelta(minutes=4)
    results = [DataResult(None, "captured", NOW), DataResult(None, "mock", old, stale=True)]
    reply = formatter.finish_response("测试正文", results)
    assert reply.startswith("数据暂时无法更新，以下为最近缓存")
    assert "【模拟数据 · 非实时战况】" in reply
    assert "官方战局 API / 本地模拟" in reply
    assert old.astimezone().isoformat(timespec="seconds") in reply
    assert "测试正文" in reply
