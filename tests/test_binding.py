import logging
from unittest.mock import AsyncMock

import pytest
from test_career import TOKEN, response, ws_server

from hd2bot.career.client import CareerClient, ConnectionConfig
from hd2bot.career.models import CareerError
from hd2bot.commands.parser import parse_command
from hd2bot.logging_setup import SensitiveFilter
from hd2bot.presentation import ChatContext
from hd2bot.router import CommandRouter

CODE = "HD2v1:" + "A" * 500


@pytest.mark.parametrize("prefix", ["bind", "BIND", "绑定", "/bind"])
def test_bind_aliases(prefix):
    result = parse_command(prefix + " " + CODE)
    assert result.name == "绑定" and result.argument == CODE


async def test_group_does_not_forward_or_echo_binding_parameter():
    service = AsyncMock()
    router = CommandRouter(None, service)
    reply = await router.respond("bind " + CODE, context=ChatContext("group", "g", "u"))
    assert "私聊" in reply.text and CODE not in reply.text
    service.bind.assert_not_awaited()


async def test_c2c_binds_authenticated_sender():
    from hd2bot.career.models import parse_reply

    service = AsyncMock()
    service.query_user.return_value = parse_reply(response(), "test", "owner")
    router = CommandRouter(None, service)
    result = await router.respond(
        "绑定 " + CODE, context=ChatContext("c2c", "real-user", "real-user")
    )
    service.bind.assert_awaited_once_with("qq:real-user", CODE)
    assert "同步成功" in result.text and CODE not in result.text


async def test_unbind_uses_sender_no_target_argument():
    service = AsyncMock()
    router = CommandRouter(None, service)
    result = await router.respond("unbind", context=ChatContext("c2c", "u", "u"))
    service.unbind.assert_awaited_once_with("qq:u")
    assert "已解绑" in result.text


async def test_wire_identity_and_envelope_are_separate_and_replies_checked():
    async def reply(ws, payload):
        await ws.send_json(
            {
                "requestId": payload["requestId"],
                "ok": True,
                "status": "bound",
                "snapshot": True,
                "credentialsRetained": False,
            }
        )

    async with ws_server(reply) as (url, connections, requests):
        client = CareerClient(ConnectionConfig(url, TOKEN))
        try:
            await client.request_user("binding.import", "qq:real-user", CODE)
            assert requests[0]["chatType"] == "private"
            assert requests[0]["userId"] == "qq:real-user" and requests[0]["envelope"] == CODE
        finally:
            await client.close()


async def test_two_users_query_their_own_binding_without_global_owner_cache():
    async def reply(ws, payload):
        n = 1 if payload["userId"] == "qq:a" else 2
        result = response(payload["requestId"], bindingId="b_" + str(n) * 24)
        result["career"]["totalDeaths"] = n
        await ws.send_json(result)

    async with ws_server(reply) as (url, connections, requests):
        client = CareerClient(ConnectionConfig(url, TOKEN))
        try:
            a = await client.request_user("career.query", "qq:a")
            b = await client.request_user("career.query", "qq:b")
            assert a.values["totalDeaths"] == 1 and b.values["totalDeaths"] == 2
            assert all("bindingId" not in r for r in requests)
        finally:
            await client.close()


async def test_unknown_backend_message_not_exposed():
    async def reply(ws, payload):
        await ws.send_json({"requestId": payload["requestId"], "ok": False, "status": CODE})

    async with ws_server(reply) as (url, _, requests):
        client = CareerClient(ConnectionConfig(url, TOKEN))
        try:
            with pytest.raises(CareerError, match="backend_unavailable"):
                await client.request_user("binding.import", "qq:real-user", CODE)
        finally:
            await client.close()


def test_binding_redacted_from_logs():
    record = logging.LogRecord("test", 20, "", 0, "received bind %s", (CODE,), None)
    SensitiveFilter().filter(record)
    assert CODE not in record.getMessage() and "BINDING_REDACTED" in record.getMessage()


def test_snapshot_footer_identifies_user_and_preserves_collection_time():
    from hd2bot.career.formatter import format_career
    from hd2bot.career.models import parse_reply
    from hd2bot.presentation import career_card

    payload = response(bindingId="b_" + "a" * 24, snapshot=True, credentialsRetained=False)
    stats = parse_reply(payload, "test", payload["bindingId"])
    text = format_career(stats)
    card = career_card(stats)
    assert "当前私聊用户" in text and "快照采集时间" in text
    assert "本次未连接游戏接口" in text and "凭据已清除" in text
    assert "仅保留 3 天" in text and "自动删除" in text
    assert "每次获取新的战绩数据" in text and "旧凭据不能复用" in text
    assert "测试账号" not in text and "测试账号" not in str(card)
    assert "HD2-" not in " ".join(card.footer)
    assert any("3 天" in notice and "自动删除" in notice for notice in card.notices)
    assert "快照采集时间" in card.footer[1]


