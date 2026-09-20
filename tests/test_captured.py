"""Synthetic raw responses: no capture files, credentials, or network required."""

import asyncio
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from hd2bot.hd2.errors import HD2SchemaError, HD2UnavailableError
from hd2bot.hd2.models import Faction
from hd2bot.hd2.providers import captured
from hd2bot.hd2.providers.captured import PATHS, CapturedAPIProvider


class StubHTTP:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    async def get(self, path, ttl=0):
        self.calls.append(path)
        await asyncio.sleep(0)
        value = self.responses[path]
        if isinstance(value, Exception):
            raise value
        return deepcopy(value)


@pytest.fixture
def sample(monkeypatch):
    monkeypatch.setattr(captured, "metadata_for", lambda index: {
        "name": f"示例星球{index}", "english_name": f"Planet {index}",
        "aliases": (f"P{index}",), "sector": "测试星区",
        "biome": "沙漠", "hazards": (),
    })
    paths = {key: value.format(war_id=999, election_id="test-election")
             for key, value in PATHS.items()}
    data = {
        paths["war_id"]: {"id": 999},
        paths["status"]: {
            "warId": 999, "time": 1000, "impactMultiplier": 0.8,
            "planetStatus": [
                {"index": 9, "owner": 1, "health": 900, "players": 20},
                {"index": 2, "owner": 3, "health": 250, "players": 30},
            ],
            "campaigns": [{"id": 77, "planetIndex": 2, "type": 0, "race": 3}],
            "planetEvents": [{"id": 10, "planetIndex": 9, "eventType": 1,
                              "race": 2, "health": 3000, "maxHealth": 4000,
                              "startTime": 500, "expireTime": 4600}],
            "futureExtension": {"value": "ignored"},
        },
        paths["war_info"]: {
            "warId": 999, "startDate": 1700000000,
            "planetInfos": [{"index": 2, "maxHealth": 1000, "waypoints": [9]},
                            {"index": 9, "maxHealth": 2000}],
        },
        paths["assignments"]: [{
            "id32": 123, "progress": [0, 4], "expiresIn": 600,
            "setting": {
                "overrideTitle": "主要指令", "overrideBrief": "解放目标星球",
                "tasks": [{"type": 11, "values": [1, 1, 2], "valueTypes": [3, 11, 12]},
                          {"type": 987, "values": [10], "valueTypes": [66]}],
                "rewards": [{"type": 1, "amount": 45}],
                "reward": {"type": 1, "amount": 45},
            },
        }],
        paths["statistics"]: {"galaxy_stats": {"missionsWon": 100, "bugKills": 900}},
    }
    settings = SimpleNamespace(cache_ttl=60, static_ttl=3600, order_ttl=60,
                               statistics_ttl=60)
    http = StubHTTP(data)
    return CapturedAPIProvider(http, settings), http, paths


async def test_planets_merge_by_index_and_defense_uses_event_health(sample):
    provider, _, _ = sample
    planets = {planet.index: planet for planet in await provider.get_planets()}
    assert planets[2].health == 250
    assert planets[2].max_health == 1000
    assert planets[2].liberation == 75
    assert planets[2].waypoints == (9,)
    assert planets[2].english_name == "Planet 2"
    assert planets[9].liberation is None
    assert planets[9].event.progress == 25
    assert planets[9].event.faction is Faction.TERMINIDS


async def test_relative_event_and_order_dates_do_not_slide_on_cache_hits(sample, monkeypatch):
    provider, _, _ = sample
    anchor = datetime(2026, 1, 2, tzinfo=UTC)
    monkeypatch.setattr(captured, "utcnow", lambda: anchor)
    event = (await provider.get_planet(9)).event
    order = (await provider.get_major_order())[0]
    assert event.ends_at == anchor + timedelta(seconds=3600)
    assert event.starts_at == anchor - timedelta(seconds=500)
    assert order.expires_at == anchor + timedelta(seconds=600)
    monkeypatch.setattr(captured, "utcnow", lambda: anchor + timedelta(seconds=60))
    assert (await provider.get_planet(9)).event.ends_at == event.ends_at
    assert (await provider.get_major_order())[0].expires_at == order.expires_at


