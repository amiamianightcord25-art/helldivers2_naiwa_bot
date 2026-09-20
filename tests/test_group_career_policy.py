from unittest.mock import AsyncMock

import pytest
from test_qq import make_event
from test_snapshot_sharing import CODE, stats

from hd2bot.config import Settings
from hd2bot.presentation import ChatContext
from hd2bot.qq.adapter import QQDispatcher
from hd2bot.router import CommandRouter

CAPSULE = "HD2v1:" + "A" * 500


@pytest.mark.parametrize("command", [
    "获取战绩", "/下载助手", "同步战绩", "同步帮助", "绑定", "bind",
    "获取验证码", "领取验证码", "准备好了", "已打开助手", "解绑", "unbind",
    "战绩", "查询战绩", "查战绩", "战绩查询", "查詢戰績", "查戰績", "戰績",
    "分享战绩", "关闭分享", CAPSULE, "bind " + CAPSULE,
    "这是我的 " + CAPSULE, "武器 " + CAPSULE, "查询战绩 " + CAPSULE,
    "装饰 " + CAPSULE.lower(),
])
async def test_group_entries_never_call_career_or_echo_capsule(command):
    career, catalog = AsyncMock(), AsyncMock()
    reply = await CommandRouter(None, career, catalog=catalog).respond(
        command, context=ChatContext("group", "g", "m"))
    assert "私聊" in reply.text
    assert "A" * 100 not in reply.text
    assert career.mock_calls == []
    assert catalog.mock_calls == []


@pytest.mark.parametrize("scope,prefix", [("c2c", "qq:"), ("dms", "qqdms:")])
async def test_private_challenge_uses_context_specific_identity(scope, prefix):
    service = AsyncMock()
    service.challenge.return_value = {"challenge": "AB3K9M"}
    result = await CommandRouter(None, service).respond(
        "获取验证码", context=ChatContext(scope, "target", "same-id"))
    service.challenge.assert_awaited_once_with(prefix + "same-id")
    assert "AB3K9M" in result.text


async def test_real_group_event_rejects_capsule_then_queries_only_shared_snapshot():
    career, bot = AsyncMock(), AsyncMock()
    career.query_shared.return_value = stats()
    dispatcher = QQDispatcher(Settings(), CommandRouter(None, career))
    try:
        await dispatcher.process(make_event("group", "capsule", "这串怎么用 " + CAPSULE), bot)
        assert career.mock_calls == []
        first = bot.post_group_messages.await_args.kwargs["content"]
        assert "首次查询" in first and "点击机器人头像" in first
        assert CAPSULE not in first
        bot.post_c2c_messages.assert_not_awaited()
        await dispatcher.process(make_event("group", "snapshot", "查询战绩 " + CODE), bot)
        career.query_shared.assert_awaited_once_with(CODE, "qqgroup:member-local", "group")
        career.query_user.assert_not_awaited()
        career.bind.assert_not_awaited()
        payload = bot.post_group_messages.await_args.kwargs
        assert CODE in payload["markdown"].content
        assert payload["keyboard"].content.rows[0].buttons[0].render_data.label == "我也要查询"
    finally:
        await dispatcher.close()
