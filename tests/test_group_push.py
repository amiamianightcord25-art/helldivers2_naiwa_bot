import json
from unittest.mock import AsyncMock

import pytest
from test_notifications import Clock, Service
from test_qq import make_event

from hd2bot.config import Settings
from hd2bot.presentation import ChatContext, CommandReply
from hd2bot.qq.adapter import QQDispatcher
from hd2bot.router import CommandRouter
from hd2bot.services.group_push import DEFAULT_POLICY, GroupPushAccess
from hd2bot.services.notifications import NotificationManager
from hd2bot.services.push_access import PushAccess
from hd2bot.storage.database import Database


@pytest.fixture
async def setup_group(tmp_path):
    async with Database(tmp_path / "bot.db") as db:
        config = tmp_path / "group_push.json"
        config.write_text(json.dumps(DEFAULT_POLICY))
        clock = Clock()
        service = Service(clock)
        private = PushAccess(db, "", tmp_path / "no-pairing.json")
        access = GroupPushAccess(private, db, config, "app-one", clock=clock)
        manager = NotificationManager(db, service, clock=clock, access=access)
        router = CommandRouter(service, notifications=manager)
        yield db, clock, service, access, manager, router, config


def context(role="member", group="g", user="u"):
    return ChatContext("group", group, user, role)


async def test_no_group_push_until_admin_enable_and_subscription(setup_group):
    db, clock, service, access, manager, router, _ = setup_group
    sender = AsyncMock()
    assert "群主" in (await router.respond("开启推送", context=context())).text
    assert not await access.allowed("group", "g")
    assert "尚未开启" in (await router.respond("订阅 战况 30", context=context("owner"))).text
    await manager.tick(sender)
    assert service.calls == 0
    await router.respond("开启推送", context=context("owner"))
    await manager.tick(sender)
    sender.assert_not_awaited()
    assert await db.fetch_all("SELECT * FROM subscriptions") == []
    reply = await router.respond("订阅 战况 30", context=context("owner"))
    assert "已订阅" in reply.text
    await manager.tick(sender)
    sender.assert_not_awaited()
    clock.advance(1800)
    await manager.tick(sender)
    assert sender.await_args.args[:2] == ("group", "g")


async def test_group_members_cannot_edit_or_cancel_but_can_read_status(setup_group):
    db, _, _, _, _, router, _ = setup_group
    await router.respond("开启推送", context=context("admin"))
    await router.respond("订阅 战况 30", context=context("admin"))
    for command in ("暂停推送", "取消订阅", "订阅 新闻", "推送设置 每日上限 20"):
        assert "管理" in (await router.respond(command, context=context())).text
    assert len(await db.fetch_all("SELECT * FROM subscriptions")) == 1
    assert "战况" in (await router.respond("订阅列表", context=context())).text
    assert "每日上限" in (await router.respond("推送设置", context=context())).text


async def test_pause_resume_and_restart_keep_group_configuration(setup_group):
    db, clock, service, access, manager, router, config = setup_group
    sender = AsyncMock()
    await router.respond("开启推送", context=context("owner"))
    await router.respond("订阅 战况 30", context=context("owner"))
    clock.advance(1800)
    await router.respond("暂停推送", context=context("owner"))
    await manager.tick(sender)
    sender.assert_not_awaited()
    restored = GroupPushAccess(access.private, db, config, "app-one", clock=clock)
    assert not await restored.allowed("group", "g")
    await router.respond("恢复推送", context=context("owner"))
    await manager.tick(sender)
    sender.assert_not_awaited()
    clock.advance(1800)
    await manager.tick(sender)
    sender.assert_awaited_once()
    changed_app = GroupPushAccess(access.private, db, config, "app-two", clock=clock)
    assert not await changed_app.allowed("group", "g")


async def test_daily_limit_gap_and_default_interval_are_enforced(setup_group):
    _, clock, _, _, manager, router, _ = setup_group
    admin = context("owner")
    sender = AsyncMock()
    for command in (
        "开启推送",
        "推送设置 每日上限 2",
        "推送设置 最短间隔 60",
        "推送设置 默认间隔 30",
    ):
        await router.respond(command, context=admin)
    reply = await router.respond("订阅 战况", context=admin)
    assert "每 30 分钟" in reply.text and "最多 2 条" in reply.text
    clock.advance(1800)
    await manager.tick(sender)
    assert sender.await_count == 1
    clock.advance(1800)
    await manager.tick(sender)
    assert sender.await_count == 1
    clock.advance(1800)
    await manager.tick(sender)
    assert sender.await_count == 2
    clock.advance(7200)
    await manager.tick(sender)
    assert sender.await_count == 2


async def test_global_policy_disable_and_group_allowlist(setup_group):
    _, _, _, access, _, router, config = setup_group
    await router.respond("开启推送", context=context("owner"))
    config.write_text(json.dumps({**DEFAULT_POLICY, "enabled": False}))
    assert not await access.allowed("group", "g")
    config.write_text(json.dumps({**DEFAULT_POLICY, "allowed_groups": ["another"]}))
    assert not await access.allowed("group", "g")
    config.write_text("{invalid")
    assert not await access.allowed("group", "g")


async def test_configured_manager_fallback_and_private_isolation(setup_group):
    _, _, _, access, _, router, config = setup_group
    config.write_text(json.dumps({**DEFAULT_POLICY, "manager_user_ids": ["maintainer"]}))
    reply = await router.respond("开启推送", context=context(user="maintainer"))
    assert "已开启" in reply.text
    assert not await access.allowed("c2c", "maintainer")
    assert not await access.allowed("group", "other-group")


async def test_members_cannot_forge_role_in_command_text(setup_group):
    _, _, _, access, _, router, _ = setup_group
    await router.respond("开启推送 owner", context=context())
    assert not await access.allowed("group", "g")
    reply = await router.respond("推送设置 每日上限 999", context=context("owner"))
    assert "1～50" in reply.text


async def test_group_role_is_copied_from_platform_event_only():
    router = AsyncMock()
    router.respond.return_value = CommandReply("ok")
    bot = AsyncMock()
    dispatcher = QQDispatcher(Settings(), router)
    message = make_event("group", content="推送设置")
    message.author.member_role = "admin"
    await dispatcher.process(message, bot)
    assert router.respond.await_args.kwargs["context"].group_role == "admin"
    await dispatcher.close()


async def test_pause_while_fetch_inflight_blocks_send(setup_group):
    _, clock, service, access, manager, router, _ = setup_group
    await router.respond("开启推送", context=context("owner"))
    await router.respond("订阅 战况 30", context=context("owner"))
    original = service.get_war

    async def fetch():
        await access.configure("暂停推送", "", context("owner"))
        return await original()

    service.get_war = fetch
    sender = AsyncMock()
    clock.advance(1800)
    await manager.tick(sender)
    sender.assert_not_awaited()
