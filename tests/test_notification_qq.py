"""Subscription context and opt-in delivery through the official QQ boundary."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from nonebot.adapters.qq.exception import ActionFailed, NetworkError
from nonebot.drivers import Response

from hd2bot.config import Settings
from hd2bot.presentation import ChatContext
from hd2bot.qq.adapter import QQDispatcher
from hd2bot.router import CommandRouter
from hd2bot.services.notifications import (
    NotificationManager,
    PermanentPushError,
    RetryablePushError,
    UncertainPushError,
)
from hd2bot.storage.database import Database


async def test_subscription_context_comes_from_platform_not_command_arguments():
    manager = AsyncMock(spec=NotificationManager)
    manager.handle_command.return_value = "订阅成功"
    router = CommandRouter(AsyncMock(), notifications=manager)
    reply = await router.respond("<@123> 订阅 战况 60", context=ChatContext("group", "real-target"))
    manager.handle_command.assert_awaited_once_with("订阅", "战况 60", "group", "real-target")
    assert reply.card is None and reply.text == "订阅成功"
    assert "real-target" not in repr(ChatContext("group", "real-target"))


@pytest.mark.parametrize("command", ["订阅 主线", "订阅列表", "取消订阅"])
async def test_cli_subscription_commands_never_create_a_target(command):
    manager = AsyncMock(spec=NotificationManager)
    reply = await CommandRouter(AsyncMock(), notifications=manager).respond(command)
    assert "QQ" in reply.text and reply.card is None
    manager.handle_command.assert_not_called()


@pytest.mark.parametrize("scope", ["group", "c2c"])
async def test_proactive_sender_uses_no_expired_passive_ids(scope):
    dispatcher = QQDispatcher(Settings(), AsyncMock())
    bot = AsyncMock()
    dispatcher.connected_bot(bot)
    try:
        await dispatcher.send_proactive(scope, "opted-in", "定时摘要")
        api = bot.post_group_messages if scope == "group" else bot.post_c2c_messages
        api.assert_awaited_once_with(**{
            "group_openid" if scope == "group" else "openid": "opted-in",
        }, msg_type=0, content="定时摘要")
        bot.post_c2c_files.assert_not_called()
        bot.post_group_files.assert_not_called()
    finally:
        await dispatcher.close()


@pytest.mark.parametrize("status", [400, 401, 403, 404])
async def test_platform_rejection_is_permanent_and_logs_no_private_body(status, caplog):
    dispatcher = QQDispatcher(Settings(), AsyncMock())
    bot = AsyncMock()
    bot.post_c2c_messages.side_effect = ActionFailed(Response(status, content=json.dumps({
        "code": 1234, "message": "PRIVATE_REJECTION_DETAIL", "data": {"secret": "PRIVATE_TOKEN"},
    }).encode()))
    dispatcher.connected_bot(bot)
    with pytest.raises(PermanentPushError, match="platform_denied"):
        await dispatcher.send_proactive("c2c", "synthetic-private-target", "内容")
    assert all(text not in caplog.text for text in (
        "PRIVATE_REJECTION_DETAIL", "PRIVATE_TOKEN", "synthetic-private-target",
    ))
    await dispatcher.close()


async def test_http_429_remains_retryable_and_logs_no_private_body(caplog):
    dispatcher = QQDispatcher(Settings(), AsyncMock())
    bot = AsyncMock()
    failure = ActionFailed(Response(429, content=json.dumps({
        "code": 1234, "message": "PRIVATE_RATE_LIMIT_DETAIL",
        "data": {"secret": "PRIVATE_TOKEN"},
    }).encode()))
    bot.post_c2c_messages.side_effect = failure
    dispatcher.connected_bot(bot)

    with pytest.raises(RetryablePushError, match="rate_limited"):
        await dispatcher.send_proactive("c2c", "synthetic-private-target", "内容")

    assert all(text not in caplog.text for text in (
        "PRIVATE_RATE_LIMIT_DETAIL", "PRIVATE_TOKEN", "synthetic-private-target",
    ))
    await dispatcher.close()


async def test_disconnected_or_temporary_failure_does_not_become_permanent():
    dispatcher = QQDispatcher(Settings(), AsyncMock())
    with pytest.raises(RuntimeError):
        await dispatcher.send_proactive("c2c", "target", "内容")
    bot = AsyncMock()
    bot.post_c2c_messages.side_effect = NetworkError("private")
    dispatcher.connected_bot(bot)
    with pytest.raises(UncertainPushError, match="outcome_unknown"):
        await dispatcher.send_proactive("c2c", "target", "内容")
    dispatcher.disconnected_bot(bot)
    assert dispatcher._bot is None
    await dispatcher.close()


async def test_explicit_temporary_http_failure_remains_retryable():
    dispatcher = QQDispatcher(Settings(), AsyncMock())
    bot = AsyncMock()
    bot.post_c2c_messages.side_effect = ActionFailed(Response(503, content=b"unavailable"))
    dispatcher.connected_bot(bot)

    with pytest.raises(RetryablePushError, match="http_failure"):
        await dispatcher.send_proactive("c2c", "target", "内容")

    await dispatcher.close()


async def test_connect_without_subscriptions_sends_nothing_and_shutdown_cancels_poll(tmp_path):
    async with Database(tmp_path / "bot.db") as db:
        service = AsyncMock()
        manager = NotificationManager(db, service, poll_interval=0.01)
        dispatcher = QQDispatcher(Settings(), AsyncMock(), notifications=manager)
        bot = AsyncMock()
        dispatcher.connected_bot(bot)
        task = dispatcher._notification_task
        dispatcher.connected_bot(bot)
        assert dispatcher._notification_task is task
        await asyncio.sleep(0.025)
        service.get_war.assert_not_called()
        bot.post_c2c_messages.assert_not_called()
        bot.post_group_messages.assert_not_called()
        await dispatcher.close()
        assert task.cancelled()
