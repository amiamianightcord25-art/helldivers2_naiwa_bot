"""Exercise refresh scheduling and child-process ownership without network or spawning."""

import asyncio
import json
import os
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from hd2bot.config import Settings
from hd2bot.wiki.sync import WikiSyncManager

NOW = datetime(2026, 9, 16, 12, tzinfo=UTC).timestamp()
DAY = 24 * 3600


class FakeProcess:
    def __init__(self, *, exit_code=0, pending=False, terminate_exits=True):
        self.returncode = None
        self.exit_code = exit_code
        self.terminate_exits = terminate_exits
        self.wait_calls = 0
        self.cancelled_waits = 0
        self.terminate_calls = 0
        self.kill_calls = 0
        self.wait_started = asyncio.Event()
        self.completed = asyncio.Event()
        if not pending:
            self.completed.set()

    async def wait(self):
        self.wait_calls += 1
        self.wait_started.set()
        try:
            await self.completed.wait()
        except asyncio.CancelledError:
            self.cancelled_waits += 1
            raise
        self.returncode = self.exit_code
        return self.returncode

    def terminate(self):
        self.terminate_calls += 1
        if self.terminate_exits:
            self.exit_code = -15
            self.completed.set()

    def kill(self):
        self.kill_calls += 1
        self.exit_code = -9
        self.completed.set()


