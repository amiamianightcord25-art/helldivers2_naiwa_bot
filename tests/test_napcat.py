"""NapCat transport boundaries using real OneBot event/message models."""

import asyncio
import logging
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from nonebot.adapters.onebot.v11 import Adapter
from nonebot.adapters.onebot.v11.exception import NetworkError

from hd2bot.config import Settings, validate_bot_settings
from hd2bot.napcat.adapter import (
    NapCatDispatcher,
    addressed_message,
    chat_context,
    plain_reply,
    watch_connection,
)
from hd2bot.presentation import ChatContext, CommandButton, CommandReply
from hd2bot.router import CommandRouter
from hd2bot.storage.database import Database

TOKEN = "SYNTHETIC_NAPCAT_TOKEN"


def settings(**kwargs):
    return Settings(bot_backend="napcat", napcat_ws_url="ws://127.0.0.1:3001",
                    napcat_access_token=TOKEN, **kwargs)


def event(scope="group", *, content="帮助", at=True, user=20002, group=30003,
          message_id=4, self_id=10001, sub_type=None, segments=None):
    message = segments if segments is not None else (
        ([{"type": "at", "data": {"qq": str(self_id)}}] if scope == "group" and at else [])
        + [{"type": "text", "data": {"text": content}}])
    payload = dict(time=1, self_id=self_id, post_type="message",
                   message_type=scope, sub_type=sub_type or ("normal" if scope == "group" else "friend"),
                   message_id=message_id, user_id=user, message=message, raw_message=content,
                   font=0, sender={"user_id": user, "nickname": "测试昵称", "role": "member"})
    if scope == "group":
        payload["group_id"] = group
    return Adapter.json_to_event(payload)


def bot():
    result = AsyncMock()
    result.self_id = "10001"
    return result


@pytest.mark.parametrize("scope", ["private", "group"])
async def test_reply_uses_only_event_target_and_text_segments(scope):
    api, router = bot(), AsyncMock()
    router.respond.return_value = CommandReply("[CQ:at,qq=all] literal output")
    dispatcher = NapCatDispatcher(settings(image_enabled=False), router)
    await dispatcher.process(event(scope, content="帮助 99999"), api)
    send = api.send_group_msg if scope == "group" else api.send_private_msg
    send.assert_awaited_once()
    payload = send.await_args.kwargs
    assert payload["group_id" if scope == "group" else "user_id"] == (30003 if scope == "group" else 20002)
    assert [seg.type for seg in payload["message"]] == ["text"]
    assert payload["message"][0].data["text"] == "[CQ:at,qq=all] literal output"
    context = router.respond.await_args.kwargs["context"]
    assert context.target_id == "onebot:10001:" + str(30003 if scope == "group" else 20002)
    assert context.user_id == "onebot:10001:20002"
    await dispatcher.close()


@pytest.mark.parametrize("ignored", [
    event(at=False), event(user=10001), event("private", user=10001),
    event("private", sub_type="group"),
    event(segments=[{"type": "text", "data": {"text": "@机器人 帮助"}}]),
    event(segments=[{"type": "at", "data": {"qq": "all"}},
                    {"type": "text", "data": {"text": "帮助"}}]),
    event(segments=[{"type": "at", "data": {"qq": "10001"}},
                    {"type": "image", "data": {"file": "unused"}},
                    {"type": "text", "data": {"text": "帮助"}}]),
])
async def test_ignores_self_unaddressed_temporary_private_and_attachments(ignored):
    router, api = AsyncMock(), bot()
    dispatcher = NapCatDispatcher(settings(), router)
    await dispatcher.process(ignored, api)
    router.respond.assert_not_awaited()
    assert api.mock_calls == []


async def test_quotes_do_not_substitute_for_explicit_group_mention():
    row = event(at=False)
    row.to_me = True
    assert not addressed_message(row)


