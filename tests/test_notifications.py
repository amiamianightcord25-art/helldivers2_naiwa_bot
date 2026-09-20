"""Opt-in, difference detection, persistent quotas and failure-aware delivery."""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from hd2bot.hd2.models import Faction, MajorOrder, OrderTask, Planet, PlanetEvent, WarStatus
from hd2bot.hd2.service import DataResult
from hd2bot.services.notifications import NotificationManager, PermanentPushError
from hd2bot.storage.database import Database


class Clock:
    def __init__(self):
        self.now = datetime(2026, 9, 16, tzinfo=UTC).timestamp()

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class Service:
    def __init__(self, clock):
        self.clock = clock
        self.calls = 0
        self.stale = False
        self.planets = [Planet(64, "梅里迪亚", "Meridia", faction=Faction.TERMINIDS, liberation=21)]
        self.orders = [MajorOrder(12, "完成任务", tasks=[OrderTask(progress=2, target=10)])]

    def data(self, value):
        self.calls += 1
        return DataResult(value, "mock", datetime.fromtimestamp(self.clock(), UTC), self.stale)

    async def get_war(self):
        return self.data(WarStatus(war_id=900, players=1234))

    async def get_planets(self):
        return self.data(self.planets)

    async def get_major_order(self):
        return self.data(self.orders)


@pytest.fixture
async def setup(tmp_path):
    async with Database(tmp_path / "bot.db") as db:
        clock = Clock()
        service = Service(clock)
        manager = NotificationManager(db, service, clock=clock)
        yield db, clock, service, manager


async def test_no_opt_in_never_queries_or_sends(setup):
    _, _, service, manager = setup
    sender = AsyncMock()
    await manager.tick(sender)
    assert service.calls == 0
    sender.assert_not_called()


async def test_war_waits_interval_then_repeats_and_survives_restart(setup):
    db, clock, service, manager = setup
    sender = AsyncMock()
    reply = await manager.handle_command("订阅", "战况 30", "c2c", "synthetic")
    assert "当前私聊" in reply
    await manager.tick(sender)
    assert service.calls == 0
    clock.advance(1799)
    await manager.tick(sender)
    sender.assert_not_called()
    clock.advance(1)
    await manager.tick(sender)
    assert sender.await_count == 1
    assert "模拟数据" in sender.call_args.args[2]
    manager = NotificationManager(db, service, clock=clock)
    await manager.tick(sender)
    assert sender.await_count == 1
    clock.advance(1800)
    await manager.tick(sender)
    assert sender.await_count == 2
    assert len(await db.fetch_all("SELECT * FROM notification_state")) == 1


async def test_change_topics_baseline_no_initial_push_or_progress_noise(setup):
    db, clock, service, manager = setup
    sender = AsyncMock()
    await manager.handle_command("订阅", "主线", "group", "synthetic")
    assert await db.fetch_all("SELECT * FROM notification_state") == []
    await manager.tick(sender)
    service.orders[0].tasks[0].progress = 9
    await manager.tick(sender)
    sender.assert_not_called()
    service.orders[0].tasks[0].progress = 10
    await manager.tick(sender)
    assert sender.await_count == 1
    clock.advance(300)
    await manager.tick(sender)
    assert sender.await_count == 1
    service.orders = []
    await manager.tick(sender)
    assert sender.await_count == 2


async def test_planet_bucket_owner_unknown_and_stale(setup):
    db, clock, service, manager = setup
    sender = AsyncMock()
    await manager.handle_command("订阅", "星球 Meridia", "group", "synthetic")
    service.planets[0].liberation = 29.9
    await manager.tick(sender)
    sender.assert_not_called()
    service.planets[0].liberation = 30
    service.stale = True
    await manager.tick(sender)
    sender.assert_not_called()
    service.stale = False
    await manager.tick(sender)
    assert sender.await_count == 1
    clock.advance(300)
    service.planets[0].liberation = None
    service.planets[0].faction = Faction.UNKNOWN
    await manager.tick(sender)
    assert sender.await_count == 1
    service.planets[0].faction = Faction.HUMANS
    await manager.tick(sender)
    assert sender.await_count == 2
    assert "暂无数据" in sender.call_args.args[2]


