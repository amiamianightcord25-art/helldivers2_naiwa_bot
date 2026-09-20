"""Offline integration through real NoneBot dispatch and the official QQ adapter."""

import asyncio
import base64
import json
import logging
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import nonebot
import pytest
from nonebot.adapters.qq import Bot
from nonebot.adapters.qq.event import GroupMessageCreateEvent
from nonebot.adapters.qq.exception import ActionFailed
from nonebot.adapters.qq.models import Dispatch, User
from nonebot.drivers import Request, Response
from pydantic import ValidationError

from hd2bot.config import Settings
from hd2bot.hd2.providers.fallback import FallbackProvider
from hd2bot.hd2.providers.mock import MockProvider
from hd2bot.hd2.service import HD2Service
from hd2bot.presentation import ChatContext, CommandReply
from hd2bot.qq import adapter as adapter_module
from hd2bot.qq.adapter import (
    QQAdapter,
    QQDispatcher,
    configure_sdk_logging,
    driver_lifespan,
    initialize_nonebot,
    run_qq,
    snapshot_copy_text,
)
from hd2bot.qq.boundaries import ReplayWindow, split_reply
from hd2bot.router import CommandRouter

TEST_SECRET = "LOCAL_TEST_SECRET_NOT_A_CREDENTIAL"


def make_event(scope="c2c", message_id="message-local", content="主线"):
    data = {"id": message_id, "content": content, "timestamp": "2026-09-16T00:00:00Z"}
    if scope == "group":
        data.update(group_openid="group-local", author={"member_openid": "member-local"})
    else:
        data["author"] = {"user_openid": "user-local"}
    return QQAdapter.payload_to_event(Dispatch.model_validate({
        "op": 0, "s": 1, "id": "event-" + message_id,
        "t": "GROUP_AT_MESSAGE_CREATE" if scope == "group" else "C2C_MESSAGE_CREATE", "d": data,
    }))


@pytest.fixture
def settings():
    return Settings(provider="mock", qq_app_id="local-test-app", qq_app_secret=TEST_SECRET)


@pytest.fixture
async def dispatcher(settings):
    router = AsyncMock(spec=CommandRouter)
    router.respond.return_value = CommandReply("本地测试回复")
    renderer = AsyncMock()
    renderer.render.return_value = b"\xff\xd8local-jpeg\xff\xd9"
    dispatcher = QQDispatcher(settings, router, renderer)
    yield dispatcher
    await dispatcher.close()


@pytest.fixture
def bot():
    value = AsyncMock()
    value.post_c2c_files.return_value = SimpleNamespace(file_info="local-file-info")
    value.post_group_files.return_value = SimpleNamespace(file_info="local-file-info")
    return value


@pytest.mark.parametrize("scope", ["group", "c2c"])
async def test_callbacks_forward_original_content_and_passive_reply(dispatcher, bot, scope):
    await dispatcher.process(make_event(scope, content="<@12345> /帮助"), bot)
    dispatcher.router.respond.assert_awaited_once_with("<@12345> /帮助", context=ChatContext(
        scope, "group-local" if scope == "group" else "user-local",
        "member-local" if scope == "group" else "user-local"))
    method = bot.post_group_messages if scope == "group" else bot.post_c2c_messages
    method.assert_awaited_once_with(
        **({"group_openid": "group-local"} if scope == "group" else {"openid": "user-local"}),
        msg_id="message-local", msg_seq=1, content="本地测试回复", msg_type=0,
    )
    dispatcher.renderer.render.assert_not_awaited()
    bot.post_group_files.assert_not_awaited()
    bot.post_c2c_files.assert_not_awaited()


async def test_unknown_command_uses_real_router_help(settings, bot):
    router = CommandRouter(HD2Service(FallbackProvider([MockProvider()]), settings))
    dispatcher = QQDispatcher(settings, router)
    await dispatcher.process(make_event(content="unknown command"), bot)
    assert "帮助" in bot.post_c2c_messages.await_args.kwargs["content"]


