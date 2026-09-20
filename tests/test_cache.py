"""Cache behavior under concurrent requests and temporary upstream failures."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from hd2bot.hd2.errors import HD2UnavailableError
from hd2bot.services.cache import TTLCache


class Clock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value


async def test_same_key_concurrent_requests_share_one_loader():
    cache = TTLCache()
    started, release = asyncio.Event(), asyncio.Event()

    async def load():
        started.set()
        await release.wait()
        return {"players": 42}

    loader = AsyncMock(side_effect=load)
    tasks = [asyncio.create_task(cache.get("war", loader, ttl=10)) for _ in range(12)]
    await asyncio.wait_for(started.wait(), timeout=1)
    release.set()
    results = await asyncio.gather(*tasks)
    loader.assert_awaited_once()
    assert all(result.value == {"players": 42} and not result.stale for result in results)
    assert len({result.fetched_at for result in results}) == 1


async def test_different_keys_load_concurrently():
    cache = TTLCache()
    started_a, started_b, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def load(started, value):
        started.set()
        await release.wait()
        return value

    tasks = [
        asyncio.create_task(cache.get("a", lambda: load(started_a, "A"), ttl=10)),
        asyncio.create_task(cache.get("b", lambda: load(started_b, "B"), ttl=10)),
    ]
    try:
        await asyncio.wait_for(asyncio.gather(started_a.wait(), started_b.wait()), timeout=1)
    finally:
        release.set()
        results = await asyncio.gather(*tasks)
    assert [result.value for result in results] == ["A", "B"]


async def test_fresh_cache_skips_loader_then_refreshes_at_expiry():
    clock = Clock()
    cache = TTLCache(clock=clock)
    loader = AsyncMock(side_effect=["first", "second"])
    first = await cache.get("war", loader, ttl=10)
    clock.value = 9.9
    assert await cache.get("war", loader, ttl=10) is first
    clock.value = 10
    second = await cache.get("war", loader, ttl=10)
    assert second.value == "second" and not second.stale
    assert loader.await_count == 2


async def test_upstream_failure_returns_bounded_stale_and_preserves_fetch_time():
    clock = Clock()
    cache = TTLCache(clock=clock, failure_ttl=5)
    loader = AsyncMock(side_effect=["known data", HD2UnavailableError("offline")])
    first = await cache.get("war", loader, ttl=10, stale_ttl=20)
    clock.value = 11
    stale = await cache.get("war", loader, ttl=10, stale_ttl=20)
    assert stale.value == first.value
    assert stale.stale and stale.fetched_at == first.fetched_at
    clock.value = 12
    cooldown_stale = await cache.get("war", loader, ttl=10, stale_ttl=20)
    assert cooldown_stale.stale and cooldown_stale.fetched_at == first.fetched_at
    assert loader.await_count == 2


async def test_expired_stale_is_not_returned_even_during_failure_cooldown():
    clock = Clock()
    cache = TTLCache(clock=clock, failure_ttl=30)
    loader = AsyncMock(side_effect=["known data", HD2UnavailableError("offline")])
    await cache.get("war", loader, ttl=10, stale_ttl=5)
    clock.value = 11
    assert (await cache.get("war", loader, ttl=10, stale_ttl=5)).stale
    clock.value = 15
    with pytest.raises(HD2UnavailableError, match="offline"):
        await cache.get("war", loader, ttl=10, stale_ttl=5)
    assert loader.await_count == 2


async def test_failure_cooldown_prevents_request_storm_and_recovers():
    clock = Clock()
    cache = TTLCache(clock=clock, failure_ttl=5)
    loader = AsyncMock(side_effect=[HD2UnavailableError("offline"), "recovered"])
    with pytest.raises(HD2UnavailableError):
        await cache.get("war", loader, ttl=10)
    results = await asyncio.gather(
        *(cache.get("war", loader, ttl=10) for _ in range(8)), return_exceptions=True
    )
    assert all(isinstance(result, HD2UnavailableError) for result in results)
    loader.assert_awaited_once()
    clock.value = 5
    recovered = await cache.get("war", loader, ttl=10)
    assert recovered.value == "recovered" and not recovered.stale
    assert loader.await_count == 2


async def test_programming_errors_do_not_fall_back_to_stale_data():
    clock = Clock()
    cache = TTLCache(clock=clock)
    loader = AsyncMock(side_effect=["known data", TypeError("programming bug"), "fixed"])
    await cache.get("war", loader, ttl=10, stale_ttl=60)
    clock.value = 11
    with pytest.raises(TypeError, match="programming bug"):
        await cache.get("war", loader, ttl=10, stale_ttl=60)
    assert (await cache.get("war", loader, ttl=10)).value == "fixed"


async def test_clear_removes_entries_and_failure_cooldown():
    clock = Clock()
    cache = TTLCache(clock=clock)
    loader = AsyncMock(side_effect=["old", HD2UnavailableError("offline"), "new"])
    await cache.get("war", loader, ttl=1)
    clock.value = 2
    with pytest.raises(HD2UnavailableError):
        await cache.get("war", loader, ttl=1)
    cache.clear()
    assert (await cache.get("war", loader, ttl=10)).value == "new"
    assert loader.await_count == 3