async def test_defense_start_and_end_ignore_percent_changes(setup):
    _, clock, service, manager = setup
    sender = AsyncMock()
    await manager.handle_command("订阅", "防守", "group", "synthetic")
    p = service.planets[0]
    p.event = PlanetEvent(id=66, event_type=1, progress=5,
                          ends_at=datetime.fromtimestamp(clock(), UTC) + timedelta(hours=1))
    await manager.tick(sender)
    assert sender.await_count == 1
    clock.advance(300)
    p.event.progress = 99
    await manager.tick(sender)
    assert sender.await_count == 1
    p.event = None
    await manager.tick(sender)
    assert sender.await_count == 2


async def test_future_defense_event_does_not_trigger_until_start(setup):
    _, clock, service, manager = setup
    sender = AsyncMock()
    await manager.handle_command("订阅", "防守", "group", "synthetic")
    p = service.planets[0]
    p.event = PlanetEvent(
        id=67, event_type=1,
        starts_at=datetime.fromtimestamp(clock() + 600, UTC),
        ends_at=datetime.fromtimestamp(clock() + 1200, UTC),
    )
    await manager.tick(sender)
    sender.assert_not_called()
    clock.advance(600)
    await manager.tick(sender)
    assert sender.await_count == 1


async def test_transient_failure_backoff_and_does_not_mark_success(setup):
    db, clock, service, manager = setup
    await manager.handle_command("订阅", "主线", "group", "synthetic")
    before = (await db.fetch_one("SELECT baseline_json FROM subscriptions"))[0]
    service.orders = [MajorOrder(44, "新的主要指令")]
    sender = AsyncMock(side_effect=RuntimeError("synthetic private upstream error"))
    await manager.tick(sender)
    assert (await db.fetch_one("SELECT baseline_json FROM subscriptions"))[0] == before
    assert await db.fetch_all("SELECT * FROM notification_state") == []
    assert await db.fetch_all("SELECT * FROM notification_deliveries") == []
    await manager.tick(sender)
    assert sender.await_count == 1
    clock.advance(60)
    sender.side_effect = None
    await manager.tick(sender)
    assert sender.await_count == 2
    assert len(await db.fetch_all("SELECT * FROM notification_deliveries")) == 1


async def test_platform_rejection_pauses_and_explicit_resubscribe_resumes(setup):
    db, clock, service, manager = setup
    await manager.handle_command("订阅", "主线", "group", "synthetic")
    service.orders = []
    sender = AsyncMock(side_effect=PermanentPushError("platform_denied"))
    await manager.tick(sender)
    row = await db.fetch_one("SELECT * FROM subscriptions")
    assert row["enabled"] == 0
    assert "权限/额度" in row["pause_reason"]
    assert "已暂停" in await manager.handle_command("订阅列表", "", "group", "synthetic")
    assert await db.fetch_all("SELECT * FROM notification_state") == []
    clock.advance(500)
    await manager.tick(sender)
    assert sender.await_count == 1
    await manager.handle_command("订阅", "主线", "group", "synthetic")
    assert (await db.fetch_one("SELECT enabled FROM subscriptions"))[0] == 1
    sender.side_effect = None
    service.orders = [MajorOrder(77, "新主线")]
    await manager.tick(sender)
    assert sender.await_count == 2


async def test_scope_isolation_cancel_all_and_cancel_before_send(setup):
    db, _, service, manager = setup
    await manager.handle_command("订阅", "主线", "group", "synthetic")
    await manager.handle_command("订阅", "主线", "c2c", "synthetic")
    await manager.handle_command("取消订阅", "", "group", "synthetic")
    service.orders = []
    sender = AsyncMock()
    await manager.tick(sender)
    assert sender.call_args.args[:2] == ("c2c", "synthetic")
    await manager.handle_command("订阅", "防守", "group", "synthetic")
    original = service.get_planets

    async def cancelling_fetch():
        await manager.handle_command("取消订阅", "防守", "group", "synthetic")
        service.planets[0].event = PlanetEvent(id=1, event_type=1)
        return await original()

    service.get_planets = cancelling_fetch
    await manager.tick(sender)
    assert sender.await_count == 1
    assert len(await db.fetch_all("SELECT * FROM subscriptions")) == 1


