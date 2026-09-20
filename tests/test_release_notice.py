import asyncio
import json
from unittest.mock import AsyncMock

from hd2bot.commands.parser import parse_command
from hd2bot.router import CommandRouter
from hd2bot.services.notifications import (
    NotificationManager,
    PermanentPushError,
    RetryablePushError,
    UncertainPushError,
)
from hd2bot.services.push_access import KEY as PRIVATE_TARGET_KEY
from hd2bot.services.release_notice import ReleaseNoticeService
from hd2bot.storage.database import Database


class Clock:
    def __init__(self, value=10_000.0):
        self.value = value

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class Access:
    async def allowed(self, scope, target_id):
        return True

    async def limits(self, scope, target_id):
        return 5, 300, 60


def write_notice(path, version="2026.09.19-1"):
    path.write_text(json.dumps({
        "schema": 1,
        "version": version,
        "title": "机器人更新",
        "body": "新增更新日志查询。",
    }), encoding="utf-8")


async def add_subscription(db, target_type, target_id, topic):
    await db.execute(
        "INSERT INTO subscriptions(target_type, target_id, topic) VALUES (?, ?, ?)",
        (target_type, target_id, topic),
    )


async def test_release_notice_sends_once_to_each_distinct_opted_in_target(tmp_path):
    path = tmp_path / "release_notice.json"
    write_notice(path)
    clock = Clock()
    async with Database(tmp_path / "bot.db") as db:
        await add_subscription(db, "group", "group-one", "major_order")
        await add_subscription(db, "group", "group-one", "defense")
        await add_subscription(db, "c2c", "private-one", "news")
        await db.set_setting(PRIVATE_TARGET_KEY, {"c2c_openid": "private-one"})
        service = ReleaseNoticeService(db, path, access=Access(), clock=clock)
        sender = AsyncMock()

        await service.tick(sender)
        await service.tick(sender)

        assert sender.await_count == 2
        assert {(call.args[0], call.args[1]) for call in sender.await_args_list} == {
            ("group", "group-one"), ("c2c", "private-one"),
        }
        assert all("机器人更新日志" in call.args[2] for call in sender.await_args_list)
        assert len(await db.fetch_all("SELECT * FROM notification_deliveries")) == 2


async def test_release_notice_with_no_opted_in_target_does_nothing(tmp_path):
    path = tmp_path / "release_notice.json"
    write_notice(path)
    async with Database(tmp_path / "bot.db") as db:
        service = ReleaseNoticeService(db, path, access=Access())
        sender = AsyncMock()

        await service.tick(sender)

        sender.assert_not_awaited()
        assert await db.fetch_all("SELECT * FROM notification_deliveries") == []
        assert await db.fetch_all(
            "SELECT key FROM bot_settings WHERE key LIKE 'release_notice_delivery:%'"
        ) == []


async def test_group_only_notice_never_sends_to_bound_private_recipient(tmp_path):
    path = tmp_path / "release_notice.json"
    write_notice(path)
    notice = json.loads(path.read_text(encoding="utf-8"))
    notice["scopes"] = ["group"]
    path.write_text(json.dumps(notice), encoding="utf-8")
    async with Database(tmp_path / "bot.db") as db:
        await add_subscription(db, "group", "group-one", "news")
        await add_subscription(db, "c2c", "private-one", "news")
        await db.set_setting(PRIVATE_TARGET_KEY, {"c2c_openid": "private-one"})
        service = ReleaseNoticeService(db, path, access=Access())
        sender = AsyncMock()
        await service.tick(sender)
        sender.assert_awaited_once_with("group", "group-one", service.command_text())
        assert len(await db.fetch_all(
            "SELECT key FROM bot_settings WHERE key LIKE 'release_notice_delivery:%'"
        )) == 1


