from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from hd2bot.commands import Command, parse_command
from hd2bot.hd2.errors import CommandError
from hd2bot.hd2.models import Dispatch
from hd2bot.hd2.service import DataResult, HD2Service
from hd2bot.intelligence import (
    format_dispatch_detail,
    format_dispatches,
    parse_dispatch_argument,
)
from hd2bot.router import CommandRouter


def _dispatches(count: int = 7) -> list[Dispatch]:
    now = datetime(2026, 9, 19, 3, tzinfo=UTC)
    return [Dispatch(index, f"<i=3>标题 {index}</i>\n正文 {index}",
                     now - timedelta(hours=index)) for index in range(1, count + 1)]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("新闻", Command("新闻")),
        ("新闻 列表", Command("新闻", "列表")),
        ("新闻 列表 2", Command("新闻", "列表 2")),
        ("新闻 123", Command("新闻", "123")),
        ("战报 123", Command("新闻", "123")),
    ],
)
def test_news_parser_supports_unambiguous_list_and_detail_queries(text, expected):
    assert parse_command(text) == expected


@pytest.mark.parametrize("text", [
    "新闻 foo", "新闻 列表 0", "新闻 列表 第二页", "新闻 " + "9" * 21,
    "新闻 列表 " + "9" * 21,
])
def test_news_parser_rejects_ambiguous_or_invalid_arguments(text):
    with pytest.raises(CommandError, match="新闻用法"):
        parse_command(text)


def test_dispatch_argument_parser_keeps_numeric_ids_as_details():
    assert parse_dispatch_argument("") == ("page", 1)
    assert parse_dispatch_argument("列表 3") == ("page", 3)
    assert parse_dispatch_argument("987") == ("detail", 987)


def test_dispatch_list_is_capped_at_recent_25_and_paginated():
    result = format_dispatches(_dispatches(30), page=2, page_size=5, history_limit=25)
    assert "最近 25 条 · 第 2/5 页" in result
    assert "战报 #25" in result and "战报 #21" in result
    assert "战报 #26" not in result and "战报 #20" not in result
    assert "标题 25" in result and "<i=" not in result


def test_dispatch_detail_formats_one_item_and_unknown_returns_none():
    items = _dispatches(2)
    result = format_dispatch_detail(items, 2)
    assert result is not None
    assert "战报 #2" in result and "正文 2" in result and "<i=" not in result
    assert format_dispatch_detail(items, 99) is None


@pytest.mark.asyncio
async def test_router_news_list_and_detail_reuse_one_dispatch_fetch():
    service = AsyncMock(spec=HD2Service)
    service.get_dispatches.return_value = DataResult(
        _dispatches(), "mock", datetime(2026, 9, 19, 3, tzinfo=UTC), False,
    )
    router = CommandRouter(service)

    page = await router.handle("新闻 列表 2")
    detail = await router.handle("新闻 3")
    missing = await router.handle("新闻 99")

    assert "第 2/2 页" in page and "战报 #2" in page
    assert "战报 #3" in detail and "正文 3" in detail
    assert "未找到战报 #99" in missing and "新闻 列表" in missing
    assert service.get_dispatches.await_count == 3