async def test_task_mapping_preserves_unknown_tasks_without_guessing(sample):
    provider, _, _ = sample
    order = (await provider.get_major_order())[0]
    assert order.tasks[0].planet_index == 2
    assert order.tasks[0].target == 1
    assert order.tasks[0].progress == 0
    assert order.tasks[1].type == 987
    assert order.tasks[1].values == (10,)
    assert order.tasks[1].progress == 4
    assert order.tasks[1].target is None
    assert len(order.rewards) == 1


async def test_missing_optional_values_stay_unknown_and_nonmatching_sets_merge(sample):
    provider, http, paths = sample
    http.responses[paths["status"]]["planetStatus"].append({"index": 55, "owner": 888})
    http.responses[paths["war_info"]]["planetInfos"].append({"index": 88})
    planets = {planet.index: planet for planet in await provider.get_planets()}
    assert set(planets) == {2, 9, 55, 88}
    assert planets[55].faction is Faction.UNKNOWN
    assert planets[55].players is None
    assert planets[55].max_health is None
    assert planets[88].health is None
    assert (await provider.get_war()).players is None
    statistics = await provider.get_statistics()
    assert statistics.players is None
    assert statistics.missions_lost is None
    assert statistics.terminid_kills == 900


async def test_dynamic_war_id_single_flight_and_campaign_joins(sample):
    provider, http, paths = sample
    war, planets, campaigns = await asyncio.gather(
        provider.get_war(), provider.get_planets(), provider.get_campaigns(),
    )
    assert war.war_id == 999
    assert war.players == 50
    assert war.started_at.year == 2023
    assert campaigns[0].planet.index == 2
    assert campaigns[0].planet.health == 250
    assert len(planets) == 2
    assert http.calls.count(paths["war_id"]) == 1
    assert http.calls.count(paths["status"]) == 1
    assert http.calls.count(paths["war_info"]) == 1


async def test_statistics_group_defense_players_by_attacking_faction(sample):
    provider, _, _ = sample
    statistics = await provider.get_statistics()
    assert statistics.players == 50
    assert statistics.faction_players[Faction.TERMINIDS] == 20
    assert statistics.faction_players[Faction.AUTOMATONS] == 30
    assert statistics.faction_players[Faction.ILLUMINATE] is None
    assert statistics.faction_players[Faction.HUMANS] is None


@pytest.mark.parametrize(("endpoint", "payload", "method"), [
    ("war_id", {"id": True}, "get_war"),
    ("status", {"planetStatus": "wrong"}, "get_planets"),
    ("status", {}, "get_war"),
    ("war_info", {"planetInfos": [{"index": "bad"}]}, "get_planets"),
    ("assignments", {}, "get_major_order"),
    ("assignments", [{"id32": 1, "setting": {}}], "get_major_order"),
    ("statistics", {"galaxy_stats": {}}, "get_statistics"),
])
async def test_bad_required_structures_raise_schema_error(sample, endpoint, payload, method):
    provider, http, paths = sample
    http.responses[paths[endpoint]] = payload
    with pytest.raises(HD2SchemaError):
        await getattr(provider, method)()


async def test_empty_orders_are_valid_and_missing_progress_is_not_zero(sample):
    provider, http, paths = sample
    http.responses[paths["assignments"]][0].pop("progress")
    assert (await provider.get_major_order())[0].tasks[0].progress is None
    provider._cache.clear()
    http.responses[paths["assignments"]] = []
    assert await provider.get_major_order() == []


async def test_expired_cache_does_not_mask_failure_and_recovers(sample):
    provider, http, paths = sample
    assert len(await provider.get_planets()) == 2
    previous = http.responses[paths["status"]]
    provider._cache[paths["status"]].expires = 0
    http.responses[paths["status"]] = HD2UnavailableError("unavailable")
    with pytest.raises(HD2UnavailableError):
        await provider.get_planets()
    http.responses[paths["status"]] = previous
    assert len(await provider.get_planets()) == 2


async def test_custom_path_settings_are_honored(sample):
    _, http, _ = sample
    http.responses["/public/season"] = {"id": 321}
    settings = SimpleNamespace(cache_ttl=60, static_ttl=3600, order_ttl=60,
                               statistics_ttl=60, captured_paths={"war_id": "/public/season"})
    provider = CapturedAPIProvider(http, settings)
    assert await provider._war_id() == 321
    assert http.calls == ["/public/season"]