def test_invalid_release_scopes_cannot_expand_delivery_targets(tmp_path):
    path = tmp_path / "release_notice.json"
    write_notice(path)
    notice = json.loads(path.read_text(encoding="utf-8"))
    service = ReleaseNoticeService(None, path)
    for scopes in ([], ["group", "group"], ["channel"], "group", [None], ["group", "c2c", "group"]):
        path.write_text(json.dumps({**notice, "scopes": scopes}), encoding="utf-8")
        assert service.current() is None


async def test_invalid_release_notice_is_not_shown_or_sent(tmp_path):
    path = tmp_path / "release_notice.json"
    path.write_text('{"schema": 1, "version": "bad version", "title": "x", "body": "y"}')
    async with Database(tmp_path / "bot.db") as db:
        await add_subscription(db, "group", "group-one", "news")
        service = ReleaseNoticeService(db, path, access=Access())
        manager = NotificationManager(db, None, release_notices=service)
        sender = AsyncMock()

        await service.tick(sender)
        reply = await CommandRouter(None, notifications=manager).respond("changelog")

        sender.assert_not_awaited()
        assert reply.text == "当前没有可用的机器人更新日志。"


async def test_release_notice_command_hot_reads_replaced_file(tmp_path):
    path = tmp_path / "release_notice.json"
    write_notice(path, "2026.09.19-1")
    async with Database(tmp_path / "bot.db") as db:
        service = ReleaseNoticeService(db, path, access=Access())
        manager = NotificationManager(db, None, release_notices=service)
        router = CommandRouter(None, notifications=manager)

        assert "2026.09.19-1" in (await router.respond("更新日志")).text
        write_notice(path, "2026.09.19-2")
        assert "2026.09.19-2" in (await router.respond("changelog")).text


async def test_release_notice_does_not_mutate_game_subscriptions(tmp_path):
    path = tmp_path / "release_notice.json"
    write_notice(path)
    async with Database(tmp_path / "bot.db") as db:
        await add_subscription(db, "group", "group-one", "campaign")
        before = [tuple(row) for row in await db.fetch_all("SELECT * FROM subscriptions")]
        service = ReleaseNoticeService(db, path, access=Access())

        await service.tick(AsyncMock())

        after = [tuple(row) for row in await db.fetch_all("SELECT * FROM subscriptions")]
        assert after == before


async def test_transient_failure_retries_but_permanent_rejection_is_suppressed(tmp_path):
    path = tmp_path / "release_notice.json"
    write_notice(path)
    clock = Clock()
    async with Database(tmp_path / "bot.db") as db:
        await add_subscription(db, "group", "temporary", "news")
        await add_subscription(db, "group", "permanent", "news")
        service = ReleaseNoticeService(db, path, access=Access(), clock=clock)
        attempts = {"temporary": 0, "permanent": 0}

        async def sender(scope, target, text):
            attempts[target] += 1
            if target == "permanent":
                raise PermanentPushError("platform_denied")
            if attempts[target] == 1:
                raise RetryablePushError("temporary")

        await service.tick(sender)
        await service.tick(sender)
        assert attempts == {"temporary": 1, "permanent": 1}

        clock.advance(60)
        await service.tick(sender)
        await service.tick(sender)
        assert attempts == {"temporary": 2, "permanent": 1}


async def test_release_version_history_prevents_a_b_a_redelivery(tmp_path):
    path = tmp_path / "release_notice.json"
    clock = Clock()
    async with Database(tmp_path / "bot.db") as db:
        await add_subscription(db, "group", "group-one", "news")
        service = ReleaseNoticeService(db, path, access=Access(), clock=clock)
        sender = AsyncMock()

        write_notice(path, "release-a")
        await service.tick(sender)
        clock.advance(300)
        write_notice(path, "release-b")
        await service.tick(sender)
        clock.advance(300)
        write_notice(path, "release-a")
        await service.tick(sender)

        assert [call.args[2].split("\n", 1)[0] for call in sender.await_args_list] == [
            "【机器人更新日志 · release-a】",
            "【机器人更新日志 · release-b】",
        ]
        assert len(await db.fetch_all(
            "SELECT key FROM bot_settings WHERE key LIKE 'release_notice_delivery:%'"
        )) == 2