async def test_per_target_daily_quota_and_minimum_gap_across_topics(setup):
    _, clock, service, manager = setup
    sender = AsyncMock()
    await manager.handle_command("订阅", "主线", "group", "synthetic")
    await manager.handle_command("订阅", "防守", "group", "synthetic")
    service.orders = []
    service.planets[0].event = PlanetEvent(id=1, event_type=1)
    await manager.tick(sender)
    assert sender.await_count == 1
    clock.advance(299)
    await manager.tick(sender)
    assert sender.await_count == 1
    clock.advance(1)
    await manager.tick(sender)
    assert sender.await_count == 2
    for identity in range(10, 16):
        clock.advance(300)
        service.orders = [MajorOrder(identity, "变化")]
        await manager.tick(sender)
    assert sender.await_count == 5
    clock.advance(86400)
    await manager.tick(sender)
    assert sender.await_count == 6


async def test_shared_fetch_per_tick_and_stale_baseline_deferred(setup):
    db, _, service, manager = setup
    service.stale = True
    await manager.handle_command("订阅", "主线", "group", "synthetic-a")
    await manager.handle_command("订阅", "主线", "group", "synthetic-b")
    assert all(row[0] is None for row in await db.fetch_all("SELECT baseline_json FROM subscriptions"))
    service.calls = 0
    service.stale = False
    sender = AsyncMock()
    await manager.tick(sender)
    assert service.calls == 1
    sender.assert_not_called()


@pytest.mark.parametrize("argument", ["战况 29", "战况 1441", "战况 aaa", "星球", "unknown"])
async def test_invalid_commands_do_not_subscribe(setup, argument):
    db, _, _, manager = setup
    assert await manager.handle_command("订阅", argument, "c2c", "synthetic")
    assert await db.fetch_all("SELECT * FROM subscriptions") == []


@pytest.mark.parametrize("argument", ["战况 " + "9" * 5000, "星球 " + "９" * 5000])
async def test_oversized_numeric_arguments_return_usage(setup, argument):
    db, _, _, manager = setup
    reply = await manager.handle_command("订阅", argument, "c2c", "synthetic")
    assert reply and "Traceback" not in reply
    assert await db.fetch_all("SELECT * FROM subscriptions") == []