@pytest.mark.parametrize("scope", ["group", "c2c"])
async def test_image_upload_then_passive_media_reply(dispatcher, bot, scope):
    card = object()
    dispatcher.router.respond.return_value = CommandReply("完整战绩文本", card)
    await dispatcher.process(make_event(scope), bot)
    dispatcher.renderer.render.assert_awaited_once_with(card)
    upload = bot.post_group_files if scope == "group" else bot.post_c2c_files
    send = bot.post_group_messages if scope == "group" else bot.post_c2c_messages
    upload.assert_awaited_once_with(
        **({"group_openid": "group-local"} if scope == "group" else {"openid": "user-local"}),
        file_type=1, file_data=b"\xff\xd8local-jpeg\xff\xd9", srv_send_msg=False,
    )
    reply = send.await_args.kwargs
    assert reply["msg_type"] == 7 and reply["msg_seq"] == 1
    assert reply["msg_id"] == "message-local"
    assert reply["media"].file_info == "local-file-info"
    assert "content" not in reply


def test_snapshot_copy_text_extracts_only_labeled_ids():
    content = ("快照 ID：HD2-ABCDEFGH2345\n"
               "群聊可用：战绩 HD2-ABCDEFGH2345\n"
               "其他文本 HD2-ZZZZZZZZZZZZ")
    assert snapshot_copy_text(content) == (
        "快照 ID：HD2-ABCDEFGH2345\n群聊可用：战绩 HD2-ABCDEFGH2345"
    )


@pytest.mark.parametrize("scope", ["group", "c2c"])
async def test_snapshot_id_is_sent_as_copyable_text_after_image(dispatcher, bot, scope):
    dispatcher.router.respond.return_value = CommandReply(
        "战绩文本\n快照 ID：HD2-ABCDEFGH2345\n群聊可用：战绩 HD2-ABCDEFGH2345", object()
    )
    await dispatcher.process(make_event(scope), bot)
    send = bot.post_group_messages if scope == "group" else bot.post_c2c_messages
    assert send.await_count == 2
    assert send.await_args_list[0].kwargs["msg_type"] == 7
    assert send.await_args_list[1].kwargs["msg_seq"] == 2
    assert send.await_args_list[1].kwargs["content"] == snapshot_copy_text(
        dispatcher.router.respond.return_value.text
    )


@pytest.mark.parametrize("failure", ["render", "upload", "file_info", "send"])
async def test_image_failure_falls_back_without_reusing_ambiguous_sequence(
    dispatcher, bot, failure, caplog,
):
    dispatcher.router.respond.return_value = CommandReply("完整战绩文本", object())
    error = RuntimeError("PRIVATE_REMOTE_BODY")
    if failure == "render":
        dispatcher.renderer.render.side_effect = error
    elif failure == "upload":
        bot.post_c2c_files.side_effect = error
    elif failure == "file_info":
        bot.post_c2c_files.return_value = SimpleNamespace(file_info=None)
    else:
        bot.post_c2c_messages.side_effect = [
            ActionFailed(Response(400, content=b'{"code":304036}')), {},
        ]
    await dispatcher.process(make_event(), bot)
    reply = bot.post_c2c_messages.await_args.kwargs
    assert reply["content"] == "完整战绩文本" and reply["msg_type"] == 0
    assert reply["msg_seq"] == (2 if failure == "send" else 1)
    assert bot.post_c2c_files.await_count <= 1
    assert "PRIVATE_REMOTE_BODY" not in caplog.text


async def test_images_can_be_disabled_without_rendering(dispatcher, bot):
    dispatcher.settings = replace(dispatcher.settings, image_enabled=False)
    dispatcher.router.respond.return_value = CommandReply("文字战绩", object())
    await dispatcher.process(make_event(), bot)
    dispatcher.renderer.render.assert_not_awaited()
    assert bot.post_c2c_messages.await_args.kwargs["msg_type"] == 0


