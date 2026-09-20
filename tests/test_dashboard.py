"""The strategic dashboard combines existing public feeds without new I/O."""

from unittest.mock import AsyncMock

import pytest

from hd2bot.application import create_service
from hd2bot.config import Settings
from hd2bot.hd2.errors import HD2UnavailableError
from hd2bot.hd2.providers.fallback import FallbackProvider
from hd2bot.hd2.providers.mock import MockProvider
from hd2bot.hd2.service import HD2Service
from hd2bot.rendering.renderer import HtmlRenderer
from hd2bot.router import UNAVAILABLE, CommandRouter
from hd2bot.services.cache import TTLCache


@pytest.mark.parametrize("command", ["看板", "/dashboard", "战略看板"])
async def test_dashboard_aliases_return_the_same_public_overview(command):
    settings = Settings(provider="mock")
    async with create_service(settings) as service:
        reply = await CommandRouter(service).respond(command)

    assert reply.card is not None
    assert reply.card.title == "战略看板"
    assert "战略看板" in reply.text
    assert "全服在线" in reply.text
    assert "进攻战区" in reply.text
    assert "防守战区" in reply.text
    assert "DSS" in reply.text
    assert "银河事件" in reply.text
    assert "模拟数据" in reply.text
    assert "重点进攻战区" in HtmlRenderer().render_html(reply.card)


async def test_dashboard_rejects_arguments_without_querying_public_feeds():
    service = AsyncMock(spec=HD2Service)

    reply = await CommandRouter(service).respond("dashboard extra")

    assert "不需要额外参数" in reply.text
    assert reply.card is None
    assert not service.mock_calls


async def test_dashboard_optional_feed_failure_keeps_the_other_sections():
    provider = MockProvider()
    for method in ("get_planets", "get_campaigns", "get_major_order",
                   "get_space_stations", "get_global_events"):
        setattr(provider, method, AsyncMock(side_effect=HD2UnavailableError("offline")))
    settings = Settings(provider="mock", cache_ttl=0, order_ttl=0,
                        statistics_ttl=0, stale_ttl=0)
    service = HD2Service(FallbackProvider([provider], cooldown=0), settings,
                         cache=TTLCache(failure_ttl=0))

    reply = await CommandRouter(service).respond("看板")

    assert reply.card is not None
    assert "战争状态" in reply.text
    assert "超级地球：暂无数据" in reply.text
    assert "暂不可用：星球、进攻战区、主线、DSS、银河事件" in reply.text
    assert "暂时处理失败" not in reply.text


async def test_dashboard_required_war_failure_returns_standard_unavailable_message():
    provider = MockProvider()
    provider.get_war = AsyncMock(side_effect=HD2UnavailableError("offline"))
    settings = Settings(provider="mock", cache_ttl=0, order_ttl=0,
                        statistics_ttl=0, stale_ttl=0)
    service = HD2Service(FallbackProvider([provider], cooldown=0), settings,
                         cache=TTLCache(failure_ttl=0))

    reply = await CommandRouter(service).respond("看板")

    assert reply.text == UNAVAILABLE
    assert reply.card is None
