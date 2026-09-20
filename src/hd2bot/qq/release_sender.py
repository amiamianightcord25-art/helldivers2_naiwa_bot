"""One-shot group release delivery, sharing the bot's durable delivery ledger."""

from __future__ import annotations

import json
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import quote

import aiohttp
import aiosqlite

from hd2bot.config import Settings
from hd2bot.qq.native_ui import API_BASE, NativeUIClient, NativeUIError
from hd2bot.services.group_push import GroupPushAccess
from hd2bot.services.notifications import (
    PermanentPushError,
    RetryablePushError,
    UncertainPushError,
)
from hd2bot.services.release_notice import ReleaseNoticeService
from hd2bot.storage.database import Database


class ReleaseSenderError(RuntimeError):
    """A bounded local failure without recipient or credential values."""


class GroupOnlyAccess:
    """Keep C2C and other destinations out even when a notice includes them."""

    def __init__(self, access):
        self.access = access

    async def allowed(self, scope, target):
        return scope == "group" and await self.access.allowed(scope, target)

    async def limits(self, scope, target):
        return await self.access.limits(scope, target)


class _NoPrivateAccess:
    async def allowed(self, scope, target):
        return False


class GroupReleaseSender:
    """Send only group text; a missing acknowledgement never becomes a blind retry."""

    def __init__(self, settings: Settings, access, *, session=None):
        self.settings, self.access = settings, access
        self._session = session
        self._owns_session = session is None
        self._auth = None
        self.outcomes: Counter = Counter()

    async def __aenter__(self):
        if self.settings.qq_sandbox:
            raise ReleaseSenderError("production_required")
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25))
        self._auth = NativeUIClient(self.settings, session=self._session)
        try:
            await self._auth.__aenter__()
        except BaseException:
            if self._owns_session:
                await self._session.close()
            raise
        return self

    async def __aexit__(self, *args):
        if self._auth is not None:
            await self._auth.__aexit__(*args)
        if self._owns_session and self._session is not None:
            await self._session.close()

    async def proactive_enabled(self, target: str) -> bool | None:
        """Read platform state for diagnostics; the actual send remains authoritative."""
        try:
            token = await self._auth.access_token()
            async with self._session.get(
                API_BASE + f"/v2/groups/{quote(target, safe='')}/bot_state",
                headers={"Authorization": f"QQBot {token}",
                         "X-Union-Appid": self.settings.qq_app_id},
            ) as response:
                body = await response.json(content_type=None)
                if (200 <= response.status < 300 and isinstance(body, dict)
                        and body.get("err_code", body.get("code", 0)) in (0, "0", None)
                        and type(body.get("allow_proactive_msg")) is bool):
                    return body["allow_proactive_msg"]
        except (NativeUIError, aiohttp.ClientError, TimeoutError, ValueError, UnicodeError):
            pass
        return None

    @staticmethod
    def _check(status: int, body: Any) -> None:
        code = body.get("err_code", body.get("code", 0)) if isinstance(body, dict) else None
        bounded = str(code) if re.fullmatch(r"[0-9]{1,12}", str(code)) else None
        reason = f"qq_{bounded}" if bounded and bounded != "0" else f"http_{status}"
        if status == 429 or 500 <= status < 600 or status in {408, 425}:
            raise RetryablePushError(reason)
        if not 200 <= status < 300 or code not in (0, "0", None):
            raise PermanentPushError(reason)
        if (not isinstance(body, dict) or not isinstance(body.get("id"), str)
                or not body["id"].strip()):
            raise UncertainPushError("missing_message_id")

    async def __call__(self, scope: str, target: str, content: str) -> None:
        try:
            await self._send(scope, target, content)
        except (PermanentPushError, RetryablePushError, UncertainPushError) as exc:
            status = {PermanentPushError: "rejected", RetryablePushError: "retry",
                      UncertainPushError: "uncertain"}[type(exc)]
            self.outcomes[(status, exc.code)] += 1
            raise
        else:
            self.outcomes[("sent", "ok")] += 1

    async def _send(self, scope: str, target: str, content: str) -> None:
        if scope != "group":
            raise PermanentPushError("invalid_scope")
        if not isinstance(target, str) or not 1 <= len(target) <= 128:
            raise PermanentPushError("invalid_target")
        if not await self.access.allowed(scope, target):
            raise PermanentPushError("recipient_not_allowed")
        if (not isinstance(content, str) or not content.strip()
                or len(content.encode("utf-8")) > ReleaseNoticeService.MAX_MESSAGE_BYTES):
            raise PermanentPushError("invalid_content")
        if self._auth is None or self._session is None:
            raise RetryablePushError("client_not_open")
        try:
            token = await self._auth.access_token()
        except NativeUIError:
            # No message request has occurred, so another token attempt is safe.
            raise RetryablePushError("token_unavailable") from None
        if not await self.access.allowed(scope, target):
            raise PermanentPushError("recipient_not_allowed")
        url = API_BASE + f"/v2/groups/{quote(target, safe='')}/messages"
        try:
            async with self._session.post(
                url, json={"msg_type": 0, "content": content},
                headers={"Authorization": f"QQBot {token}",
                         "X-Union-Appid": self.settings.qq_app_id},
            ) as response:
                try:
                    body = await response.json(content_type=None)
                except (ValueError, UnicodeError):
                    body = None
                self._check(response.status, body)
        except (aiohttp.ClientError, TimeoutError):
            raise UncertainPushError("request_incomplete") from None