async def test_allowlist_and_wrong_account_drop_before_dispatch():
    router, api = AsyncMock(), bot()
    dispatcher = NapCatDispatcher(settings(napcat_allowed_groups=("30003",)), router)
    await dispatcher.process(event(group=99999), api)
    await dispatcher.process(event(self_id=99999), api)
    router.respond.assert_not_awaited()


async def test_duplicate_events_after_reconnect_do_not_repeat_replies():
    api, router = bot(), AsyncMock()
    router.respond.return_value = CommandReply("OK")
    dispatcher = NapCatDispatcher(settings(), router)
    row = event()
    dispatcher.connected_bot(api)
    await dispatcher.process(row, api)
    dispatcher.disconnected_bot(api)
    dispatcher.connected_bot(api)
    await dispatcher.process(row, api)
    api.send_group_msg.assert_awaited_once()
    # Message identifiers in different chats are independent.
    await dispatcher.process(event(group=30004), api)
    assert api.send_group_msg.await_count == 2


@pytest.mark.parametrize("scope", ["private", "group"])
async def test_images_use_base64_not_server_paths(scope):
    api, router, renderer = bot(), AsyncMock(), AsyncMock()
    renderer.render.return_value = b"synthetic-jpeg"
    router.respond.return_value = CommandReply("完整文本", object(), (
        (CommandButton("下一页", "下一页"),),))
    dispatcher = NapCatDispatcher(settings(), router, renderer)
    await dispatcher.process(event(scope), api)
    send = api.send_group_msg if scope == "group" else api.send_private_msg
    message = send.await_args.kwargs["message"]
    assert message[0].type == "image"
    assert message[0].data["file"].startswith("base64://")
    assert "下一页" in message.extract_plain_text()
    assert send.await_count == 1


async def test_renderer_failure_falls_back_but_send_failure_never_retries(caplog):
    api, router, renderer = bot(), AsyncMock(), AsyncMock()
    renderer.render.side_effect = ValueError("SYNTHETIC_PRIVATE_DETAIL")
    router.respond.return_value = CommandReply("完整文字备用", object())
    dispatcher = NapCatDispatcher(settings(), router, renderer)
    with caplog.at_level(logging.WARNING):
        await dispatcher.process(event(), api)
    assert api.send_group_msg.await_args.kwargs["message"][0].type == "text"
    assert "SYNTHETIC_PRIVATE_DETAIL" not in caplog.text
    api.send_group_msg.reset_mock()
    renderer.render.side_effect = None
    renderer.render.return_value = b"image"
    api.send_group_msg.side_effect = NetworkError("SYNTHETIC_ENDPOINT_AND_TOKEN")
    with caplog.at_level(logging.WARNING):
        await dispatcher.process(event(message_id=5), api)
    api.send_group_msg.assert_awaited_once()
    assert "SYNTHETIC_ENDPOINT_AND_TOKEN" not in caplog.text


async def test_long_reply_stops_on_first_ambiguous_send_failure():
    api, router = bot(), AsyncMock()
    router.respond.return_value = CommandReply("很长的文字" * 2000)
    api.send_private_msg.side_effect = NetworkError("unknown")
    dispatcher = NapCatDispatcher(settings(), router)
    await dispatcher.process(event("private"), api)
    api.send_private_msg.assert_awaited_once()


def test_buttons_become_text_instructions_and_retired_commands_are_removed():
    context = ChatContext("group", "onebot:1:2", "onebot:1:3")
    reply = CommandReply("点击下方按钮，或直接发送对应指令：", keyboard=((
        CommandButton("更多", "下一页", ("onebot:1:3",)),
        CommandButton("别人的按钮", "上一页", ("onebot:1:4",)),
        CommandButton("下线", "获取战绩"),
    ),))
    text = plain_reply(reply, context)
    assert "按钮" not in text and "下一页" in text and "上一页" not in text and "获取战绩" not in text


