"""Public coordinates, supply links and map replies without external services."""

import math
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from hd2bot import formatter
from hd2bot.application import create_service
from hd2bot.config import Settings
from hd2bot.hd2.errors import HD2UnavailableError
from hd2bot.hd2.models import Campaign, Faction, MajorOrder, OrderTask, Planet, PlanetEvent
from hd2bot.hd2.service import DataResult, HD2Service
from hd2bot.rendering.models import MapPlanet, QueryCard, StarMap
from hd2bot.rendering.renderer import HtmlRenderer
from hd2bot.router import UNAVAILABLE, CommandRouter
from hd2bot.starmap import map_reply

NOW = datetime(2026, 9, 16, 8, tzinfo=UTC)


def result(value, *, stale=False):
    return DataResult(value, "captured", NOW, stale=stale)


def draw(planets, *, campaigns=(), orders=(), focus=None):
    return map_reply(planets, list(campaigns), list(orders), [result(planets)], focus)


def service_with(planets):
    service = AsyncMock(spec=HD2Service)
    service.get_planets.return_value = result(planets)
    service.get_campaigns.return_value = result([])
    service.get_major_order.return_value = result([])
    return service


@pytest.mark.parametrize("local", [False, True])
def test_projection_preserves_direction_distance_and_aspect_ratio(local):
    planets = [
        Planet(1000, "中心", position=(0.2, -0.1), waypoints=(1001, 1002, 1003)),
        Planet(1001, "东侧", position=(0.3, -0.1)),
        Planet(1002, "北侧", position=(0.2, 0.0)),
        Planet(1003, "东北", position=(0.4, 0.1)),
    ]
    _, card = draw(planets, focus=planets[0] if local else None)
    nodes = {node.index: node for node in card.star_map.planets}
    center, east, north, northeast = (nodes[index] for index in range(1000, 1004))
    assert east.x > center.x and east.y == center.y
    assert north.y < center.y and north.x == center.x
    assert east.x - center.x == pytest.approx(center.y - north.y, abs=0.002)
    assert northeast.x - center.x == pytest.approx(
        2 * (east.x - center.x), abs=0.002,
    )
    assert center.y - northeast.y == pytest.approx(
        2 * (center.y - north.y), abs=0.002,
    )
    assert all(math.isfinite(node.x) and math.isfinite(node.y) for node in nodes.values())
    if local:
        assert center.focused and center.x == center.y == 500


@pytest.mark.parametrize("position", [
    None, (), (0.1,), (0.0, 0.0, 0.0), (float("nan"), 0), (0, float("inf")),
    (float("-inf"), 0), (1.01, 0), (0, -1.01), (True, 0), ("0.1", 0),
])
def test_missing_or_invalid_coordinates_are_not_fabricated(position):
    planets = [Planet(1000, "有效", position=(0, 0), waypoints=(1001,)),
               Planet(1001, "缺失", position=position)]
    text, card = draw(planets)
    assert [node.index for node in card.star_map.planets] == [1000]
    assert not card.star_map.links
    assert any("1 个星球坐标" in notice for notice in card.notices)
    assert "1 个星球坐标" in text


def test_links_are_undirected_deduplicated_and_require_distinct_visible_endpoints():
    planets = [
        Planet(1000, "甲", position=(0, 0), waypoints=(1000, 1001, 1001, 1002, 9999)),
        Planet(1001, "乙", position=(0.2, 0), waypoints=(1000,)),
        Planet(1002, "无坐标", waypoints=(1000,)),
    ]
    _, card = draw(planets)
    assert len(card.star_map.links) == 1
    link = card.star_map.links[0]
    endpoints = {(link.x1, link.y1), (link.x2, link.y2)}
    assert endpoints == {(node.x, node.y) for node in card.star_map.planets}


def test_disabled_planet_remains_visible_but_is_not_an_active_battle():
    planet = Planet(1000, "不可部署", faction=Faction.AUTOMATONS,
                    disabled=True, position=(0.2, 0.2),
                    event=PlanetEvent(event_type=1, ends_at=NOW + timedelta(days=1)))
    _, card = draw([planet], campaigns=[Campaign(1, planet)])
    node, = card.star_map.planets
    assert node.disabled
    assert not node.active and not node.defense


def test_focus_includes_incoming_and_outgoing_neighbors_and_excludes_far_worlds():
    planets = [
        Planet(1000, "中心", position=(0, 0), waypoints=(1001,)),
        Planet(1001, "出向", position=(0.1, 0)),
        Planet(1002, "入向", position=(-0.2, 0.1), waypoints=(1000,)),
        Planet(1003, "远方", position=(0.9, -0.9)),
    ]
    _, card = draw(planets, focus=planets[0])
    assert {node.index for node in card.star_map.planets} == {1000, 1001, 1002}
    assert len(card.star_map.links) == 2
    assert card.star_map.focus_name == "中心"
    assert [node.index for node in card.star_map.planets if node.focused] == [1000]
    assert all(0 <= node.x <= 1000 and 0 <= node.y <= 1000
               for node in card.star_map.planets)


