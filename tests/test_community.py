from copy import deepcopy

import pytest

from hd2bot.config import Settings
from hd2bot.hd2.errors import HD2SchemaError
from hd2bot.hd2.models import Faction
from hd2bot.hd2.providers.community import CommunityProvider
from hd2bot.hd2.providers.mock import MockProvider


class FakeHTTP:
    def __init__(self, **responses):
        self.responses = responses
        self.calls = []

    async def get(self, path, ttl=None):
        self.calls.append((path, ttl))
        return deepcopy(self.responses[path.rsplit("/", 1)[-1]])


@pytest.fixture
def planet():
    # Deliberately outside the real catalog so this is an independent fixture.
    return {
        "index": 9999, "name": "Test Planet", "currentOwner": "Humans",
        "health": 100, "maxHealth": 100, "statistics": {"playerCount": 12},
        "event": {"id": 5, "eventType": 1, "faction": "Automaton",
                  "health": 25, "maxHealth": 100},
    }


@pytest.fixture
def war():
    return {
        "started": "2024-01-23T20:05:13Z", "now": "1972-07-27T21:30:40Z",
        "impactMultiplier": 0.02, "statistics": {"playerCount": 999},
    }


async def test_human_control_and_defense_have_independent_progress(planet):
    parsed = (await CommunityProvider(FakeHTTP(planets=[planet]), Settings()).get_planets())[0]
    assert parsed.liberation == 100
    assert parsed.event.progress == 75
    assert parsed.event.faction == Faction.AUTOMATONS
    assert parsed.players == 12


async def test_catalog_names_and_aliases_take_priority(monkeypatch, planet):
    monkeypatch.setattr("hd2bot.hd2.providers.community.metadata_for", lambda index: {
        "name": "测试星球", "english_name": "Test World", "aliases": ["测试别名"],
        "sector": "测试星区", "biome": "沙漠", "hazards": ["火龙卷"],
    })
    parsed = (await CommunityProvider(FakeHTTP(planets=[planet]), Settings()).get_planets())[0]
    assert parsed.name == "测试星球"
    assert parsed.english_name == "Test World"
    assert {"测试别名", "Test World", "Test Planet"} <= set(parsed.aliases)
    assert parsed.biome == "沙漠"


async def test_enemy_progress_and_optional_unknowns(planet):
    planet.update(currentOwner="Terminids", health=40, maxHealth=100,
                  statistics={}, regenPerSecond="bad", event=None)
    parsed = (await CommunityProvider(FakeHTTP(planets=[planet]), Settings()).get_planets())[0]
    assert parsed.liberation == 60
    assert parsed.players is None
    assert parsed.regen_rate is None
    assert parsed.event is None
    assert parsed.position is None


@pytest.mark.parametrize("maximum", [0, -1, "100", None, float("inf")])
async def test_invalid_maximum_does_not_invent_progress(planet, maximum):
    planet.update(currentOwner="Terminids", maxHealth=maximum)
    parsed = (await CommunityProvider(FakeHTTP(planets=[planet]), Settings()).get_planets())[0]
    assert parsed.liberation is None


async def test_invalid_records_are_skipped_with_warning(planet, caplog):
    rows = [None, {"index": "bad", "currentOwner": "Humans"}, planet]
    parsed = await CommunityProvider(FakeHTTP(planets=rows), Settings()).get_planets()
    assert len(parsed) == 1
    assert "Skipped invalid community planet" in caplog.text


@pytest.mark.parametrize("payload", [None, {}, {"error": "unavailable"}, [None],
                                    [{"index": True, "currentOwner": "Humans"}],
                                    [{"index": 1}], [{"index": 1, "currentOwner": False}]])
async def test_bad_planet_schema_is_rejected(payload):
    with pytest.raises(HD2SchemaError):
        await CommunityProvider(FakeHTTP(planets=payload), Settings()).get_planets()


async def test_war_ignores_relative_timestamp_and_missing_players(war):
    war["statistics"] = {}
    parsed = await CommunityProvider(FakeHTTP(war=war), Settings()).get_war()
    assert parsed.started_at.year == 2024
    assert parsed.war_time is None
    assert parsed.players is None