async def test_concurrent_duplicate_message_is_replied_to_once(dispatcher, bot):
    await asyncio.gather(*(dispatcher.process(make_event("group"), bot) for _ in range(10)))
    dispatcher.router.respond.assert_awaited_once()
    bot.post_group_messages.assert_awaited_once()


async def test_group_messages_from_bots_do_not_start_reply_loops(dispatcher, bot):
    event = make_event("group")
    event.author.bot = True
    await dispatcher.process(event, bot)
    dispatcher.router.respond.assert_not_awaited()
    bot.post_group_messages.assert_not_awaited()


@pytest.mark.parametrize(("scope", "limit"), [("group", 5), ("c2c", 4)])
async def test_long_text_preserves_message_id_and_sequence(dispatcher, bot, scope, limit):
    dispatcher.router.respond.return_value = CommandReply("星球战况🌍\n" * 3000)
    await dispatcher.process(make_event(scope), bot)
    api = bot.post_group_messages if scope == "group" else bot.post_c2c_messages
    assert api.await_count == limit
    replies = [call.kwargs for call in api.await_args_list]
    assert [reply["msg_seq"] for reply in replies] == list(range(1, limit + 1))
    assert all(reply["msg_id"] == "message-local" for reply in replies)
    assert all(len(reply["content"].encode("utf-8")) <= 1800 for reply in replies)
    assert "已缩略" in replies[-1]["content"]


async def test_image_failure_uses_remaining_passive_reply_budget(dispatcher, bot):
    dispatcher.router.respond.return_value = CommandReply("星球战况🌍\n" * 3000, object())
    bot.post_c2c_messages.side_effect = [
        ActionFailed(Response(400, content=b'{"code":304036}')), {}, {}, {},
    ]
    await dispatcher.process(make_event(), bot)
    calls = [call.kwargs for call in bot.post_c2c_messages.await_args_list]
    assert [call["msg_seq"] for call in calls] == [1, 2, 3, 4]
    assert calls[0]["msg_type"] == 7 and "已缩略" in calls[-1]["content"]


def test_long_campaign_list_preserved_across_chunks():
    content = "\n".join(f"{i}. 梅里迪亚 Meridia | 光能者战线 | 主线目标 | 解放40% | 在线12345"
                        for i in range(33))
    parts = split_reply(content, max_parts=4)
    assert "".join(parts) == content
    assert len(parts) > 1
    assert all(len(part.encode("utf-8")) <= 1800 for part in parts)


async def test_send_error_does_not_crash_later_message(dispatcher, bot, caplog):
    bot.post_c2c_messages.side_effect = [RuntimeError("PRIVATE_REMOTE_BODY"), {}]
    await dispatcher.process(make_event(message_id="first"), bot)
    await dispatcher.process(make_event(message_id="second"), bot)
    assert bot.post_c2c_messages.await_count == 2
    assert "PRIVATE_REMOTE_BODY" not in caplog.text


async def test_command_timeout_recovers(dispatcher, bot, monkeypatch):
    monkeypatch.setattr(adapter_module, "COMMAND_TIMEOUT", 0.01)
    release = asyncio.Event()

    async def slow(content, **kwargs):
        await release.wait()
        return CommandReply("late response")

    dispatcher.router.respond.side_effect = slow
    await dispatcher.process(make_event(message_id="slow"), bot)
    bot.post_c2c_messages.assert_not_awaited()
    assert not dispatcher._requests
    dispatcher.router.respond.side_effect = None
    await dispatcher.process(make_event(message_id="next"), bot)
    bot.post_c2c_messages.assert_awaited_once()


async def test_close_cancels_business_and_prevents_later_replies(dispatcher, bot):
    started, release = asyncio.Event(), asyncio.Event()

    async def slow(content, **kwargs):
        started.set()
        await release.wait()
        return CommandReply("too late")

    dispatcher.router.respond.side_effect = slow
    task = asyncio.create_task(dispatcher.process(make_event(), bot))
    await asyncio.wait_for(started.wait(), timeout=1)
    await dispatcher.close()
    assert task.cancelled() and not dispatcher._requests
    await dispatcher.process(make_event(message_id="after-close"), bot)
    bot.post_c2c_messages.assert_not_awaited()