def test_snapshot_cannot_claim_credentials_removed_when_response_retains_them():
    from hd2bot.career.models import parse_reply

    for changes in ({"credentialsRetained": True}, {"refreshed": True}):
        payload = response(snapshot=True, credentialsRetained=False)
        payload.update(changes)
        with pytest.raises(CareerError):
            parse_reply(payload, "test", "owner")


async def test_challenge_command_private_only():
    service = AsyncMock()
    service.challenge.return_value = {"challenge": "AB3K9M", "expiresInSeconds": 120}
    router = CommandRouter(None, service)
    result = await router.respond("/获取验证码", context=ChatContext("c2c", "u", "u"))
    assert "AB3K9M" in result.text and "120" in result.text
    service.challenge.assert_awaited_once_with("qq:u")
    service.challenge.reset_mock()
    await router.respond("/获取验证码", context=ChatContext("group", "g", "u"))
    service.challenge.assert_not_awaited()


def test_bare_capsule_submission():
    parsed = parse_command(CODE)
    assert parsed.name == "绑定" and parsed.argument == CODE


async def test_capsule_not_resent_after_disconnected_submission():
    async def drop(ws, payload):
        await ws.close()

    async with ws_server(drop) as (url, _, requests):
        client = CareerClient(ConnectionConfig(url, TOKEN))
        try:
            with pytest.raises(CareerError, match="submission_unknown"):
                await client.request_user("binding.import", "qq:u", CODE)
            assert len(requests) == 1
        finally:
            await client.close()


@pytest.mark.parametrize(
    "action,error_code",
    [
        ("binding.import", "submission_unknown"),
        ("binding.challenge", "challenge_unknown"),
        ("binding.share", "share_unknown"),
        ("binding.unshare", "unshare_unknown"),
        ("binding.revoke", "revoke_unknown"),
    ],
)
@pytest.mark.parametrize("failure", ["disconnect", "timeout", "binary", "json", "wrong_id"])
async def test_non_idempotent_requests_are_not_repeated_after_submission(
    action, error_code, failure
):
    async def lose_reply(ws, payload):
        if failure == "disconnect":
            await ws.close()
        elif failure == "binary":
            await ws.send_bytes(b"invalid response")
        elif failure == "json":
            await ws.send_str("{invalid response")
        elif failure == "wrong_id":
            await ws.send_json({"requestId": "other-request", "ok": True})

    async with ws_server(lose_reply) as (url, _, requests):
        client = CareerClient(ConnectionConfig(url, TOKEN), timeout=0.2 if failure == "timeout" else 5)
        try:
            with pytest.raises(CareerError, match=error_code):
                await client.request_user(
                    action, "qq:u", CODE if action == "binding.import" else None
                )
            assert len(requests) == 1
        finally:
            await client.close()


async def test_capsule_timeout_before_submission_does_not_claim_it_was_sent():
    client = CareerClient(ConnectionConfig("ws://127.0.0.1:1/ws", TOKEN), timeout=0.01)
    try:
        async with client._lock:
            with pytest.raises(CareerError, match="^timeout$"):
                await client.request_user("binding.import", "qq:u", CODE)
        assert client._connection is None
    finally:
        await client.close()


async def test_user_query_rejects_binary_response_without_retry():
    async def reply(ws, payload):
        await ws.send_bytes(b"invalid response")

    async with ws_server(reply) as (url, _, requests):
        client = CareerClient(ConnectionConfig(url, TOKEN), timeout=2)
        try:
            with pytest.raises(CareerError, match="schema_error"):
                await client.request_user("career.query", "qq:u")
            assert len(requests) == 1
        finally:
            await client.close()


def test_tunnel_config_rejects_arbitrary_args(tmp_path):
    import json

    path = tmp_path / "config.json"
    for host in ("-oProxyCommand=cmd", "bad host", "abc;cmd"):
        path.write_text(
            json.dumps({"url": "ws://127.0.0.1:8765/ws", "token": TOKEN, "sshHost": host})
        )
        with pytest.raises(CareerError):
            ConnectionConfig.load(path)
