"""One-time bot release notices for existing proactive-message recipients."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import secrets
import time
from dataclasses import dataclass
from pathlib import Path

from hd2bot.services.push_access import KEY as PRIVATE_TARGET_KEY
from hd2bot.storage.database import canonical_json

logger = logging.getLogger(__name__)

_STATE_PREFIX = "release_notice_delivery:"
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_EXPECTED_KEYS = {"schema", "version", "title", "body"}


@dataclass(frozen=True)
class ReleaseNotice:
    version: str
    title: str
    body: str
    scopes: tuple[str, ...] = ("group", "c2c")

    def format(self) -> str:
        return f"【机器人更新日志 · {self.version}】\n{self.title}\n{self.body}"


class ReleaseNoticeService:
    """Hot-read a local notice and deliver it once to each existing opt-in target."""

    MAX_FILE_BYTES = 16_384
    MAX_TITLE_CHARS = 120
    MAX_BODY_CHARS = 1_400
    MAX_MESSAGE_BYTES = 1_700
    MAX_PER_DAY = 5
    MIN_GAP = 300

    def __init__(self, db, path: Path, *, access=None, clock=time.time):
        self.db = db
        self.path = Path(path)
        self.access = access
        self.clock = clock

    def current(self) -> ReleaseNotice | None:
        """Read and validate on every call so operators can replace the file live."""
        try:
            raw = self.path.read_bytes()
            if not raw or len(raw) > self.MAX_FILE_BYTES:
                return None
            value = json.loads(raw.decode("utf-8-sig"))
        except (OSError, UnicodeError, ValueError, TypeError):
            return None
        if (not isinstance(value, dict) or not _EXPECTED_KEYS <= set(value)
                or set(value) - (_EXPECTED_KEYS | {"scopes"})):
            return None
        if value.get("schema") != 1 or type(value.get("schema")) is not int:
            return None
        version, title, body = value.get("version"), value.get("title"), value.get("body")
        if not isinstance(version, str) or _VERSION.fullmatch(version) is None:
            return None
        if not isinstance(title, str) or not isinstance(body, str):
            return None
        title, body = title.strip(), body.strip()
        if (not title or not body or len(title) > self.MAX_TITLE_CHARS
                or len(body) > self.MAX_BODY_CHARS
                or any(ord(char) < 32 for char in title)
                or any(ord(char) < 32 and char not in "\n\t" for char in body)):
            return None
        scopes = value.get("scopes", ["group", "c2c"])
        if (not isinstance(scopes, list) or not 1 <= len(scopes) <= 2
                or any(not isinstance(scope, str) or scope not in {"group", "c2c"}
                       for scope in scopes)
                or len(set(scopes)) != len(scopes)):
            return None
        notice = ReleaseNotice(version, title, body, tuple(scopes))
        if len(notice.format().encode("utf-8")) > self.MAX_MESSAGE_BYTES:
            return None
        return notice

    def command_text(self) -> str:
        notice = self.current()
        return notice.format() if notice is not None else "当前没有可用的机器人更新日志。"

    @staticmethod
    def _state_key(scope: str, target_id: str, version: str) -> str:
        digest = hashlib.sha256(
            canonical_json([scope, target_id, version]).encode("utf-8"),
        ).hexdigest()
        return _STATE_PREFIX + digest

    async def _targets(self) -> list[tuple[str, str]]:
        rows = await self.db.fetch_all(
            "SELECT DISTINCT target_type, target_id FROM subscriptions "
            "WHERE enabled = 1 ORDER BY target_type, target_id",
        )
        targets = {(row["target_type"], row["target_id"]) for row in rows}
        bound = await self.db.get_setting(PRIVATE_TARGET_KEY, {})
        private = bound.get("c2c_openid") if isinstance(bound, dict) else None
        if isinstance(private, str) and 1 <= len(private) <= 128 and private.strip() == private:
            targets.add(("c2c", private))
        return sorted(targets)

    async def _allowed(self, scope: str, target_id: str) -> bool:
        return self.access is None or await self.access.allowed(scope, target_id)

    async def _limits(self, scope: str, target_id: str) -> tuple[int, int]:
        if self.access is not None and callable(getattr(self.access, "limits", None)):
            daily, gap, _ = await self.access.limits(scope, target_id)
            return daily, gap
        return self.MAX_PER_DAY, self.MIN_GAP

    @staticmethod
    async def _target_is_current(connection, scope: str, target_id: str) -> bool:
        async with connection.execute(
            "SELECT 1 FROM subscriptions WHERE target_type = ? AND target_id = ? "
            "AND enabled = 1 LIMIT 1",
            (scope, target_id),
        ) as cursor:
            if await cursor.fetchone() is not None:
                return True
        if scope != "c2c":
            return False
        async with connection.execute(
            "SELECT value_json FROM bot_settings WHERE key = ?", (PRIVATE_TARGET_KEY,),
        ) as cursor:
            row = await cursor.fetchone()
        try:
            bound = json.loads(row["value_json"]) if row else {}
        except (TypeError, ValueError):
            return False
        return isinstance(bound, dict) and bound.get("c2c_openid") == target_id

    @staticmethod
    def _valid_state(value) -> dict:
        if not isinstance(value, dict):
            return {}
        failures, retry_at = value.get("failures", 0), value.get("retry_at", 0)
        if (type(failures) is not int or not 0 <= failures <= 100
                or type(retry_at) not in {int, float} or not math.isfinite(retry_at)):
            return {}
        return value

    async def _claim_attempt(self, key: str, notice: ReleaseNotice, scope: str,
                             target_id: str, now: float, daily: int,
                             gap: int) -> tuple[dict, str] | None:
        """Persist an attempt before sending, while rechecking current opt-in state."""
        claim_token = secrets.token_hex(16)
        async with self.db.transaction() as connection:
            if not await self._target_is_current(connection, scope, target_id):
                return None
            async with connection.execute(
                "SELECT COUNT(*) AS count, MAX(sent_at) AS latest "
                "FROM notification_deliveries WHERE target_type = ? AND target_id = ? "
                "AND sent_at > ?",
                (scope, target_id, now - 86_400),
            ) as cursor:
                quota = await cursor.fetchone()
            if quota["count"] >= daily or (
                    quota["latest"] is not None and now - quota["latest"] < gap):
                return None
            async with connection.execute(
                "SELECT value_json FROM bot_settings WHERE key = ?", (key,),
            ) as cursor:
                row = await cursor.fetchone()
            if row is None:
                previous = {}
            else:
                try:
                    previous = self._valid_state(json.loads(row["value_json"]))
                except (TypeError, ValueError):
                    previous = {}
                # A record for this target/version proves an earlier attempt. Only an
                # explicitly recorded, due retry may create another external attempt.
                if (not previous or previous.get("version") != notice.version
                        or previous.get("status") != "retry"
                        or previous.get("retry_at", 0) > now):
                    return None
            claimed = canonical_json({
                "version": notice.version,
                "status": "attempting",
                "failures": previous.get("failures", 0),
                "retry_at": 0,
                "attempted_at": now,
                "claim_token": claim_token,
            })
            await connection.execute(
                "INSERT INTO bot_settings(key, value_json) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json",
                (key, claimed),
            )
        return previous, claim_token

    @staticmethod
    def _claim_matches(row, claim_token: str) -> bool:
        try:
            value = json.loads(row["value_json"]) if row else {}
        except (TypeError, ValueError):
            return False
        return (isinstance(value, dict) and value.get("status") == "attempting"
                and value.get("claim_token") == claim_token)

    async def _mark_success(self, key: str, notice: ReleaseNotice,
                            scope: str, target_id: str, now: float,
                            claim_token: str) -> bool:
        state = canonical_json({
            "version": notice.version, "status": "sent", "failures": 0, "retry_at": 0,
        })
        async with self.db.transaction() as connection:
            async with connection.execute(
                "SELECT value_json FROM bot_settings WHERE key = ?", (key,),
            ) as cursor:
                row = await cursor.fetchone()
            if not self._claim_matches(row, claim_token):
                return False
            await connection.execute(
                "UPDATE bot_settings SET value_json = ? WHERE key = ?", (state, key),
            )
            await connection.execute(
                "INSERT INTO notification_deliveries(target_type, target_id, sent_at) "
                "VALUES (?, ?, ?)", (scope, target_id, now),
            )
        return True

    async def _store_outcome(self, key: str, claim_token: str, value: dict,
                             *, event: str, scope: str) -> bool:
        try:
            serialized = canonical_json(value)
            async with self.db.transaction() as connection:
                async with connection.execute(
                    "SELECT value_json FROM bot_settings WHERE key = ?", (key,),
                ) as cursor:
                    row = await cursor.fetchone()
                if not self._claim_matches(row, claim_token):
                    return False
                await connection.execute(
                    "UPDATE bot_settings SET value_json = ? WHERE key = ?",
                    (serialized, key),
                )
        except Exception as exc:
            logger.warning("event=%s_state_failed scope=%s reason=%s",
                           event, scope, type(exc).__name__)
            return False
        return True

    async def tick(self, sender) -> None:
        notice = self.current()
        if notice is None:
            return
        from hd2bot.services.notifications import (
            PermanentPushError,
            RetryablePushError,
            UncertainPushError,
        )

        for scope, target_id in await self._targets():
            if scope not in notice.scopes:
                continue
            now = self.clock()
            key = self._state_key(scope, target_id, notice.version)
            if not await self._allowed(scope, target_id):
                continue
            daily, gap = await self._limits(scope, target_id)
            if not await self._allowed(scope, target_id):
                continue
            claim = await self._claim_attempt(
                key, notice, scope, target_id, now, daily, gap,
            )
            if claim is None:
                continue
            state, claim_token = claim
            try:
                await sender(scope, target_id, notice.format())
            except PermanentPushError as exc:
                await self._store_outcome(key, claim_token, {
                    "version": notice.version, "status": "rejected", "reason": exc.code,
                    "failures": state.get("failures", 0), "retry_at": 0,
                }, event="release_notice_rejected", scope=scope)
                logger.warning("event=release_notice_rejected scope=%s", scope)
            except RetryablePushError as exc:
                failures = min(state.get("failures", 0) + 1, 100)
                delay = min(3_600, 60 * (2 ** min(failures - 1, 6)))
                await self._store_outcome(key, claim_token, {
                    "version": notice.version, "status": "retry", "failures": failures,
                    "retry_at": now + delay,
                }, event="release_notice_retry", scope=scope)
                logger.warning("event=release_notice_retry scope=%s reason=%s",
                               scope, type(exc).__name__)
            except UncertainPushError as exc:
                logger.warning("event=release_notice_uncertain scope=%s reason=%s",
                               scope, type(exc).__name__)
            except Exception as exc:
                # The provider may have accepted the message before reporting this
                # failure. Keep the durable claim as an uncertain terminal attempt.
                logger.warning("event=release_notice_uncertain scope=%s reason=%s",
                               scope, type(exc).__name__)
            else:
                try:
                    stored = await self._mark_success(
                        key, notice, scope, target_id, self.clock(), claim_token,
                    )
                    if not stored:
                        logger.warning("event=release_notice_success_state_conflict scope=%s",
                                       scope)
                except Exception as exc:
                    # The durable attempt remains authoritative. Retrying here could
                    # duplicate a message that QQ already accepted.
                    logger.warning("event=release_notice_success_state_failed "
                                   "scope=%s reason=%s", scope, type(exc).__name__)
