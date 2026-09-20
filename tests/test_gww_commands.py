"""User entry points for the GWW-inspired features, entirely offline."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, call

import aiohttp
import pytest
from nonebot.adapters.qq.models import Dispatch

from hd2bot.application import create_service
from hd2bot.config import Settings
from hd2bot.hd2.providers.captured import CapturedAPIProvider
from hd2bot.hd2.providers.fallback import FallbackProvider
from hd2bot.hd2.service import HD2Service
from hd2bot.qq.adapter import QQAdapter, QQDispatcher
from hd2bot.router import HELP, UNAVAILABLE, CommandRouter
from hd2bot.steam import SteamService


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    request = AsyncMock(side_effect=AssertionError("This integration test must remain offline"))
    monkeypatch.setattr(aiohttp.ClientSession, "_request", request)
    yield
    request.assert_not_awaited()


@pytest.fixture
def settings(tmp_path):
    return Settings(provider="mock", root=tmp_path, database_path=tmp_path / "bot.db",
                    stale_ttl=0, qq_app_id="synthetic-gww-test-app",
                    qq_app_secret="SYNTHETIC_GWW_TEST_SECRET")


@pytest.fixture
async def app(settings):
    async with create_service(settings) as service, SteamService(settings) as steam:
        yield CommandRouter(service, steam=steam), service, steam
    assert steam._closed
    assert steam._session is None  # Mock mode never creates a Steam HTTP session.


@pytest.mark.parametrize(("command", "alias", "expected"), [
    ("星图", "/map", "银河星图"),
    ("星图 Meridia", "/map Meridia", "梅里迪亚"),
    ("战线", "/warfront", "阵营战线"),
    ("战线 机器人", "/warfront automatons", "马勒维隆溪"),
    ("战线 虫族", "/warfront terminids", "天使进取"),
    ("战线 光能族", "/warfront illuminate", "梅里迪亚"),
    ("DSS", "/dss", "模拟飞鹰风暴"),
    ("DSS票数", "/dss_votes", "迁移投票"),
    ("公告", "/global_events", "模拟银河事件"),
    ("公告 9001", "/global_events 9001", "#9001"),
    ("控制中心", "/control_centre", "民主长城"),
    ("控制中心 5001", "/control_centre 5001", "模拟部队已准备就绪"),
    ("区域", "/区域", "区域 #1"),
    ("区域 Meridia", "/区域 Meridia", "没有对应的区域战况"),
    ("区域 127", "/区域 127", "75.00%"),
    ("特殊部队", "/subfaction", "JET BRIGADE"),
    ("更新", "/steam", "Steam 官方公告"),
    ("更新 10002", "/steam 10002", "此为本地演示公告"),
    ("补丁", "/补丁", "模拟补丁：武器平衡调整"),
    ("Steam在线", "/steam在线", "12,345"),
])
async def test_mock_commands_and_slash_aliases_reach_real_services(app, command, alias, expected):
    router, _, _ = app
    response = await router.respond(command)
    aliased = await router.respond(alias)
    assert expected in response.text
    assert aliased.text == response.text
    assert response.text != UNAVAILABLE and "暂时处理失败" not in response.text
    assert "模拟" in response.text
    if command.startswith("星图"):
        assert response.card is not None and response.card.star_map is not None
        assert aliased.card.star_map == response.card.star_map
    if command == "Steam在线":
        assert "仅含 Steam 平台" in response.text
        assert response.card is None


async def test_patch_list_omits_general_announcements_and_detail_preserves_source(app):
    router, _, _ = app
    patches = await router.respond("补丁")
    assert "10003" in patches.text and "10001" in patches.text
    assert "10002" not in patches.text and "超级地球通讯" not in patches.text
    detail = await router.respond("更新 10003")
    assert "此为本地演示补丁" in detail.text
    assert "官方原文：https://store.steampowered.com/" in detail.text
    assert patches.card is None and detail.card is None


@pytest.mark.parametrize("command", [
    "公告 abc", "控制中心 -1", "公告 12345678901", "控制中心 1.5",
    "DSS 123", "特殊部队 extra", "Steam在线 extra", "战线 invalid-faction",
])
async def test_invalid_parameters_stay_text_and_call_neither_service(app, command):
    _, service, steam = app
    hd2_spy = AsyncMock(spec=HD2Service, wraps=service)
    steam_spy = AsyncMock(spec=SteamService, wraps=steam)
    reply = await CommandRouter(hd2_spy, steam=steam_spy).respond(command)
    assert reply.card is None
    assert "用法" in reply.text or "不需要额外参数" in reply.text
    assert not hd2_spy.mock_calls and not steam_spy.mock_calls


@pytest.mark.parametrize(("command", "method", "expected"), [
    ("公告 999999", "get_global_events", "未找到该事件"),
    ("控制中心 999999", "get_episodes", "未找到该战役"),
    ("区域 impossible-planet-zzzz", "get_planets", "未找到该星球"),
    ("星图 impossible-planet-zzzz", "get_planets", "未找到该星球"),
    ("星图 e", "get_planets", "未找到唯一匹配"),
])
async def test_missing_selection_queries_only_its_resource_and_stays_text(
    app, command, method, expected,
):
    _, service, steam = app
    hd2_spy = AsyncMock(spec=HD2Service, wraps=service)
    steam_spy = AsyncMock(spec=SteamService, wraps=steam)
    reply = await CommandRouter(hd2_spy, steam=steam_spy).respond(command)
    assert expected in reply.text and reply.card is None
    assert hd2_spy.mock_calls == [getattr(call, method)()]
    assert not steam_spy.mock_calls


@pytest.mark.parametrize(("command", "expected"), [
    ("更新 invalid-id", "用法"), ("更新 999999", "未找到该新闻 ID"),
])
async def test_bad_steam_selection_never_falls_through_to_galaxy_queries(app, command, expected):
    _, service, steam = app
    hd2_spy = AsyncMock(spec=HD2Service, wraps=service)
    reply = await CommandRouter(hd2_spy, steam=steam).respond(command)
    assert expected in reply.text and reply.card is None
    assert not hd2_spy.mock_calls


@pytest.mark.parametrize("command", ["DSS", "公告", "控制中心", "区域", "特殊部队"])
async def test_malformed_public_data_is_friendly_and_never_falls_back_to_mock(settings, command):
    malformed = "PRIVATE_MALFORMED_UPSTREAM_PAYLOAD"

    async def raw_get(path, ttl=0):
        if path.endswith("/WarID"):
            return {"id": 801}
        if path.endswith("/Status"):
            return {"planetStatus": [], "spaceStations": malformed, "globalEvents": malformed,
                    "planetRegions": malformed, "planetActiveEffects": malformed}
        if path.endswith("/WarInfo"):
            return {"planetInfos": [], "planetRegions": []}
        if path.endswith("GalacticWarEffects"):
            return []
        if path.endswith("/Episode/801"):
            return {"episodes": malformed}
        raise AssertionError(f"Unexpected public path: {path}")

    public_settings = replace(settings, provider="captured")
    public = CapturedAPIProvider(SimpleNamespace(get=raw_get), public_settings)
    service = HD2Service(FallbackProvider([public]), public_settings)
    steam = AsyncMock(spec=SteamService)
    reply = await CommandRouter(service, steam=steam).respond(command)
    assert reply.text == UNAVAILABLE and reply.card is None
    assert all(word not in reply.text for word in (malformed, "模拟", "mock", "Traceback"))
    assert not steam.mock_calls


@pytest.mark.parametrize("command", ["个人指令", "/superstore"])
async def test_unconnected_private_features_explain_the_boundary_without_queries(app, command):
    _, service, steam = app
    hd2_spy = AsyncMock(spec=HD2Service, wraps=service)
    steam_spy = AsyncMock(spec=SteamService, wraps=steam)
    reply = await CommandRouter(hd2_spy, steam=steam_spy).respond(command)
    assert "授权数据" in reply.text and reply.card is None
    assert not hd2_spy.mock_calls and not steam_spy.mock_calls


def _event(scope, content, message_id):
    data = {"id": message_id, "content": content, "timestamp": "2026-09-16T14:00:00Z"}
    if scope == "group":
        data.update(group_openid="synthetic-group", author={"member_openid": "synthetic-member"})
    else:
        data["author"] = {"user_openid": "synthetic-user"}
    return QQAdapter.payload_to_event(Dispatch.model_validate({
        "op": 0, "s": 1, "id": "event-" + message_id,
        "t": "GROUP_AT_MESSAGE_CREATE" if scope == "group" else "C2C_MESSAGE_CREATE",
        "d": data,
    }))


@pytest.mark.parametrize("scope", ["group", "c2c"])
async def test_qq_event_routes_short_and_long_dss_as_structured_jpeg(
    app, settings, monkeypatch, scope,
):
    router, service, _ = app
    renderer = AsyncMock()
    renderer.render.return_value = b"\xff\xd8synthetic-gww-jpeg\xff\xd9"
    bot = AsyncMock()
    upload = bot.post_group_files if scope == "group" else bot.post_c2c_files
    send = bot.post_group_messages if scope == "group" else bot.post_c2c_messages
    upload.return_value = SimpleNamespace(file_info="synthetic-gww-file")
    dispatcher = QQDispatcher(settings, router, renderer)
    try:
        await dispatcher.process(_event(scope, "<@12345> /dss", "short-dss"), bot)
        renderer.render.assert_awaited_once()
        upload.assert_awaited_once()
        assert renderer.render.await_args.args[0].vote_charts
        assert any("模拟飞鹰风暴" in section.title
                   for section in renderer.render.await_args.args[0].sections)
        assert send.await_args_list[0].kwargs["msg_type"] == 7
        assert send.await_args.kwargs["msg_type"] == 2
        assert send.await_args.kwargs["msg_id"] == "short-dss"
        renderer.render.reset_mock()
        upload.reset_mock()

        provider = service.provider.providers[0]
        original = provider.get_space_stations

        async def detailed_stations():
            stations = await original()
            stations[0].tactical_actions[0].strategic_description = "长篇模拟支援说明。" * 100
            return stations

        monkeypatch.setattr(provider, "get_space_stations", detailed_stations)
        service.cache.clear()
        send.reset_mock()
        await dispatcher.process(_event(scope, "DSS", "long-dss"), bot)
        renderer.render.assert_awaited_once()
        card = renderer.render.await_args.args[0]
        assert card.title == "民主空间站 DSS" and any("模拟" in notice for notice in card.notices)
        upload.assert_awaited_once()
        assert upload.await_args.kwargs["file_data"] == renderer.render.return_value
        assert upload.await_args.kwargs["srv_send_msg"] is False
        assert send.await_count == 2
        media_reply = send.await_args_list[0].kwargs
        assert media_reply["msg_type"] == 7 and media_reply["msg_seq"] == 1
        assert media_reply["msg_id"] == "long-dss"
        assert media_reply["media"].file_info == "synthetic-gww-file"
        assert send.await_args.kwargs["msg_seq"] == 2
        assert send.await_args.kwargs["keyboard"].content.rows[0].buttons[0].action.data == "DSS票数"

        send.reset_mock()
        await dispatcher.process(_event(scope, "/help", "plain-help"), bot)
        from html import unescape

        text = ''.join(call.kwargs.get("content", "")
                       + unescape(getattr(call.kwargs.get("markdown"), "content", ""))
                       for call in send.await_args_list)
        assert HELP in text
        assert send.await_args.kwargs["msg_type"] == 2
        renderer.render.assert_awaited_once()
    finally:
        await dispatcher.close()
