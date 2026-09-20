"""Failure cleanup, concurrent rate limits and time-sensitive war state."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from hd2bot import formatter
from hd2bot.config import Settings
from hd2bot.hd2 import http as http_module
from hd2bot.hd2.errors import HD2RateLimitError, HD2UnavailableError
from hd2bot.hd2.http import JSONHTTPClient
from hd2bot.hd2.models import WarStatus
from hd2bot.hd2.providers.captured import CapturedAPIProvider
from hd2bot.hd2.providers.community import CommunityProvider
from hd2bot.hd2.service import DataResult
from hd2bot.router import HELP, UNAVAILABLE, CommandRouter
from hd2bot.services.concurrency import gather_cancel_on_error

NOW = datetime(2026, 9, 11, tzinfo=UTC)


async def test_parallel_success_preserves_result_order():
    first, second = AsyncMock(return_value="first"), AsyncMock(return_value="second")
    assert await gather_cancel_on_error(first(), second()) == ["first", "second"]


async def test_parallel_failure_cancels_and_awaits_sibling_cleanup_preserving_error():
    started, cleaned = asyncio.Event(), asyncio.Event()
    original = HD2UnavailableError("offline")

    async def failing():
        await started.wait()
        raise original

    async def pending():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            cleaned.set()

    with pytest.raises(HD2UnavailableError) as result:
        await gather_cancel_on_error(failing(), pending())
    assert result.value is original
    assert cleaned.is_set()


async def test_parallel_caller_cancellation_drains_all_children():
    started = [asyncio.Event(), asyncio.Event()]
    cleaned = [asyncio.Event(), asyncio.Event()]

    async def pending(index):
        started[index].set()
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            cleaned[index].set()

    parent = asyncio.create_task(gather_cancel_on_error(pending(0), pending(1)))
    await asyncio.gather(*(event.wait() for event in started))
    parent.cancel()
    with pytest.raises(asyncio.CancelledError):
        await parent
    assert all(event.is_set() for event in cleaned)


async def test_router_friendly_failure_returns_only_after_optional_reads_are_cleaned_up():
    started = [asyncio.Event(), asyncio.Event()]
    cleaned = [asyncio.Event(), asyncio.Event()]

    async def fail():
        await asyncio.gather(*(event.wait() for event in started))
        raise HD2UnavailableError("offline")

    async def pending(index):
        started[index].set()
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            cleaned[index].set()

    service = SimpleNamespace(
        get_war=fail, get_planets=lambda: pending(0), get_campaigns=lambda: pending(1)
    )
    router = CommandRouter(service)
    assert await router.handle("战况") == UNAVAILABLE
    assert all(event.is_set() for event in cleaned)
    assert await router.handle("帮助") == HELP


@pytest.mark.parametrize(
    ("provider_type", "method", "failed_method", "pending_method"),
    [
        (CapturedAPIProvider, "get_war", "_status", "_info"),
        (CapturedAPIProvider, "get_planets", "_status", "_info"),
        (CapturedAPIProvider, "get_campaigns", "_status", "get_planets"),
        (CapturedAPIProvider, "get_statistics", "_resource", "get_planets"),
        (CommunityProvider, "get_statistics", "_war_payload", "get_planets"),
    ],
)
async def test_provider_failure_cancels_other_read_before_fallback(
    provider_type, method, failed_method, pending_method
):
    provider = provider_type(AsyncMock(), Settings(provider="mock"))
    started, cleaned = asyncio.Event(), asyncio.Event()
    original = HD2UnavailableError("offline")

    async def fail(*args, **kwargs):
        await started.wait()
        raise original

    async def pending(*args, **kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    setattr(provider, failed_method, fail)
    setattr(provider, pending_method, pending)
    with pytest.raises(HD2UnavailableError) as result:
        await getattr(provider, method)()
    assert result.value is original
    assert cleaned.is_set()


async def test_429_received_during_interval_wait_prevents_queued_network_request(monkeypatch):
    now = [100.0]
    waiting, release = asyncio.Event(), asyncio.Event()

    async def sleep(delay):
        waiting.set()
        await release.wait()
        now[0] += delay

    monkeypatch.setattr(http_module, "time", SimpleNamespace(monotonic=lambda: now[0]))
    session = SimpleNamespace(get=AsyncMock())
    client = JSONHTTPClient(session, "https://local-test.invalid", provider="mock",
                            min_interval=2, sleep=sleep)
    client._next_request = 102
    task = asyncio.create_task(client.get("/queued"))
    await asyncio.wait_for(waiting.wait(), timeout=1)
    # Another in-flight request receives a 60-second Retry-After while this one waits.
    client._blocked_until = 160
    release.set()
    with pytest.raises(HD2RateLimitError) as result:
        await task
    assert result.value.retry_after == 58
    session.get.assert_not_called()


@pytest.mark.parametrize(
    ("started", "ended", "expected"),
    [
        (NOW - timedelta(days=2), NOW - timedelta(days=1), "已结束"),
        (NOW - timedelta(days=1), NOW, "已结束"),
        (NOW + timedelta(days=1), NOW + timedelta(days=2), "尚未开始"),
        (NOW - timedelta(days=1), NOW + timedelta(days=1), "进行中"),
    ],
)
def test_war_state_respects_known_dates_and_preserves_mock_banner(
    started, ended, expected, monkeypatch
):
    monkeypatch.setattr(formatter, "utcnow", lambda: NOW)
    war = WarStatus(started_at=started, ended_at=ended)
    body = formatter.format_war_status(war, None, None)
    result = formatter.finish_response(body, [DataResult(war, "mock", NOW)])
    assert f"战争状态：{expected}" in result
    assert "模拟数据" in result


def test_war_state_handles_naive_end_dates_without_changing_provider_contract(monkeypatch):
    monkeypatch.setattr(formatter, "utcnow", lambda: NOW)
    war = WarStatus(ended_at=(NOW - timedelta(days=1)).replace(tzinfo=None))
    assert "战争状态：已结束" in formatter.format_war_status(war, None, None)
    assert war.status == "进行中"
