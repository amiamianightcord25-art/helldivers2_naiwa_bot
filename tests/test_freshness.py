"""Regression coverage for shared upstream resources and outer service caches."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from hd2bot.hd2 import observation
from hd2bot.hd2.errors import HD2UnavailableError
from hd2bot.hd2.http import JSONHTTPClient
from hd2bot.hd2.models import Faction, WarStatus
from hd2bot.hd2.providers import captured
from hd2bot.hd2.providers.community import CommunityProvider
from hd2bot.hd2.providers.fallback import FallbackProvider, SourcedValue
from hd2bot.hd2.service import HD2Service
from hd2bot.services import cache


class Clock:
    value = 0.0
    anchor = datetime(2026, 9, 11, tzinfo=UTC)

    def __call__(self):
        return self.value

    def utcnow(self):
        return self.anchor + timedelta(seconds=self.value)


@pytest.fixture
def clock(monkeypatch):
    current = Clock()
    monkeypatch.setattr(captured, "monotonic", current)
    monkeypatch.setattr(captured, "utcnow", current.utcnow)
    monkeypatch.setattr(cache, "utcnow", current.utcnow)
    return current


class RawHTTP:
    def __init__(self, clock):
        self.clock = clock
        self.status_calls = 0
        self.fail = False

    async def get(self, path, ttl=0):
        await asyncio.sleep(0)
        if path.endswith("/WarID"):
            return {"id": 801}
        if path.endswith("/WarInfo"):
            return {"planetInfos": [{"index": 9999, "maxHealth": 1000}]}
        if self.fail:
            raise HD2UnavailableError("offline")
        self.status_calls += 1
        return {"planetStatus": [{"index": 9999, "owner": 3,
                                  "players": self.status_calls * 10, "health": 500}],
                "campaigns": []}


def settings():
    return SimpleNamespace(cache_ttl=20, static_ttl=3600, order_ttl=30,
                           statistics_ttl=30, stale_ttl=900)


async def test_captured_status_time_survives_cross_command_cache_hits(clock):
    http = RawHTTP(clock)
    provider = captured.CapturedAPIProvider(http, settings())
    service = HD2Service(FallbackProvider([provider], clock=clock), settings(),
                         cache.TTLCache(clock=clock))
    first = await service.get_war()
    clock.value = 19
    planets = await service.get_planets()
    assert planets.fetched_at == first.fetched_at == clock.anchor
    assert http.status_calls == 1
    clock.value = 20
    refreshed = await service.get_planets()
    assert http.status_calls == 2
    assert refreshed.value[0].players == 20
    assert refreshed.fetched_at == clock.anchor + timedelta(seconds=20)
    assert not refreshed.stale


async def test_stale_fallback_keeps_the_original_source_timestamp(clock):
    http = RawHTTP(clock)
    provider = captured.CapturedAPIProvider(http, settings())
    service = HD2Service(FallbackProvider([provider], clock=clock), settings(),
                         cache.TTLCache(clock=clock))
    await service.get_war()
    clock.value = 19
    await service.get_planets()
    clock.value = 21
    http.fail = True
    stale = await service.get_planets()
    assert stale.stale
    assert stale.fetched_at == clock.anchor
    clock.value = 921
    with pytest.raises(HD2UnavailableError):
        await service.get_planets()


async def test_http_cache_hit_records_actual_source_time(clock):
    client = JSONHTTPClient(None, "https://hd2.invalid", provider="test")
    client.cache = cache.TTLCache(clock=clock)
    client._request = AsyncMock(return_value={"players": 1})
    await client.get("/api/v1/war", ttl=20)
    clock.value = 19
    reads = {}
    token = observation.observations.set(reads)
    try:
        await client.get("/api/v1/war", ttl=20)
    finally:
        observation.observations.reset(token)
    assert list(reads.values()) == [clock.anchor]
    client._request.assert_awaited_once()


async def test_parallel_reads_use_oldest_dynamic_time_and_failed_source_is_isolated(clock):
    class Failed:
        name = "failed"

        async def get_war(self):
            observation.record_observation("old", clock.anchor - timedelta(days=1))
            raise HD2UnavailableError("offline")

    class Working:
        name = "working"

        async def get_war(self):
            async def read(key, age):
                await asyncio.sleep(0)
                observation.record_observation(key, clock.anchor + timedelta(seconds=age))
            await asyncio.gather(read("war", 10), read("planets", 5))
            return WarStatus(players=20)

    result = await FallbackProvider([Failed(), Working()], clock=clock).fetch("get_war")
    assert result.source == "working"
    assert result.fetched_at == clock.anchor + timedelta(seconds=5)
    assert observation.observations.get() is None


async def test_source_timestamp_does_not_extend_the_stale_window(clock):
    storage = cache.TTLCache(clock=clock)
    source = SourcedValue("known", "test", clock.anchor)
    clock.value = 19
    result = await storage.get("resource", AsyncMock(return_value=source), ttl=20,
                               stale_ttl=10, timestamp=lambda value: value.fetched_at)
    assert result.fetched_at == clock.anchor
    clock.value = 25
    failing = AsyncMock(side_effect=HD2UnavailableError("offline"))
    assert (await storage.get("resource", failing, ttl=20, stale_ttl=10,
                              timestamp=lambda value: value.fetched_at)).stale
    clock.value = 30
    with pytest.raises(HD2UnavailableError):
        await storage.get("resource", failing, ttl=20, stale_ttl=10,
                          timestamp=lambda value: value.fetched_at)


@pytest.mark.parametrize(("event_type", "expected"), [(1, Faction.AUTOMATONS),
                                                      (2, Faction.HUMANS)])
async def test_community_only_defense_events_reassign_front_players(event_type, expected):
    class HTTP:
        async def get(self, path, ttl=0):
            if path.endswith("/war"):
                return {"started": "2024-01-01T00:00:00Z", "statistics": {"playerCount": 12}}
            return [{"index": 9999, "currentOwner": "Humans", "name": "Example",
                     "statistics": {"playerCount": 12},
                     "event": {"eventType": event_type, "faction": "Automaton"}}]

    result = await CommunityProvider(HTTP(), settings()).get_statistics()
    assert result.faction_players[expected] == 12
    other = Faction.HUMANS if expected is Faction.AUTOMATONS else Faction.AUTOMATONS
    assert result.faction_players[other] is None
