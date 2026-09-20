import asyncio
import hashlib
import sqlite3
from contextlib import asynccontextmanager

import pytest

from hd2bot.services.subscriptions import SubscriptionService
from hd2bot.storage import Database


async def test_schema_initialization_is_idempotent_and_creates_parent(tmp_path):
    path = tmp_path / "nested" / "bot.db"
    async with Database(path) as db:
        await db.initialize()
        await db.initialize()
        assert path.exists()
        tables = await db.fetch_all(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
        assert {row["name"] for row in tables} == {
            "subscriptions", "bot_settings", "notification_state", "notification_deliveries",
        }
        assert (await db.fetch_one("PRAGMA user_version"))[0] == 4
        assert (await db.fetch_one("PRAGMA busy_timeout"))[0] == 5000
    await db.close()
    async with Database(path) as reopened:
        assert (await reopened.fetch_one("PRAGMA user_version"))[0] == 4


async def test_duplicate_subscriptions_are_unique_including_null_planet(tmp_path):
    async with Database(tmp_path / "bot.db") as db:
        service = SubscriptionService(db)
        orders = await asyncio.gather(*(
            service.subscribe("group", "synthetic-group", "major_order") for _ in range(20)
        ))
        planets = await asyncio.gather(*(
            service.subscribe("group", "synthetic-group", "planet", 64) for _ in range(20)
        ))
        assert len({item.id for item in orders}) == 1
        assert len({item.id for item in planets}) == 1
        assert len(await service.list_subscriptions("group", "synthetic-group")) == 2


async def test_two_connections_cannot_insert_duplicate_subscriptions(tmp_path):
    path = tmp_path / "bot.db"
    async with Database(path) as first, Database(path) as second:
        results = await asyncio.gather(
            SubscriptionService(first).subscribe("c2c", "synthetic-user", "defense"),
            SubscriptionService(second).subscribe("c2c", "synthetic-user", "defense"),
        )
        assert results[0].id == results[1].id


async def test_subscription_scope_isolates_targets_and_target_types(tmp_path):
    async with Database(tmp_path / "bot.db") as db:
        service = SubscriptionService(db)
        group = await service.subscribe("group", "synthetic-a", "major_order")
        user = await service.subscribe("c2c", "synthetic-a", "major_order")
        other = await service.subscribe("group", "synthetic-b", "major_order")
        assert await service.list_subscriptions("group", "synthetic-a") == [group]
        assert await service.unsubscribe("group", "synthetic-a") == 1
        assert await service.list_subscriptions("group", "synthetic-a") == []
        assert await service.list_subscriptions("c2c", "synthetic-a") == [user]
        assert await service.list_subscriptions("group", "synthetic-b") == [other]


async def test_unsubscribe_can_remove_one_planet_one_topic_or_the_target(tmp_path):
    async with Database(tmp_path / "bot.db") as db:
        service = SubscriptionService(db)
        for topic, index in [("planet", 64), ("planet", 127), ("defense", None)]:
            await service.subscribe("group", "synthetic-group", topic, index)
        assert await service.unsubscribe("group", "synthetic-group", "planet", 64) == 1
        assert await service.unsubscribe("group", "synthetic-group", "planet", 64) == 0
        assert await service.unsubscribe("group", "synthetic-group", "planet") == 1
        assert await service.unsubscribe("group", "synthetic-group") == 1
        assert await service.unsubscribe("group", "synthetic-group") == 0


@pytest.mark.parametrize("target_type,target_id,topic,index", [
    ("other", "synthetic", "defense", None),
    ("group", "", "defense", None),
    ("group", "   ", "defense", None),
    ("group", "synthetic", "unknown", None),
    ("group", "synthetic", "planet", None),
    ("group", "synthetic", "planet", -1),
    ("group", "synthetic", "planet", True),
    ("group", "synthetic", "planet", 1.5),
    ("group", "synthetic", "defense", 64),
])
async def test_invalid_subscriptions_are_rejected(tmp_path, target_type, target_id, topic, index):
    async with Database(tmp_path / "bot.db") as db:
        with pytest.raises(ValueError):
            await SubscriptionService(db).subscribe(target_type, target_id, topic, index)
        assert await db.fetch_all("SELECT * FROM subscriptions") == []


async def test_unsubscribe_rejects_ambiguous_planet_filter(tmp_path):
    async with Database(tmp_path / "bot.db") as db:
        with pytest.raises(ValueError):
            await SubscriptionService(db).unsubscribe("group", "synthetic", planet_index=64)


async def test_notifications_are_read_then_mark_and_survive_restart(tmp_path):
    path = tmp_path / "bot.db"
    payload = {"planet": 64, "status": {"progress": 70, "event": "synthetic"}}
    async with Database(path) as db:
        service = SubscriptionService(db)
        assert await service.has_changed("synthetic-scope", "defense", payload)
        assert await service.has_changed("synthetic-scope", "defense", payload)
        assert await db.fetch_all("SELECT * FROM notification_state") == []
        await service.mark_notified("synthetic-scope", "defense", payload)
        assert not await service.has_changed("synthetic-scope", "defense", payload)
    async with Database(path) as db:
        service = SubscriptionService(db)
        reordered = {"status": {"event": "synthetic", "progress": 70}, "planet": 64}
        assert not await service.has_changed("synthetic-scope", "defense", reordered)
        assert await service.has_changed("synthetic-scope", "defense", {"planet": 64})
        assert await service.has_changed("another-scope", "defense", payload)
        assert await service.has_changed("synthetic-scope", "major-order", payload)


async def test_only_notification_fingerprint_is_stored(tmp_path):
    payload = {"private": "synthetic-private-content"}
    async with Database(tmp_path / "bot.db") as db:
        service = SubscriptionService(db)
        await service.mark_notified("synthetic-scope", "event", payload)
        row = dict(await db.fetch_one("SELECT * FROM notification_state"))
        assert row["fingerprint"] == hashlib.sha256(
            b'{"private":"synthetic-private-content"}',
        ).hexdigest()
        assert "synthetic-private-content" not in str(row)


async def test_setting_json_roundtrip_and_restart(tmp_path):
    path = tmp_path / "bot.db"
    async with Database(path) as db:
        assert await db.get_setting("absent", {"default": True}) == {"default": True}
        await db.set_setting("display", {"language": "中文", "items": [1, None, False]})
        await db.set_setting("replace", "old")
        await db.set_setting("replace", {"new": 2})
    async with Database(path) as db:
        assert await db.get_setting("display") == {"language": "中文", "items": [1, None, False]}
        assert await db.get_setting("replace") == {"new": 2}


@pytest.mark.parametrize("payload", [float("nan"), float("inf"), object(), {"bad": {1, 2}}])
async def test_non_json_settings_and_fingerprints_are_rejected(tmp_path, payload):
    async with Database(tmp_path / "bot.db") as db:
        with pytest.raises(ValueError):
            await db.set_setting("synthetic-key", payload)
        service = SubscriptionService(db)
        with pytest.raises(ValueError):
            await service.has_changed("synthetic-scope", "event", payload)
        with pytest.raises(ValueError):
            await service.mark_notified("synthetic-scope", "event", payload)


async def test_sql_parameters_are_bound_for_target_setting_and_notification(tmp_path):
    injection = "synthetic'); DROP TABLE subscriptions; --"
    async with Database(tmp_path / "bot.db") as db:
        service = SubscriptionService(db)
        item = await service.subscribe("group", injection, "defense")
        await service.subscribe("group", "synthetic-safe-target", "defense")
        assert await service.list_subscriptions("group", injection) == [item]
        await db.set_setting(injection, {"text": injection})
        assert await db.get_setting(injection) == {"text": injection}
        await service.mark_notified(injection, injection, {"value": injection})
        assert not await service.has_changed(injection, injection, {"value": injection})
        assert await service.unsubscribe("group", injection) == 1
        assert len(await service.list_subscriptions("group", "synthetic-safe-target")) == 1


async def test_failed_transaction_rolls_back_and_connection_remains_usable(tmp_path):
    async with Database(tmp_path / "bot.db") as db:
        with pytest.raises(RuntimeError, match="synthetic failure"):
            async with db.transaction() as connection:
                async with connection.execute(
                    "INSERT INTO bot_settings(key, value_json) VALUES (?, ?)", ("rolled-back", "1"),
                ):
                    pass
                raise RuntimeError("synthetic failure")
        assert await db.get_setting("rolled-back") is None
        await db.set_setting("after", 2)
        assert await db.get_setting("after") == 2


async def test_cancellation_while_begin_completes_does_not_leave_transaction_open(tmp_path, monkeypatch):
    async with Database(tmp_path / "bot.db") as db:
        connection = db._require_connection()
        execute = connection.execute

        @asynccontextmanager
        async def cancel_after_begin(query, parameters=()):
            async with execute(query, parameters) as cursor:
                if query == "BEGIN IMMEDIATE":
                    raise asyncio.CancelledError
                yield cursor

        with monkeypatch.context() as patch:
            patch.setattr(connection, "execute", cancel_after_begin)
            with pytest.raises(asyncio.CancelledError):
                await db.set_setting("cancelled", 1)
        assert not connection.in_transaction
        await db.set_setting("after", 2)
        assert await db.get_setting("after") == 2


async def test_closed_database_rejects_operations_and_can_be_reopened(tmp_path):
    db = Database(tmp_path / "bot.db")
    await db.close()
    with pytest.raises(RuntimeError):
        await db.get_setting("key")
    await db.initialize()
    await db.set_setting("key", "value")
    await db.close()
    await db.close()
    await db.initialize()
    assert await db.get_setting("key") == "value"
    await db.close()


async def test_newer_schema_is_not_downgraded(tmp_path):
    path = tmp_path / "future.db"
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version = 5")
    with pytest.raises(RuntimeError, match="不能自动降级"):
        await Database(path).initialize()
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 5


async def test_real_v1_migration_preserves_rows_settings_and_fingerprints(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE subscriptions (
                id INTEGER PRIMARY KEY, target_type TEXT NOT NULL, target_id TEXT NOT NULL,
                topic TEXT NOT NULL CHECK (topic IN ('major_order', 'defense', 'planet')),
                planet_index INTEGER, created_at TEXT NOT NULL DEFAULT 'legacy-time'
            );
            CREATE UNIQUE INDEX subscriptions_unique ON subscriptions(
                target_type, target_id, topic, COALESCE(planet_index, -1));
            CREATE TABLE bot_settings (key TEXT PRIMARY KEY, value_json TEXT NOT NULL);
            CREATE TABLE notification_state (scope TEXT, event_key TEXT, fingerprint TEXT,
                updated_at TEXT DEFAULT 'legacy-time', PRIMARY KEY(scope, event_key));
            INSERT INTO subscriptions VALUES(9, 'group', 'synthetic', 'planet', 64, 'original');
            INSERT INTO bot_settings VALUES('language', '"中文"');
            INSERT INTO notification_state VALUES('synthetic', 'event', 'saved-hash', 'original');
            PRAGMA user_version = 1;
        """)
    first, second = Database(path), Database(path)
    await asyncio.gather(first.initialize(), second.initialize())
    await first.close()
    await second.close()
    async with Database(path) as db:
        row = await db.fetch_one("SELECT * FROM subscriptions WHERE id = 9")
        assert row["created_at"] == "original"
        assert row["planet_index"] == 64
        assert row["enabled"] == 1
        assert row["interval_minutes"] == 60
        assert row["baseline_json"] is None
        assert await db.get_setting("language") == "中文"
        assert (await db.fetch_one("SELECT * FROM notification_state"))["fingerprint"] == "saved-hash"
        assert (await db.fetch_one("PRAGMA user_version"))[0] == 4
        war = await SubscriptionService(db).subscribe("group", "synthetic", "war", interval_minutes=30)
        assert war.topic == "war"
        assert war.interval_minutes == 30
    async with Database(path) as db:
        assert len(await SubscriptionService(db).list_subscriptions("group", "synthetic")) == 2


@pytest.mark.parametrize("retain_subscription", [True, False])
@pytest.mark.parametrize("version", [2, 3])
async def test_v2_v3_to_v4_migration_preserves_state_quota_and_deleted_id_high_watermark(
    tmp_path, retain_subscription, version,
):
    path = tmp_path / "v2.db"
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target_type TEXT NOT NULL, target_id TEXT NOT NULL,
                topic TEXT NOT NULL CHECK (topic IN (
                    'major_order', 'defense', 'planet', 'war' /* extra topics */)),
                planet_index INTEGER, interval_minutes INTEGER NOT NULL DEFAULT 60,
                enabled INTEGER NOT NULL DEFAULT 1, pause_reason TEXT NOT NULL DEFAULT '',
                baseline_json TEXT, next_due REAL NOT NULL DEFAULT 0,
                retry_at REAL NOT NULL DEFAULT 0, failures INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT 'legacy'
            );
            CREATE UNIQUE INDEX subscriptions_unique ON subscriptions(
                target_type, target_id, topic, COALESCE(planet_index, -1));
            CREATE TABLE bot_settings (key TEXT PRIMARY KEY, value_json TEXT NOT NULL);
            CREATE TABLE notification_state (
                scope TEXT, event_key TEXT, fingerprint TEXT, updated_at TEXT,
                PRIMARY KEY(scope, event_key));
            CREATE TABLE notification_deliveries (
                target_type TEXT NOT NULL, target_id TEXT NOT NULL, sent_at REAL NOT NULL);
            INSERT INTO subscriptions VALUES(
                9, 'c2c', 'synthetic', 'war', NULL, 120, 0, 'platform_denied',
                '{"war_id":801}', 12345, 12346, 3, 'original');
            INSERT INTO subscriptions(id, target_type, target_id, topic)
                VALUES(100, 'c2c', 'removed', 'defense');
            DELETE FROM subscriptions WHERE id = 100;
            INSERT INTO bot_settings VALUES('private-recipient', '"synthetic"');
            INSERT INTO notification_state VALUES('c2c:synthetic', 'subscription:9',
                                                   'saved-hash', 'original');
            INSERT INTO notification_deliveries VALUES('c2c', 'synthetic', 12000);
            PRAGMA user_version = 2;
        """.replace("/* extra topics */", ", 'news', 'announcement', 'dss', 'campaign'"
                    if version == 3 else ""))
        if not retain_subscription:
            connection.execute("DELETE FROM subscriptions")
        if version == 3:
            # Exercise an actual v3-only topic as well as the wider CHECK constraint.
            connection.execute("UPDATE subscriptions SET topic = 'dss' WHERE id = 9")
            connection.execute("PRAGMA user_version = 3")
        before = connection.execute("SELECT * FROM subscriptions").fetchall()
    first, second = Database(path), Database(path)
    await asyncio.gather(first.initialize(), second.initialize())
    try:
        assert [tuple(row) for row in await first.fetch_all("SELECT * FROM subscriptions")] == before
        assert (await second.fetch_one("PRAGMA user_version"))[0] == 4
        assert await second.get_setting("private-recipient") == "synthetic"
        state = await first.fetch_one("SELECT * FROM notification_state")
        assert state["fingerprint"] == "saved-hash" and state["updated_at"] == "original"
        assert tuple(await first.fetch_one("SELECT * FROM notification_deliveries")) == (
            "c2c", "synthetic", 12000,
        )
        service = SubscriptionService(first)
        for topic in ("news", "announcement", "dss", "campaign", "region", "patch"):
            subscription = await service.subscribe("c2c", "new-synthetic", topic)
            assert subscription.id > 100
        with pytest.raises(sqlite3.IntegrityError):
            await first.execute(
                "INSERT INTO subscriptions(target_type, target_id, topic) VALUES (?, ?, ?)",
                ("c2c", "synthetic", "invalid"),
            )
    finally:
        await first.close()
        await second.close()
    async with Database(path) as db:
        assert (await db.fetch_one("PRAGMA user_version"))[0] == 4
        assert len(await db.fetch_all("SELECT * FROM subscriptions")) == 6 + retain_subscription