@pytest.mark.parametrize("payload", [{}, [], {"started": "no", "statistics": {}},
                                    {"started": "2024-01-01", "statistics": []}])
async def test_bad_war_schema_is_rejected(payload):
    with pytest.raises(HD2SchemaError):
        await CommunityProvider(FakeHTTP(war=payload), Settings()).get_war()


async def test_statistics_total_comes_from_war_and_defense_from_attacker(planet, war):
    provider = CommunityProvider(FakeHTTP(war=war, planets=[planet]), Settings())
    stats = await provider.get_statistics()
    assert stats.players == 999
    assert stats.faction_players[Faction.AUTOMATONS] == 12
    assert stats.faction_players[Faction.HUMANS] is None
    assert stats.terminid_kills is None


async def test_statistics_missing_planet_count_is_unknown(planet, war):
    missing = deepcopy(planet)
    missing["index"] += 1
    missing["statistics"] = {"playerCount": "unknown"}
    stats = await CommunityProvider(
        FakeHTTP(war=war, planets=[planet, missing]), Settings(),
    ).get_statistics()
    assert stats.faction_players[Faction.AUTOMATONS] is None


async def test_campaign_preserves_source_faction_and_embedded_planet(planet):
    row = {"id": 10, "planet": planet, "type": 1, "faction": "Humans"}
    parsed = (await CommunityProvider(FakeHTTP(campaigns=[row]), Settings()).get_campaigns())[0]
    assert parsed.id == 10
    assert parsed.planet.index == 9999
    assert parsed.faction == Faction.HUMANS
    assert parsed.planet.event.faction == Faction.AUTOMATONS


async def test_order_preserves_unknown_tasks_and_null_translation():
    row = {
        "id": 2, "title": {"en-US": "Major Order", "zh-Hans": "主要指令"},
        "description": None, "tasks": [
            {"type": 11, "values": [1, 1, 9999], "valueTypes": [3, 11, 12]},
            {"type": 999, "values": [20], "valueTypes": [777]},
        ], "progress": [0, 6], "reward": {"type": 1, "amount": 45},
        "expiration": "2026-09-13T12:01:04.7611144Z",
    }
    parsed = (await CommunityProvider(FakeHTTP(assignments=[row]), Settings()).get_major_order())[0]
    assert parsed.title == "主要指令"
    assert parsed.description is None
    assert parsed.tasks[0].planet_index == 9999
    assert parsed.tasks[0].target == 1
    assert parsed.tasks[1].values == (20,)
    assert parsed.tasks[1].progress == 6
    assert parsed.tasks[1].target is None
    assert parsed.rewards[0].amount == 45
    assert parsed.expires_at.year == 2026


@pytest.mark.parametrize("task", [None, {"type": "bad"},
    {"type": 11, "values": [1], "valueTypes": []},
    {"type": 11, "values": [float("nan")], "valueTypes": [12]},
])
async def test_bad_order_tasks_raise_schema_error(task):
    provider = CommunityProvider(FakeHTTP(assignments=[{"id": 1, "tasks": [task]}]), Settings())
    with pytest.raises(HD2SchemaError):
        await provider.get_major_order()


async def test_empty_collections_are_valid():
    provider = CommunityProvider(FakeHTTP(planets=[], campaigns=[], assignments=[]), Settings())
    assert await provider.get_planets() == []
    assert await provider.get_campaigns() == []
    assert await provider.get_major_order() == []


async def test_mock_covers_all_commands_and_returns_isolated_data():
    provider = MockProvider()
    assert provider.name == "mock"
    planets = await provider.get_planets()
    assert {"Meridia", "Angel's Venture"} <= {p.english_name for p in planets}
    assert any(p.event for p in planets)
    assert any(p.owner != Faction.HUMANS and not p.event for p in planets)
    assert await provider.get_campaigns()
    assert await provider.get_major_order()
    assert (await provider.get_statistics()).players == (await provider.get_war()).players
    planets[0].name = "modified"
    assert (await provider.get_planets())[0].name != "modified"
