"""HTTP failure policy is exercised without contacting real API servers."""

from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from inspect import signature
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
import pytest
from aioresponses import aioresponses
from aioresponses import core as aioresponses_core

from hd2bot.hd2 import http as http_module
from hd2bot.hd2.errors import HD2RateLimitError, HD2SchemaError, HD2UnavailableError
from hd2bot.hd2.http import JSONHTTPClient, retry_after_seconds
from hd2bot.services.cache import TTLCache

BASE = "https://hd2.invalid"


@pytest.fixture(autouse=True)
def aioresponses_stream_writer_compatibility(monkeypatch):
    """Bridge aioresponses' constructor call to aiohttp 3.14's public signature."""
    if "stream_writer" not in signature(aiohttp.ClientResponse).parameters:
        return
    original = aioresponses_core.ClientResponse

    def response_with_stream_writer(*args, **kwargs):
        kwargs.setdefault("stream_writer", SimpleNamespace(output_size=0))
        return original(*args, **kwargs)

    monkeypatch.setattr(aioresponses_core, "ClientResponse", response_with_stream_writer)


class Clock:
    def __init__(self):
        self.value = 100.0

    def __call__(self):
        return self.value

    async def sleep(self, seconds):
        self.value += seconds


@pytest.fixture
def clock(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(http_module, "time", SimpleNamespace(monotonic=clock))
    monkeypatch.setattr(http_module.random, "uniform", lambda *_: 0.0)
    return clock


def make_client(session, clock, **options):
    sleep = AsyncMock(side_effect=clock.sleep)
    client = JSONHTTPClient(
        session, BASE, provider="test", min_interval=0, sleep=sleep, **options
    )
    client.cache = TTLCache(clock=clock, failure_ttl=0)
    return client, sleep


def request_count(mock):
    return sum(len(requests) for requests in mock.requests.values())


@pytest.mark.parametrize("header", ["2", "date"])
async def test_short_429_waits_for_retry_after_then_succeeds(header, monkeypatch, clock):
    fixed_now = datetime(2026, 9, 11, tzinfo=UTC)
    expected_delay = 2.0
    if header == "date":
        monkeypatch.setattr(
            http_module, "datetime", SimpleNamespace(now=lambda _: fixed_now)
        )
        header = format_datetime(fixed_now + timedelta(seconds=3), usegmt=True)
        expected_delay = 3.0
    with aioresponses() as mock:
        mock.get(BASE + "/status", status=429, headers={"Retry-After": header})
        mock.get(BASE + "/status", payload={"players": 12})
        async with aiohttp.ClientSession() as session:
            client, sleep = make_client(session, clock)
            assert await client.get("/status") == {"players": 12}
            sleep.assert_awaited_once_with(expected_delay)
            assert request_count(mock) == 2


async def test_long_429_blocks_other_paths_until_cooldown_expires(clock):
    with aioresponses() as mock:
        mock.get(BASE + "/status", status=429, headers={"Retry-After": "60"})
        mock.get(BASE + "/planets", payload=[])
        async with aiohttp.ClientSession() as session:
            client, sleep = make_client(session, clock)
            with pytest.raises(HD2RateLimitError) as first:
                await client.get("/status")
            assert first.value.retry_after == 60
            with pytest.raises(HD2RateLimitError):
                await client.get("/planets")
            assert request_count(mock) == 1
            sleep.assert_not_awaited()
            clock.value += 61
            assert await client.get("/planets") == []
            assert request_count(mock) == 2


async def test_500_retries_with_backoff_then_returns_success(clock):
    with aioresponses() as mock:
        mock.get(BASE + "/status", status=500)
        mock.get(BASE + "/status", status=503)
        mock.get(BASE + "/status", payload={"ok": True})
        async with aiohttp.ClientSession() as session:
            client, sleep = make_client(session, clock)
            assert await client.get("/status") == {"ok": True}
            assert [call.args[0] for call in sleep.await_args_list] == [1, 2]
            assert request_count(mock) == 3


async def test_repeated_500_raises_after_retry_budget(clock):
    with aioresponses() as mock:
        mock.get(BASE + "/status", status=500, repeat=True)
        async with aiohttp.ClientSession() as session:
            client, sleep = make_client(session, clock, retries=2)
            with pytest.raises(HD2UnavailableError, match="HTTP 500"):
                await client.get("/status")
            assert request_count(mock) == 3
            assert sleep.await_count == 2


async def test_timeout_retries_then_returns_safe_error(clock):
    with aioresponses() as mock:
        mock.get(BASE + "/status", exception=TimeoutError("private response"), repeat=True)
        async with aiohttp.ClientSession() as session:
            client, sleep = make_client(session, clock, retries=1)
            with pytest.raises(HD2UnavailableError) as caught:
                await client.get("/status")
            assert str(caught.value) == "TimeoutError"
            assert request_count(mock) == 2
            sleep.assert_awaited_once_with(1)


async def test_malformed_json_is_not_retried_or_included_in_error(clock):
    with aioresponses() as mock:
        mock.get(BASE + "/status", body='{"private":"response", BROKEN')
        async with aiohttp.ClientSession() as session:
            client, sleep = make_client(session, clock)
            with pytest.raises(HD2SchemaError, match="Invalid JSON response") as caught:
                await client.get("/status")
            assert "private" not in str(caught.value)
            assert request_count(mock) == 1
            sleep.assert_not_awaited()


@pytest.mark.parametrize("status", [400, 403, 404])
async def test_permanent_http_errors_are_not_retried(clock, status):
    with aioresponses() as mock:
        mock.get(BASE + "/status", status=status, body="private response")
        async with aiohttp.ClientSession() as session:
            client, sleep = make_client(session, clock)
            with pytest.raises(HD2UnavailableError, match=f"HTTP {status}"):
                await client.get("/status")
            assert request_count(mock) == 1
            sleep.assert_not_awaited()


@pytest.mark.parametrize(
    ("header", "expected"), [(None, 10), ("invalid", 10), ("0", 1), ("90000", 86400)]
)
def test_retry_after_fallback_and_bounds(header, expected):
    assert retry_after_seconds(header) == expected
