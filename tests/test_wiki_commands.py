"""Official QQ/CLI command integration for the offline equipment reference."""

import json
from unittest.mock import AsyncMock

import pytest
from test_qq import make_event

from hd2bot.commands.parser import parse_command
from hd2bot.config import Settings
from hd2bot.presentation import ChatContext
from hd2bot.qq.adapter import QQDispatcher
from hd2bot.router import CommandRouter
from hd2bot.wiki.service import CatalogService


@pytest.mark.parametrize("text,name,argument", [
    ("武器解放者", "武器", "解放者"), ("／武器 AR23", "武器", "AR23"),
    ("<@123> 下一页", "下一页", ""), ("武器列表", "武器", ""),
    ("<@123> ２", "选择", "2"), ("选择 3", "选择", "3"),
    ("护甲", "盔甲", ""), ("wiki breaker", "百科", "breaker"),
    ("装饰品", "装饰", ""), ("百科状态", "资料状态", ""),
])
def test_equipment_command_names_and_relaxed_input(text, name, argument):
    command = parse_command(text)
    assert (command.name, command.argument) == (name, argument)


@pytest.fixture
def catalog(tmp_path):
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps({
        "schema_version": 1, "synced_at": "2026-09-16T12:00:00Z",
        "source": {"name": "Helldivers Wiki", "url": "https://helldivers.wiki.gg/",
                   "license": "CC BY-NC-SA 4.0",
                   "license_url": "https://creativecommons.org/licenses/by-nc-sa/4.0/"},
        "entries": [{"id": "weapons:primary:base", "category": "weapons", "name": "AR-23 Liberator",
                     "code": "AR-23", "aliases": ["解放者", "步枪"],
                     "summary": "合成的基础步枪条目", "fields": [["类型", "突击步枪"]],
                     "source_url": "https://helldivers.wiki.gg/wiki/AR-23_Liberator"},
                    {"id": "weapons:primary:variant", "category": "weapons", "name": "AR-23P Liberator Penetrator",
                     "code": "AR-23P", "aliases": ["穿甲解放者", "步枪"],
                     "summary": "合成的穿甲步枪条目", "fields": [],
                     "source_url": "https://helldivers.wiki.gg/wiki/AR-23P_Liberator_Penetrator"}],
    }), encoding="utf8")
    return CatalogService(path)


async def test_catalog_queries_do_not_touch_live_game_or_steam_services(catalog):
    hd2, steam = AsyncMock(), AsyncMock()
    router = CommandRouter(hd2, steam=steam, catalog=catalog)
    reply = await router.respond("武器 AR23")
    assert "AR-23 Liberator" in reply.text and "穿甲步枪" not in reply.text
    assert not hd2.mock_calls and not steam.mock_calls
    listing = await router.handle("武器 步枪")
    assert "AR-23P" in listing and "AR-23" in listing
    selected = await router.handle("选择 1")
    assert "来源" in selected


async def test_official_group_selection_is_per_sender_and_passive(catalog):
    settings = Settings(provider="mock", image_enabled=False)
    router = CommandRouter(AsyncMock(), catalog=catalog)
    dispatcher = QQDispatcher(settings, router)
    bot = AsyncMock()
    try:
        await dispatcher.process(make_event("group", "menu", "武器 步枪"), bot)
        menu = bot.post_group_messages.await_args.kwargs["markdown"].content
        assert "AR-23P" in menu
        other = make_event("group", "other-choice", "选择 1")
        other.author = other.author.model_copy(update={"member_openid": "different-member"})
        await dispatcher.process(other, bot)
        denied = bot.post_group_messages.await_args.kwargs["content"]
        assert "合成的" not in denied
        await dispatcher.process(make_event("group", "owner-choice", "选择 1"), bot)
        reply = bot.post_group_messages.await_args.kwargs
        assert "合成的" in reply["markdown"].content
        assert reply["msg_id"] == "owner-choice" and reply["group_openid"] == "group-local"
        bot.post_c2c_messages.assert_not_called()
    finally:
        await dispatcher.close()


def test_chat_identity_is_not_exposed_by_repr():
    assert "private" not in repr(ChatContext("group", "private-group", "private-user"))


async def test_stable_id_can_select_an_entry_without_remembering_its_name(catalog):
    reply = await CommandRouter(AsyncMock(), catalog=catalog).respond("百科 weapons:primary:variant")
    assert "AR-23P Liberator Penetrator" in reply.text and "合成的穿甲步枪条目" in reply.text
