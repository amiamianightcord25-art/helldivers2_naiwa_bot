from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_qq import make_event
from test_snapshot_sharing import CODE, stats

from hd2bot.config import Settings
from hd2bot.presentation import ChatContext
from hd2bot.qq.adapter import QQDispatcher
from hd2bot.router import CommandRouter
from hd2bot.services.tips import TipsService, load_tips


@pytest.mark.parametrize("scope", ["c2c", "group", "channel", "dms"])
async def test_bundled_tips_are_available_offline_in_all_scenes(scope):
    hd2 = AsyncMock()
    router = CommandRouter(hd2, tips=TipsService())
    context = ChatContext(scope, "room", "reader")
    first = await router.respond("<@123> ／小贴士", context=context)
    second = await router.respond("tips", context=context)
    assert "加载小贴士" in first.text and "中文为参考翻译" in first.text
    assert first.text != second.text
    assert first.keyboard[0][0].command == "小贴士"
    source = await router.respond("小贴士 来源", context=context)
    assert "Training_Manual_Tips?oldid=108731" in source.text
    assert not hd2.mock_calls


def test_shipped_tips_keep_english_provenance_and_translation_status():
    tips = load_tips()
    assert len(tips) == 83
    assert len({tip.english for tip in tips}) == len(tips)
    assert all(tip.translation_status == "reference_translation" for tip in tips)
    assert all(tip.source_url.endswith("Training_Manual_Tips?oldid=108731") for tip in tips)
    service = TipsService()
    assert "本研究由 Permacura 资助" in service.reply("lore-03").text
    assert "懦夫" in service.reply("general-05").text


async def test_group_snapshot_keeps_image_copyable_id_and_clickers_query_entry():
    career = AsyncMock()
    career.query_shared.return_value = stats()
    renderer = AsyncMock()
    renderer.render.return_value = b"jpeg"
    bot = AsyncMock()
    bot.post_group_files.return_value = SimpleNamespace(file_info="uploaded-image")
    dispatcher = QQDispatcher(Settings(), CommandRouter(None, career), renderer)
    try:
        await dispatcher.process(make_event("group", "shared", "查战绩 " + CODE), bot)
        messages = bot.post_group_messages.await_args_list
        assert len(messages) == 2
        assert messages[0].kwargs["msg_type"] == 7
        assert messages[1].kwargs["msg_type"] == 2
        assert CODE in messages[1].kwargs["markdown"].content
        button = messages[1].kwargs["keyboard"].content.rows[0].buttons[0]
        assert button.render_data.label == "我也要查询"
        assert button.action.data == "获取战绩" and button.action.permission.type == 2
        assert [call.kwargs["msg_seq"] for call in messages] == [1, 2]
        career.reset_mock()
        await dispatcher.process(make_event("group", "cta", button.action.data), bot)
        assert "私聊" in bot.post_group_messages.await_args.kwargs["content"]
        career.query_shared.assert_not_awaited()
        career.query_user.assert_not_awaited()
    finally:
        await dispatcher.close()