def snapshot(path, timestamp, **overrides):
    data = {
        "schema_version": 1,
        "synced_at": datetime.fromtimestamp(timestamp, UTC).isoformat(),
        "entries": [{"id": "liberator", "category": "weapons", "name": "解放者",
                     "code": "AR-23", "source_url": "https://helldivers.wiki.gg/wiki/AR-23_Liberator"}],
        **overrides,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf8")
    return data


def read_status(manager):
    return json.loads(manager.status_path.read_text(encoding="utf8"))


@pytest.fixture
def settings(tmp_path):
    return Settings(root=tmp_path, provider="auto", log_dir=tmp_path / "logs",
                    wiki_catalog_path=tmp_path / "data/wiki_catalog.json")


@pytest.fixture
def blocked_spawn(monkeypatch):
    spawn = AsyncMock(side_effect=AssertionError("a real child must never be spawned in tests"))
    monkeypatch.setattr("hd2bot.wiki.sync.asyncio.create_subprocess_exec", spawn)
    monkeypatch.setattr("hd2bot.wiki.sync.asyncio.create_subprocess_shell", spawn)
    return spawn


async def test_recent_bundled_catalog_delays_initial_refresh(settings, blocked_spawn):
    bundled = settings.root / "src/hd2bot/assets/wiki_catalog.json"
    snapshot(bundled, NOW - 3600)
    manager = WikiSyncManager(settings, clock=lambda: NOW)
    assert manager.next_due() == NOW - 3600 + DAY
    assert await manager.run_once() is False
    blocked_spawn.assert_not_called()
    assert not manager.status_path.exists()


async def test_refresh_runs_when_daily_interval_expires(settings, monkeypatch, blocked_spawn):
    clock = [NOW]
    snapshot(settings.wiki_catalog_path, NOW)
    process = FakeProcess()

    async def import_snapshot(*args, **kwargs):
        snapshot(settings.wiki_catalog_path, clock[0])
        return process

    spawn = AsyncMock(side_effect=import_snapshot)
    monkeypatch.setattr("hd2bot.wiki.sync.asyncio.create_subprocess_exec", spawn)
    manager = WikiSyncManager(settings, clock=lambda: clock[0])
    clock[0] += DAY - 1
    assert await manager.run_once() is False
    spawn.assert_not_called()
    clock[0] += 1
    assert await manager.run_once() is True
    spawn.assert_awaited_once()
    assert manager.next_due() == clock[0] + DAY
    assert await manager.run_once() is False
    assert spawn.await_count == 1


def test_newest_valid_snapshot_controls_schedule(settings):
    snapshot(settings.root / "src/hd2bot/assets/wiki_catalog.json", NOW - DAY)
    snapshot(settings.wiki_catalog_path, NOW - 200)
    assert WikiSyncManager(settings, clock=lambda: NOW).next_due() == NOW - 200 + DAY


@pytest.mark.parametrize("bad_stamp", [None, "not a date", "NaN", "999999-01-01T00:00:00Z"])
def test_invalid_timestamp_does_not_defer_refresh(settings, bad_stamp):
    snapshot(settings.wiki_catalog_path, NOW, synced_at=bad_stamp)
    assert WikiSyncManager(settings, clock=lambda: NOW).next_due() <= NOW


def test_future_timestamp_cannot_postpone_refresh_indefinitely(settings):
    snapshot(settings.wiki_catalog_path, NOW + DAY * 365)
    assert WikiSyncManager(settings, clock=lambda: NOW).next_due() <= NOW


@pytest.mark.parametrize("provider,enabled", [("mock", True), ("auto", False), ("mock", False)])
async def test_mock_or_disabled_mode_never_starts_worker_or_child(
    settings, blocked_spawn, provider, enabled,
):
    manager = WikiSyncManager(replace(settings, provider=provider, wiki_sync_enabled=enabled))
    manager.start()
    assert manager._task is None
    assert await manager.run_once(force=True) is False
    await manager.close()
    blocked_spawn.assert_not_called()


async def test_failed_import_preserves_last_good_and_retries_after_one_hour(
    settings, monkeypatch, blocked_spawn,
):
    snapshot(settings.wiki_catalog_path, NOW - DAY)
    last_good = settings.wiki_catalog_path.read_bytes()
    process = FakeProcess(exit_code=2)
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr("hd2bot.wiki.sync.asyncio.create_subprocess_exec", spawn)
    clock = [NOW]
    manager = WikiSyncManager(settings, clock=lambda: clock[0])
    assert await manager.run_once() is False
    assert settings.wiki_catalog_path.read_bytes() == last_good
    status = read_status(manager)
    assert status["status"] == "failed"
    assert status["last_attempt"] == NOW
    assert status["retry_at"] == NOW + 3600
    assert status["error_type"] == "ValueError"
    assert manager.next_due() == NOW + 3600
    clock[0] += 3599
    assert await manager.run_once() is False
    assert spawn.await_count == 1
    clock[0] += 1
    assert await manager.run_once() is False
    assert spawn.await_count == 2
    assert process.terminate_calls == process.kill_calls == 0


async def test_spawn_failure_records_bounded_error_and_keeps_snapshot(
    settings, monkeypatch, blocked_spawn,
):
    snapshot(settings.wiki_catalog_path, NOW - DAY)
    last_good = settings.wiki_catalog_path.read_bytes()
    spawn = AsyncMock(side_effect=OSError("synthetic-secret-should-not-be-recorded"))
    monkeypatch.setattr("hd2bot.wiki.sync.asyncio.create_subprocess_exec", spawn)
    manager = WikiSyncManager(settings, clock=lambda: NOW)
    assert await manager.run_once() is False
    assert settings.wiki_catalog_path.read_bytes() == last_good
    status = read_status(manager)
    assert status["error_type"] == "OSError"
    assert status["retry_at"] == NOW + 3600
    assert "synthetic-secret" not in manager.status_path.read_text(encoding="utf8")


async def test_success_records_metadata_and_resets_prior_backoff(
    settings, monkeypatch, blocked_spawn,
):
    manager = WikiSyncManager(settings, clock=lambda: NOW)
    manager.status_path.parent.mkdir(parents=True)
    manager.status_path.write_text(json.dumps({"status": "failed", "retry_at": NOW + 100,
                                               "last_success": NOW - DAY, "error_type": "OSError"}))
    process = FakeProcess()

    async def import_snapshot(*args, **kwargs):
        snapshot(settings.wiki_catalog_path, NOW)
        return process

    monkeypatch.setattr("hd2bot.wiki.sync.asyncio.create_subprocess_exec", import_snapshot)
    assert await manager.run_once(force=True) is True
    status = read_status(manager)
    assert status["status"] == "ok"
    assert status["last_attempt"] == status["last_success"] == NOW
    assert status["entries"] == 1
    assert status["retry_at"] == 0
    assert status["error_type"] is None
    assert not manager.status_path.with_suffix(".tmp").exists()
    assert manager._child is None
    assert process.wait_calls == 1


@pytest.mark.parametrize("overrides", [
    {"schema_version": 2}, {"entries": []}, {"entries": {"bad": "shape"}},
    {"synced_at": "2020-01-01T00:00:00Z"},
    {"synced_at": "2099-01-01T00:00:00Z"},
])
async def test_zero_exit_without_valid_current_catalog_is_failure(
    settings, monkeypatch, blocked_spawn, overrides,
):
    async def bad_import(*args, **kwargs):
        snapshot(settings.wiki_catalog_path, NOW, **overrides)
        return FakeProcess()

    monkeypatch.setattr("hd2bot.wiki.sync.asyncio.create_subprocess_exec", bad_import)
    manager = WikiSyncManager(settings, clock=lambda: NOW)
    assert await manager.run_once(force=True) is False
    assert read_status(manager)["status"] == "failed"
    assert read_status(manager)["retry_at"] == NOW + 3600


async def test_timeout_terminates_and_reaps_only_owned_child(settings, monkeypatch, blocked_spawn):
    owned = FakeProcess(pending=True)
    unrelated = FakeProcess(pending=True)
    spawn = AsyncMock(return_value=owned)
    monkeypatch.setattr("hd2bot.wiki.sync.asyncio.create_subprocess_exec", spawn)
    manager = WikiSyncManager(settings, clock=lambda: NOW, timeout=0.001)
    assert await manager.run_once() is False
    assert owned.terminate_calls == 1
    assert owned.kill_calls == 0
    assert owned.wait_calls == 2
    assert owned.cancelled_waits == 1
    assert owned.returncode == -15
    assert unrelated.terminate_calls == unrelated.kill_calls == unrelated.wait_calls == 0
    assert manager._child is None
    status = read_status(manager)
    assert status["error_type"] == "TimeoutError"
    assert status["retry_at"] == NOW + 3600


async def test_unresponsive_child_is_killed_and_reaped_after_terminate_timeout(
    settings, monkeypatch, blocked_spawn,
):
    owned = FakeProcess(pending=True, terminate_exits=False)
    monkeypatch.setattr("hd2bot.wiki.sync.asyncio.create_subprocess_exec",
                        AsyncMock(return_value=owned))
    real_wait_for = asyncio.wait_for

    async def quick_timeout(awaitable, timeout):
        return await real_wait_for(awaitable, timeout=min(timeout, 0.001))

    monkeypatch.setattr("hd2bot.wiki.sync.asyncio.wait_for", quick_timeout)
    manager = WikiSyncManager(settings, clock=lambda: NOW, timeout=0.001)
    assert await manager.run_once() is False
    assert owned.terminate_calls == owned.kill_calls == 1
    assert owned.wait_calls == 3
    assert owned.returncode == -9
    assert manager._child is None


async def test_cancelling_run_reaps_owned_child_and_propagates_cancel(
    settings, monkeypatch, blocked_spawn,
):
    owned = FakeProcess(pending=True)
    unrelated = FakeProcess(pending=True)
    monkeypatch.setattr("hd2bot.wiki.sync.asyncio.create_subprocess_exec",
                        AsyncMock(return_value=owned))
    manager = WikiSyncManager(settings, clock=lambda: NOW)
    task = asyncio.create_task(manager.run_once())
    await asyncio.wait_for(owned.wait_started.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert owned.terminate_calls == 1
    assert owned.wait_calls == 2
    assert owned.returncode == -15
    assert unrelated.terminate_calls == unrelated.kill_calls == unrelated.wait_calls == 0
    assert manager._child is None
    assert read_status(manager)["status"] == "cancelled"
    assert read_status(manager)["retry_at"] == NOW + 3600


async def test_start_is_idempotent_and_close_cancels_active_child(
    settings, monkeypatch, blocked_spawn,
):
    owned = FakeProcess(pending=True)
    spawn = AsyncMock(return_value=owned)
    monkeypatch.setattr("hd2bot.wiki.sync.asyncio.create_subprocess_exec", spawn)
    manager = WikiSyncManager(settings, clock=lambda: NOW)
    manager.start()
    worker = manager._task
    manager.start()
    assert manager._task is worker
    await asyncio.wait_for(owned.wait_started.wait(), timeout=1)
    await manager.close()
    assert worker.done()
    assert manager._task is manager._child is None
    assert owned.terminate_calls == 1
    assert owned.returncode == -15
    await manager.close()
    assert owned.terminate_calls == 1
    spawn.assert_awaited_once()


async def test_refresh_argv_uses_exec_paths_and_contains_no_chat_input_or_credentials(
    settings, monkeypatch, blocked_spawn,
):
    # Spaces and shell punctuation remain literal path arguments.
    root = settings.root / "project & space"
    settings = replace(settings, root=root, log_dir=root / "logs",
                       wiki_catalog_path=root / "data/wiki catalog.json",
                       qq_app_id="synthetic-app-id", qq_app_secret="synthetic-app-secret")

    async def import_snapshot(*args, **kwargs):
        snapshot(settings.wiki_catalog_path, NOW)
        return FakeProcess()

    spawn = AsyncMock(side_effect=import_snapshot)
    monkeypatch.setattr("hd2bot.wiki.sync.asyncio.create_subprocess_exec", spawn)
    manager = WikiSyncManager(settings, clock=lambda: NOW)
    assert await manager.run_once() is True
    args, kwargs = spawn.call_args
    assert args == (sys.executable, str(root / "scripts/sync_wiki_catalog.py"),
                    "--output", str(settings.wiki_catalog_path),
                    "--cache-dir", str(root / ".cache/wiki"), "--refresh",
                    "--image-dir", str(settings.wiki_catalog_path.parent / "wiki_images"))
    assert not kwargs.get("shell", False)
    assert settings.qq_app_secret not in repr(args)
    assert settings.qq_app_id not in repr(args)
    assert kwargs["cwd"] == root
    assert kwargs["stdin"] == asyncio.subprocess.DEVNULL
    assert kwargs["stdout"] is kwargs["stderr"]
    assert kwargs["stdout"].closed
    if os.name == "nt":
        assert kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW
    blocked_spawn.assert_not_called()


@pytest.fixture
def clean_config_environment(monkeypatch):
    for key in tuple(os.environ):
        if key.startswith(("HD2_", "QQ_")) or key in {"DATABASE_PATH", "LOG_DIR", "LOG_LEVEL"}:
            monkeypatch.delenv(key)


@pytest.mark.parametrize("value", ["5.99", "720.01", "0", "-24", "nan", "inf", "-inf"])
def test_settings_reject_invalid_sync_intervals(tmp_path, monkeypatch, clean_config_environment, value):
    monkeypatch.setenv("HD2_WIKI_SYNC_INTERVAL_HOURS", value)
    with pytest.raises(ValueError, match="百科同步间隔"):
        Settings.load(tmp_path)


@pytest.mark.parametrize("value", ["6", "24", "720"])
def test_settings_accept_supported_sync_intervals(
    tmp_path, monkeypatch, clean_config_environment, value,
):
    monkeypatch.setenv("HD2_WIKI_SYNC_INTERVAL_HOURS", value)
    assert Settings.load(tmp_path).wiki_sync_interval_hours == float(value)


def test_settings_defaults_and_relative_snapshot_path(tmp_path, clean_config_environment):
    settings = Settings.load(tmp_path)
    assert settings.wiki_sync_enabled is True
    assert settings.wiki_sync_interval_hours == 24
    assert settings.wiki_catalog_path == tmp_path / "data/wiki_catalog.json"


def test_settings_support_disabled_refresh_and_custom_snapshot_path(
    tmp_path, monkeypatch, clean_config_environment,
):
    monkeypatch.setenv("HD2_WIKI_SYNC_ENABLED", "false")
    monkeypatch.setenv("HD2_WIKI_CATALOG_PATH", "snapshots/custom.json")
    settings = Settings.load(tmp_path)
    assert settings.wiki_sync_enabled is False
    assert settings.wiki_catalog_path == tmp_path / "snapshots/custom.json"


def test_settings_reject_invalid_refresh_boolean(tmp_path, monkeypatch, clean_config_environment):
    monkeypatch.setenv("HD2_WIKI_SYNC_ENABLED", "ture")
    with pytest.raises(ValueError, match="HD2_WIKI_SYNC_ENABLED"):
        Settings.load(tmp_path)
