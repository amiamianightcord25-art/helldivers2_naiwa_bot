import asyncio
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from hd2bot.hd2.errors import CommandError
from hd2bot.presentation import ChatContext
from hd2bot.services.checkin import (
    MAX_LEVEL,
    MAX_XP,
    RANKS,
    CheckinService,
    level_for_xp,
    title_for_level,
    xp_for_level,
)
from hd2bot.storage.database import Database


class Clock:
    def __init__(self, stamp="2026-09-19T04:00:00+00:00"):
        self.value = datetime.fromisoformat(stamp).timestamp()

    def __call__(self):
        return self.value

    def days(self, days=1):
        self.value += days * 86_400


def context(scope="group", target="group-a", user="member-a", name=""):
    # Tests keep working before/after ChatContext gains the optional platform nickname.
    return SimpleNamespace(scope=scope, target_id=target, user_id=user,
                           display_name=name, is_private=scope in {"c2c", "dms"})


async def test_first_checkin_and_repeat_do_not_duplicate_xp(tmp_path):
    async with Database(tmp_path / "bot.db") as db:
        service = CheckinService(db, clock=Clock())
        ctx = ChatContext("group", "group-a", "member-a")
        before = await service.profile(ctx)
        assert before.xp == 0 and before.level == 1 and before.total_days == 0
        first, second = await service.check_in(ctx), await service.check_in(ctx)
        assert first.awarded_xp == 100 and first.profile.level == 2
        assert not first.already_signed
        assert second.already_signed and second.awarded_xp == 0
        assert second.profile == first.profile
        assert len(await db.fetch_all("SELECT * FROM checkin_profiles")) == 1


async def test_checkin_china_midnight_and_timezone_independence(tmp_path):
    clock = Clock("2026-09-19T15:59:59+00:00")
    async with Database(tmp_path / "bot.db") as db:
        service = CheckinService(db, clock=clock)
        first = await service.check_in(context())
        clock.value += 2
        second = await service.check_in(context())
        assert first.date == "2026-09-19"
        assert second.date == "2026-09-20"
        assert second.profile.streak == 2 and second.awarded_xp == 110


async def test_streak_bonus_caps_and_missing_day_resets_only_streak(tmp_path):
    clock = Clock()
    async with Database(tmp_path / "bot.db") as db:
        service = CheckinService(db, clock=clock)
        gains = []
        for _ in range(10):
            gains.append((await service.check_in(context())).awarded_xp)
            clock.days()
        assert gains == [100, 110, 120, 130, 140, 150, 160, 160, 160, 160]
        assert (await service.profile(context())).streak == 10
        clock.days()
        assert (await service.profile(context())).streak == 0
        result = await service.check_in(context())
        assert result.awarded_xp == 100 and result.profile.streak == 1
        assert result.profile.xp == sum(gains) + 100
        assert result.profile.total_days == 11


async def test_concurrent_calls_across_connections_award_once(tmp_path):
    path = tmp_path / "bot.db"
    clock = Clock()
    async with Database(path) as first_db, Database(path) as second_db:
        first = CheckinService(first_db, clock=clock)
        second = CheckinService(second_db, clock=clock)
        results = await asyncio.gather(*(
            (first if index % 2 else second).check_in(context()) for index in range(24)
        ))
        assert sum(result.awarded_xp for result in results) == 100
        assert sum(not result.already_signed for result in results) == 1
        assert (await first.profile(context())).total_days == 1


async def test_clock_rollback_cannot_award_an_old_day_again(tmp_path):
    clock = Clock()
    async with Database(tmp_path / "bot.db") as db:
        service = CheckinService(db, clock=clock)
        await service.check_in(context())
        clock.days()
        before = await service.check_in(context())
        clock.days(-1)
        after = await service.check_in(context())
        assert after.already_signed and after.awarded_xp == 0
        assert after.profile.xp == before.profile.xp
        assert after.profile.total_days == 2


