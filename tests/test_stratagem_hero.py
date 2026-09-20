import asyncio
import json

import pytest

from hd2bot.services.stratagem_hero import (
    ArrowInputError,
    Stratagem,
    StratagemHeroService,
    is_arrow_input,
    load_stratagems,
    normalize_arrows,
)
from hd2bot.storage.database import Database


class Clock:
    def __init__(self):
        self.value = 10_000

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


PRECISION = Stratagem("precision", "轨道精确打击", "→→↑")
REINFORCE = Stratagem("reinforce", "增援", "↑↓→←↑")
KEY = {"scope": "group", "target_id": "g1", "user_id": "u1", "display_name": "潜兵甲"}


async def state_for(db, **overrides):
    key = KEY | overrides
    row = await db.fetch_one(
        "SELECT state_json FROM stratagem_hero_sessions "
        "WHERE scope = ? AND target_id = ? AND user_id = ?",
        (key["scope"], key["target_id"], key["user_id"]),
    )
    return json.loads(row["state_json"]) if row else None


@pytest.mark.parametrize("value", [
    "↑↓←→", "⬆️⬇️⬅️➡️", "⬆︎⬇︎⬅︎➡︎", "↑ ↓ ← →", "^v<>", "＾ｖ＜＞",
    "ＷＳＡＤ", "WsAd", "上下左右", "向上,向下;向左|向右", "up down left right",
    "上箭头 下箭头 左箭头 右箭头", "⇧⇩⇦⇨", "⇑⇓⇐⇒", "🔼🔽◀️▶️", "△▽◁▷",
    "⮝⮟⮜⮞", "🡅🡇🡄🡆", "^ v <- ->", "↑，↓、←；→", "↑\n↓\t←　→",
    "⏫⏬⏪⏩", "👆👇👈👉", "👆🏻👇🏽👈🏿👉🏼", "︿﹀﹤﹥", "[上][下][左][右]",
    "［上箭头］［下箭头］［左箭头］［右箭头］", "☝️👇🏻👈🏾👉",
])
def test_normalize_arrows_for_common_keyboards(value):
    assert normalize_arrows(value) == "↑↓←→"
    assert is_arrow_input(value)


@pytest.mark.parametrize("value", [
    "", " , ", "↑hello↓", "↑?↓", "↗", "↕", "↔", "↑\u200d↓", "️↑", "↑️️",
    "↑/↓", " ↑ -> banana ", "wxyz", "→" * 33, " " * 513,
    "🏻↑", "↑🏻", "👆🏻🏽", "👆up🏽", "[上]unknown[下]",
])
def test_invalid_and_ambiguous_characters_are_not_silently_removed(value):
    with pytest.raises(ArrowInputError):
        normalize_arrows(value)
    assert not is_arrow_input(value)


def test_dss_command_is_not_intercepted_as_wasd():
    assert not is_arrow_input("DSS")


def test_catalog_uses_real_codes_chinese_names_and_excludes_mission_only_calls():
    pool = load_stratagems()
    by_name = {item.name: item for item in pool}
    assert len(pool) >= 40
    assert by_name["轨道精确打击"].arrows == "→→↑"
    assert by_name["增援"].arrows == "↑↓→←↑"
    assert by_name["飞鹰500公斤炸弹"].arrows == "↑→↓↓↓"
    assert by_name["MG-43 机枪"].arrows == "↓←↓↑→"
    assert all(item.source_url.startswith("https://helldivers.wiki.gg/") for item in pool)
    assert not any("Other / Mission" in item.id or "Unavailable" in item.id for item in pool)


async def test_full_and_partial_inputs_and_round_bonuses(tmp_path):
    clock = Clock()
    async with Database(tmp_path / "bot.db") as db:
        game = StratagemHeroService(db, clock=clock, stratagems=(PRECISION,))
        result = await game.handle("随机战备", **KEY)
        assert "第 1/5 题" in result and "→ → ↑" in result
        assert "已输入：→" in await game.handle("➡️", **KEY)
        assert "已输入：→ →" in await game.handle("ｄ", **KEY)
        assert "正确！+15" in await game.handle("上", **KEY)
        assert (await state_for(db))["score"] == 15
        for _ in range(4):
            result = await game.handle("→→↑", **KEY)
        state = await state_for(db)
        assert "关卡 +75 / 时间 +100 / 完美 +100" in result
        assert state["score"] == 350
        assert state["round"] == 2 and state["completed"] == 0
        assert "第 1/6 题" in result


