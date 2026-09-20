import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from hd2bot.config import Settings
from hd2bot.qq.adapter import QQDispatcher
from hd2bot.services.notifications import PermanentPushError
from hd2bot.services.push_access import KEY, PushAccess
from hd2bot.storage.database import Database


def pending(path: Path, *, number="12345678", code="local-once", expires=200):
    path.write_text(json.dumps({"qq_number": number,
        "digest": hashlib.sha256(code.encode()).hexdigest(), "expires_at": expires}), encoding="utf8")


async def test_no_binding_is_fail_closed_and_valid_pairing_allows_only_one_c2c(tmp_path):
    path = tmp_path / "pair.private.json"
    pending(path)
    async with Database(tmp_path / "bot.db") as db:
        gate = PushAccess(db, "12345678", path, clock=lambda: 100)
        assert not await gate.allowed("c2c", "12345678")  # QQ number isn't an OpenID.
        assert "私聊" in await gate.bind("local-once", "group", "group-openid")
        assert path.exists()
        assert "无效" in await gate.bind("wrong", "c2c", "user-openid")
        assert not await gate.allowed("c2c", "user-openid")
        assert "已绑定" in await gate.bind("local-once", "c2c", "user-openid")
        assert not path.exists()
        assert await gate.allowed("c2c", "user-openid")
        assert not await gate.allowed("group", "user-openid")
        assert not await gate.allowed("c2c", "someone-else")
    async with Database(tmp_path / "bot.db") as db:
        assert await PushAccess(db, "12345678", path).allowed("c2c", "user-openid")
        assert not await PushAccess(db, "87654321", path).allowed("c2c", "user-openid")
        assert not await PushAccess(db, "", path).allowed("c2c", "user-openid")


@pytest.mark.parametrize("number,expires", [("different", 200), ("12345678", 99)])
async def test_wrong_number_and_expired_codes_cannot_pair(tmp_path, number, expires):
    path = tmp_path / "pair.private.json"
    pending(path, number=number, expires=expires)
    async with Database(tmp_path / "bot.db") as db:
        gate = PushAccess(db, "12345678", path, clock=lambda: 100)
        assert "无效" in await gate.bind("local-once", "c2c", "user")
        assert await db.get_setting(KEY) is None


async def test_one_time_code_cannot_rebind_a_different_user_even_if_file_restored(tmp_path):
    path = tmp_path / "pair.private.json"
    pending(path)
    original = path.read_bytes()
    async with Database(tmp_path / "bot.db") as db:
        gate = PushAccess(db, "12345678", path, clock=lambda: 100)
        first, second = await asyncio.gather(gate.bind("local-once", "c2c", "first"),
                                            gate.bind("local-once", "c2c", "second"))
        assert "已绑定" in first and "暂无" in second
        path.write_bytes(original)
        assert "已经使用" in await gate.bind("local-once", "c2c", "second")
        assert await gate.allowed("c2c", "first")
        assert not await gate.allowed("c2c", "second")


@pytest.mark.parametrize("separate_connection", [False, True])
async def test_concurrent_access_instances_cannot_consume_same_code_twice(tmp_path, separate_connection):
    path = tmp_path / "pair.private.json"
    pending(path)
    async with Database(tmp_path / "bot.db") as db, Database(tmp_path / "bot.db") as other_db:
        first = PushAccess(db, "12345678", path, clock=lambda: 100)
        second = PushAccess(other_db if separate_connection else db, "12345678", path, clock=lambda: 100)
        replies = await asyncio.gather(first.bind("local-once", "c2c", "first"),
                                       second.bind("local-once", "c2c", "second"))
        assert sum("已绑定" in reply for reply in replies) == 1


@pytest.mark.parametrize("change", [{"digest": "非ASCII摘要"}, {"expires_at": float("inf")}])
async def test_malformed_pairing_record_is_rejected_without_binding(tmp_path, change):
    path = tmp_path / "pair.private.json"
    pending(path)
    record = json.loads(path.read_text())
    path.write_text(json.dumps({**record, **change}))
    async with Database(tmp_path / "bot.db") as db:
        gate = PushAccess(db, "12345678", path, clock=lambda: 100)
        assert "无效" in await gate.bind("local-once", "c2c", "first")
        assert not await gate.allowed("c2c", "first")


async def test_code_expiring_while_waiting_for_database_is_not_consumed(tmp_path):
    path = tmp_path / "pair.private.json"
    pending(path)
    checked = asyncio.Event()
    now = 100

    def clock():
        checked.set()
        return now

    async with Database(tmp_path / "bot.db") as db:
        gate = PushAccess(db, "12345678", path, clock=clock)
        async with db.transaction():
            task = asyncio.create_task(gate.bind("local-once", "c2c", "first"))
            await asyncio.wait_for(checked.wait(), timeout=1)
            now = 201
        assert "过期" in await task
        assert not await gate.allowed("c2c", "first")


async def test_consumed_binding_survives_pairing_file_cleanup_failure(tmp_path, monkeypatch):
    path = tmp_path / "pair.private.json"
    pending(path)

    def deny_unlink(*_, **__):
        raise PermissionError("synthetic cleanup failure")

    async with Database(tmp_path / "bot.db") as db:
        gate = PushAccess(db, "12345678", path, clock=lambda: 100)
        with monkeypatch.context() as patch:
            patch.setattr(Path, "unlink", deny_unlink)
            assert "已绑定" in await gate.bind("local-once", "c2c", "first")
        assert await gate.allowed("c2c", "first")
        assert path.exists()
        assert "已经使用" in await gate.bind("local-once", "c2c", "second")


async def test_final_sender_rechecks_recipient_and_cannot_send_to_group(tmp_path):
    path = tmp_path / "pair.private.json"
    pending(path)
    async with Database(tmp_path / "bot.db") as db:
        gate = PushAccess(db, "12345678", path, clock=lambda: 100)
        await gate.bind("local-once", "c2c", "allowed")
        dispatcher = QQDispatcher(Settings(), AsyncMock(), notifications=SimpleNamespace(access=gate))
        bot = AsyncMock()
        dispatcher._bot = bot
        for scope, target in [("group", "allowed"), ("c2c", "other")]:
            with pytest.raises(PermanentPushError, match="recipient_not_allowed"):
                await dispatcher.send_proactive(scope, target, "private push")
        bot.post_group_messages.assert_not_called()
        bot.post_c2c_messages.assert_not_called()
        await dispatcher.send_proactive("c2c", "allowed", "authorized")
        bot.post_c2c_messages.assert_awaited_once_with(openid="allowed", msg_type=0, content="authorized")
        await dispatcher.close()