async def test_signin_persists_after_restart(tmp_path):
    path = tmp_path / "bot.db"
    clock = Clock()
    async with Database(path) as db:
        await CheckinService(db, clock=clock).check_in(context())
    async with Database(path) as db:
        result = await CheckinService(db, clock=clock).check_in(context())
        assert result.already_signed and result.profile.xp == 100


@pytest.mark.parametrize("scope", ["c2c", "group", "dms", "channel"])
async def test_every_chat_scope_supports_all_commands(tmp_path, scope):
    async with Database(tmp_path / "bot.db") as db:
        service = CheckinService(db, clock=Clock())
        ctx = context(scope)
        assert "签到成功" in (await service.handle("签到", ctx)).text
        assert "2 级" in (await service.handle("我的等级", ctx)).text
        assert "签到排行" in (await service.handle("签到排行", ctx)).text
        assert "300" in (await service.handle("等级表", ctx)).text


async def test_same_id_in_different_identity_domains_is_not_merged(tmp_path):
    async with Database(tmp_path / "bot.db") as db:
        service = CheckinService(db, clock=Clock())
        results = [await service.check_in(context(scope))
                   for scope in ("c2c", "group", "dms", "channel")]
        assert all(result.awarded_xp == 100 for result in results)
        assert len(await db.fetch_all("SELECT * FROM checkin_profiles")) == 4


async def test_same_group_identity_cannot_get_extra_xp_by_changing_groups(tmp_path):
    async with Database(tmp_path / "bot.db") as db:
        service = CheckinService(db, clock=Clock())
        await service.check_in(context(target="group-a"))
        repeat = await service.check_in(context(target="group-b"))
        assert repeat.already_signed and repeat.profile.xp == 100
        assert len(await service.leaderboard(context(target="group-b"))) == 1


async def test_leaderboards_only_show_members_of_current_conversation(tmp_path):
    clock = Clock()
    async with Database(tmp_path / "bot.db") as db:
        service = CheckinService(db, clock=clock)
        alice = context(user="private-user-alice", name="爱丽丝")
        bob = context(user="private-user-bob", name="鲍勃")
        outsider = context(target="group-b", user="outsider", name="外群成员")
        await service.check_in(alice)
        await service.check_in(bob)
        await service.check_in(outsider)
        clock.days()
        await service.check_in(bob)
        entries = await service.leaderboard(alice)
        assert [entry.display_name for entry in entries] == ["鲍勃", "爱丽丝"]
        assert [entry.is_self for entry in entries] == [False, True]
        text = (await service.handle("签到排行", alice)).text
        assert "private-user" not in text and "outsider" not in text and "外群成员" not in text
        assert "爱丽丝（你）" in text


@pytest.mark.parametrize("scope", ["c2c", "dms"])
async def test_private_ranking_never_shows_other_users(tmp_path, scope):
    async with Database(tmp_path / "bot.db") as db:
        service = CheckinService(db, clock=Clock())
        one, two = context(scope=scope, user="one"), context(scope=scope, user="two")
        await service.check_in(one)
        await service.check_in(two)
        entries = await service.leaderboard(one)
        assert len(entries) == 1 and entries[0].is_self
        text = (await service.handle("签到排行", one)).text
        assert "未提供昵称（你）" in text and "私聊仅显示本人" in text


async def test_real_nickname_is_updated_but_not_replaced_by_missing_name(tmp_path):
    async with Database(tmp_path / "bot.db") as db:
        service = CheckinService(db, clock=Clock())
        await service.check_in(context(name="真实名字"))
        await service.check_in(context(name="修改后的名字"))
        await service.check_in(context())
        rows = await service.leaderboard(context())
        assert rows[0].display_name == "修改后的名字"


