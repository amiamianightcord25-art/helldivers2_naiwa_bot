"""Fresh public-event differences reuse private opt-in and persisted push limits."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from hd2bot.hd2.models import (
    Campaign,
    Dispatch,
    Faction,
    GlobalEvent,
    Planet,
    PlanetRegion,
    SpaceStation,
    TacticalAction,
    TacticalCost,
)
from hd2bot.hd2.service import DataResult
from hd2bot.services.localization import metadata_for
from hd2bot.services.notifications import NotificationManager, PermanentPushError
from hd2bot.steam import SteamNews
from hd2bot.storage.database import Database

TOPICS = [("新闻", "news", "get_dispatches"),
          ("公告", "announcement", "get_global_events"),
          ("DSS", "dss", "get_space_stations"),
          ("战役", "campaign", "get_campaigns")]
ALL_TOPICS = [*TOPICS, ("区域", "region", "get_planet_regions"),
              ("补丁", "patch", "get_patch_notes")]


class PublicService:
    def __init__(self):
        self.now = datetime(2026, 9, 16, 8, tzinfo=UTC).timestamp()
        self.stale = set()
        self.calls = []
        self.news = [Dispatch(10, "历史新闻")]
        self.events = [GlobalEvent(20, "已有公告")]
        self.stations = [SpaceStation(1, 64, tactical_actions=[
            TacticalAction(1, "轨道轰炸", 1, costs=[TacticalCost(1, 100, 1000)]),
        ])]
        self.planets = [Planet(64, "梅里迪亚", faction=Faction.TERMINIDS)]
        self.campaigns = [Campaign(30, self.planets[0])]
        self.regions = [PlanetRegion(64, 1, faction=Faction.TERMINIDS, available=True,
                                     health=900, max_health=1000, players=50)]
        self.patches = [self.patch("10000", "已有补丁", when=self.now - 3600)]

    def clock(self):
        return self.now

    def data(self, method, value):
        self.calls.append(method)
        return DataResult(value, "mock", datetime.fromtimestamp(self.now, UTC),
                          stale=method in self.stale)

    async def get_dispatches(self):
        return self.data("get_dispatches", self.news)

    async def get_global_events(self):
        return self.data("get_global_events", self.events)

    async def get_space_stations(self):
        return self.data("get_space_stations", self.stations)

    async def get_planets(self):
        return self.data("get_planets", self.planets)

    async def get_campaigns(self):
        return self.data("get_campaigns", self.campaigns)

    async def get_planet_regions(self):
        return self.data("get_planet_regions", self.regions)

    async def get_patch_notes(self):
        return self.data("get_patch_notes", tuple(self.patches))

    def patch(self, gid, title, *, when=None, patch=True):
        return SteamNews(gid, title, "正文", f"https://store.steampowered.com/news/app/553850/view/{gid}",
                         datetime.fromtimestamp(self.now if when is None else when, UTC), patch)

    def change(self, label):
        if label == "新闻":
            self.news.append(Dispatch(11, "新新闻"))
        elif label == "公告":
            self.events.append(GlobalEvent(21, "新公告"))
        elif label == "DSS":
            self.stations[0].planet_index = 127
        elif label == "区域":
            self.regions.append(PlanetRegion(64, 2, faction=Faction.HUMANS, available=False))
        elif label == "补丁":
            self.patches.append(self.patch("9000", "新补丁"))
        else:
            self.campaigns.append(Campaign(31, self.planets[0]))


@pytest.fixture
async def public_setup(tmp_path):
    async with Database(tmp_path / "bot.db") as db:
        service = PublicService()
        manager = NotificationManager(db, service, steam=service, clock=service.clock)
        yield db, service, manager


@pytest.mark.parametrize("label,topic,method", ALL_TOPICS)
async def test_new_topics_establish_a_baseline_then_notify_and_persist(public_setup,
                                                                   label, topic, method):
    db, service, manager = public_setup
    sender = AsyncMock()
    response = await manager.handle_command("订阅", label, "c2c", "allowed")
    assert "已订阅" in response
    row = await db.fetch_one("SELECT * FROM subscriptions")
    assert row["topic"] == topic and row["baseline_json"] is not None
    await manager.tick(sender)
    sender.assert_not_called()
    service.change(label)
    await manager.tick(sender)
    sender.assert_awaited_once()
    assert sender.call_args.args[:2] == ("c2c", "allowed")
    assert "模拟数据" in sender.call_args.args[2] and "数据时间" in sender.call_args.args[2]
    service.now += 300
    manager = NotificationManager(db, service, steam=service, clock=service.clock)
    await manager.tick(sender)
    assert sender.await_count == 1
    assert label in await manager.handle_command("订阅列表", "", "c2c", "allowed")
    assert "已取消" in await manager.handle_command("取消订阅", label, "c2c", "allowed")
    assert not await db.fetch_all("SELECT * FROM subscriptions")


async def test_news_ignores_edits_disappearance_and_reappearing_old_ids(public_setup):
    db, service, manager = public_setup
    await manager.handle_command("订阅", "新闻", "c2c", "allowed")
    sender = AsyncMock()
    for items in ([Dispatch(10, "正文修订")], [], [Dispatch(7, "旧战报")],
                  [Dispatch(10, "历史重新出现")]):
        service.news = items
        await manager.tick(sender)
    sender.assert_not_called()
    service.news = [Dispatch(10, "不能补发的历史正文"), Dispatch(11, "新增正文")]
    await manager.tick(sender)
    text = sender.call_args.args[2]
    assert "新增正文" in text and "不能补发的历史正文" not in text
    state = json.loads((await db.fetch_one("SELECT baseline_json FROM subscriptions"))[0])
    assert state == {"latest_id": 11}


async def test_new_news_is_not_marked_seen_after_a_failed_send(public_setup):
    db, service, manager = public_setup
    await manager.handle_command("订阅", "新闻", "c2c", "allowed")
    service.change("新闻")
    sender = AsyncMock(side_effect=RuntimeError("temporary"))
    await manager.tick(sender)
    state = json.loads((await db.fetch_one("SELECT baseline_json FROM subscriptions"))[0])
    assert state["latest_id"] == 10
    sender.side_effect = None
    service.now += 60
    await manager.tick(sender)
    assert sender.await_count == 2
    assert len(await db.fetch_all("SELECT * FROM notification_deliveries")) == 1


async def test_announcement_only_appearing_or_ending_id_triggers(public_setup):
    _, service, manager = public_setup
    await manager.handle_command("订阅", "公告", "c2c", "allowed")
    sender = AsyncMock()
    service.events = [replace(service.events[0], title="已有公告修订")]
    await manager.tick(sender)
    sender.assert_not_called()
    service.events.append(GlobalEvent(21, "新公告"))
    await manager.tick(sender)
    assert "新公告" in sender.call_args.args[2]
    assert "已有公告修订" not in sender.call_args.args[2]
    service.now += 300
    service.events = [service.events[1]]
    await manager.tick(sender)
    assert sender.await_count == 2
    assert "已有公告修订" in sender.call_args.args[2]
    assert "结束或撤下" in sender.call_args.args[2]


async def test_expired_announcement_is_excluded_and_expiry_can_end_existing_event(public_setup):
    _, service, manager = public_setup
    now = datetime.fromtimestamp(service.now, UTC)
    service.events = [GlobalEvent(20, "当前公告", expires_at=now + timedelta(minutes=1)),
                      GlobalEvent(19, "过去公告", expires_at=now - timedelta(minutes=1))]
    await manager.handle_command("订阅", "公告", "c2c", "allowed")
    sender = AsyncMock()
    service.now += 60
    await manager.tick(sender)
    sender.assert_awaited_once()
    assert "当前公告" in sender.call_args.args[2] and "过去公告" not in sender.call_args.args[2]


async def test_dss_ignores_resource_progress_expiry_and_unknown_states(public_setup):
    _, service, manager = public_setup
    await manager.handle_command("订阅", "DSS", "c2c", "allowed")
    sender = AsyncMock()
    station, = service.stations
    action, = station.tactical_actions
    action.costs = [TacticalCost(1, 200, 1000)]
    action.expires_at = datetime.fromtimestamp(service.now + 600, UTC)
    station.election_ends_at = datetime.fromtimestamp(service.now + 1200, UTC)
    await manager.tick(sender)
    for unknown in [None, 99]:
        action.status = unknown
        station.planet_index = None
        await manager.tick(sender)
    sender.assert_not_called()
    action.status, station.planet_index = 1, 64
    await manager.tick(sender)
    sender.assert_not_called()
    action.status = 2
    await manager.tick(sender)
    sender.assert_awaited_once()
    assert "轨道轰炸" in sender.call_args.args[2]
    assert "准备中" in sender.call_args.args[2] and "已启用" in sender.call_args.args[2]


async def test_dss_missing_station_or_new_unknown_action_does_not_invent_a_transition(public_setup):
    _, service, manager = public_setup
    await manager.handle_command("订阅", "DSS", "c2c", "allowed")
    saved = service.stations
    sender = AsyncMock()
    service.stations = []
    await manager.tick(sender)
    service.stations = saved
    saved[0].tactical_actions.append(TacticalAction(2, "新行动", None))
    await manager.tick(sender)
    saved[0].tactical_actions[1].status = 1
    await manager.tick(sender)
    sender.assert_not_called()
    saved[0].tactical_actions[1].status = 2
    await manager.tick(sender)
    assert "新行动" in sender.call_args.args[2]


async def test_campaign_removal_does_not_infer_victory_and_owner_changes_use_planets(public_setup):
    _, service, manager = public_setup
    await manager.handle_command("订阅", "战役", "c2c", "allowed")
    service.campaigns = []
    sender = AsyncMock()
    await manager.tick(sender)
    text = sender.call_args.args[2]
    assert "战役 #30 已结束或撤下" in text and "不据此判定胜负" in text
    assert "胜利" not in text and "失败" not in text
    service.now += 300
    service.planets[0].faction = Faction.HUMANS
    await manager.tick(sender)
    assert sender.await_count == 2
    assert "梅里迪亚" in sender.call_args.args[2]
    assert "终结族" in sender.call_args.args[2] and "超级地球" in sender.call_args.args[2]


async def test_campaign_unknown_owner_and_name_changes_do_not_notify(public_setup):
    _, service, manager = public_setup
    await manager.handle_command("订阅", "战役", "c2c", "allowed")
    sender = AsyncMock()
    service.planets[0].faction = Faction.UNKNOWN
    service.planets[0].name = "名称修正"
    await manager.tick(sender)
    service.planets[0].faction = Faction.TERMINIDS
    await manager.tick(sender)
    sender.assert_not_called()


@pytest.mark.parametrize("label,topic,method", ALL_TOPICS + [("战役", "campaign", "get_planets")])
async def test_stale_resources_never_send_or_advance_baseline(public_setup, label, topic, method):
    db, service, manager = public_setup
    await manager.handle_command("订阅", label, "c2c", "allowed")
    before = (await db.fetch_one("SELECT baseline_json FROM subscriptions"))[0]
    service.change(label)
    service.stale.add(method)
    sender = AsyncMock()
    await manager.tick(sender)
    sender.assert_not_called()
    assert (await db.fetch_one("SELECT baseline_json FROM subscriptions"))[0] == before
    service.stale.clear()
    await manager.tick(sender)
    sender.assert_awaited_once()


@pytest.mark.parametrize("label,topic,method", ALL_TOPICS)
async def test_initial_stale_snapshot_waits_for_fresh_baseline_without_backfill(
    public_setup, label, topic, method,
):
    db, service, manager = public_setup
    service.stale.add(method)
    await manager.handle_command("订阅", label, "c2c", "allowed")
    assert (await db.fetch_one("SELECT baseline_json FROM subscriptions"))[0] is None
    service.change(label)
    service.stale.clear()
    sender = AsyncMock()
    await manager.tick(sender)
    sender.assert_not_called()
    assert (await db.fetch_one("SELECT baseline_json FROM subscriptions"))[0] is not None


async def test_new_topics_share_rolling_target_quota_and_minimum_gap(public_setup):
    _, service, manager = public_setup
    sender = AsyncMock()
    for label, _, _ in TOPICS:
        await manager.handle_command("订阅", label, "c2c", "allowed")
        service.change(label)
    await manager.tick(sender)
    assert sender.await_count == 1
    service.now += 299
    await manager.tick(sender)
    assert sender.await_count == 1
    service.now += 1
    await manager.tick(sender)
    assert sender.await_count == 2
    for _ in range(2):
        service.now += 300
        await manager.tick(sender)
    assert sender.await_count == 4
    for identity in (12, 13, 14):
        service.now += 300
        service.news.append(Dispatch(identity, "更新"))
        await manager.tick(sender)
    assert sender.await_count == 5
    service.now += 86400
    await manager.tick(sender)
    assert sender.await_count == 6


async def test_platform_refusal_pauses_news_until_explicit_resubscribe(public_setup):
    db, service, manager = public_setup
    await manager.handle_command("订阅", "新闻", "c2c", "allowed")
    service.change("新闻")
    sender = AsyncMock(side_effect=PermanentPushError("platform_denied"))
    await manager.tick(sender)
    assert (await db.fetch_one("SELECT enabled FROM subscriptions"))[0] == 0
    service.now += 86400
    await manager.tick(sender)
    assert sender.await_count == 1
    assert not await db.fetch_all("SELECT * FROM notification_deliveries")
    await manager.handle_command("订阅", "新闻", "c2c", "allowed")
    sender.side_effect = None
    await manager.tick(sender)
    assert sender.await_count == 1
    service.news.append(Dispatch(12, "恢复后的新闻"))
    await manager.tick(sender)
    assert sender.await_count == 2


@pytest.mark.parametrize("label,topic,method", ALL_TOPICS)
async def test_each_new_topic_enforces_private_recipient_before_fetch(public_setup,
                                                                   label, topic, method):
    db, service, _ = public_setup

    class Access:
        async def allowed(self, scope, target):
            return scope == "c2c" and target == "allowed"

    manager = NotificationManager(db, service, steam=service, clock=service.clock, access=Access())
    for scope, target in [("group", "allowed"), ("c2c", "another")]:
        assert "指定测试账号" in await manager.handle_command("订阅", label, scope, target)
    assert not service.calls and not await db.fetch_all("SELECT * FROM subscriptions")
    assert "已订阅" in await manager.handle_command("订阅", label, "c2c", "allowed")
    assert method in service.calls


async def test_region_health_players_and_unknown_fields_do_not_trigger(public_setup):
    _, service, manager = public_setup
    await manager.handle_command("订阅", "区域", "c2c", "allowed")
    sender = AsyncMock()
    original, = service.regions
    service.regions = [replace(original, health=750, players=400)]
    await manager.tick(sender)
    service.regions = [replace(original, faction=Faction.UNKNOWN, available=None)]
    await manager.tick(sender)
    service.regions = [original]
    await manager.tick(sender)
    sender.assert_not_called()
    service.regions = [replace(original, faction=Faction.HUMANS)]
    await manager.tick(sender)
    assert "终结族 → 超级地球" in sender.call_args.args[2]
    service.now += 300
    service.regions = [replace(service.regions[0], available=False)]
    await manager.tick(sender)
    assert sender.await_count == 2
    assert "当前不可进入" in sender.call_args.args[2]


async def test_region_identity_includes_planet_and_removal_does_not_claim_victory(public_setup):
    _, service, manager = public_setup
    await manager.handle_command("订阅", "区域", "c2c", "allowed")
    service.regions.append(PlanetRegion(127, 1, faction=Faction.HUMANS, available=True))
    sender = AsyncMock()
    await manager.tick(sender)
    assert "新增区域" in sender.call_args.args[2]
    service.now += 300
    service.regions = [service.regions[1]]
    await manager.tick(sender)
    assert sender.await_count == 2
    assert "区域记录已结束或撤下" in sender.call_args.args[2]
    assert metadata_for(64)["name"] in sender.call_args.args[2]
    assert "胜利" not in sender.call_args.args[2]


async def test_patch_uses_unseen_ids_not_gid_order_and_ignores_edits_and_old_posts(public_setup):
    db, service, manager = public_setup
    await manager.handle_command("订阅", "补丁", "c2c", "allowed")
    sender = AsyncMock()
    original = service.patches[0]
    for posts in ([replace(original, title="补丁正文更正")], [],
                  [service.patch("20000", "未见过但已过期的补丁", when=service.now - 30)],
                  [service.patch("30000", "普通公告", patch=False)]):
        service.patches = posts
        await manager.tick(sender)
    sender.assert_not_called()
    service.now += 60
    service.patches = [original, service.patch("5", "新的热修复")]
    await manager.tick(sender)
    sender.assert_awaited_once()
    text = sender.call_args.args[2]
    assert "新的热修复" in text and "已有补丁" not in text
    assert "Steam 官方补丁" in text and "https://store.steampowered.com" in text
    state = json.loads((await db.fetch_one("SELECT baseline_json FROM subscriptions"))[0])
    assert "5" in state["ids"] and "10000" in state["ids"]


async def test_empty_initial_patch_feed_does_not_backfill_old_posts(public_setup):
    _, service, manager = public_setup
    service.patches = []
    await manager.handle_command("订阅", "补丁", "c2c", "allowed")
    service.patches = [service.patch("1234", "历史补丁", when=service.now - 86400)]
    sender = AsyncMock()
    await manager.tick(sender)
    sender.assert_not_called()
    service.now += 300
    service.patches.append(service.patch("12", "新补丁"))
    await manager.tick(sender)
    assert sender.await_count == 1


async def test_patch_service_failure_uses_notification_backoff_and_keeps_baseline(public_setup):
    db, service, manager = public_setup
    await manager.handle_command("订阅", "补丁", "c2c", "allowed")
    baseline = (await db.fetch_one("SELECT baseline_json FROM subscriptions"))[0]
    service.get_patch_notes = AsyncMock(side_effect=TimeoutError())
    sender = AsyncMock()
    await manager.tick(sender)
    row = await db.fetch_one("SELECT * FROM subscriptions")
    assert row["baseline_json"] == baseline and row["retry_at"] == service.now + 60
    assert row["failures"] == 1
    sender.assert_not_called()


async def test_patch_subscription_without_steam_reports_unavailable_and_can_unsubscribe(public_setup):
    db, service, manager = public_setup
    manager.steam = None
    assert "尚未配置" in await manager.handle_command("订阅", "补丁", "c2c", "allowed")
    assert not await db.fetch_all("SELECT * FROM subscriptions")
    assert "已取消" in await manager.handle_command("取消订阅", "补丁", "c2c", "allowed")
    assert not service.calls


async def test_patch_and_region_share_target_gap_and_quota_with_other_topics(public_setup):
    _, service, manager = public_setup
    for label in ("补丁", "区域", "新闻"):
        await manager.handle_command("订阅", label, "c2c", "allowed")
        service.change(label)
    sender = AsyncMock()
    for expected in range(1, 4):
        await manager.tick(sender)
        assert sender.await_count == expected
        service.now += 300
    for index in range(10, 14):
        service.patches.append(service.patch(str(index), "新的补丁"))
        await manager.tick(sender)
        service.now += 300
    assert sender.await_count == 5