def test_factions_and_current_battle_objectives_keep_their_distinct_meanings(monkeypatch):
    monkeypatch.setattr(formatter, "utcnow", lambda: NOW)
    planets = [Planet(1000 + index, faction.value, faction=faction,
                      position=(index * 0.1, 0)) for index, faction in enumerate(Faction)]
    planets[0].event = PlanetEvent(event_type=1, faction=Faction.TERMINIDS,
                                   ends_at=NOW + timedelta(hours=1))
    planets[1].event = PlanetEvent(event_type=1, ends_at=NOW - timedelta(seconds=1))
    orders = [
        MajorOrder(1, tasks=[OrderTask(planet_index=1002)],
                   expires_at=NOW + timedelta(hours=1)),
        MajorOrder(2, tasks=[OrderTask(planet_index=1003)],
                   expires_at=NOW - timedelta(seconds=1)),
    ]
    _, card = draw(planets, campaigns=[Campaign(1, planets[0]), Campaign(2, planets[2])],
                   orders=orders)
    nodes = {node.index: node for node in card.star_map.planets}
    assert {node.faction for node in nodes.values()} == {faction.value for faction in Faction}
    assert nodes[1000].defense and nodes[1000].faction == Faction.HUMANS.value
    assert not nodes[1001].defense
    assert nodes[1002].active and nodes[1002].major
    assert not nodes[1003].major
    assert not nodes[1004].active


@pytest.mark.parametrize("planets", [[], [Planet(1000, "无坐标")]])
def test_no_coordinates_returns_an_explanatory_text_without_a_card(planets):
    text, card = draw(planets)
    assert card is None
    assert "未提供有效星球坐标" in text


def test_focused_planet_with_missing_coordinates_does_not_become_galactic_map():
    focus = Planet(1000, "目标缺失")
    text, card = draw([focus, Planet(1001, "其他", position=(0, 0))], focus=focus)
    assert card is None
    assert "目标缺失" in text and "坐标暂缺或无效" in text


async def test_optional_battle_data_failure_still_returns_map_with_unknown_counts():
    service = service_with([Planet(1000, "可见", position=(0, 0))])
    service.get_campaigns.side_effect = HD2UnavailableError("campaigns unavailable")
    service.get_major_order.side_effect = HD2UnavailableError("orders unavailable")
    reply = await CommandRouter(service).respond("星图")
    assert reply.card is not None and len(reply.card.star_map.planets) == 1
    metrics = {metric.label: metric.value for metric in reply.card.metrics}
    assert metrics["图中战区"] == "暂无数据"
    assert metrics["主线目标"] == "暂无数据"
    assert "战役数据暂时不可用" in reply.text
    assert "主线数据暂时不可用" in reply.text


async def test_required_planets_failure_returns_friendly_text():
    service = service_with([])
    service.get_planets.side_effect = HD2UnavailableError("planets unavailable")
    reply = await CommandRouter(service).respond("星图")
    assert reply.card is None and reply.text == UNAVAILABLE


async def test_ambiguous_focus_lists_choices_without_fetching_optional_resources():
    planets = [Planet(1000, "共同星甲", position=(0, 0)),
               Planet(1001, "共同星乙", position=(0.2, 0.2))]
    service = service_with(planets)
    reply = await CommandRouter(service).respond("星图 共同星")
    assert reply.card is None
    assert "未找到唯一匹配" in reply.text
    assert "星图 共同星甲" in reply.text and "星图 共同星乙" in reply.text
    service.get_campaigns.assert_not_awaited()
    service.get_major_order.assert_not_awaited()


@pytest.mark.parametrize("command", ["星图", "/星图", "<@12345> /星图",
                                     "星图 Meridia", "星图 64"])
async def test_mock_star_map_is_available_through_normal_command_routing(command):
    async with create_service(Settings(provider="mock")) as service:
        router = CommandRouter(service)
        reply = await router.respond(command)
        assert reply.card is not None and reply.card.star_map.planets
        assert any("模拟" in notice for notice in reply.card.notices)
        assert "模拟" in reply.text
        assert reply.card.footer
        assert await router.handle(command) == reply.text
        if command.endswith(("Meridia", "64")):
            assert [node.index for node in reply.card.star_map.planets if node.focused] == [64]


def test_stale_map_preserves_source_time_and_discloses_cache():
    planets = [Planet(1000, "星球", position=(0, 0))]
    text, card = map_reply(planets, [], [], [result(planets, stale=True)])
    assert "最近缓存" in text
    assert any("最近缓存" in notice for notice in card.notices)
    assert any("官方战局 API" in line for line in card.footer)
    assert any("2026-09-16" in line for line in card.footer)


def test_map_template_escapes_names_at_the_html_boundary():
    attack = '<img src="https://invalid.example/leak" onerror="alert(1)">'
    card = QueryCard("星图", star_map=StarMap((
        MapPlanet(1000, attack, 500, 500, "Humans", focused=True, marker="1"),
    ), (), focus_name=attack))
    html = HtmlRenderer().render_html(card)
    assert "<svg" in html
    assert attack not in html and "&lt;img" in html
    assert 'onerror="alert' not in html
    assert "<script" not in html
def test_current_planet_snapshot_controls_active_marker():
    from hd2bot.hd2.models import utcnow

    old = Planet(1, "旧战役", faction=Faction.AUTOMATONS, position=(0, 0))
    current = Planet(1, "已解放", faction=Faction.HUMANS, position=(0, 0), disabled=True)
    _, card = map_reply([current], [Campaign(10, old)], [],
                        [DataResult([current], "captured", utcnow())])
    node = card.star_map.planets[0]
    assert node.disabled and not node.active and node.faction == "Humans"
