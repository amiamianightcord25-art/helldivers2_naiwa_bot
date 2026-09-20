import json
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from nonebot.adapters.qq.exception import ActionFailed, NetworkError
from nonebot.adapters.qq.models import Dispatch
from nonebot.drivers import Response

from hd2bot.config import Settings
from hd2bot.presentation import ChatContext, CommandReply
from hd2bot.qq.adapter import QQAdapter, QQDispatcher
from hd2bot.router import CommandRouter
from hd2bot.services.menu import DEFAULT_PATH, MenuService


@pytest.mark.parametrize("scope", ["c2c", "group", "channel", "dms"])
async def test_menu_all_scenes_has_command_buttons_and_text(scope):
    result = await CommandRouter(None).respond(
        "／指令面板", context=ChatContext(scope, "target", "member"),
    )
    assert "签到" in result.text and "战备英雄" in result.text
    assert any(button.command == "签到" for row in result.keyboard for button in row)
    dispatcher = QQDispatcher(Settings(image_enabled=False), CommandRouter(None))
    bot = AsyncMock()
    await dispatcher._reply(bot, scope, "target", "message", result)
    name, destination = {
        "c2c": ("post_c2c_messages", "openid"),
        "group": ("post_group_messages", "group_openid"),
        "channel": ("post_messages", "channel_id"),
        "dms": ("post_dms_messages", "guild_id"),
    }[scope]
    payload = getattr(bot, name).await_args.kwargs
    assert payload[destination] == "target" and payload["msg_id"] == "message"
    first = payload["keyboard"].content.rows[0].buttons[0]
    assert first.action.type == 2 and first.action.data == "签到"
    assert first.action.enter == (scope == "c2c")
    assert "签到" in payload["markdown"].content
    if scope in {"group", "c2c"}:
        assert payload["msg_type"] == 2
    else:
        assert "msg_type" not in payload
    await dispatcher.close()


async def test_keyboard_rejection_falls_back_to_text_with_new_sequence():
    bot = AsyncMock()
    bot.post_group_messages.side_effect = [
        ActionFailed(Response(400, content=b'{"code":304036}')), None,
    ]
    dispatcher = QQDispatcher(Settings(), CommandRouter(None))
    reply = MenuService().reply()
    await dispatcher._reply(bot, "group", "g", "m", reply)
    assert bot.post_group_messages.await_count == 2
    fallback = bot.post_group_messages.await_args.kwargs
    assert fallback["content"] == reply.text
    assert fallback["msg_seq"] == 2 and fallback["msg_type"] == 0
    await dispatcher.close()


async def test_keyboard_unknown_outcome_does_not_duplicate_reply():
    bot = AsyncMock()
    bot.post_c2c_messages.side_effect = NetworkError("unknown outcome")
    dispatcher = QQDispatcher(Settings(), CommandRouter(None))
    with pytest.raises(NetworkError):
        await dispatcher._reply(bot, "c2c", "u", "m", MenuService().reply())
    bot.post_c2c_messages.assert_awaited_once()
    await dispatcher.close()


def test_custom_menu_hot_load_and_invalid_file_preserves_last_valid(tmp_path):
    path = tmp_path / "menu.json"
    menu = MenuService(path)
    data = json.loads(DEFAULT_PATH.read_text(encoding="utf-8"))
    data["pages"]["首页"]["title"] = "测试自定义菜单"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert "测试自定义菜单" in menu.reply().text
    path.write_text('{"schema": 1, "pages": null}', encoding="utf-8")
    assert "测试自定义菜单" in menu.reply().text


def channel_event(content="菜单"):
    return QQAdapter.payload_to_event(Dispatch.model_validate({
        "op": 0, "s": 1, "t": "AT_MESSAGE_CREATE", "id": "event-channel",
        "d": {"id": "channel-msg", "guild_id": "guild", "channel_id": "channel",
              "content": content, "author": {"id": "user", "username": "战友", "bot": False},
              "timestamp": "2026-09-19T08:00:00Z"},
    }))


async def test_channel_event_uses_channel_identity_and_reply_endpoint():
    bot, router = AsyncMock(), AsyncMock()
    router.respond.return_value = CommandReply("频道文字")
    dispatcher = QQDispatcher(Settings(), router)
    await dispatcher.process(channel_event("帮助"), bot)
    context = router.respond.await_args.kwargs["context"]
    assert context.scope == "channel" and context.target_id == "channel"
    assert context.user_id == "user" and context.display_name == "战友"
    assert not context.is_private
    bot.post_messages.assert_awaited_once_with(
        channel_id="channel", msg_id="channel-msg", content="频道文字",
    )
    bot.post_dms_messages.assert_not_awaited()
    bot.post_c2c_messages.assert_not_awaited()
    await dispatcher.close()


async def test_channel_uses_public_career_rules():
    career = AsyncMock()
    reply = await CommandRouter(None, career).respond(
        "获取验证码", context=ChatContext("channel", "room", "user"),
    )
    assert "私聊" in reply.text
    assert career.mock_calls == []


async def test_welcome_preserves_menu_buttons():
    from test_qq import make_event

    bot, store = AsyncMock(), AsyncMock()
    store.claim_first_message.return_value = True
    dispatcher = QQDispatcher(Settings(), CommandRouter(None), welcome_store=store)
    await dispatcher.process(make_event(content="菜单"), bot)
    assert bot.post_c2c_messages.await_args.kwargs["keyboard"].content.rows
    await dispatcher.close()


async def test_channel_image_transport():
    bot, router, renderer = AsyncMock(), AsyncMock(), AsyncMock()
    router.respond.return_value = CommandReply("图文", object())
    renderer.render.return_value = b"image"
    dispatcher = QQDispatcher(Settings(), router, renderer)
    await dispatcher.process(channel_event("战况"), bot)
    bot.post_messages.assert_awaited_once_with(
        channel_id="channel", msg_id="channel-msg", file_image=b"image",
    )
    await dispatcher.close()


def test_display_name_does_not_change_identity_comparison():
    context = ChatContext("channel", "room", "user")
    assert replace(context, display_name="新昵称") == context


async def test_shared_checkin_button_awards_the_member_sending_the_command(tmp_path):
    from test_qq import make_event

    from hd2bot.services.checkin import CheckinService
    from hd2bot.storage.database import Database

    async with Database(tmp_path / "bot.db") as db:
        checkin = CheckinService(db)
        dispatcher = QQDispatcher(Settings(), CommandRouter(None, checkin=checkin))
        bot = AsyncMock()
        first = make_event("group", "first-checkin", "签到")
        await dispatcher.process(first, bot)
        payload = bot.post_group_messages.await_args.kwargs
        assert payload["msg_type"] == 2 and "签到成功" in payload["markdown"].content
        button = payload["keyboard"].content.rows[0].buttons[0]
        assert button.render_data.label == "我也要签到"
        assert button.action.permission.type == 2
        assert button.action.data == "签到"

        other = make_event("group", "other-checkin", button.action.data)
        other.author = other.author.model_copy(update={"id": "other-member", "member_openid": "other-member"})
        await dispatcher.process(other, bot)
        await dispatcher.process(make_event("group", "first-again", "我也要签到"), bot)

        assert "已经签到" in bot.post_group_messages.await_args.kwargs["markdown"].content
        profiles = await db.fetch_all("SELECT xp, total_days FROM checkin_profiles ORDER BY user_id")
        assert [(row["xp"], row["total_days"]) for row in profiles] == [(100, 1), (100, 1)]
        await dispatcher.close()