async def test_run_survives_poll_error_and_cancels_cleanly(setup):
    _, _, _, manager = setup
    attempted = asyncio.Event()

    async def tick(_):
        attempted.set()
        raise RuntimeError("synthetic failure")

    manager.tick = tick
    manager.poll_interval = 30
    task = asyncio.create_task(manager.run(AsyncMock()))
    await asyncio.wait_for(attempted.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_database_reopen_preserves_baseline_and_quota(tmp_path):
    path = tmp_path / "persistent.db"
    clock = Clock()
    service = Service(clock)
    sender = AsyncMock()
    async with Database(path) as db:
        manager = NotificationManager(db, service, clock=clock)
        await manager.handle_command("订阅", "主线", "group", "synthetic")
        service.orders = []
        await manager.tick(sender)
    async with Database(path) as db:
        manager = NotificationManager(db, service, clock=clock)
        clock.advance(300)
        await manager.tick(sender)
        assert sender.await_count == 1
        assert len(await db.fetch_all("SELECT * FROM notification_deliveries")) == 1


async def test_cancel_and_resubscribe_during_send_cannot_overwrite_new_subscription(setup):
    db, _, service, manager = setup
    await manager.handle_command("订阅", "主线", "group", "synthetic")
    original_id = (await db.fetch_one("SELECT id FROM subscriptions"))[0]
    service.orders = []

    async def cancelling_sender(*_):
        await manager.handle_command("取消订阅", "", "group", "synthetic")
        service.orders = [MajorOrder(777, "重订阅时的新主线")]
        await manager.handle_command("订阅", "主线", "group", "synthetic")

    await manager.tick(cancelling_sender)
    current = await db.fetch_one("SELECT * FROM subscriptions")
    assert current["id"] > original_id
    assert '"777"' in current["baseline_json"]
    assert current["next_due"] == 0


async def test_summary_honors_war_dates_and_marks_unknown_task_progress(setup):
    _, clock, service, manager = setup

    async def ended_war():
        return service.data(WarStatus(players=2, ended_at=datetime.fromtimestamp(clock()-1, UTC)))

    service.get_war = ended_war
    await manager.handle_command("订阅", "战况 30", "c2c", "war-target")
    await manager.handle_command("订阅", "主线", "c2c", "order-target")
    service.orders = [MajorOrder(13, "新任务", tasks=[OrderTask(progress=None, target=100)])]
    clock.advance(1800)
    sender = AsyncMock()
    await manager.tick(sender)
    texts = [call.args[2] for call in sender.call_args_list]
    assert any("战争状态：已结束" in text for text in texts)
    assert any("1 项进度未知" in text for text in texts)


async def test_unknown_defense_event_is_not_treated_as_ended(setup):
    _, _, service, manager = setup
    service.planets[0].event = PlanetEvent(id=19, event_type=1)
    await manager.handle_command("订阅", "防守", "group", "synthetic")
    service.planets[0].event.event_type = None
    sender = AsyncMock()
    await manager.tick(sender)
    sender.assert_not_called()


async def test_resubscribe_during_send_preserves_new_interval_and_due_time(setup):
    db, clock, _, manager = setup
    await manager.handle_command("订阅", "战况 30", "group", "synthetic")
    clock.advance(1800)

    async def updating_sender(*_):
        await manager.handle_command("订阅", "战况 60", "group", "synthetic")

    await manager.tick(updating_sender)
    row = await db.fetch_one("SELECT * FROM subscriptions")
    assert row["interval_minutes"] == 60
    assert row["next_due"] == clock() + 3600
    assert row["baseline_json"] is None
    assert len(await db.fetch_all("SELECT * FROM notification_deliveries")) == 1
    assert len(await db.fetch_all("SELECT * FROM notification_state")) == 1


async def test_restricted_access_blocks_commands_fetches_and_sending_but_allows_cleanup(setup):
    db, clock, service, manager = setup
    await manager.handle_command("订阅", "主线", "group", "synthetic-group")
    await manager.handle_command("订阅", "主线", "c2c", "synthetic-private")

    class Access:
        async def allowed(self, scope, target_id):
            return False

    manager = NotificationManager(db, service, clock=clock, access=Access())
    service.calls = 0
    sender = AsyncMock()
    await manager.tick(sender)
    assert service.calls == 0
    sender.assert_not_called()
    assert "指定测试账号" in await manager.handle_command("订阅列表", "", "c2c", "synthetic-private")
    assert "指定测试账号" in await manager.handle_command("订阅", "战况 30", "group", "another")
    assert "已取消" in await manager.handle_command("取消订阅", "", "c2c", "synthetic-private")
    assert len(await db.fetch_all("SELECT * FROM subscriptions")) == 1


async def test_access_is_rechecked_after_api_fetch_before_sender(setup):
    db, clock, service, manager = setup
    await manager.handle_command("订阅", "主线", "c2c", "synthetic-private")

    class Access:
        permitted = True

        async def allowed(self, scope, target_id):
            return self.permitted

    gate = Access()
    manager = NotificationManager(db, service, clock=clock, access=gate)

    async def revoking_fetch():
        gate.permitted = False
        return service.data([])

    service.get_major_order = revoking_fetch
    sender = AsyncMock()
    await manager.tick(sender)
    sender.assert_not_called()
    assert await db.fetch_all("SELECT * FROM notification_deliveries") == []


async def test_resubscribe_during_initial_fetch_preserves_new_baseline(setup):
    db, _, service, manager = setup
    service.stale = True
    await manager.handle_command("订阅", "主线", "group", "synthetic")
    service.stale = False
    original = service.get_major_order

    async def resubscribing_fetch():
        old_result = await original()
        service.get_major_order = original
        service.orders = [MajorOrder(777, "重订阅的新主线")]
        await manager.handle_command("订阅", "主线", "group", "synthetic")
        return old_result

    service.get_major_order = resubscribing_fetch
    sender = AsyncMock()
    await manager.tick(sender)
    row = await db.fetch_one("SELECT * FROM subscriptions")
    assert '"777"' in row["baseline_json"]
    await manager.tick(sender)
    sender.assert_not_awaited()


@pytest.mark.parametrize("error", [PermanentPushError("platform_denied"), RuntimeError("temporary")])
async def test_old_send_failure_does_not_pause_or_backoff_new_subscription(setup, error):
    db, clock, _, manager = setup
    await manager.handle_command("订阅", "战况 30", "group", "synthetic")
    clock.advance(1800)

    async def resubscribing_sender(*_):
        await manager.handle_command("订阅", "战况 60", "group", "synthetic")
        raise error

    await manager.tick(resubscribing_sender)
    row = await db.fetch_one("SELECT * FROM subscriptions")
    assert row["enabled"] == 1
    assert row["failures"] == 0
    assert row["retry_at"] == 0
    assert row["pause_reason"] == ""
    assert row["next_due"] == clock() + 3600
