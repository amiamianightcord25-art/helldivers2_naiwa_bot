import json
from inspect import signature
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
import pytest
from aioresponses import aioresponses
from aioresponses import core as aioresponses_core

from hd2bot.config import Settings
from hd2bot.qq.native_ui import API_BASE, TOKEN_URL
from hd2bot.qq.release_sender import GroupReleaseSender, run_release_notice
from hd2bot.services.group_push import GroupPushAccess
from hd2bot.services.notifications import (
    PermanentPushError,
    RetryablePushError,
    UncertainPushError,
)
from hd2bot.services.release_notice import ReleaseNoticeService
from hd2bot.storage.database import Database


@pytest.fixture(autouse=True)
def mock_response_compatibility(monkeypatch):
    if "stream_writer" not in signature(aiohttp.ClientResponse).parameters:
        return
    original = aioresponses_core.ClientResponse

    def response(*args, **kwargs):
        kwargs.setdefault("stream_writer", SimpleNamespace(output_size=0))
        return original(*args, **kwargs)

    monkeypatch.setattr(aioresponses_core, "ClientResponse", response)


def settings(tmp_path):
    return Settings(root=tmp_path, database_path=tmp_path / "bot.db", qq_app_id="test-app",
                    qq_app_secret="DO_NOT_SHOW_SECRET", qq_sandbox=False)


def access():
    return SimpleNamespace(allowed=AsyncMock(return_value=True))


def token(mocked):
    mocked.post(TOKEN_URL, payload={"access_token": "DO_NOT_SHOW_TOKEN", "expires_in": 7200})


@pytest.mark.parametrize("status,body,error,code", [
    (403, {"code": 40034105, "message": "DO_NOT_SHOW"}, PermanentPushError, "qq_40034105"),
    (200, {"code": 40034105}, PermanentPushError, "qq_40034105"),
    (429, {"code": 429}, RetryablePushError, "qq_429"),
    (503, {}, RetryablePushError, "http_503"),
    (200, {"code": 0}, UncertainPushError, "missing_message_id"),
    (200, {"code": 0, "id": ""}, UncertainPushError, "missing_message_id"),
    (200, ["DO_NOT_SHOW"], UncertainPushError, "missing_message_id"),
    (400, {"code": "DO_NOT_SHOW_TOKEN"}, PermanentPushError, "http_400"),
])
async def test_http_results_are_bounded_and_classified(tmp_path, status, body, error, code):
    with aioresponses() as mocked:
        token(mocked)
        mocked.post(API_BASE + "/v2/groups/group-one/messages", status=status, payload=body)
        async with GroupReleaseSender(settings(tmp_path), access()) as sender:
            with pytest.raises(error) as caught:
                await sender("group", "group-one", "更新日志")
        assert caught.value.code == code
        assert "DO_NOT_SHOW" not in str(caught.value)


async def test_no_response_is_uncertain_and_never_retried(tmp_path):
    with aioresponses() as mocked:
        token(mocked)
        mocked.post(API_BASE + "/v2/groups/group-one/messages", exception=TimeoutError())
        async with GroupReleaseSender(settings(tmp_path), access()) as sender:
            with pytest.raises(UncertainPushError, match="request_incomplete"):
                await sender("group", "group-one", "更新日志")
        assert sum(len(calls) for (_, url), calls in mocked.requests.items()
                   if str(url).endswith("/messages")) == 1


async def test_success_uses_current_domain_and_only_proactive_text_body(tmp_path):
    with aioresponses() as mocked:
        token(mocked)
        url = API_BASE + "/v2/groups/group-one/messages"
        mocked.post(url, payload={"id": "message-id", "code": 0})
        gate = access()
        async with GroupReleaseSender(settings(tmp_path), gate) as sender:
            await sender("group", "group-one", "更新日志")
        calls = [calls[0] for (_, target), calls in mocked.requests.items() if str(target) == url]
        assert calls[0].kwargs["json"] == {"msg_type": 0, "content": "更新日志"}
        assert calls[0].kwargs["headers"]["Authorization"] == "QQBot DO_NOT_SHOW_TOKEN"
        assert gate.allowed.await_count == 2


async def test_scope_and_access_are_checked_before_message_post(tmp_path):
    gate = access()
    with aioresponses() as mocked:
        async with GroupReleaseSender(settings(tmp_path), gate) as sender:
            with pytest.raises(PermanentPushError, match="invalid_scope"):
                await sender("c2c", "private-one", "更新日志")
            gate.allowed.return_value = False
            with pytest.raises(PermanentPushError, match="recipient_not_allowed"):
                await sender("group", "group-one", "更新日志")
        assert not mocked.requests


