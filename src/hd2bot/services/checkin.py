"""Daily bot experience, HD2 rank titles and conversation-scoped leaderboards."""

from __future__ import annotations

import asyncio
import json
import time
import unicodedata
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from hd2bot.hd2.errors import CommandError
from hd2bot.presentation import ChatContext, CommandButton, CommandReply

CHINA_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")
BASE_XP = 100
STREAK_STEP_XP = 10
STREAK_BONUS_DAYS = 7


@dataclass(frozen=True)
class RankTitle:
    level: int
    title: str
    english_name: str


_RANK_DATA = json.loads(
    (Path(__file__).parents[1] / "assets" / "rank_titles.json").read_text(encoding="utf-8")
)
RANKS = tuple(RankTitle(row["level"], row["title"], row["english_name"])
              for row in _RANK_DATA["ranks"])
MAX_LEVEL = RANKS[-1].level


def xp_for_level(level: int) -> int:
    """Bot-only curve: level L needs 100 + 2 * (L - 1) XP to advance."""
    if type(level) is not int or not 1 <= level <= MAX_LEVEL:
        raise ValueError("等级超出范围。")
    completed = level - 1
    return 100 * completed + completed * (completed - 1)


_THRESHOLDS = tuple(xp_for_level(level) for level in range(1, MAX_LEVEL + 1))
MAX_XP = _THRESHOLDS[-1]


def level_for_xp(xp: int) -> int:
    return max(1, min(MAX_LEVEL, bisect_right(_THRESHOLDS, xp)))


def title_for_level(level: int) -> str:
    return RANKS[max(0, bisect_right(tuple(rank.level for rank in RANKS), level) - 1)].title


@dataclass(frozen=True)
class CheckinProfile:
    xp: int = 0
    total_days: int = 0
    streak: int = 0
    last_day: str = ""

    @property
    def level(self) -> int:
        return level_for_xp(self.xp)

    @property
    def title(self) -> str:
        return title_for_level(self.level)

    @property
    def remaining_xp(self) -> int:
        return max(0, xp_for_level(self.level + 1) - self.xp) if self.level < MAX_LEVEL else 0


@dataclass(frozen=True)
class CheckinResult:
    profile: CheckinProfile
    awarded_xp: int
    already_signed: bool
    previous_level: int
    date: str


@dataclass(frozen=True)
class CheckinRankEntry:
    position: int
    display_name: str
    profile: CheckinProfile
    is_self: bool