async def test_level_cap_keeps_counting_days_and_reports_no_extra_xp(tmp_path):
    clock = Clock()
    async with Database(tmp_path / "bot.db") as db:
        service = CheckinService(db, clock=clock)
        await service.check_in(context())
        await db.execute("UPDATE checkin_profiles SET xp = ?", (MAX_XP - 1,))
        clock.days()
        result = await service.check_in(context())
        assert result.awarded_xp == 1 and result.profile.level == MAX_LEVEL
        assert result.profile.xp == MAX_XP and result.profile.remaining_xp == 0
        clock.days()
        result = await service.check_in(context())
        assert result.awarded_xp == 0 and result.profile.total_days == 3
        assert "后续签到继续累计天数" in (await service.handle("签到", context())).text


def test_rank_titles_match_numeric_unlock_order_and_exclude_special_titles():
    source = Path(__file__).parents[1] / "src/hd2bot/assets/wiki_catalog.json"
    entries = json.loads(source.read_text(encoding="utf-8"))["entries"]
    official = [(int(dict(entry["fields"])["Level Earned"]), entry["english_name"])
                for entry in entries if entry.get("subcategory") == "Titles"
                and "Level Earned" in dict(entry["fields"])]
    assert [(rank.level, rank.english_name) for rank in RANKS] == sorted(official)
    assert MAX_LEVEL == 300 and len(RANKS) == 36
    # The game includes literal digits and a plus sign (e.g. 300级 and +1).
    assert all(any("\u4e00" <= char <= "\u9fff" for char in rank.title) for rank in RANKS)
    assert all(all("\u4e00" <= char <= "\u9fff" or char in "0123456789+"
                   for char in rank.title) for rank in RANKS)
    assert title_for_level(139) == title_for_level(130)
    assert title_for_level(140) == "列兵"
    assert title_for_level(150) == "超级列兵"
    assert "Super Citizen" not in {rank.english_name for rank in RANKS}


def test_every_level_boundary_and_rank_interval_is_monotonic():
    assert level_for_xp(0) == 1 and level_for_xp(10 ** 10) == MAX_LEVEL
    for level in range(2, MAX_LEVEL + 1):
        threshold = xp_for_level(level)
        assert level_for_xp(threshold - 1) == level - 1
        assert level_for_xp(threshold) == level
        assert threshold > xp_for_level(level - 1)


@pytest.mark.parametrize("ctx", [None, ChatContext("other", "target", "user"),
                                 ChatContext("group", "", "user"),
                                 ChatContext("group", "target")])
async def test_missing_identity_has_clear_user_response_and_no_award(tmp_path, ctx):
    async with Database(tmp_path / "bot.db") as db:
        service = CheckinService(db, clock=Clock())
        with pytest.raises(CommandError, match="私聊、群聊或频道"):
            await service.handle("签到", ctx)
        assert "等级称号表" in (await service.handle("等级表", ctx)).text


async def test_profile_and_empty_ranking_do_not_enroll_users(tmp_path):
    async with Database(tmp_path / "bot.db") as db:
        service = CheckinService(db, clock=Clock())
        assert "还没有签到记录" in (await service.handle("我的等级", context())).text
        assert "暂无签到记录" in (await service.handle("签到排行", context())).text
        assert await db.fetch_all("SELECT * FROM checkin_profiles") == []
        assert await db.fetch_all("SELECT * FROM checkin_members") == []


async def test_failed_award_rolls_back_membership_and_profile(tmp_path):
    async with Database(tmp_path / "bot.db") as db:
        service = CheckinService(db, clock=Clock())
        await service.initialize()
        await db.execute(
            "CREATE TRIGGER fail_checkin BEFORE INSERT ON checkin_profiles "
            "BEGIN SELECT RAISE(ABORT, 'test transaction rollback'); END"
        )
        with pytest.raises(sqlite3.IntegrityError, match="test transaction rollback"):
            await service.check_in(context())
        assert await db.fetch_all("SELECT * FROM checkin_profiles") == []
        assert await db.fetch_all("SELECT * FROM checkin_members") == []
