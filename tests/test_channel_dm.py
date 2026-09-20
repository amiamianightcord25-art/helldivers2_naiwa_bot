"""Channel private messages must use guild-DM events and reply APIs, not C2C."""
from unittest.mock import AsyncMock

from nonebot.adapters.qq.models import Dispatch
from test_wiki_service import equipment, service_for

from hd2bot.config import Settings
from hd2bot.presentation import ChatContext, CommandReply
from hd2bot.qq.adapter import QQAdapter, QQDispatcher
from hd2bot.router import CommandRouter


def event(content="帮助", *, message_id="dm-message", user="guild-user", guild="dm-session", bot=False):
    return QQAdapter.payload_to_event(Dispatch.model_validate({
        "op": 0, "s": 1, "id": "event-" + message_id, "t": "DIRECT_MESSAGE_CREATE",
        "d": {"id": message_id, "guild_id": guild, "channel_id": "dm-channel",
              "src_guild_id": "source-community-not-dm-session", "content": content,
              "author": {"id": user, "username": "Test User", "bot": bot},
              "timestamp": "2026-09-19T01:05:00Z"},
    }))


async def test_channel_dm_help_replies_to_virtual_dm_guild():
    bot = AsyncMock()
    dispatcher = QQDispatcher(Settings(image_enabled=False), CommandRouter(None))
    await dispatcher.process(event(), bot)
    reply = bot.post_dms_messages.await_args.kwargs
    assert reply["guild_id"] == "dm-session" and reply["msg_id"] == "dm-message"
    bodies = [call.kwargs.get("content", "")
              + getattr(call.kwargs.get("markdown"), "content", "")
              for call in bot.post_dms_messages.await_args_list]
    assert "战绩" in "\n".join(bodies)
    assert reply["keyboard"].content.rows[0].buttons[0].action.data == "菜单"
    assert not {"openid", "group_openid", "msg_seq", "msg_type"} & reply.keys()
    bot.post_c2c_messages.assert_not_awaited()
    bot.post_group_messages.assert_not_awaited()
    await dispatcher.close()


async def test_channel_dm_image_uses_direct_multipart_upload():
    bot, router, renderer = AsyncMock(), AsyncMock(), AsyncMock()
    card = object()
    router.respond.return_value = CommandReply("fallback", card)
    renderer.render.return_value = b"jpeg-bytes"
    dispatcher = QQDispatcher(Settings(), router, renderer)
    await dispatcher.process(event(), bot)
    bot.post_dms_messages.assert_awaited_once_with(
        guild_id="dm-session", msg_id="dm-message", file_image=b"jpeg-bytes")
    bot.post_c2c_files.assert_not_awaited()
    bot.post_group_files.assert_not_awaited()
    await dispatcher.close()


async def test_channel_dm_snapshot_id_is_sent_as_copyable_text_after_image():
    bot, router, renderer = AsyncMock(), AsyncMock(), AsyncMock()
    router.respond.return_value = CommandReply(
        "战绩文本\n快照 ID：HD2-ABCDEFGH2345\n群聊可用：战绩 HD2-ABCDEFGH2345", object()
    )
    renderer.render.return_value = b"jpeg-bytes"
    dispatcher = QQDispatcher(Settings(), router, renderer)
    await dispatcher.process(event(), bot)
    assert bot.post_dms_messages.await_count == 2
    assert bot.post_dms_messages.await_args_list[1].kwargs["content"] == (
        "快照 ID：HD2-ABCDEFGH2345\n群聊可用：战绩 HD2-ABCDEFGH2345"
    )
    await dispatcher.close()


async def test_channel_dm_image_failure_falls_back_to_own_text_api():
    bot, router, renderer = AsyncMock(), AsyncMock(), AsyncMock()
    router.respond.return_value = CommandReply("文字回退", object())
    renderer.render.side_effect = RuntimeError("synthetic-render-failure")
    dispatcher = QQDispatcher(Settings(), router, renderer)
    await dispatcher.process(event(), bot)
    assert bot.post_dms_messages.await_args.kwargs["content"] == "文字回退"
    await dispatcher.close()


async def test_channel_dm_challenge_and_revocation_use_distinct_identity_namespace():
    service = AsyncMock()
    service.challenge.return_value = {"challenge": "AB3K9M"}
    router = CommandRouter(None, service)
    context = ChatContext("dms", "dm-session", "same-string-as-c2c")
    reply = await router.respond("/获取验证码", context=context)
    assert "AB3K9M" in reply.text
    service.challenge.assert_awaited_once_with("qqdms:same-string-as-c2c")
    assert context.career_user_id != ChatContext("c2c", "u", "same-string-as-c2c").career_user_id
    await router.respond("解绑", context=context)
    service.unbind.assert_awaited_once_with("qqdms:same-string-as-c2c")


async def test_channel_dm_snapshot_id_query_uses_private_transport():
    from test_snapshot_sharing import stats
    service = AsyncMock()
    service.query_shared.return_value = stats()
    router = CommandRouter(None, service)
    code = "HD2-ABCDEFGH2345"
    await router.respond("战绩 " + code, context=ChatContext("dms", "session", "u"))
    service.query_shared.assert_awaited_once_with(code, "qqdms:u", "private")


def test_channel_dm_catalog_selection_isolated_from_c2c_and_other_senders(tmp_path):
    catalog = service_for(tmp_path, [equipment(i, aliases=["公共名称"]) for i in (1, 2)])
    context = ChatContext("dms", "session", "u")
    assert "共 2 项" in catalog.handle("武器", "公共名称", context=context).text
    for other in (ChatContext("dms", "session", "other"), ChatContext("c2c", "session", "u")):
        assert "没有可用的候选" in catalog.handle("选择", "1", context=other).text
    assert "伤害" in catalog.handle("选择", "1", context=context).text


async def test_channel_dm_duplicate_and_bot_messages_do_not_loop():
    bot, router = AsyncMock(), AsyncMock()
    router.respond.return_value = CommandReply("ok")
    dispatcher = QQDispatcher(Settings(), router)
    await dispatcher.process(event(bot=True), bot)
    router.respond.assert_not_awaited()
    await dispatcher.process(event(), bot)
    await dispatcher.process(event(), bot)
    router.respond.assert_awaited_once()
    await dispatcher.close()


async def test_channel_dm_subscription_guides_to_supported_entry():
    notifications = AsyncMock()
    reply = await CommandRouter(None, notifications=notifications).respond(
        "订阅 主线", context=ChatContext("dms", "session", "u"))
    assert "普通 QQ 单聊" in reply.text
    notifications.handle_command.assert_not_awaited()