class ReadOnlyDatabase:
    """Preview existing state without schema creation, claims, or quota writes."""

    def __init__(self, path: Path):
        self.path = path
        self.connection = None

    async def __aenter__(self):
        self.connection = await aiosqlite.connect(
            self.path.resolve().as_uri() + "?mode=ro", uri=True,
        )
        self.connection.row_factory = aiosqlite.Row
        return self

    async def __aexit__(self, *args):
        await self.connection.close()

    async def fetch_all(self, query, parameters=()):
        async with self.connection.execute(query, parameters) as cursor:
            return await cursor.fetchall()

    async def fetch_one(self, query, parameters=()):
        async with self.connection.execute(query, parameters) as cursor:
            return await cursor.fetchone()

    async def get_setting(self, key, default=None):
        row = await self.fetch_one("SELECT value_json FROM bot_settings WHERE key = ?", (key,))
        return json.loads(row["value_json"]) if row else default


async def _preview(service, notice) -> dict[str, int]:
    counts: Counter = Counter()
    rows = await service.db.fetch_all(
        "SELECT DISTINCT target_id FROM subscriptions "
        "WHERE enabled = 1 AND target_type = 'group'",
    )
    now = service.clock()
    for row in rows:
        target = row["target_id"]
        if "group" not in getattr(notice, "scopes", ("group", "c2c")):
            counts["scope_excluded"] += 1
            continue
        if not await service.access.allowed("group", target):
            counts["access_denied"] += 1
            continue
        key = service._state_key("group", target, notice.version)
        stored = await service.db.get_setting(key)
        if stored is not None:
            state = service._valid_state(stored)
            if (not state or state.get("version") != notice.version
                    or state.get("status") != "retry"):
                status = state.get("status", "recorded")
                counts[status if status in {"sent", "rejected", "attempting"} else "recorded"] += 1
                continue
            if state.get("retry_at", 0) > now:
                counts["retry_wait"] += 1
                continue
        daily, gap = await service._limits("group", target)
        quota = await service.db.fetch_one(
            "SELECT COUNT(*) AS count, MAX(sent_at) AS latest FROM notification_deliveries "
            "WHERE target_type = 'group' AND target_id = ? AND sent_at > ?",
            (target, now - 86_400),
        )
        if quota["count"] >= daily or (quota["latest"] is not None and now - quota["latest"] < gap):
            counts["quota_wait"] += 1
        else:
            counts["ready"] += 1
    return dict(sorted(counts.items()))


async def run_release_notice(settings: Settings, notice_path: Path, *, send=False,
                             session=None, clock=time.time) -> dict:
    if not settings.database_path.is_file():
        raise ReleaseSenderError("database_missing")
    database = Database(settings.database_path) if send else ReadOnlyDatabase(settings.database_path)
    async with database as db:
        group_access = GroupPushAccess(
            _NoPrivateAccess(), db, settings.database_path.parent / "group_push.json",
            settings.qq_app_id,
        )
        access = GroupOnlyAccess(group_access)
        service = ReleaseNoticeService(db, notice_path, access=access, clock=clock)
        notice = service.current()
        if notice is None:
            raise ReleaseSenderError("invalid_notice")
        result = {"mode": "send" if send else "dry_run", "scope": "group",
                  "version": notice.version, "before": await _preview(service, notice)}
        if result["before"].get("ready", 0):
            async with GroupReleaseSender(settings, access, session=session) as sender:
                states: Counter = Counter()
                if "group" in getattr(notice, "scopes", ("group", "c2c")):
                    rows = await db.fetch_all(
                        "SELECT DISTINCT target_id FROM subscriptions "
                        "WHERE enabled = 1 AND target_type = 'group'",
                    )
                    for row in rows:
                        target = row["target_id"]
                        if await access.allowed("group", target):
                            enabled = await sender.proactive_enabled(target)
                            states[{True: "enabled", False: "blocked", None: "unknown"}[enabled]] += 1
                result["bot_state"] = dict(sorted(states.items()))
                if send:
                    await service.tick(sender)
                    result["attempts"] = [
                        {"status": status, "code": code, "count": count}
                        for (status, code), count in sorted(sender.outcomes.items())
                    ]
            result["after"] = await _preview(service, notice)
        return result
