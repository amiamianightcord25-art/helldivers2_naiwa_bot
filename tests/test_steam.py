"""Steam source validation, markup cleanup, cache/failure policy and lifecycle."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import aiohttp
import pytest

from hd2bot.config import Settings
from hd2bot.hd2.errors import HD2SchemaError, HD2UnavailableError
from hd2bot.services.cache import TTLCache
from hd2bot.steam import (
    NEWS_PATH,
    PLAYERS_PATH,
    SteamService,
    clean_steam_text,
    official_source_url,
    parse_news,
)


def news_item(gid="123456", **changes):
    return {
        "gid": gid, "title": "Official transmission", "contents": "[p]For Super Earth.[/p]",
        "appid": 553850, "feedname": "steam_community_announcements",
        "date": 1789531200,
        "url": f"https://steamstore-a.akamaihd.net/news/externalpost/steam_community_announcements/{gid}",
        **changes,
    }


def envelope(*items):
    return {"appnews": {"appid": 553850, "newsitems": list(items)}}


def service_with_payload(payload, **settings):
    service = SteamService(replace(Settings(), **settings))
    service._client = AsyncMock()
    service._client.get.return_value = payload
    return service


def test_clean_bbcode_html_and_media_without_script_or_markup():
    raw = ('[h1]Patch &amp; notes[/h1][p]A <b>bold</b> fix[/p]'
           '[list][*]One[*][url=https://untrusted.invalid]Two[/url][/list]'
           '[img]https://invalid.example/image.jpg[/img]'
           '<script>alert("no")</script><style>body{color:red}</style><p>End</p>')
    text = clean_steam_text(raw)
    assert "Patch & notes" in text
    assert "A bold fix" in text
    assert "• One\n• Two" in text
    assert text.endswith("End")
    assert all(value not in text for value in ("[h1]", "<b>", "https:", "alert", "color:red"))


@pytest.mark.parametrize("url", [
    "https://store.steampowered.com.attacker.invalid/news/app/553850/view/1",
    "https://store.steampowered.com@attacker.invalid/news/app/553850/view/1",
    "https://attacker.invalid/news/app/553850/view/1",
    "javascript:alert(1)",
    "https://store.steampowered.com/news/app/440/view/1",
    "https://store.steampowered.com/news/app/553850/view/1?next=https://attacker.invalid",
    "https://store.steampowered.com:9999/news/app/553850/view/1",
    "https://store.steampowered.com:invalid/news/app/553850/view/1",
])
def test_only_expected_steam_source_routes_are_accepted(url):
    assert official_source_url(url) is None


def test_cdn_alias_and_http_normalized_to_official_https_source():
    assert official_source_url(
        "http://steamstore-a.akamaihd.net/news/externalpost/steam_community_announcements/123"
    ) == "https://store.steampowered.com/news/externalpost/steam_community_announcements/123"
    assert official_source_url(
        "https://steamcommunity.com/games/553850/announcements/detail/123"
    ) == "https://steamcommunity.com/games/553850/announcements/detail/123"


def test_parse_only_official_app_announcements_and_distinguish_patch_tags():
    news = parse_news(envelope(
        news_item("1", date=100, title="New Warbond!"),
        news_item("2", date=300, title="Devoid of Liberty: 7.0.2", tags=["patchnotes"]),
        news_item("3", date=200, title="Hotfix 01.003.200"),
        news_item("4", feedname="pcgamer"),
        news_item("5", appid=440),
        news_item("6", url="https://evil.example/announcement"),
        news_item("7", date=float("nan")),
        news_item("8", title="<script>bad()</script>"),
        news_item("9", contents=None),
    ))
    assert [item.gid for item in news] == ["2", "3", "1"]
    assert [item.patch for item in news] == [True, True, False]
    assert news[0].published_at == datetime.fromtimestamp(300, UTC)


@pytest.mark.parametrize("payload", [None, [], {}, {"appnews": []},
    {"appnews": {"appid": 440, "newsitems": []}},
    {"appnews": {"appid": 553850, "newsitems": {}}},
])
def test_bad_envelope_is_a_safe_provider_failure(payload):
    with pytest.raises(HD2SchemaError):
        parse_news(payload)


async def test_list_stays_text_uses_latest_three_and_coalesces_fetches():
    service = service_with_payload(envelope(*(news_item(str(i), date=i) for i in range(1, 6))))
    replies = await asyncio.gather(service.query(), service.query())
    assert replies[0] == replies[1]
    reply = replies[0]
    assert reply.card is None
    assert reply.text.count("详情：更新 ") == 3
    assert "详情：更新 5" in reply.text
    assert "详情：更新 2" not in reply.text
    assert "UTC+8" in reply.text
    service._client.get.assert_awaited_once_with(NEWS_PATH, ttl=0)


async def test_patch_filter_does_not_call_every_announcement_a_patch():
    service = service_with_payload(envelope(
        news_item("1", title="New Warbond"),
        news_item("2", title="Patch notes 1.2.3"),
    ))
    reply = await service.query(patches_only=True)
    assert "Patch notes 1.2.3" in reply.text
    assert "New Warbond" not in reply.text
    assert reply.card is None


async def test_detail_has_clean_body_source_and_optional_card_only_for_long_content():
    service = service_with_payload(envelope(
        news_item("1", contents="[p]Fixed stability.[/p]"),
        news_item("2", contents="[h1]Fixes[/h1]" + "This is a longer fix.\n" * 80),
    ))
    small = await service.query("1")
    assert small.card is None
    assert "Fixed stability." in small.text and "[p]" not in small.text
    assert "官方原文：https://store.steampowered.com" in small.text
    long = await service.query("2")
    assert long.card is not None
    assert "This is a longer fix." in long.card.sections[0].rows[0].value
    assert "未找到该新闻 ID" in (await service.query("3")).text
    assert "用法" in (await service.query("../../invalid")).text


async def test_invalid_id_never_opens_a_session():
    service = SteamService(Settings())
    assert "用法" in (await service.query("https://invalid.example")).text
    assert service._session is None


async def test_mock_queries_never_create_or_call_transport(monkeypatch):
    service = SteamService(Settings(provider="mock"))
    monkeypatch.setattr(service, "_http", lambda: pytest.fail("mock opened a network client"))
    for reply in (await service.query(), await service.query("10003"), await service.players()):
        assert "模拟数据" in reply.text
    assert service._session is None
    await service.close()


@pytest.mark.parametrize("failure", [HD2UnavailableError("internal failure"), TimeoutError()])
async def test_transport_failure_is_friendly_and_does_not_leak_internals(failure):
    service = service_with_payload({})
    service._client.get.side_effect = failure
    reply = await service.query()
    assert "暂时无法取得" in reply.text
    assert "internal" not in reply.text
    assert reply.card is None


async def test_cached_news_is_marked_stale_when_refresh_fails():
    clock = [0.0]
    service = service_with_payload(envelope(news_item()), stale_ttl=300)
    service._cache = TTLCache(clock=lambda: clock[0])
    first = await service.query()
    assert "最近缓存" not in first.text
    clock[0] = 61
    service._client.get.side_effect = HD2UnavailableError("unavailable")
    stale = await service.query()
    assert "最近缓存" in stale.text and "详情：更新 123456" in stale.text
    clock[0] = 500
    assert "暂时无法取得" in (await service.query()).text


async def test_online_count_is_explicitly_steam_only_and_cached():
    service = service_with_payload({"response": {"player_count": 123456, "result": 1}})
    reply = await service.players()
    assert "123,456" in reply.text
    assert "仅含 Steam 平台，不代表全平台总人数" in reply.text
    assert reply.card is None
    assert await service.players() == reply
    service._client.get.assert_awaited_once_with(PLAYERS_PATH, ttl=0)


@pytest.mark.parametrize("payload", [None, {}, {"response": {"player_count": 12, "result": 0}},
    {"response": {"player_count": -1, "result": 1}},
    {"response": {"player_count": True, "result": 1}},
])
async def test_invalid_count_is_not_displayed_as_real_players(payload):
    service = service_with_payload(payload)
    assert "暂时无法取得" in (await service.players()).text


async def test_service_closes_owned_session_and_preserves_caller_session():
    async with SteamService(Settings()) as service:
        service._http()
        owned = service._session
        assert not owned.closed
    assert owned.closed
    await service.close()
    assert "暂时不可用" in (await service.query()).text
    async with aiohttp.ClientSession() as session:
        service = SteamService(Settings(), session=session)
        service._http()
        await service.close()
        assert not session.closed


async def test_patch_data_interface_reuses_query_cache_and_preserves_original_sample(monkeypatch):
    from hd2bot.services import cache as cache_module

    sampled = datetime(2026, 9, 16, 8, tzinfo=UTC)
    monkeypatch.setattr(cache_module, "utcnow", lambda: sampled)
    service = service_with_payload(envelope(
        news_item("100", title="普通公告"),
        news_item("50", title="Hotfix 1.2.3"),
        news_item("25", title="Devoid of Liberty", tags=["patchnotes"]),
    ))
    await service.query()
    later = datetime(2026, 9, 16, 9, tzinfo=UTC)
    monkeypatch.setattr(cache_module, "utcnow", lambda: later)
    data = await service.get_patch_notes()
    assert {item.gid for item in data.value} == {"50", "25"}
    assert all(item.patch for item in data.value)
    assert data.source == "steam" and data.fetched_at == sampled and not data.stale
    service._client.get.assert_awaited_once_with(NEWS_PATH, ttl=0)


async def test_patch_data_interface_exposes_stale_and_propagates_expired_failure():
    ticks = [0.0]
    service = service_with_payload(envelope(news_item(title="Patch notes")), stale_ttl=300)
    service._cache = TTLCache(clock=lambda: ticks[0])
    fresh = await service.get_patch_notes()
    ticks[0] = 61
    service._client.get.side_effect = HD2UnavailableError("temporary")
    stale = await service.get_patch_notes()
    assert stale.stale and stale.fetched_at == fresh.fetched_at
    assert stale.value == fresh.value
    ticks[0] = 500
    with pytest.raises(HD2UnavailableError):
        await service.get_patch_notes()


async def test_mock_patch_data_never_creates_a_session_and_closed_service_raises(monkeypatch):
    service = SteamService(Settings(provider="mock"))
    monkeypatch.setattr(service, "_http", lambda: pytest.fail("mock opened a network client"))
    data = await service.get_patch_notes()
    assert data.source == "mock" and len(data.value) == 2 and not data.stale
    assert service._session is None
    await service.close()
    with pytest.raises(HD2UnavailableError):
        await service.get_patch_notes()


async def test_patch_data_call_bounds_total_time_even_with_larger_http_setting(monkeypatch):
    timeout = asyncio.timeout
    requested = []

    def bounded(seconds):
        requested.append(seconds)
        return timeout(seconds)

    service = service_with_payload(envelope(news_item(title="Patch notes")), timeout=60)
    monkeypatch.setattr(asyncio, "timeout", bounded)
    await service.get_patch_notes()
    assert requested == [25]