async def test_persisted_attempt_blocks_duplicate_when_success_recording_fails(
        tmp_path, monkeypatch):
    path = tmp_path / "release_notice.json"
    write_notice(path)
    async with Database(tmp_path / "bot.db") as db:
        await add_subscription(db, "group", "group-one", "news")
        service = ReleaseNoticeService(db, path, access=Access())
        sender = AsyncMock()
        mark_success = AsyncMock(side_effect=RuntimeError("database unavailable"))
        monkeypatch.setattr(service, "_mark_success", mark_success)

        await service.tick(sender)
        await service.tick(sender)

        sender.assert_awaited_once()
        mark_success.assert_awaited_once()
        rows = await db.fetch_all(
            "SELECT value_json FROM bot_settings "
            "WHERE key LIKE 'release_notice_delivery:%'"
        )
        assert len(rows) == 1
        assert json.loads(rows[0]["value_json"])["status"] == "attempting"
        assert await db.fetch_all("SELECT * FROM notification_deliveries") == []


async def test_uncertain_sender_failure_keeps_attempt_terminal(tmp_path):
    path = tmp_path / "release_notice.json"
    write_notice(path)
    async with Database(tmp_path / "bot.db") as db:
        await add_subscription(db, "group", "group-one", "news")
        service = ReleaseNoticeService(db, path, access=Access())
        sender = AsyncMock(side_effect=UncertainPushError())

        await service.tick(sender)
        await service.tick(sender)

        sender.assert_awaited_once()
        rows = await db.fetch_all(
            "SELECT value_json FROM bot_settings "
            "WHERE key LIKE 'release_notice_delivery:%'"
        )
        assert json.loads(rows[0]["value_json"])["status"] == "attempting"


async def test_two_service_instances_claim_one_external_attempt(tmp_path):
    path = tmp_path / "release_notice.json"
    database_path = tmp_path / "bot.db"
    write_notice(path)
    started = asyncio.Event()
    release = asyncio.Event()
    attempts = 0

    async def sender(scope, target, text):
        nonlocal attempts
        attempts += 1
        started.set()
        await release.wait()

    async with Database(database_path) as first_db:
        await add_subscription(first_db, "group", "group-one", "news")
        async with Database(database_path) as second_db:
            first = ReleaseNoticeService(first_db, path, access=Access())
            second = ReleaseNoticeService(second_db, path, access=Access())

            first_tick = asyncio.create_task(first.tick(sender))
            await asyncio.wait_for(started.wait(), timeout=1)
            second_tick = asyncio.create_task(second.tick(sender))
            try:
                await asyncio.wait_for(second_tick, timeout=1)
            finally:
                release.set()
                await first_tick

            assert attempts == 1


async def test_group_unsubscribe_during_tick_is_rechecked_before_attempt(tmp_path):
    class PausingAccess(Access):
        def __init__(self):
            self.checking_quota = asyncio.Event()
            self.resume = asyncio.Event()

        async def limits(self, scope, target_id):
            self.checking_quota.set()
            await self.resume.wait()
            return await super().limits(scope, target_id)

    path = tmp_path / "release_notice.json"
    write_notice(path)
    async with Database(tmp_path / "bot.db") as db:
        await add_subscription(db, "group", "group-one", "news")
        access = PausingAccess()
        service = ReleaseNoticeService(db, path, access=access)
        sender = AsyncMock()

        task = asyncio.create_task(service.tick(sender))
        await access.checking_quota.wait()
        await db.execute(
            "UPDATE subscriptions SET enabled = 0 WHERE target_type = ? AND target_id = ?",
            ("group", "group-one"),
        )
        access.resume.set()
        await task

        sender.assert_not_awaited()
        assert await db.fetch_all(
            "SELECT key FROM bot_settings WHERE key LIKE 'release_notice_delivery:%'"
        ) == []


def test_changelog_alias_parses_as_release_notice_command():
    assert parse_command("changelog").name == "更新日志"
