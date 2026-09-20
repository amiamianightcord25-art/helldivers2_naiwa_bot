from unittest.mock import AsyncMock

import pytest

from hd2bot.commands import parse_command
from hd2bot.config import Settings
from hd2bot.hd2.errors import CommandError
from hd2bot.hd2.models import Campaign, Faction, Planet, PlanetEvent
from hd2bot.hd2.providers.fallback import FallbackProvider
from hd2bot.hd2.providers.mock import MockProvider
from hd2bot.hd2.service import HD2Service
from hd2bot.router import CommandRouter
from hd2bot.warfront import format_front, parse_front


@pytest.mark.parametrize("text,expected", [
    ("/map", "星图"), ("/control_centre", "控制中心"), ("/dss", "DSS"),
    ("/global_events", "公告"), ("/warfront Terminids", "战线"),
    ("/steam", "更新"), ("/subfaction", "特殊部队"),
    ("/dispatches", "新闻"), ("/major_order", "主线"),
])
def test_gww_command_names_are_available_as_aliases(text, expected):
    assert parse_command(text).name == expected


def test_defenses_are_grouped_by_attacker_not_human_owner_and_unknown_players_stay_unknown():
    defending = Planet(1, "守卫星", faction=Faction.HUMANS, event=PlanetEvent(
        id=1, event_type=1, faction=Faction.TERMINIDS), players=None)
    attacking = Planet(2, "虫星", faction=Faction.TERMINIDS, players=12)
    robot = Planet(3, "机器人星", faction=Faction.AUTOMATONS, players=100)
    body = format_front([defending, attacking, robot], [Campaign(1, attacking),
                        Campaign(2, attacking)], [], Faction.TERMINIDS)
    assert "进攻 1 处 · 防守 1 处" in body
    assert "战区在线 暂无完整数据" in body
    assert "守卫星" in body and body.count("虫星") == 1
    assert "机器人星" not in body


def test_front_rejects_unknown_faction():
    with pytest.raises(CommandError):
        parse_front("任意人名")
    assert parse_front("Automaton") == Faction.AUTOMATONS
    assert parse_front("虫族") == Faction.TERMINIDS


async def test_invalid_front_argument_does_not_fetch_data():
    service = AsyncMock()
    reply = await CommandRouter(service).respond("战线 全部人")
    assert reply.card is None and "用法" in reply.text
    service.get_planets.assert_not_called()


async def test_mock_front_preserves_data_metadata():
    service = HD2Service(FallbackProvider([MockProvider()]), Settings(provider="mock"))
    reply = await CommandRouter(service).respond("战线")
    assert all(word in reply.text for word in ["机器人", "终结族", "光能者", "模拟", "采样时间"])


@pytest.mark.parametrize("command", ["个人指令", "超级商店"])
async def test_unavailable_private_data_is_explicit_and_never_replaced_by_mock(command):
    service = AsyncMock()
    reply = await CommandRouter(service).respond(command)
    assert reply.card is None and "尚未接入" in reply.text
    service.get_planets.assert_not_called()


def test_current_planet_sample_overrides_older_campaign_owner_and_player_count():
    old = Planet(9, "刚解放星", faction=Faction.TERMINIDS, players=10000)
    current = Planet(9, "刚解放星", faction=Faction.HUMANS, players=2)
    text = format_front([current], [Campaign(1, old)], [], Faction.TERMINIDS)
    assert "进攻 0 处" in text and "刚解放星" not in text


def test_urgent_campaign_and_idle_controlled_planets_are_visible():
    urgent = Planet(1, "紧急星", faction=Faction.AUTOMATONS,
                    event=PlanetEvent(id=1, event_type=0))
    idle = Planet(2, "非战区星", faction=Faction.AUTOMATONS, disabled=True)
    text = format_front([urgent, idle], [Campaign(1, urgent)], [], Faction.AUTOMATONS)
    assert "紧急解放" in text and "其他控制星球：非战区星 #2（不可部署）" in text