async def test_retired_features_never_reach_backends_over_napcat():
    career, notifications = AsyncMock(), AsyncMock()
    router = CommandRouter(None, career, notifications, career_enabled=False, proactive_enabled=False)
    api = bot()
    dispatcher = NapCatDispatcher(settings(), router)
    for index, command in enumerate(("战绩", "获取验证码", "订阅 主线", "HD2v1:" + "A" * 160)):
        await dispatcher.process(event("private", content=command, message_id=index), api)
        assert api.send_private_msg.await_args.kwargs["message"].extract_plain_text() == "该功能当前已下线。"
    assert career.mock_calls == notifications.mock_calls == []


async def test_checkin_state_isolated_from_official_ids_and_between_login_accounts(tmp_path):
    from hd2bot.services.checkin import CheckinService

    async with Database(tmp_path / "test.db") as db:
        checkin = CheckinService(db)
        one = chat_context(event("private"))
        two = chat_context(event("private", self_id=10002))
        official = ChatContext("c2c", "20002", "20002")
        await checkin.check_in(one)
        assert (await checkin.profile(one)).total_days == 1
        assert (await checkin.profile(two)).total_days == 0
        assert (await checkin.profile(official)).total_days == 0


async def test_close_cancels_pending_request_without_sending():
    api, router = bot(), AsyncMock()
    entered = asyncio.Event()
    async def hold(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()
    router.respond.side_effect = hold
    dispatcher = NapCatDispatcher(settings(), router)
    pending = asyncio.create_task(dispatcher.process(event(), api))
    await entered.wait()
    await dispatcher.close()
    assert pending.cancelled()
    assert not dispatcher._requests
    api.send_group_msg.assert_not_awaited()


async def test_health_requires_online_status_and_exits_after_repeated_failure(monkeypatch):
    import hd2bot.napcat.adapter as module

    ready = Mock()
    monkeypatch.setattr(module, "notify_ready", ready)
    dispatcher = NapCatDispatcher(settings(), AsyncMock())
    api = bot()
    api.get_status.return_value = {"online": False, "good": False}
    dispatcher.connected_bot(api)
    with pytest.raises(ConnectionError):
        await watch_connection(dispatcher, interval=0.001, max_failures=2)
    assert api.get_status.await_count == 2
    ready.assert_not_called()


async def test_reconnect_restores_ready_and_missing_connection_times_out(monkeypatch):
    import hd2bot.napcat.adapter as module

    ready = asyncio.Event()
    monkeypatch.setattr(module, "notify_ready", ready.set)
    dispatcher = NapCatDispatcher(settings(), AsyncMock())
    api = bot()
    api.get_status.return_value = {"online": True, "good": True}
    dispatcher.connected_bot(api)
    task = asyncio.create_task(watch_connection(dispatcher, interval=0.005, connect_timeout=0.1))
    await asyncio.wait_for(ready.wait(), 1)
    ready.clear()
    dispatcher.disconnected_bot(api)
    dispatcher.connected_bot(api)
    await asyncio.wait_for(ready.wait(), 1)
    dispatcher.disconnected_bot(api)
    with pytest.raises(TimeoutError):
        await task


def test_napcat_does_not_require_official_credentials():
    validate_bot_settings(settings())
    assert not settings().qq_app_secret
    assert TOKEN not in repr(settings())
    with pytest.raises(ValueError, match="NAPCAT"):
        validate_bot_settings(replace(settings(), napcat_access_token=""))


@pytest.mark.parametrize("value", ["https://example.invalid", "ws://user:pass@example.invalid",
                                  "ws://localhost:0", "ws://localhost:99999",
                                  "ws://localhost?token=secret", "ws://localhost/#secret", "ws://bad host"])
def test_invalid_url_rejected_without_echoing_value(tmp_path, monkeypatch, value):
    monkeypatch.setenv("BOT_BACKEND", "napcat")
    monkeypatch.setenv("NAPCAT_WS_URL", value)
    with pytest.raises(ValueError) as error:
        Settings.load(tmp_path)
    assert value not in str(error.value)


def test_environment_loading_and_group_allowlist(tmp_path, monkeypatch):
    monkeypatch.setenv("BOT_BACKEND", "napcat")
    monkeypatch.setenv("NAPCAT_WS_URL", "ws://127.0.0.1:3001")
    monkeypatch.setenv("NAPCAT_ACCESS_TOKEN", TOKEN)
    monkeypatch.setenv("NAPCAT_ALLOWED_GROUPS", "30003, 30004")
    loaded = Settings.load(tmp_path)
    assert loaded.napcat_allowed_groups == ("30003", "30004")
    validate_bot_settings(loaded)
    monkeypatch.setenv("NAPCAT_ALLOWED_GROUPS", "not-a-group")
    with pytest.raises(ValueError, match="NAPCAT_ALLOWED_GROUPS"):
        Settings.load(tmp_path)


def test_full_nonebot_onebot_loopback_in_isolated_process(tmp_path):
    # NoneBot has process-global plugin state; never mix two live drivers in a test process.
    harness = Path(__file__).with_name("napcat_loopback.py")
    result = subprocess.run([sys.executable, "-X", "utf8", str(harness), str(tmp_path)],
                            capture_output=True, text=True, encoding="utf-8", timeout=25)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "NAPCAT_LOOPBACK_OK" in result.stdout
    assert TOKEN not in result.stdout + result.stderr


async def test_native_ui_does_not_use_old_official_credentials_in_napcat_mode():
    from hd2bot.qq.native_ui import NativeUIClient, NativeUIError

    with pytest.raises(NativeUIError, match="NapCat"):
        async with NativeUIClient(settings(qq_app_id="test-app", qq_app_secret="test-secret")):
            pytest.fail("official client must not open")


def test_napcat_images_ignore_legacy_official_qr(monkeypatch, tmp_path):
    from PIL import Image

    import hd2bot.rendering.renderer as module

    assets = tmp_path / "assets"
    assets.mkdir()
    Image.new("RGB", (40, 40), "white").save(assets / "qq_experience_qr.png")
    monkeypatch.setattr(module, "files", lambda name: tmp_path)
    renderer = module.HtmlRenderer(include_qr=False)
    assert renderer._qr_data == ""


async def test_backend_selection_loads_only_selected_runner(monkeypatch):
    import types

    from hd2bot.transports import run_bot

    official, personal = AsyncMock(return_value=11), AsyncMock(return_value=12)
    monkeypatch.setitem(sys.modules, "hd2bot.qq.adapter", types.SimpleNamespace(run_qq=official))
    monkeypatch.setitem(sys.modules, "hd2bot.napcat.adapter", types.SimpleNamespace(run_napcat=personal))
    assert await run_bot(settings(), object()) == 12
    official.assert_not_awaited()
    assert await run_bot(Settings(), object()) == 11
    official.assert_awaited_once()


def test_publish_guard_rejects_napcat_runtime_but_allows_source():
    import runpy

    check = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/check_publish.py"))
    for name in ("napcat/config/settings.json", "NapCat/config/session.json",
                 "napcat-data/session.json", "qq-data/session.json", "onebot11_example.json"):
        assert check["private_path"](name)
    assert not check["private_path"]("src/hd2bot/napcat/adapter.py")


def test_log_filter_removes_napcat_url_and_access_token(tmp_path, capsys, monkeypatch):
    from hd2bot.logging_setup import configure_logging

    configured = {}
    monkeypatch.setattr(logging, "basicConfig", lambda **kwargs: configured.update(kwargs))
    value = settings(log_dir=tmp_path)
    configure_logging(value)
    try:
        record = logging.LogRecord("transport", logging.WARNING, "", 0,
                                   "connection %s credential %s",
                                   (value.napcat_ws_url, value.napcat_access_token), None)
        configured["handlers"][0].handle(record)
        captured = capsys.readouterr()
        assert value.napcat_ws_url not in captured.err and TOKEN not in captured.err
        assert "<REDACTED>" in captured.err
    finally:
        for handler in configured["handlers"]:
            handler.close()
