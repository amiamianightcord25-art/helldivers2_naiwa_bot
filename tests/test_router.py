"""Commands exercise the shared domain service without QQ or external APIs."""

import logging
from unittest.mock import AsyncMock

import pytest

from hd2bot.application import create_service
from hd2bot.config import Settings
from hd2bot.hd2.errors import HD2UnavailableError
from hd2bot.hd2.providers.fallback import FallbackProvider
from hd2bot.hd2.providers.mock import MockProvider
from hd2bot.hd2.service import HD2Service
from hd2bot.router import HELP, UNAVAILABLE, CommandRouter
from hd2bot.services.cache import TTLCache


def make_router(provider=None):
    provider = provider or MockProvider()
    service = HD2Service(
        FallbackProvider([provider], cooldown=0),
        Settings(provider="mock", cache_ttl=0, order_ttl=0, statistics_ttl=0, stale_ttl=0),
        cache=TTLCache(failure_ttl=0),
    )
    return CommandRouter(service)


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("战况", "10000"),
        ("主线", "为了超级地球"),
        ("星球 Meridia", "梅里迪亚"),
        ("进攻", "马勒维隆溪"),
        ("防守", "天使进取"),
        ("玩家", "10000"),
    ],
)
async def test_six_commands_run_through_mock_application(command, expected):
    async with create_service(Settings(provider="mock")) as service:
        response = await CommandRouter(service).handle(command)
    assert expected in response.replace(",", "")
    assert "模拟" in response or "mock" in response.lower()
    assert "Traceback" not in response
    assert response != UNAVAILABLE


@pytest.mark.parametrize("command", ["帮助", "/帮助", "<@12345> /帮助"])
async def test_help_does_not_require_a_working_provider(command):
    router = CommandRouter(AsyncMock(spec=HD2Service))
    assert await router.handle(command) == HELP
    router.service.get_war.assert_not_called()


@pytest.mark.parametrize(
    ("command", "expected"),
    [("这不是命令", "帮助"), ("星球", "名称或编号"), ("战况 extra", "不需要额外参数")],
)
async def test_invalid_commands_return_usage_without_loading_data(command, expected):
    service = AsyncMock(spec=HD2Service)
    response = await CommandRouter(service).handle(command)
    assert expected in response
    service.get_war.assert_not_called()
    service.get_planets.assert_not_called()


async def test_unmatched_planet_returns_clear_message():
    response = await make_router().handle("星球 zzzzzzzzzzzzzzzzzz")
    assert "未找到该星球" in response


async def test_misspelled_planet_offers_candidate_instead_of_silent_selection():
    response = await make_router().handle("星球 Merida")
    assert "未找到唯一匹配" in response
    assert "Meridia" in response


async def test_unique_partial_planet_name_shows_planet():
    response = await make_router().handle("星球 Merid")
    assert "梅里迪亚" in response
    assert "未找到唯一匹配" not in response


async def test_ambiguous_planet_name_lists_candidates():
    response = await make_router().handle("星球 e")
    assert "未找到唯一匹配" in response
    assert "Meridia" in response
    assert "Super Earth" in response


async def test_all_provider_resources_unavailable_returns_friendly_response():
    provider = MockProvider()
    for method in (
        "get_war", "get_planets", "get_campaigns", "get_major_order", "get_statistics"
    ):
        setattr(provider, method, AsyncMock(side_effect=HD2UnavailableError("offline")))
    router = make_router(provider)
    for command in ("战况", "主线", "星球 Meridia", "进攻", "防守", "玩家"):
        assert await router.handle(command) == UNAVAILABLE
    assert await router.handle("帮助") == HELP


async def test_next_command_recovers_after_failure_and_help_still_works():
    provider = MockProvider()
    statistics = await provider.get_statistics()
    provider.get_statistics = AsyncMock(
        side_effect=[HD2UnavailableError("offline"), statistics]
    )
    router = make_router(provider)
    assert await router.handle("玩家") == UNAVAILABLE
    assert await router.handle("帮助") == HELP
    assert "10000" in (await router.handle("玩家")).replace(",", "")
    assert provider.get_statistics.await_count == 2


async def test_unexpected_exception_does_not_expose_traceback_or_message(caplog, capsys):
    service = AsyncMock(spec=HD2Service)
    service.get_statistics.side_effect = RuntimeError("PRIVATE_EXCEPTION_DETAIL")
    with caplog.at_level(logging.ERROR):
        response = await CommandRouter(service).handle("玩家")
    assert "暂时处理失败" in response
    assert "帮助" in response
    captured = capsys.readouterr()
    combined = response + caplog.text + captured.out + captured.err
    assert "PRIVATE_EXCEPTION_DETAIL" not in combined
    assert "Traceback" not in combined
    assert "RuntimeError" in caplog.text


async def test_war_overview_survives_optional_planet_and_campaign_failures():
    provider = MockProvider()
    provider.get_planets = AsyncMock(side_effect=HD2UnavailableError("planets unavailable"))
    provider.get_campaigns = AsyncMock(side_effect=HD2UnavailableError("campaigns unavailable"))
    response = await make_router(provider).handle("战况")
    assert "10000" in response.replace(",", "")
    assert response != UNAVAILABLE
    assert "Traceback" not in response