async def test_work_is_bounded_to_four_active_and_32_pending(dispatcher, bot):
    release = asyncio.Event()
    active = peak = 0

    async def slow(content, **kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await release.wait()
        active -= 1
        return CommandReply("ok")

    dispatcher.router.respond.side_effect = slow
    tasks = [asyncio.create_task(dispatcher.process(make_event(message_id=str(i)), bot))
             for i in range(50)]
    await asyncio.sleep(0)
    assert len(dispatcher._requests) == 32 and peak == 4
    release.set()
    await asyncio.gather(*tasks)
    assert bot.post_c2c_messages.await_count == 32


def test_replay_memory_expires_and_stays_bounded():
    clock = [100]
    replay = ReplayWindow(ttl=10, capacity=2, clock=lambda: clock[0])
    assert replay.claim("one") and not replay.claim("one")
    assert replay.claim("two") and replay.claim("three")
    assert len(replay._seen) == 2
    clock[0] = 111
    assert replay.claim("three")


@pytest.mark.parametrize("scope", ["group", "c2c"])
def test_legacy_payload_gets_only_compatible_fields(scope):
    event = make_event(scope)
    assert event.author.id == ("member-local" if scope == "group" else "user-local")
    assert event.id == "message-local"
    if scope == "group":
        assert event.group_id == event.group_openid == "group-local"
        assert event.author.member_role == "member"


def test_missing_openid_is_not_invented():
    payload = Dispatch.model_validate({"op": 0, "s": 1, "t": "C2C_MESSAGE_CREATE", "d": {
        "id": "local", "timestamp": "2026-09-16", "content": "帮助", "author": {"id": "local"},
    }})
    with pytest.raises(ValidationError):
        QQAdapter.payload_to_event(payload)


@pytest.fixture(scope="module")
def framework():
    settings = Settings(provider="mock", qq_app_id="offline-framework", qq_app_secret=TEST_SECRET)
    driver = initialize_nonebot(settings, QQDispatcher(settings, AsyncMock()))
    qq_adapter = driver._adapters["QQ"]
    bot = Bot(qq_adapter, "offline-framework", qq_adapter.qq_config.qq_bots[0])
    bot._self_info = User(id="offline-bot", username="Offline", bot=True)
    return driver, qq_adapter, bot


@pytest.mark.parametrize("scope", ["group", "c2c"])
async def test_real_nonebot_matcher_dispatches_official_event(framework, dispatcher, monkeypatch, scope):
    _, _, real_bot = framework
    plugin = nonebot.get_plugin_by_module_name("hd2bot.qq.plugin")
    plugin.module.configure(dispatcher)
    send = AsyncMock()
    method = "post_group_messages" if scope == "group" else "post_c2c_messages"
    monkeypatch.setattr(real_bot, method, send)
    await real_bot.handle_event(make_event(scope, content="帮助"))
    dispatcher.router.respond.assert_awaited_once_with("帮助", context=ChatContext(
        scope, "group-local" if scope == "group" else "user-local",
        "member-local" if scope == "group" else "user-local"))
    send.assert_awaited_once()
    assert plugin.module.commands.plugin == plugin


async def test_real_nonebot_rule_ignores_unmentioned_group(framework, dispatcher):
    _, _, real_bot = framework
    nonebot.get_plugin_by_module_name("hd2bot.qq.plugin").module.configure(dispatcher)
    event = GroupMessageCreateEvent.model_validate(make_event("group").model_dump())
    await real_bot.handle_event(event)
    dispatcher.router.respond.assert_not_awaited()


@pytest.mark.parametrize("scope", ["group", "c2c"])
async def test_real_adapter_serializes_jpeg_base64_and_passive_json(framework, monkeypatch, scope):
    _, qq_adapter, real_bot = framework
    requests = []

    async def request(req):
        requests.append(req)
        data = {"file_info": "local-file-info"} if req.url.path.endswith("/files") else {"id": "reply"}
        return Response(200, content=json.dumps(data).encode())

    monkeypatch.setattr(qq_adapter, "request", request)
    monkeypatch.setattr(real_bot, "get_access_token", AsyncMock(return_value="SYNTHETIC_TOKEN"))
    settings = Settings(provider="mock")
    router, renderer = AsyncMock(), AsyncMock()
    router.respond.return_value = CommandReply("文本", object())
    renderer.render.return_value = b"\xff\xd8local-jpeg\xff\xd9"
    dispatcher = QQDispatcher(settings, router, renderer)
    await dispatcher.process(make_event(scope), real_bot)
    assert len(requests) == 2
    assert requests[0].url.path.endswith("/files")
    assert requests[0].json["file_data"] == base64.b64encode(renderer.render.return_value).decode()
    assert requests[0].json["srv_send_msg"] is False
    assert requests[1].json["msg_type"] == 7
    assert requests[1].json["media"] == {"file_info": "local-file-info"}
    assert requests[1].json["msg_id"] == "message-local" and requests[1].json["msg_seq"] == 1


async def test_lifespan_shutdown_on_cancellation_cleans_driver(framework, monkeypatch):
    driver, qq_adapter, _ = framework
    trace = []
    entered = asyncio.Event()
    # The startup callback is already registered; patch only its network worker.
    async def no_network(bot_info):
        trace.append("startup")
        await asyncio.Event().wait()

    monkeypatch.setattr(qq_adapter, "run_bot_websocket", no_network)

    async def run():
        async with driver_lifespan(driver):
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(run())
    await entered.wait()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert task.cancelled() and trace == ["startup"]
    assert driver._lifespan._task_group is None
    assert all(task.done() for task in qq_adapter.tasks)


async def test_ready_is_only_recorded_on_official_bot_connection(framework, monkeypatch):
    driver, qq_adapter, real_bot = framework
    ready = asyncio.Event()
    notify = Mock(side_effect=ready.set)
    monkeypatch.setattr(adapter_module, "notify_ready", notify)

    async def no_network(bot_info):
        await asyncio.Event().wait()

    monkeypatch.setattr(qq_adapter, "run_bot_websocket", no_network)
    async with driver_lifespan(driver):
        notify.assert_not_called()
        qq_adapter.bot_connect(real_bot)
        try:
            await asyncio.wait_for(ready.wait(), timeout=1)
            notify.assert_called_once()
        finally:
            qq_adapter.bot_disconnect(real_bot)


async def test_missing_ready_exits_and_closes_renderer(settings, monkeypatch):
    from hd2bot.rendering import renderer as renderer_module

    stopped = []

    @asynccontextmanager
    async def lifespan():
        try:
            yield
        finally:
            stopped.append(True)

    renderer = AsyncMock()
    monkeypatch.setattr(renderer_module, "HtmlRenderer", lambda **kwargs: renderer)
    monkeypatch.setattr(adapter_module, "initialize_nonebot",
                        lambda settings, dispatcher: SimpleNamespace(_lifespan=lifespan()))
    monkeypatch.setattr(adapter_module, "CONNECT_TIMEOUT", 0.01)
    notify = Mock()
    monkeypatch.setattr(adapter_module, "notify_ready", notify)
    assert await run_qq(settings, AsyncMock()) == 1
    assert stopped == [True]
    renderer.close.assert_awaited_once()
    notify.assert_not_called()


def test_nonebot_logging_omits_payload_tokens_and_exception(caplog):
    configure_sdk_logging()
    with caplog.at_level(logging.WARNING):
        adapter_module.nonebot_logger.warning("QQBot PRIVATE_TOKEN PRIVATE_USER_MESSAGE")
        try:
            raise RuntimeError("PRIVATE_REMOTE_BODY")
        except RuntimeError:
            adapter_module.nonebot_logger.exception("PRIVATE_EXCEPTION")
    assert "nonebot_diagnostic" in caplog.text
    assert "reason=RuntimeError" in caplog.text
    assert "function=test_nonebot_logging_omits_payload_tokens_and_exception" in caplog.text
    assert "line=" in caplog.text
    assert all(secret not in caplog.text for secret in (
        "PRIVATE_TOKEN", "PRIVATE_USER_MESSAGE", "PRIVATE_REMOTE_BODY", "PRIVATE_EXCEPTION",
    ))


async def test_websocket_receive_timeout_allows_quiet_qq_heartbeat(framework, monkeypatch):
    from nonebot.drivers import aiohttp as aiohttp_driver

    driver, qq_adapter, _ = framework
    original_api_timeout = driver.config.api_timeout
    observed = {}
    raw_websocket = object()

    class FakeSession:
        def __init__(self, **kwargs):
            observed["session"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            observed["closed"] = True

        @asynccontextmanager
        async def ws_connect(self, url, **kwargs):
            observed["url"] = url
            observed["ws"] = kwargs
            yield raw_websocket

    monkeypatch.setattr(aiohttp_driver.aiohttp, "ClientSession", FakeSession)
    setup = Request("GET", "wss://gateway.invalid/offline", timeout=30.0,
                    headers={"X-Offline": "local"})
    async with qq_adapter.websocket(setup) as connection:
        assert connection.websocket is raw_websocket
        assert connection.request.timeout.read == 120
        assert observed["ws"]["timeout"].ws_receive == 120
        assert observed["ws"]["timeout"].ws_close == 5
        assert observed["ws"]["headers"]["X-Offline"] == "local"
        assert observed["url"] == setup.url
    assert observed["closed"] is True
    assert setup.timeout == 30.0  # Do not mutate requests shared by the SDK.
    assert driver.config.api_timeout == original_api_timeout

    # The same adapter's HTTP path is not rerouted through the WS timeout fix.
    http_request = Request("GET", "https://api.invalid/offline", timeout=12.0)
    http = AsyncMock(return_value=Response(200, content=b"{}"))
    monkeypatch.setattr(driver, "request", http)
    await qq_adapter.request(http_request)
    http.assert_awaited_once_with(http_request)
    assert http_request.timeout == 12.0


def test_configuration_uses_group_c2c_and_channel_dm_intents(framework):
    driver, qq_adapter, _ = framework
    assert driver.config.driver == "~aiohttp"
    assert qq_adapter.qq_config.qq_is_sandbox is True
    info = qq_adapter.qq_config.qq_bots[0]
    assert info.intent.to_int() == (1 << 25) | (1 << 12) | (1 << 30)
    assert info.token == "" and info.use_websocket is True


async def test_channel_real_matcher_routes_persistent_signin(framework, tmp_path, monkeypatch):
    from test_command_menu import channel_event

    from hd2bot.services.checkin import CheckinService
    from hd2bot.storage.database import Database

    _, _, bot = framework
    async with Database(tmp_path / "bot.db") as db:
        router = CommandRouter(None, checkin=CheckinService(db))
        dispatcher = QQDispatcher(Settings(), router)
        nonebot.get_plugin_by_module_name("hd2bot.qq.plugin").module.configure(dispatcher)
        send = AsyncMock()
        monkeypatch.setattr(bot, "post_messages", send)
        await bot.handle_event(channel_event("签到"))
        assert "签到成功" in send.await_args.kwargs["markdown"].content
        assert send.await_args.kwargs["keyboard"].content.rows[0].buttons[0].render_data.label == "我也要签到"
        assert send.await_args.kwargs["channel_id"] == "channel"
        await dispatcher.close()


async def test_missing_credentials_fail_before_connecting():
    with pytest.raises(ValueError, match="--cli"):
        await run_qq(Settings(), AsyncMock())


async def test_webhook_is_not_silently_started(settings):
    with pytest.raises(ValueError, match="websocket"):
        await run_qq(replace(settings, qq_transport="webhook"), AsyncMock())


async def test_channel_dm_real_matcher_is_registered(framework, monkeypatch):
    from test_channel_dm import event as channel_dm_event

    _, _, real_bot = framework
    bot_send = AsyncMock()
    router = AsyncMock()
    router.respond.return_value = CommandReply("已接入")
    dispatcher = QQDispatcher(Settings(), router)
    nonebot.get_plugin_by_module_name("hd2bot.qq.plugin").module.configure(dispatcher)
    monkeypatch.setattr(real_bot, "post_dms_messages", bot_send)
    await real_bot.handle_event(channel_dm_event())
    router.respond.assert_awaited_once_with("帮助", context=ChatContext("dms", "dm-session", "guild-user"))
    bot_send.assert_awaited_once()
    await dispatcher.close()


@pytest.mark.parametrize("image", [False, True])
async def test_sdk_serializes_channel_dm_endpoint_and_payload(framework, monkeypatch, image):
    from test_channel_dm import event as channel_dm_event

    _, adapter, real_bot = framework
    requests = []

    async def request(req):
        requests.append(req)
        body = {"id": "reply", "guild_id": "dm-session", "channel_id": "dm-channel",
                "author": {"id": "bot", "bot": True}}
        return Response(200, content=json.dumps(body).encode())

    monkeypatch.setattr(adapter, "request", request)
    monkeypatch.setattr(real_bot, "get_access_token", AsyncMock(return_value="SYNTHETIC_ACCESS_TOKEN"))
    router, renderer = AsyncMock(), AsyncMock()
    router.respond.return_value = CommandReply("频道私信文字", object() if image else None)
    renderer.render.return_value = b"image-data"
    dispatcher = QQDispatcher(Settings(), router, renderer)
    await dispatcher.process(channel_dm_event(), real_bot)
    assert len(requests) == 1
    assert requests[0].url.path == "/dms/dm-session/messages"
    if image:
        assert dict(requests[0].files)["file_image"][1] == b"image-data"
        assert requests[0].data["msg_id"] == "dm-message"
    else:
        assert requests[0].json == {"content": "频道私信文字", "msg_id": "dm-message"}
    await dispatcher.close()


@pytest.mark.parametrize("scope", ["group", "c2c"])
def test_gateway_diagnostic_records_only_event_kind(scope, caplog):
    secret = "HD2v1:SYNTHETIC_PRIVATE_CAPSULE"
    with caplog.at_level(logging.INFO, logger="hd2bot.qq.adapter"):
        make_event(scope, message_id="PRIVATE_MESSAGE_ID", content=secret)
    assert "event=qq_gateway_event" in caplog.text
    assert ("GROUP_AT_MESSAGE_CREATE" if scope == "group" else "C2C_MESSAGE_CREATE") in caplog.text
    assert secret not in caplog.text and "PRIVATE_MESSAGE_ID" not in caplog.text


@pytest.mark.parametrize("scope", ["group", "c2c"])
async def test_real_notice_matcher_sends_added_welcome(framework, monkeypatch, tmp_path, scope):
    from test_welcome_qr import added

    from hd2bot.qq.welcome import WelcomeStore

    _, _, real_bot = framework
    dispatcher = QQDispatcher(Settings(), AsyncMock(),
                              welcome_store=WelcomeStore(tmp_path / "welcome.db", "app"))
    nonebot.get_plugin_by_module_name("hd2bot.qq.plugin").module.configure(dispatcher)
    send = AsyncMock()
    monkeypatch.setattr(real_bot, "post_group_messages" if scope == "group" else "post_c2c_messages", send)
    await real_bot.handle_event(added(scope))
    send.assert_awaited_once()
    assert send.await_args.kwargs["event_id"] == "added-event"
    assert "战绩" in send.await_args.kwargs["content"]
    await dispatcher.close()
