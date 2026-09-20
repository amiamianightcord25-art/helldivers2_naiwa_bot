from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from hd2bot.config import Settings
from hd2bot.hd2.base import HD2Provider
from hd2bot.hd2.errors import HD2SchemaError, HD2UnavailableError
from hd2bot.hd2.models import Dispatch, Faction, Planet
from hd2bot.hd2.providers import captured
from hd2bot.hd2.providers.captured import CapturedAPIProvider
from hd2bot.hd2.providers.community import CommunityProvider
from hd2bot.hd2.providers.fallback import FallbackProvider
from hd2bot.hd2.providers.mock import MockProvider
from hd2bot.hd2.service import HD2Service
from hd2bot.intelligence import format_dispatches, format_supply_lines

NOW = datetime(2026, 9, 16, 8, tzinfo=UTC)


class HTTP:
    def __init__(self, payload, *, clock=700000):
        self.payload, self.clock = payload, clock
        self.calls = []

    async def get(self, path, ttl=0):
        self.calls.append(path)
        if path.endswith("/WarID"):
            return {"id": 999}
        if path.endswith("/Status"):
            return {"planetStatus": [], "time": self.clock}
        return deepcopy(self.payload)


async def test_official_news_uses_recent_window_and_sampled_war_clock(monkeypatch):
    monkeypatch.setattr(captured, "utcnow", lambda: NOW)
    http = HTTP([
        {"id": 1, "message": "旧战报", "published": 696400},
        {"id": 2, "message": "新战报", "published": 699400},
    ])
    provider = CapturedAPIProvider(http, Settings())
    result = await provider.get_dispatches()
    assert [item.id for item in result] == [2, 1]
    assert result[0].published_at == NOW - timedelta(minutes=10)
    assert "/api/NewsFeed/999?maxEntries=1024&fromTimestamp=95200" in http.calls
    assert not any("WarInfo" in path for path in http.calls)


async def test_official_news_cached_publication_time_does_not_slide(monkeypatch):
    monkeypatch.setattr(captured, "utcnow", lambda: NOW)
    http = HTTP([{"id": 2, "message": "消息", "published": 699400}])
    provider = CapturedAPIProvider(http, Settings())
    first = await provider.get_dispatches()
    first.clear()  # callers cannot corrupt the cached list
    monkeypatch.setattr(captured, "utcnow", lambda: NOW + timedelta(seconds=30))
    second = await provider.get_dispatches()
    assert second[0].published_at == NOW - timedelta(minutes=10)
    assert sum("NewsFeed" in path for path in http.calls) == 1


@pytest.mark.parametrize("published", [None, -1, True, "2026-09-16", float("inf"), 1e100])
async def test_official_unknown_publication_date_is_not_fabricated(monkeypatch, published):
    monkeypatch.setattr(captured, "utcnow", lambda: NOW)
    items = await CapturedAPIProvider(
        HTTP([{"id": 1, "message": "消息", "published": published}]), Settings(),
    ).get_dispatches()
    assert items[0].published_at is None


async def test_official_missing_war_clock_keeps_unknown_publication_date():
    http = HTTP([{"id": 1, "message": "消息", "published": 100}], clock=None)
    items = await CapturedAPIProvider(http, Settings()).get_dispatches()
    assert items[0].published_at is None
    assert http.calls[-1].endswith("maxEntries=4096&fromTimestamp=0")


@pytest.mark.parametrize("payload", [{}, [None], [{"id": 1}],
                                     [{"id": True, "message": "bad"}],
                                     [{"id": 1, "message": []}],
                                     [{"id": 1, "message": "a"}, {"id": 1, "message": "b"}]])
async def test_official_invalid_news_is_schema_failure(payload):
    with pytest.raises(HD2SchemaError):
        await CapturedAPIProvider(HTTP(payload), Settings()).get_dispatches()


