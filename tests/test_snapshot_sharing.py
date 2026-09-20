from unittest.mock import AsyncMock

import pytest
from test_career import response

from hd2bot.career.formatter import format_career
from hd2bot.career.models import SNAPSHOT_RETENTION_SECONDS, CareerError, parse_reply
from hd2bot.commands.parser import parse_command
from hd2bot.presentation import ChatContext, career_card
from hd2bot.rendering.renderer import HtmlRenderer
from hd2bot.router import CommandRouter

CODE = "HD2-ABCDEFGH2345"


def stats():
    payload = response(
        bindingId=CODE,
        shared=True,
        snapshot=True,
        credentialsRetained=False,
        shareId=CODE,
        player={"steamId": "76561198000000000", "displayName": "测试玩家<一号>"},
    )
    payload["expiresAt"] = payload["queriedAt"] + SNAPSHOT_RETENTION_SECONDS
    return parse_reply(payload, "test", CODE)


async def test_group_query_uses_share_not_private_user_lookup():
    service = AsyncMock()
    service.query_shared.return_value = stats()
    result = await CommandRouter(None, service).respond(
        "战绩 " + CODE, context=ChatContext("group", "group-id", "member-different-from-c2c")
    )
    service.query_shared.assert_awaited_once_with(
        CODE, "qqgroup:member-different-from-c2c", "group"
    )
    service.query_user.assert_not_awaited()
    assert result.card.title == "测试玩家<一号>"
    assert "76561198000000000" in result.card.subtitle
    assert CODE in result.text and "当前私聊用户" not in result.text


async def test_group_without_id_gets_instructions():
    service = AsyncMock()
    result = await CommandRouter(None, service).respond(
        "战绩", context=ChatContext("group", "g", "m")
    )
    assert "快照ID" in result.text
    service.query_user.assert_not_awaited()


async def test_sharing_controls_only_private():
    service = AsyncMock()
    service.sharing.return_value = {"shareId": CODE}
    router = CommandRouter(None, service)
    await router.respond("关闭分享", context=ChatContext("group", "g", "m"))
    service.sharing.assert_not_awaited()
    result = await router.respond("分享战绩", context=ChatContext("c2c", "u", "u"))
    service.sharing.assert_awaited_once_with("qq:u", True)
    assert CODE in result.text and "旧 ID 已失效" in result.text


def test_snapshot_parser_and_heading():
    assert parse_command("查战绩 " + CODE.lower()).argument == CODE
    value = stats()
    assert value.account_scope == "shared_snapshot"
    text = format_career(value)
    assert "3 天" in text and "自动删除" in text and CODE in text
    card = career_card(value)
    assert CODE not in " ".join(card.footer)
    assert CODE not in HtmlRenderer().render_html(card)
    assert "仅保留 3 天" in HtmlRenderer().render_html(card)
    assert "到期自动删除" in HtmlRenderer().render_html(card)
    assert "到期时间" in str(card.footer)


def test_invalid_player_or_share_rejected():
    for extra in (
        {"shareId": "../bad"},
        {"player": {"steamId": "not-id", "displayName": "x"}},
        {"player": {"steamId": "76561198000000000", "displayName": "x\n"}},
    ):
        with pytest.raises(CareerError):
            parse_reply(response(**extra), "test", "owner")


@pytest.mark.parametrize("command", ["查询战绩", "/查询战绩", "战绩查询", "查詢戰績", "查戰績", "戰績"])
async def test_all_career_aliases_in_group_with_and_without_snapshot_id(command):
    service = AsyncMock()
    service.query_shared.return_value = stats()
    router = CommandRouter(None, service)
    context = ChatContext("group", "group", "member")
    help_reply = await router.respond(command, context=context)
    assert "快照ID" in help_reply.text and "未识别" not in help_reply.text
    service.query_user.assert_not_awaited()
    service.query_shared.assert_not_awaited()
    result = await router.respond(command + " " + CODE, context=context)
    service.query_shared.assert_awaited_once_with(CODE, "qqgroup:member", "group")
    assert result.card is not None and CODE in result.text
