"""One small, explicitly managed SQLite connection per application."""

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite

_SCHEMA = """
BEGIN IMMEDIATE;
CREATE TABLE IF NOT EXISTS subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_type TEXT NOT NULL CHECK (target_type IN ('group', 'c2c')),
    target_id TEXT NOT NULL CHECK (length(trim(target_id)) > 0),
    topic TEXT NOT NULL CHECK (topic IN (
        'major_order', 'defense', 'planet', 'war', 'news', 'announcement', 'dss', 'campaign',
        'region', 'patch'
    )),
    planet_index INTEGER,
    interval_minutes INTEGER NOT NULL DEFAULT 60 CHECK (interval_minutes BETWEEN 30 AND 1440),
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    pause_reason TEXT NOT NULL DEFAULT '',
    baseline_json TEXT,
    next_due REAL NOT NULL DEFAULT 0,
    retry_at REAL NOT NULL DEFAULT 0,
    failures INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK (
        (topic = 'planet' AND planet_index IS NOT NULL AND planet_index >= 0)
        OR (topic != 'planet' AND planet_index IS NULL)
    )
);
CREATE UNIQUE INDEX IF NOT EXISTS subscriptions_unique
    ON subscriptions(target_type, target_id, topic, COALESCE(planet_index, -1));
CREATE TABLE IF NOT EXISTS bot_settings (
    key TEXT PRIMARY KEY NOT NULL,
    value_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notification_state (
    scope TEXT NOT NULL,
    event_key TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (scope, event_key)
);
CREATE TABLE IF NOT EXISTS notification_deliveries (
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    sent_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS notification_deliveries_target
    ON notification_deliveries(target_type, target_id, sent_at);
PRAGMA user_version = 4;
COMMIT;
"""

# Earlier topic CHECK constraints must be rebuilt. IDs and creation
# timestamps remain unchanged; the independent settings/success fingerprints stay.
_MIGRATE_V1 = _SCHEMA.replace("BEGIN IMMEDIATE;", """BEGIN IMMEDIATE;
DROP INDEX IF EXISTS subscriptions_unique;
ALTER TABLE subscriptions RENAME TO subscriptions_v1;
""").replace("COMMIT;", """
INSERT INTO subscriptions(id, target_type, target_id, topic, planet_index, created_at)
SELECT id, target_type, target_id, topic, planet_index, created_at FROM subscriptions_v1;
DROP TABLE subscriptions_v1;
COMMIT;
""")

_V2_COLUMNS = ("id, target_type, target_id, topic, planet_index, interval_minutes, enabled, "
               "pause_reason, baseline_json, next_due, retry_at, failures, created_at")
_MIGRATE_V2_V3 = _SCHEMA.replace("BEGIN IMMEDIATE;", """BEGIN IMMEDIATE;
DROP INDEX IF EXISTS subscriptions_unique;
ALTER TABLE subscriptions RENAME TO subscriptions_v2;
""").replace("COMMIT;", f"""
INSERT INTO subscriptions({_V2_COLUMNS}) SELECT {_V2_COLUMNS} FROM subscriptions_v2;
UPDATE sqlite_sequence SET seq = MAX(seq, COALESCE(
    (SELECT seq FROM sqlite_sequence WHERE name = 'subscriptions_v2'), 0
)) WHERE name = 'subscriptions';
INSERT INTO sqlite_sequence(name, seq)
    SELECT 'subscriptions', seq FROM sqlite_sequence
    WHERE name = 'subscriptions_v2'
    AND NOT EXISTS (SELECT 1 FROM sqlite_sequence WHERE name = 'subscriptions');
DROP TABLE subscriptions_v2;
COMMIT;
""")


def canonical_json(value) -> str:
    """Accept JSON values and reject NaN/infinity before writing anything."""
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise ValueError("值必须可以编码为有效 JSON。") from exc


def _key(value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("键不能为空。")


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._connection: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def __aenter__(self):
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        await self.close()

    async def initialize(self) -> None:
        async with self._lock:
            if self._connection is not None:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = await aiosqlite.connect(self.path, isolation_level=None)
            connection.row_factory = aiosqlite.Row
            try:
                async with connection.execute("PRAGMA busy_timeout = 5000"):
                    pass
                # Lock before reading user_version. A second process must observe
                # the committed schema instead of attempting the rebuild again.
                async with connection.execute("BEGIN IMMEDIATE"):
                    pass
                async with connection.execute("PRAGMA user_version") as cursor:
                    version = (await cursor.fetchone())[0]
                if version > 4:
                    raise RuntimeError("数据库版本比当前程序更新，不能自动降级。")
                script = {1: _MIGRATE_V1, 2: _MIGRATE_V2_V3,
                          3: _MIGRATE_V2_V3}.get(version, _SCHEMA)
                # executescript implicitly commits the current transaction; execute
                # our controlled DDL statements individually to keep migration atomic.
                script = script.replace("BEGIN IMMEDIATE;", "").replace("COMMIT;", "")
                for statement in script.split(";"):
                    if statement.strip():
                        async with connection.execute(statement):
                            pass
                await connection.commit()
            except BaseException:
                await connection.rollback()
                await connection.close()
                raise
            self._connection = connection

    def _require_connection(self) -> aiosqlite.Connection:
        if self._connection is None:
            raise RuntimeError("数据库尚未初始化或已关闭。")
        return self._connection

    @asynccontextmanager
    async def transaction(self):
        """Serialize a transaction; use the yielded connection inside this block."""
        async with self._lock:
            connection = self._require_connection()
            try:
                # Cancellation can arrive after SQLite begins the transaction but
                # before aiosqlite returns the cursor. Roll that back as well.
                async with connection.execute("BEGIN IMMEDIATE"):
                    pass
                yield connection
                await connection.commit()
            except BaseException:
                await connection.rollback()
                raise

    async def execute(self, query: str, parameters: tuple = ()) -> int:
        async with self.transaction() as connection:
            async with connection.execute(query, parameters) as cursor:
                return cursor.rowcount

    async def fetch_one(self, query: str, parameters: tuple = ()):
        async with self._lock:
            async with self._require_connection().execute(query, parameters) as cursor:
                return await cursor.fetchone()

    async def fetch_all(self, query: str, parameters: tuple = ()) -> list:
        async with self._lock:
            async with self._require_connection().execute(query, parameters) as cursor:
                return await cursor.fetchall()

    async def set_setting(self, key: str, value) -> None:
        _key(key)
        serialized = canonical_json(value)
        await self.execute(
            "INSERT INTO bot_settings(key, value_json) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json",
            (key, serialized),
        )

    async def get_setting(self, key: str, default=None):
        _key(key)
        row = await self.fetch_one("SELECT value_json FROM bot_settings WHERE key = ?", (key,))
        return json.loads(row["value_json"]) if row else default

    async def close(self) -> None:
        async with self._lock:
            if self._connection is not None:
                connection, self._connection = self._connection, None
                await connection.close()
