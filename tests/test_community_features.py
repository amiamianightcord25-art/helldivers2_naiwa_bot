import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from hd2bot.hd2.service import DataResult
from hd2bot.presentation import ChatContext
from hd2bot.router import CommandRouter
from hd2bot.services.checkin import CheckinService
from hd2bot.services.stratagem_hero import Stratagem, StratagemHeroService
from hd2bot.storage.database import Database


@pytest.mark.parametrize("scope", ["c2c", "group", "dms", "channel"])
async def test_signin_and_arcade_through_router_in_every_scene(tmp_path, scope):
    async with Database(tmp_path / "bot.db") as db:
        checkin = CheckinService(db)
        hero = StratagemHeroService(db, stratagems=(Stratagem("test", "测试战备", "↑↓←→"),))
        router = CommandRouter(None, checkin=checkin, hero=hero)
        context = ChatContext(scope, "chat", "member", display_name="战友")
        first = await router.respond("/签到", context=context)
        again = await router.respond("／签到", context=context)
        assert "100 经验" in first.text
        assert "已经签到" in again.text
        level = await router.respond("我的等级", context=context)
        assert "100" in level.text
        game = await router.respond("<@123> ／随机战备", context=context)
        assert "测试战备" in game.text and "↑ ↓ ← →" in game.text
        answer = await router.respond("<@123> ⬆️⬇️⬅️➡️", context=context)
        assert "正确！+20 分" in answer.text
        fullwidth = await router.respond("＾ｖ＜＞", context=context)
        assert "正确！+20 分" in fullwidth.text
        vertical = await router.respond("︿﹀﹤﹥", context=context)
        assert "正确！+20 分" in vertical.text
        await router.respond("结束游戏", context=context)
        score = await router.respond("战备记录", context=context)
        assert "60" in score.text


async def test_game_does_not_take_dss_command_or_other_users_arrows(tmp_path):
    async with Database(tmp_path / "bot.db") as db:
        hero = StratagemHeroService(db, stratagems=(Stratagem("test", "测试战备", "→↓↓"),))
        service = AsyncMock()
        service.get_space_stations.return_value = DataResult([], "mock", datetime.now(UTC))
        router = CommandRouter(service, hero=hero)
        context = ChatContext("group", "chat", "member")
        await router.respond("战备英雄", context=context)
        # Uppercase DSS remains the real station command, not a WASD answer.
        dss_reply = await router.respond("DSS", context=context)
        assert dss_reply.card is not None and "没有空间站" in dss_reply.text
        service.get_space_stations.assert_awaited_once()
        other = await router.respond("→↓↓", context=ChatContext("group", "chat", "other"))
        assert "正确" not in other.text
        state = await db.fetch_one("SELECT state_json FROM stratagem_hero_sessions")
        assert json.loads(state["state_json"])["score"] == 0
        answer = await router.respond("→↓↓", context=context)
        assert "正确" in answer.text


async def test_real_qq_event_decodes_ascii_arrows_once(tmp_path):
    from test_qq import make_event

    from hd2bot.config import Settings
    from hd2bot.qq.adapter import QQDispatcher

    async with Database(tmp_path / "bot.db") as db:
        hero = StratagemHeroService(db, stratagems=(Stratagem("test", "测试战备", "↑↓←→"),))
        dispatcher = QQDispatcher(Settings(), CommandRouter(None, hero=hero))
        bot = AsyncMock()
        await dispatcher.process(make_event(content="战备英雄", message_id="start"), bot)
        await dispatcher.process(make_event(content="^v&lt;&gt;", message_id="answer"), bot)
        assert "正确！+20 分" in bot.post_c2c_messages.await_args.kwargs["markdown"].content
        # A literal user-typed &lt; is encoded as &amp;lt; and must not decode twice.
        await dispatcher.process(make_event(content="^v&amp;lt;&amp;gt;", message_id="literal"), bot)
        assert "无法识别" in bot.post_c2c_messages.await_args.kwargs["markdown"].content
        state = await db.fetch_one("SELECT state_json FROM stratagem_hero_sessions")
        assert json.loads(state["state_json"])["score"] == 20
        await dispatcher.close()