async def prepare(tmp_path):
    config = settings(tmp_path)
    path = tmp_path / "release_notice.json"
    path.write_text(json.dumps({"schema": 1, "version": "test-1", "title": "更新",
                                "body": "DSS投票图更新"}), encoding="utf-8")
    (tmp_path / "group_push.json").write_text('{"enabled": true}', encoding="utf-8")
    async with Database(config.database_path) as db:
        gate = GroupPushAccess(None, db, tmp_path / "group_push.json", config.qq_app_id)
        for scope, target in [("group", "group-one"), ("group", "group-denied"),
                              ("c2c", "private-one")]:
            await db.execute("INSERT INTO subscriptions(target_type,target_id,topic) VALUES(?,?,?)",
                             (scope, target, "news"))
        await db.set_setting(gate.key("group-one"), {"enabled": True})
        await db.set_setting("push_allowed_recipient", {"c2c_openid": "private-one"})
    return config, path


async def table_snapshot(config):
    # Deliberately use read-only SQL: even the test's observation cannot migrate.
    from hd2bot.qq.release_sender import ReadOnlyDatabase

    async with ReadOnlyDatabase(config.database_path) as db:
        return {table: [tuple(row) for row in await db.fetch_all(f"SELECT * FROM {table}")]
                for table in ("bot_settings", "subscriptions", "notification_deliveries")}


async def test_dry_run_reads_platform_state_without_messages_or_database_changes(tmp_path):
    config, path = await prepare(tmp_path)
    before = await table_snapshot(config)
    with aioresponses() as mocked:
        token(mocked)
        mocked.get(API_BASE + "/v2/groups/group-one/bot_state",
                   payload={"allow_proactive_msg": False})
        result = await run_release_notice(config, path)
        assert all(method == "GET" or str(url) == TOKEN_URL for method, url in mocked.requests)
    assert result["mode"] == "dry_run"
    assert result["before"] == {"access_denied": 1, "ready": 1}
    assert result["bot_state"] == {"blocked": 1}
    assert await table_snapshot(config) == before
    assert "group-one" not in json.dumps(result)
    assert "private-one" not in json.dumps(result)


async def test_send_is_group_only_deduplicated_and_false_bot_state_remains_diagnostic(tmp_path):
    config, path = await prepare(tmp_path)
    with aioresponses() as mocked:
        token(mocked)
        mocked.get(API_BASE + "/v2/groups/group-one/bot_state",
                   payload={"allow_proactive_msg": False})
        mocked.post(API_BASE + "/v2/groups/group-one/messages", payload={"id": "sent"})
        result = await run_release_notice(config, path, send=True)
        again = await run_release_notice(config, path, send=True)
        assert len(mocked.requests) == 3
    assert result["attempts"] == [{"status": "sent", "code": "ok", "count": 1}]
    assert again["before"] == {"access_denied": 1, "sent": 1}
    snapshot = await table_snapshot(config)
    assert len(snapshot["notification_deliveries"]) == 1
    assert snapshot["notification_deliveries"][0][:2] == ("group", "group-one")
    assert len(snapshot["subscriptions"]) == 3
    assert not any(row[0] == ReleaseNoticeService._state_key("c2c", "private-one", "test-1")
                   for row in snapshot["bot_settings"])


async def test_group_scope_excluded_means_no_network_or_claim(tmp_path):
    config, path = await prepare(tmp_path)
    notice = json.loads(path.read_text())
    notice["scopes"] = ["c2c"]
    path.write_text(json.dumps(notice), encoding="utf-8")
    before = await table_snapshot(config)
    with aioresponses() as mocked:
        result = await run_release_notice(config, path, send=True)
        assert not mocked.requests
    assert result["before"] == {"scope_excluded": 2}
    assert await table_snapshot(config) == before


async def test_unavailable_bot_state_does_not_prevent_one_authorized_attempt(tmp_path):
    config, path = await prepare(tmp_path)
    with aioresponses() as mocked:
        token(mocked)
        mocked.get(API_BASE + "/v2/groups/group-one/bot_state",
                   status=403, payload={"code": 11253})
        mocked.post(API_BASE + "/v2/groups/group-one/messages",
                    status=400, payload={"code": 40034105})
        result = await run_release_notice(config, path, send=True)
        again = await run_release_notice(config, path, send=True)
        assert len(mocked.requests) == 3
    assert result["bot_state"] == {"unknown": 1}
    assert result["attempts"] == [{"status": "rejected", "code": "qq_40034105", "count": 1}]
    assert again["before"] == {"access_denied": 1, "rejected": 1}


async def test_local_access_revoked_during_token_request_stops_message_post(tmp_path):
    gate = access()
    gate.allowed.side_effect = [True, False]
    with aioresponses() as mocked:
        token(mocked)
        async with GroupReleaseSender(settings(tmp_path), gate) as sender:
            with pytest.raises(PermanentPushError, match="recipient_not_allowed"):
                await sender("group", "group-one", "更新日志")
        assert len(mocked.requests) == 1