async def test_wrong_input_resets_only_current_question_and_loses_perfect_bonus(tmp_path):
    async with Database(tmp_path / "bot.db") as db:
        game = StratagemHeroService(db, stratagems=(PRECISION,))
        await game.handle("战备英雄", **KEY)
        await game.handle("→", **KEY)
        result = await game.handle("↓", **KEY)
        state = await state_for(db)
        assert "进度已清空" in result
        assert state["prefix"] == "" and state["score"] == 0 and not state["perfect"]
        for _ in range(5):
            result = await game.handle("→→↑", **KEY)
        assert "完美 +0" in result


async def test_extra_input_does_not_solve_an_unseen_next_question(tmp_path):
    async with Database(tmp_path / "bot.db") as db:
        game = StratagemHeroService(db, stratagems=(PRECISION,))
        await game.handle("战备英雄", **KEY)
        result = await game.handle("→→↑→", **KEY)
        assert "方向不匹配" in result
        state = await state_for(db)
        assert state["score"] == 0 and state["completed"] == 0


async def test_invalid_symbols_preserve_progress_and_other_commands_are_unhandled(tmp_path):
    async with Database(tmp_path / "bot.db") as db:
        game = StratagemHeroService(db, stratagems=(PRECISION,))
        await game.handle("战备英雄", **KEY)
        await game.handle("→", **KEY)
        before = await state_for(db)
        assert "无法识别" in await game.handle("→x↑", **KEY)
        assert await state_for(db) == before
        for command in ("DSS", "查战绩", "/签到", "你好", "战况", "↓"):
            other_key = KEY | {"user_id": "not-playing"} if command == "↓" else KEY
            assert await game.handle(command, **other_key) is None
        assert await state_for(db) == before


async def test_sessions_are_isolated_across_people_groups_and_scenes(tmp_path):
    async with Database(tmp_path / "bot.db") as db:
        game = StratagemHeroService(db, stratagems=(REINFORCE,))
        keys = [KEY, KEY | {"user_id": "u2"}, KEY | {"target_id": "g2"},
                KEY | {"scope": "c2c"}, KEY | {"scope": "channel"}]
        for key in keys:
            await game.handle("战备英雄", **key)
        await game.handle("↑↓", **KEY)
        assert (await state_for(db))["prefix"] == "↑↓"
        for key in keys[1:]:
            assert (await state_for(db, **key))["prefix"] == ""
        assert len(await db.fetch_all("SELECT * FROM stratagem_hero_sessions")) == 5


async def test_timeout_at_exact_deadline_finalizes_once_and_never_sends_messages(tmp_path):
    clock = Clock()
    async with Database(tmp_path / "bot.db") as db:
        game = StratagemHeroService(db, clock=clock, stratagems=(PRECISION,))
        await game.handle("战备英雄", **KEY)
        await game.handle("→→↑", **KEY)
        clock.advance(120)
        result = await game.handle("→→↑", **KEY)
        assert "本局时间到！得分 15" in result
        assert await state_for(db) is None
        assert "已结束局数：1" in await game.handle("战备记录", **KEY)
        assert "没有进行中的游戏" in await game.handle("结束战备", **KEY)
        assert "已结束局数：1" in await game.handle("战备记录", **KEY)


async def test_correct_sequence_adds_time_but_never_exceeds_round_cap(tmp_path):
    clock = Clock()
    async with Database(tmp_path / "bot.db") as db:
        game = StratagemHeroService(db, clock=clock, stratagems=(PRECISION,))
        await game.handle("战备英雄", **KEY)
        clock.advance(50)
        await game.handle("→→↑", **KEY)
        assert (await state_for(db))["deadline"] - clock() == 78
        clock.advance(1)
        await game.handle("→→↑", **KEY)
        assert (await state_for(db))["deadline"] - clock() == 85


async def test_running_game_and_record_survive_service_and_database_restart(tmp_path):
    path = tmp_path / "bot.db"
    clock = Clock()
    async with Database(path) as db:
        game = StratagemHeroService(db, clock=clock, stratagems=(PRECISION,))
        await game.handle("战备英雄", **KEY)
        await game.handle("→", **KEY)
    async with Database(path) as db:
        game = StratagemHeroService(db, clock=clock, stratagems=(PRECISION,))
        result = await game.handle("战备英雄 状态", **KEY)
        assert "已输入：→" in result
        await game.handle("→↑", **KEY)
        assert "得分 15" in await game.handle("战备英雄 结束", **KEY)
    async with Database(path) as db:
        game = StratagemHeroService(db, clock=clock, stratagems=(PRECISION,))
        assert "最高分：15" in await game.handle("战备记录", **KEY)


