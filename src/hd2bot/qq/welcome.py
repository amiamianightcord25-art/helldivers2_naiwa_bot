"""Short onboarding messages and durable, app-scoped welcome deduplication."""
import asyncio
import hashlib
import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from hd2bot.project_info import OPEN_SOURCE_BRIEF

GROUP_WELCOME = (
    "🦅 超级地球通讯终端已加入本群！\n"
    "在群里 @我 后发送：帮助、战况、主线、星图。\n"
    "发送 菜单 查看分类入口；签到 领取经验，战备英雄 开始箭头小游戏。\n"
    "装备查询：武器 解放者／武器 解放者 详参／武器 解放者 配件。"
    "有多个候选时回复 选择 1，翻页用 下一页。\n"
    "个人同步和主动推送当前暂停。"
)

PRIVATE_WELCOME = (
    "🦅 欢迎使用 Helldivers 2 战情助手！\n"
    "发送 帮助 查看全部命令；战况、主线、星图可以直接查询。\n"
    "发送 菜单 使用快捷入口；签到 升级称号，战备英雄 挑战随机战备。\n"
    "装备查询：武器 解放者，或加上 详参／配件；多个结果可用 选择 1。\n"
    "个人同步和主动推送当前暂停。"
)


GROUP_WELCOME += "\n" + OPEN_SOURCE_BRIEF
PRIVATE_WELCOME += "\n" + OPEN_SOURCE_BRIEF


class WelcomeStore:
    def __init__(self, path: Path, app_id: str):
        self.path, self.app_id = Path(path), app_id

    def _key(self, *parts):
        return hashlib.sha256(json.dumps((self.app_id, *parts), separators=(",", ":")).encode()).hexdigest()

    def _claim(self, scope, target, event_id):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        now = time.time()
        subject = self._key("subject", scope, target)
        with closing(sqlite3.connect(self.path, timeout=5)) as db, db:
            db.execute("PRAGMA busy_timeout=5000")
            db.execute("CREATE TABLE IF NOT EXISTS welcomes (key TEXT PRIMARY KEY, expires REAL NOT NULL)")
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM welcomes WHERE expires<=?", (now,))
            key = self._key("event", scope, target, event_id) if event_id else subject
            if db.execute("SELECT 1 FROM welcomes WHERE key=?", (key,)).fetchone():
                return False
            # Reserve before calling QQ: ambiguous delivery errors must not cause a send loop.
            db.execute("INSERT OR REPLACE INTO welcomes VALUES (?,?)", (key, now + 365 * 86400))
            db.execute("INSERT OR REPLACE INTO welcomes VALUES (?,?)", (subject, now + 365 * 86400))
            return True

    async def claim_first_message(self, scope, target):
        return await asyncio.to_thread(self._claim, scope, target, None)

    async def claim_added_event(self, scope, target, event_id):
        return await asyncio.to_thread(self._claim, scope, target, event_id)
