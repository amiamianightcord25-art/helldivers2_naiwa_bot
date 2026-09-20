"""Offline warbond browsing built from the bundled Wiki catalogue."""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from hd2bot.commands.parser import parse_command
from hd2bot.presentation import ChatContext
from hd2bot.router import CommandRouter
from hd2bot.wiki.service import CatalogService

CATALOG = Path(__file__).resolve().parents[1] / "data" / "wiki_catalog.json"


@pytest.mark.parametrize("text,argument", [
    ("战争债券 铁血老兵", "铁血老兵"),
    ("战争债券铁血老兵", "铁血老兵"),
    ("warbonds Steeled Veterans", "Steeled Veterans"),
])
def test_warbond_command_accepts_chinese_and_english_names(text, argument):
    command = parse_command(text)
    assert (command.name, command.argument) == ("战争债券", argument)


def test_warbond_index_is_a_paginated_local_snapshot():
    service = CatalogService(CATALOG)
    context = ChatContext("group", "group", "member")

    reply = service.handle("战争债券", context=context)

    assert "战争债券目录 · 第 1/" in reply.text
    assert "本地 Wiki 资料快照" in reply.text
    assert "实时轮换" in reply.text
    assert reply.card is not None
    assert reply.card.eyebrow == "HELLDIVERS 2 / WARBONDS"
    assert "第 2/" in service.handle("下一页", context=context).text


def test_warbond_detail_lists_related_equipment_and_supports_selection():
    service = CatalogService(CATALOG)
    context = ChatContext("c2c", "user", "user")

    reply = service.handle("战争债券", "铁血老兵", context=context)

    assert "铁血老兵（Steeled Veterans）" in reply.text
    assert "债券费用：" in reply.text
    assert "关联条目 · 第 1/" in reply.text
    assert "实时轮换" in reply.text
    assert reply.card is not None
    assert reply.card.title == "铁血老兵"

    detail = service.handle("选择", "1", context=context)
    assert "Helldivers Wiki 贡献者" in detail.text


@pytest.mark.asyncio
async def test_router_serves_warbonds_without_live_provider_calls():
    catalog = CatalogService(CATALOG)
    hd2 = AsyncMock()
    router = CommandRouter(hd2, catalog=catalog)

    reply = await router.respond("战争债券 民主爆破")

    assert "民主爆破（Democratic Detonation）" in reply.text
    assert reply.card is not None
    assert not hd2.mock_calls