async def test_restart_command_preserves_running_game_and_expired_start_starts_fresh(tmp_path):
    clock = Clock()
    async with Database(tmp_path / "bot.db") as db:
        game = StratagemHeroService(db, clock=clock, stratagems=(PRECISION,))
        await game.handle("战备英雄", **KEY)
        await game.handle("→", **KEY)
        assert "已有一局" in await game.handle("战备英雄", **KEY)
        assert (await state_for(db))["prefix"] == "→"
        clock.advance(120)
        assert "本局时间到" in await game.handle("战备英雄", **KEY)
        assert (await state_for(db))["prefix"] == ""


async def test_ranking_only_in_this_conversation_with_clean_names(tmp_path):
    async with Database(tmp_path / "bot.db") as db:
        game = StratagemHeroService(db, stratagems=(PRECISION,))
        for key in [KEY, KEY | {"user_id": "u2", "display_name": "潜\n兵乙"},
                    KEY | {"target_id": "other-group"}]:
            await game.handle("战备英雄", **key)
            await game.handle("→→↑", **key)
            await game.handle("结束", **key)
        result = await game.handle("战备排行", **KEY)
        assert "1. 你" in result and "2. 潜兵乙" in result and "3." not in result
        assert "u1" not in result and "u2" not in result


async def test_concurrent_starts_and_finishes_on_two_database_connections(tmp_path):
    path = tmp_path / "bot.db"
    async with Database(path) as first, Database(path) as second:
        services = [StratagemHeroService(db, stratagems=(PRECISION,)) for db in (first, second)]
        await asyncio.gather(*(game.initialize() for game in services))
        results = await asyncio.gather(*(services[i % 2].handle("战备英雄", **KEY) for i in range(20)))
        assert sum("聊天版：" in result for result in results) == 1
        assert len(await first.fetch_all("SELECT * FROM stratagem_hero_sessions")) == 1
        await services[0].handle("→→↑", **KEY)
        await asyncio.gather(*(services[i % 2].handle("结束战备", **KEY) for i in range(20)))
        row = await first.fetch_one("SELECT best_score, games FROM stratagem_hero_records")
        assert row["best_score"] == 15 and row["games"] == 1


async def test_active_session_and_record_limits_and_lazy_cleanup(tmp_path):
    clock = Clock()
    async with Database(tmp_path / "bot.db") as db:
        game = StratagemHeroService(db, clock=clock, stratagems=(PRECISION,))
        game.MAX_SESSIONS = 1
        game.MAX_RECORDS = 2
        await game.handle("战备英雄", **KEY)
        second = KEY | {"user_id": "u2"}
        assert "人数较多" in await game.handle("战备英雄", **second)
        clock.advance(120)
        assert "聊天版：" in await game.handle("战备英雄", **second)
        assert "已结束局数：1" in await game.handle("战备记录", **KEY)
        await game.handle("结束战备", **second)
        assert "游戏记录已满" in await game.handle("战备英雄", **(KEY | {"user_id": "u3"}))
        assert "聊天版：" in await game.handle("战备英雄", **KEY)


async def test_best_score_never_decreases_and_zero_score_games_count(tmp_path):
    async with Database(tmp_path / "bot.db") as db:
        game = StratagemHeroService(db, stratagems=(PRECISION,))
        await game.handle("战备英雄", **KEY)
        await game.handle("→→↑", **KEY)
        await game.handle("结束战备", **KEY)
        await game.handle("战备英雄", **KEY)
        await game.handle("结束战备", **KEY)
        result = await game.handle("战备记录", **KEY)
        assert "最高分：15" in result and "已结束局数：2" in result


async def test_full_completion_is_bounded_and_records_last_cleared_round(tmp_path):
    async with Database(tmp_path / "bot.db") as db:
        game = StratagemHeroService(db, stratagems=(PRECISION,))
        game.MAX_ROUNDS = 1
        await game.handle("战备英雄", **KEY)
        for _ in range(5):
            result = await game.handle("→→↑", **KEY)
        assert "全部 1 关完成" in result
        assert await state_for(db) is None
        assert "最高通关：1" in await game.handle("战备记录", **KEY)


async def test_missing_identity_does_not_start_shared_anonymous_game(tmp_path):
    async with Database(tmp_path / "bot.db") as db:
        game = StratagemHeroService(db, stratagems=(PRECISION,))
        assert await game.handle("战备英雄", **(KEY | {"user_id": ""})) is None