async def test_community_news_reads_v2_iso_dates_and_sorts_newest():
    http = HTTP([
        {"id": 3, "message": {"zh-Hans": "中文新闻", "en-US": "News"},
         "published": "2026-09-16T07:00:00Z"},
        {"id": 1, "message": "旧消息", "published": 700000},
        {"id": 2, "message": "中间消息", "published": "invalid"},
    ])
    items = await CommunityProvider(http, Settings()).get_dispatches()
    assert [item.id for item in items] == [3, 2, 1]
    assert items[0].message == "中文新闻"
    assert items[0].published_at == NOW - timedelta(hours=1)
    assert items[1].published_at is None and items[2].published_at is None
    assert http.calls == ["/api/v2/dispatches"]


async def test_community_invalid_required_news_does_not_become_empty_success():
    provider = CommunityProvider(HTTP([{"id": 1}]), Settings())
    with pytest.raises(HD2SchemaError):
        await provider.get_dispatches()


@pytest.mark.parametrize("provider_type", [CapturedAPIProvider, CommunityProvider])
async def test_empty_news_is_valid(provider_type):
    assert await provider_type(HTTP([]), Settings()).get_dispatches() == []


async def test_news_failure_uses_community_fallback_and_service_cache():
    primary = CapturedAPIProvider(HTTP({}), Settings())
    backup = CommunityProvider(HTTP([{"id": 5, "message": "回退消息"}]), Settings())
    service = HD2Service(FallbackProvider([primary, backup]), Settings())
    first = await service.get_dispatches()
    assert first.source == "community" and first.value[0].message == "回退消息"
    assert await service.get_dispatches() == first
    assert len(backup.http.calls) == 1


async def test_older_provider_can_decline_optional_news_resource():
    provider = MockProvider()
    with pytest.raises(HD2UnavailableError):
        await HD2Provider.get_dispatches(provider)


def test_news_formatter_latest_three_removes_game_tags_and_marks_unknown_dates():
    result = format_dispatches([
        Dispatch(1, "should not appear"), Dispatch(3, "<i=3>标题</i>\n正文", NOW),
        Dispatch(4, "新消息"), Dispatch(2, "更早的消息", NOW - timedelta(days=1)),
    ])
    assert "最新 3 条" in result
    assert result.index("#4") < result.index("#3") < result.index("#2")
    assert "should not appear" not in result and "<i=" not in result
    assert "标题\n正文" in result and "发布时间未知" in result
    assert "2026-09-16 16:00 UTC+8" in result
    assert "暂无近期银河新闻" in format_dispatches([])


def test_supply_lines_include_incoming_outgoing_and_missing_targets_once():
    origin = Planet(1, "本星", waypoints=(2, 2, 4, 1, 99))
    targets = [origin, Planet(2, "目标二", faction=Faction.TERMINIDS, players=1200),
               Planet(3, "目标三", faction=Faction.HUMANS, waypoints=(1,)),
               Planet(4, "目标四", waypoints=(1,))]
    result = format_supply_lines(origin, targets)
    assert result.count("目标二") == 1 and "本星 → 邻星" in result
    assert "目标三" in result and "邻星 → 本星" in result
    assert "目标四" in result and "双向记录" in result
    assert "星球 #99" in result and "详情暂无数据" in result
    assert "终结族" in result and "1,200" in result and "暂无数据" in result
    assert "联通不代表当前可进攻或可部署" in result


def test_supply_no_connections_does_not_claim_route_is_broken():
    result = format_supply_lines(Planet(1, "孤星"), [])
    assert "当前数据未提供相邻连接" in result
    assert "断路" not in result


async def test_mock_covers_news_and_incoming_outgoing_supply_links():
    provider = MockProvider()
    planets = await provider.get_planets()
    result = format_supply_lines(planets[0], planets)
    assert "本星 → 邻星" in result and "邻星 → 本星" in result
    assert "模拟" in format_dispatches(await provider.get_dispatches())
