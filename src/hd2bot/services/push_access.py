"""Pair one operator-selected QQ account to its official C2C OpenID."""

import asyncio
import hashlib
import hmac
import json
import math
import time
from pathlib import Path

from hd2bot.storage.database import canonical_json

KEY = "push_allowed_recipient"


class PushAccess:
    def __init__(self, db, expected_qq: str, pairing_file: Path, *, clock=time.time):
        self.db, self.expected_qq, self.pairing_file = db, expected_qq, pairing_file
        self.clock = clock
        self._lock = asyncio.Lock()

    async def allowed(self, scope: str, target_id: str) -> bool:
        if scope != "c2c" or not self.expected_qq or not target_id:
            return False
        record = await self.db.get_setting(KEY, {})
        return (isinstance(record, dict) and record.get("qq_number") == self.expected_qq
                and record.get("c2c_openid") == target_id)

    async def bind(self, code: str, scope: str, target_id: str) -> str:
        if scope != "c2c":
            return "推送绑定只能在指定 QQ 账号的私聊中完成。"
        if not self.expected_qq or not target_id:
            return "尚未设置允许推送的用户，请联系维护者。"
        async with self._lock:
            try:
                pending = json.loads(self.pairing_file.read_text(encoding="utf8"))
            except (OSError, ValueError):
                return "暂无可用绑定码，请联系维护者生成。"
            if not isinstance(pending, dict):
                return "绑定码不可用，请联系维护者重新生成。"
            expires = pending.get("expires_at")
            digest = hashlib.sha256(code.strip().encode()).hexdigest()
            if (pending.get("qq_number") != self.expected_qq or type(expires) not in {int, float}
                    or not math.isfinite(expires) or not self.clock() < expires
                    or not isinstance(pending.get("digest"), str)
                    or not pending["digest"].isascii()
                    or not hmac.compare_digest(pending["digest"], digest)):
                return "绑定码无效或已过期，请确认后重试。"
            # The database transaction also excludes other gate instances and
            # processes; an instance-local asyncio lock alone cannot consume once.
            async with self.db.transaction() as connection:
                if not self.clock() < expires:
                    return "绑定码无效或已过期，请确认后重试。"
                async with connection.execute(
                    "SELECT value_json FROM bot_settings WHERE key = ?", (KEY,),
                ) as cursor:
                    row = await cursor.fetchone()
                existing = json.loads(row["value_json"]) if row else {}
                if isinstance(existing, dict) and existing.get("consumed_digest") == digest:
                    return "绑定码已经使用，请勿重复绑定。"
                value = canonical_json({
                    "qq_number": self.expected_qq, "c2c_openid": target_id,
                    "consumed_digest": digest, "bound_at": self.clock(),
                })
                async with connection.execute(
                    "INSERT INTO bot_settings(key, value_json) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json", (KEY, value),
                ):
                    pass
            try:
                self.pairing_file.unlink(missing_ok=True)
            except OSError:
                # Consumption is already durable; cleanup failure must not report
                # a failed binding or make the leftover code usable again.
                pass
        return ("推送用户已绑定。仅此私聊可以订阅，群聊与其他用户不发送。\n"
                "可发送：订阅 战况 60、订阅 主线、订阅 防守。\n"
                "能否送达仍取决于 QQ 平台主动消息权限。")
