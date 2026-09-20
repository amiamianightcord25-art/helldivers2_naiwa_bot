"""Regressions for configuration, input normalization and public data handling."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from hd2bot import formatter
from hd2bot.config import Settings
from hd2bot.hd2 import http as http_module
from hd2bot.hd2.errors import HD2RateLimitError
from hd2bot.hd2.http import JSONHTTPClient
from hd2bot.hd2.models import Faction, Planet, PlanetEvent
from hd2bot.hd2.parsing import integer, number
from hd2bot.presentation import ChatContext
from hd2bot.router import CommandRouter


@pytest.mark.parametrize("setting", [
    "HD2_TIMEOUT", "HD2_CACHE_TTL", "HD2_ORDER_TTL", "HD2_STATISTICS_TTL",
    "HD2_STATIC_TTL", "HD2_STALE_TTL",
])
@pytest.mark.parametrize("value", ["nan", "inf", "-inf"])
def test_duration_settings_reject_nonfinite_values(tmp_path, monkeypatch, setting, value):
    monkeypatch.setenv(setting, value)
    with pytest.raises(ValueError):
        Settings.load(tmp_path)


@pytest.mark.parametrize("prefix", ["武器 ", "查询战绩 ", ""])
async def test_fullwidth_capsule_in_group_uses_group_guide(prefix):
    catalog = Mock()
    router = CommandRouter(None, catalog=catalog)
    reply = await router.respond(
        prefix + "ＨＤ２ｖ１：" + "A" * 500,
        context=ChatContext("group", "test-group", "test-member"),
    )
    assert reply.text == router.onboarding.group_guide()
    catalog.handle.assert_not_called()


def test_large_integer_identity_and_counts_remain_exact():
    value = 2**53 + 1
    assert integer(value) == value
    assert formatter._number(value) == "9,007,199,254,740,993"


def test_number_treats_out_of_float_range_as_unknown():
    assert number(10**400) is None


@pytest.mark.parametrize("started", [False, True])
def test_defense_is_active_only_from_its_start_time(monkeypatch, started):
    now = datetime(2026, 9, 19, tzinfo=UTC)
    monkeypatch.setattr(formatter, "utcnow", lambda: now)
    event = PlanetEvent(
        event_type=1, faction=Faction.TERMINIDS,
        starts_at=now if started else now + timedelta(minutes=1),
        ends_at=now + timedelta(hours=1),
    )
    planet = Planet(1, "Test planet", faction=Faction.HUMANS, event=event)
    assert formatter._active_defense(planet) is started
    text = formatter.format_defenses([planet])
    assert ("Test planet" in text) is started


async def test_concurrent_429_responses_keep_longest_cooldown(monkeypatch):
    monkeypatch.setattr(http_module, "time", SimpleNamespace(monotonic=lambda: 100.0))
    entered = {path: asyncio.Event() for path in ("/long", "/short")}
    release = {path: asyncio.Event() for path in entered}

    class Response:
        status = 429

        def __init__(self, path):
            self.path = path
            self.headers = {"Retry-After": "60" if path == "/long" else "1"}

        async def __aenter__(self):
            entered[self.path].set()
            await release[self.path].wait()
            return self

        async def __aexit__(self, *args):
            return False

    session = SimpleNamespace(get=lambda url, **kwargs: Response(url.removeprefix("https://test.invalid")))
    client = JSONHTTPClient(session, "https://test.invalid", provider="test",
                            retries=0, min_interval=0)
    tasks = {path: asyncio.create_task(client.get(path)) for path in entered}
    try:
        await asyncio.wait_for(asyncio.gather(*(event.wait() for event in entered.values())), 1)
        for path in ("/long", "/short"):
            release[path].set()
            with pytest.raises(HD2RateLimitError):
                await tasks[path]
        with pytest.raises(HD2RateLimitError) as error:
            await client.get("/next")
        assert error.value.retry_after == 60
    finally:
        for task in tasks.values():
            task.cancel()
        await asyncio.gather(*tasks.values(), return_exceptions=True)
