"""Scoped subscription storage; this module never sends a notification."""

import hashlib
from dataclasses import dataclass

from hd2bot.storage.database import Database, canonical_json


@dataclass(frozen=True)
class Subscription:
    id: int
    target_type: str
    target_id: str
    topic: str
    planet_index: int | None = None
    interval_minutes: int = 60
    enabled: bool = True
    pause_reason: str = ""


_COLUMNS = "id, target_type, target_id, topic, planet_index, interval_minutes, enabled, pause_reason"


def _scope(target_type: str, target_id: str) -> None:
    if target_type not in ("group", "c2c"):
        raise ValueError("订阅目标类型必须是 group 或 c2c。")
    if not isinstance(target_id, str) or not target_id.strip():
        raise ValueError("订阅目标不能为空。")


def _topic(topic: str, planet_index: int | None, *, require_planet: bool) -> None:
    if topic not in ("major_order", "defense", "planet", "war", "news", "announcement",
                     "dss", "campaign", "region", "patch"):
        raise ValueError("不支持的订阅主题。")
    if planet_index is not None:
        if type(planet_index) is not int or planet_index < 0:
            raise ValueError("星球编号必须是非负整数。")
        if topic != "planet":
            raise ValueError("只有 planet 订阅可以指定星球编号。")
    elif topic == "planet" and require_planet:
        raise ValueError("planet 订阅必须指定星球编号。")


def _notification_key(scope: str, event_key: str) -> None:
    if not isinstance(scope, str) or not scope.strip():
        raise ValueError("通知作用域不能为空。")
    if not isinstance(event_key, str) or not event_key.strip():
        raise ValueError("通知事件键不能为空。")


def _fingerprint(payload) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


class SubscriptionService:
    def __init__(self, db: Database):
        self.db = db

    async def subscribe(self, target_type: str, target_id: str, topic: str,
                        planet_index: int | None = None,
                        interval_minutes: int = 60) -> Subscription:
        _scope(target_type, target_id)
        _topic(topic, planet_index, require_planet=True)
        if type(interval_minutes) is not int or not 30 <= interval_minutes <= 1440:
            raise ValueError("推送间隔须为 30 至 1440 分钟。")
        parameters = (target_type, target_id, topic, planet_index)
        async with self.db.transaction() as connection:
            async with connection.execute(
                "INSERT INTO subscriptions(target_type, target_id, topic, planet_index, "
                "interval_minutes) VALUES (?, ?, ?, ?, ?) ON CONFLICT DO UPDATE SET "
                "interval_minutes = excluded.interval_minutes, enabled = 1, pause_reason = '', "
                "retry_at = 0, failures = 0", (*parameters, interval_minutes),
            ):
                pass
            async with connection.execute(
                f"SELECT {_COLUMNS} FROM subscriptions "
                "WHERE target_type = ? AND target_id = ? AND topic = ? AND planet_index IS ?",
                parameters,
            ) as cursor:
                row = await cursor.fetchone()
        return Subscription(**dict(row))

    async def list_subscriptions(self, target_type: str, target_id: str) -> list[Subscription]:
        _scope(target_type, target_id)
        rows = await self.db.fetch_all(
            f"SELECT {_COLUMNS} FROM subscriptions "
            "WHERE target_type = ? AND target_id = ? ORDER BY id", (target_type, target_id),
        )
        return [Subscription(**dict(row)) for row in rows]

    async def unsubscribe(self, target_type: str, target_id: str, topic: str | None = None,
                          planet_index: int | None = None) -> int:
        _scope(target_type, target_id)
        if topic is not None:
            _topic(topic, planet_index, require_planet=False)
        elif planet_index is not None:
            raise ValueError("按星球退订时请指定 planet 主题。")
        query = "DELETE FROM subscriptions WHERE target_type = ? AND target_id = ?"
        parameters = [target_type, target_id]
        if topic is not None:
            query += " AND topic = ?"
            parameters.append(topic)
        if planet_index is not None:
            query += " AND planet_index = ?"
            parameters.append(planet_index)
        return await self.db.execute(query, tuple(parameters))

    async def has_changed(self, scope: str, event_key: str, payload) -> bool:
        """Read-only preparation: a failed future send must leave state untouched."""
        _notification_key(scope, event_key)
        fingerprint = _fingerprint(payload)
        row = await self.db.fetch_one(
            "SELECT fingerprint FROM notification_state WHERE scope = ? AND event_key = ?",
            (scope, event_key),
        )
        return row is None or row["fingerprint"] != fingerprint

    async def mark_notified(self, scope: str, event_key: str, payload) -> None:
        """Call only after the future notification sender confirms success."""
        _notification_key(scope, event_key)
        await self.db.execute(
            "INSERT INTO notification_state(scope, event_key, fingerprint) VALUES (?, ?, ?) "
            "ON CONFLICT(scope, event_key) DO UPDATE SET fingerprint = excluded.fingerprint, "
            "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')",
            (scope, event_key, _fingerprint(payload)),
        )