_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS checkin_profiles (
        scope TEXT NOT NULL CHECK (scope IN ('c2c', 'group', 'dms', 'channel')),
        user_id TEXT NOT NULL,
        xp INTEGER NOT NULL DEFAULT 0 CHECK (xp >= 0),
        total_days INTEGER NOT NULL DEFAULT 0 CHECK (total_days >= 0),
        streak INTEGER NOT NULL DEFAULT 0 CHECK (streak >= 0),
        last_day TEXT NOT NULL DEFAULT '',
        PRIMARY KEY (scope, user_id)
    )""",
    """CREATE TABLE IF NOT EXISTS checkin_members (
        scope TEXT NOT NULL,
        target_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        display_name TEXT NOT NULL DEFAULT '',
        PRIMARY KEY (scope, target_id, user_id)
    )""",
)


def _identity(context: ChatContext | None) -> tuple[str, str, str]:
    if (context is None or context.scope not in {"c2c", "group", "dms", "channel"}
            or not isinstance(context.user_id, str) or not context.user_id.strip()
            or not isinstance(context.target_id, str) or not context.target_id.strip()):
        raise CommandError("请在 QQ 私聊、群聊或频道中使用签到功能。")
    return context.scope, context.target_id, context.user_id


def _display_name(context: ChatContext) -> str:
    name = getattr(context, "display_name", "")
    if not isinstance(name, str):
        return ""
    # Keep platform-supplied names as plain, single-line text; do not invent nicknames.
    name = "".join(char for char in name if not unicodedata.category(char).startswith("C"))
    return " ".join(name.split())[:40]


def _profile(row, today: date) -> CheckinProfile:
    if row is None:
        return CheckinProfile()
    last_day = row["last_day"]
    # A missed day ends the displayed active streak, without erasing earned XP.
    streak = row["streak"] if last_day >= (today - timedelta(days=1)).isoformat() else 0
    return CheckinProfile(row["xp"], row["total_days"], streak, last_day)


class CheckinService:
    """Use the shared Database's BEGIN IMMEDIATE transaction for every award.

    Identity domains remain separate. The same exact user ID within a domain gets
    one award per date, even when checking in through a second group/channel.
    Public rankings only contain users who checked in within that conversation.
    """

    def __init__(self, db, *, clock=time.time):
        self.db = db
        self.clock = clock
        self._initialized = False
        self._initialize_lock = asyncio.Lock()

    async def initialize(self) -> None:
        if self._initialized:
            return
        async with self._initialize_lock:
            if self._initialized:
                return
            async with self.db.transaction() as connection:
                for statement in _SCHEMA:
                    async with connection.execute(statement):
                        pass
            self._initialized = True

    def _today(self) -> date:
        return datetime.fromtimestamp(self.clock(), CHINA_TZ).date()

    async def check_in(self, context: ChatContext) -> CheckinResult:
        scope, target, user = _identity(context)
        await self.initialize()
        async with self.db.transaction() as connection:
            # Read the date after acquiring the write lock, including midnight contention.
            today = self._today()
            today_text = today.isoformat()
            async with connection.execute(
                "SELECT * FROM checkin_profiles WHERE scope = ? AND user_id = ?",
                (scope, user),
            ) as cursor:
                row = await cursor.fetchone()
            before = _profile(row, today)
            async with connection.execute(
                "INSERT INTO checkin_members(scope, target_id, user_id, display_name) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(scope, target_id, user_id) DO UPDATE SET "
                "display_name = CASE WHEN excluded.display_name != '' "
                "THEN excluded.display_name ELSE checkin_members.display_name END",
                (scope, target, user, _display_name(context)),
            ):
                pass
            # A clock rollback must not make earlier dates payable again.
            if before.last_day >= today_text:
                return CheckinResult(before, 0, True, before.level, today_text)
            streak = (before.streak + 1 if before.last_day ==
                      (today - timedelta(days=1)).isoformat() else 1)
            award = min(
                BASE_XP + STREAK_STEP_XP * (min(streak, STREAK_BONUS_DAYS) - 1),
                max(0, MAX_XP - before.xp),
            )
            after = CheckinProfile(before.xp + award, before.total_days + 1, streak, today_text)
            async with connection.execute(
                "INSERT INTO checkin_profiles(scope, user_id, xp, total_days, streak, last_day) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(scope, user_id) DO UPDATE SET "
                "xp = excluded.xp, total_days = excluded.total_days, "
                "streak = excluded.streak, last_day = excluded.last_day",
                (scope, user, after.xp, after.total_days, after.streak, after.last_day),
            ):
                pass
        return CheckinResult(after, award, False, before.level, today_text)

    async def profile(self, context: ChatContext) -> CheckinProfile:
        scope, _, user = _identity(context)
        await self.initialize()
        row = await self.db.fetch_one(
            "SELECT * FROM checkin_profiles WHERE scope = ? AND user_id = ?", (scope, user),
        )
        return _profile(row, self._today())

    async def leaderboard(self, context: ChatContext, *, limit: int = 10
                          ) -> tuple[CheckinRankEntry, ...]:
        scope, target, user = _identity(context)
        if type(limit) is not int or not 1 <= limit <= 20:
            raise CommandError("签到排行每次最多显示 20 人。")
        await self.initialize()
        query = (
            "SELECT p.*, m.display_name FROM checkin_profiles p "
            "JOIN checkin_members m ON m.scope = p.scope AND m.user_id = p.user_id "
            "WHERE m.scope = ? AND m.target_id = ?"
        )
        parameters = [scope, target]
        if scope in {"c2c", "dms"}:
            query += " AND p.user_id = ?"
            parameters.append(user)
        # Stable ties do not depend on nicknames, which can change between calls.
        query += " ORDER BY p.xp DESC, p.total_days DESC, p.user_id LIMIT ?"
        rows = await self.db.fetch_all(query, (*parameters, limit))
        today = self._today()
        return tuple(
            CheckinRankEntry(index, row["display_name"] or "未提供昵称",
                             _profile(row, today), row["user_id"] == user)
            for index, row in enumerate(rows, 1)
        )

    @staticmethod
    def levels_text() -> str:
        lines = [f"🦅 等级称号表 · 最高 {MAX_LEVEL} 级", "按游戏等级解锁顺序排列（游戏内简体中文）："]
        lines.extend(f"{rank.level:>3} 级 · {rank.title}" for rank in RANKS)
        lines.extend((
            "签到每日基础 100 经验；连续签到每天多 10，连续 7 天起每天最多 160。",
            "北京时间每天 00:00 刷新，断签后连签从 1 天开始。",
            "每级所需经验从 100 开始，每级增加 2；累计经验不会因断签减少。",
            "机器人签到等级独立于游戏账号；不同聊天类型分别累计。",
            "等级来源：Helldivers Wiki；中文按游戏内高清实拍核对，不含战债或付费称号。",
        ))
        return "\n".join(lines)

    @staticmethod
    def _progress(profile: CheckinProfile) -> list[str]:
        lines = [f"等级：{profile.level} 级 · {profile.title}",
                 f"累计经验：{profile.xp:,}",
                 f"连续签到：{profile.streak} 天 · 累计签到：{profile.total_days} 天"]
        if profile.level == MAX_LEVEL:
            lines.append(f"已达到 {MAX_LEVEL} 级；后续签到继续累计天数。")
        else:
            lines.append(f"距离下一级：{profile.remaining_xp} 经验")
            next_title = next((rank for rank in RANKS if rank.level > profile.level), None)
            if next_title is not None:
                lines.append(f"下一称号：{next_title.level} 级 · {next_title.title}")
        return lines

    async def handle(self, command: str, context: ChatContext | None) -> CommandReply:
        if command == "等级表":
            return CommandReply(self.levels_text())
        _identity(context)
        if command == "签到":
            result = await self.check_in(context)
            if result.already_signed:
                lines = ["今天已经签到过了，不会重复发放经验。"]
            else:
                lines = [f"🦅 签到成功 · {result.date}", f"本次获得：{result.awarded_xp} 经验"]
                if result.profile.level > result.previous_level:
                    lines.append(f"升级！{result.previous_level} → {result.profile.level} 级")
            lines.extend(self._progress(result.profile))
            lines.append("北京时间每天 00:00 刷新 · 发送 我的等级 / 等级表 / 签到排行")
            return CommandReply("\n".join(lines), keyboard=(
                (CommandButton("我也要签到", "签到"),),
            ))
        if command == "我的等级":
            profile = await self.profile(context)
            lines = ["🦅 我的签到等级", *self._progress(profile)]
            if profile.total_days == 0:
                lines.append("还没有签到记录，发送 签到 开始积累经验。")
            elif profile.last_day == self._today().isoformat():
                lines.append("今日已签到。")
            else:
                lines.append("今日尚未签到，发送 签到 领取经验。")
            lines.append("机器人签到等级独立于游戏账号；不同聊天类型分别累计。")
            return CommandReply("\n".join(lines))
        if command == "签到排行":
            entries = await self.leaderboard(context)
            name = {"group": "本群", "channel": "本频道"}.get(context.scope, "当前私聊")
            lines = [f"🦅 {name}签到排行 · 前 10 名"]
            if context.is_private:
                lines.append("私聊仅显示本人；群聊和频道分别展示本会话已签到成员。")
            if not entries:
                lines.append("暂无签到记录，发送 签到 加入。")
            for entry in entries:
                suffix = "（你）" if entry.is_self else ""
                profile = entry.profile
                lines.append(f"{entry.position}. {entry.display_name}{suffix} · "
                             f"{profile.level} 级 {profile.title} · {profile.xp:,} 经验")
            return CommandReply("\n".join(lines))
        raise CommandError("签到功能支持：签到、我的等级、等级表、签到排行。")
