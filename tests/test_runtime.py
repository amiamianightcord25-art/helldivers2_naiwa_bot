"""Local process control uses OS ownership and cooperative shutdown, never QQ."""

import asyncio
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from hd2bot import main as main_module
from hd2bot.config import Settings
from hd2bot.runtime import (
    AlreadyRunningError,
    BotRuntime,
    InstanceLock,
    notify_ready,
    process_status,
    read_state,
    request_stop,
    runtime_paths,
    stop_path,
)


@pytest.fixture
def settings(tmp_path):
    return Settings(
        root=tmp_path, database_path=tmp_path / "data" / "bot.db",
        log_dir=tmp_path / "logs", provider="mock",
        qq_app_id="synthetic-app", qq_app_secret="SYNTHETIC_SECRET_FOR_RUNTIME_TEST",
    )


def child_command(code, *arguments):
    source = str(Path(main_module.__file__).resolve().parents[1])
    prefix = "import sys; sys.path.insert(0, sys.argv.pop(1)); "
    return [sys.executable, "-B", "-c", prefix + code, source, *map(str, arguments)]


def test_os_lock_rejects_real_competing_process_then_allows_it(settings):
    lock_path, _ = runtime_paths(settings)
    lock = InstanceLock(lock_path)
    code = (
        "from pathlib import Path; from hd2bot.runtime import InstanceLock; "
        "lock=InstanceLock(Path(sys.argv[1])); "
        "print('acquired' if lock.acquire() else 'busy'); lock.release()"
    )
    assert lock.acquire()
    try:
        blocked = subprocess.run(child_command(code, lock_path), capture_output=True,
                                 text=True, timeout=5, check=True)
        assert blocked.stdout.strip() == "busy"
    finally:
        lock.release()
    available = subprocess.run(child_command(code, lock_path), capture_output=True,
                               text=True, timeout=5, check=True)
    assert available.stdout.strip() == "acquired"


def test_abrupt_child_exit_releases_lock_without_manual_unlock(settings):
    lock_path, _ = runtime_paths(settings)
    code = (
        "import os; from pathlib import Path; from hd2bot.runtime import InstanceLock; "
        "lock=InstanceLock(Path(sys.argv[1])); assert lock.acquire(); "
        "print('locked',flush=True); sys.stdin.readline(); os._exit(23)"
    )
    child = subprocess.Popen(child_command(code, lock_path), stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "locked"
        assert process_status(settings)["running"]
        child.communicate("exit\n", timeout=5)
        assert child.returncode == 23
        assert not process_status(settings)["running"]
    finally:
        if child.poll() is None:
            child.communicate("exit\n", timeout=5)


def test_stale_live_pid_and_ready_json_do_not_count_as_running(settings):
    _, state_path = runtime_paths(settings)
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps({
        "pid": os.getpid(), "status": "ready", "instance": "0" * 32,
    }), encoding="utf8")
    assert not process_status(settings)["running"]
    assert not request_stop(settings)
    assert not stop_path(settings, "0" * 32).exists()


async def test_tampered_stop_target_cannot_write_outside_runtime_directory(settings):
    outside = settings.root / "unrelated.txt"
    outside.write_text("keep", encoding="utf8")
    async with BotRuntime(settings) as runtime:
        state = read_state(runtime.state_path)
        for invalid in ("../../unrelated.txt", str(outside), "not-an-instance"):
            runtime.state_path.write_text(json.dumps({**state, "instance": invalid}),
                                          encoding="utf8")
            with pytest.raises(ValueError):
                request_stop(settings)
            assert outside.read_text(encoding="utf8") == "keep"
        # An arbitrary JSON path is ignored even when the instance is valid.
        runtime.state_path.write_text(json.dumps({**state, "stop_file": str(outside)}),
                                      encoding="utf8")
        assert request_stop(settings)
        assert runtime.stop_file.exists()
        assert outside.read_text(encoding="utf8") == "keep"


async def test_starting_status_and_repeated_stop_requests_are_idempotent(settings):
    async with BotRuntime(settings) as runtime:
        state = process_status(settings)
        assert state["running"] and state["status"] == "starting"
        assert request_stop(settings)
        assert request_stop(settings)
        assert list(settings.database_path.parent.glob(".stop-*")) == [runtime.stop_file]
    assert not runtime.stop_file.exists()
    assert not request_stop(settings)


async def test_previous_instance_stop_file_does_not_stop_next_instance(settings):
    async with BotRuntime(settings) as previous:
        old_stop = previous.stop_file
        previous_instance = previous.instance
    async with BotRuntime(settings) as current:
        assert current.instance != previous_instance
        old_stop.write_text("stop", encoding="ascii")
        waiting = asyncio.create_task(current._wait_stop())
        try:
            await asyncio.sleep(0.01)
            assert not waiting.done()
            assert process_status(settings)["running"]
        finally:
            waiting.cancel()
            await asyncio.gather(waiting, return_exceptions=True)


async def test_cooperative_shutdown_keeps_lock_until_application_cleanup_finishes(settings):
    started, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def application():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release.wait()

    async with BotRuntime(settings) as runtime:
        running = asyncio.create_task(runtime.run(application()))
        await asyncio.wait_for(started.wait(), timeout=1)
        assert request_stop(settings)
        await asyncio.wait_for(cleaning.wait(), timeout=1)
        state = process_status(settings)
        assert state["running"] and state["status"] == "stopping"
        notify_ready()
        assert process_status(settings)["status"] == "stopping"
        assert not running.done()
        release.set()
        assert await asyncio.wait_for(running, timeout=1) == 0
    state = process_status(settings)
    assert not state["running"] and state["status"] == "stopped"


async def test_ready_from_child_task_writes_status_without_credentials(settings):
    async with BotRuntime(settings) as runtime:
        async def ready_callback():
            notify_ready()
        await asyncio.create_task(ready_callback())
        state = process_status(settings)
        assert state["status"] == "ready" and state["ready_at"]
        serialized = runtime.state_path.read_text(encoding="utf8")
        assert settings.qq_app_secret not in serialized
        assert settings.qq_app_id not in serialized
        assert "secret" not in serialized.lower()
    # A callback outside any runtime must be harmless and leave the old state closed.
    notify_ready()
    assert read_state(runtime.state_path)["status"] == "stopped"


async def test_duplicate_runtime_cannot_overwrite_owner_state(settings):
    async with BotRuntime(settings) as owner:
        owner.ready()
        before = owner.state_path.read_bytes()
        with pytest.raises(AlreadyRunningError):
            async with BotRuntime(settings):
                pytest.fail("duplicate runtime entered")
        assert owner.state_path.read_bytes() == before
        assert process_status(settings)["running"]


async def test_application_exception_releases_lock_and_records_only_exception_type(settings):
    async def application():
        raise RuntimeError("PRIVATE_EXCEPTION_DETAIL")

    with pytest.raises(RuntimeError, match="PRIVATE_EXCEPTION_DETAIL"):
        async with BotRuntime(settings) as runtime:
            await runtime.run(application())
    state = process_status(settings)
    assert not state["running"] and state["status"] == "failed"
    assert state["error_type"] == "RuntimeError"
    assert "PRIVATE_EXCEPTION_DETAIL" not in runtime.state_path.read_text(encoding="utf8")


async def test_nonzero_application_exit_is_recorded_as_failed(settings):
    async def application():
        return 1

    async with BotRuntime(settings) as runtime:
        assert await runtime.run(application()) == 1
    state = process_status(settings)
    assert not state["running"] and state["status"] == "failed"
    assert state["exit_code"] == 1


async def test_shutdown_does_not_hide_application_cleanup_failure(settings):
    started = asyncio.Event()

    async def application():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            raise RuntimeError("PRIVATE_CLEANUP_ERROR")

    with pytest.raises(RuntimeError, match="PRIVATE_CLEANUP_ERROR"):
        async with BotRuntime(settings) as runtime:
            running = asyncio.create_task(runtime.run(application()))
            await asyncio.wait_for(started.wait(), timeout=1)
            assert request_stop(settings)
            await asyncio.wait_for(running, timeout=1)
    state = process_status(settings)
    assert not state["running"] and state["status"] == "failed"
    assert state["error_type"] == "RuntimeError"
    assert "PRIVATE_CLEANUP_ERROR" not in runtime.state_path.read_text(encoding="utf8")


async def test_shutdown_preserves_application_cleanup_exit_code(settings):
    started = asyncio.Event()

    async def application():
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            return 7

    async with BotRuntime(settings) as runtime:
        running = asyncio.create_task(runtime.run(application()))
        await asyncio.wait_for(started.wait(), timeout=1)
        assert request_stop(settings)
        assert await asyncio.wait_for(running, timeout=1) == 7
    state = process_status(settings)
    assert not state["running"] and state["status"] == "failed"
    assert state["exit_code"] == 7


@pytest.mark.parametrize("instance", [None, 123, [], {}])
async def test_non_string_runtime_instance_is_rejected_as_invalid_state(settings, instance):
    async with BotRuntime(settings) as runtime:
        state = read_state(runtime.state_path)
        runtime.state_path.write_text(json.dumps({**state, "instance": instance}), encoding="utf8")
        with pytest.raises(ValueError, match="Invalid runtime instance"):
            request_stop(settings)
        assert not runtime.stop_file.exists()


def test_start_waits_for_new_instance_ready_instead_of_stale_previous_ready(settings, monkeypatch):
    script = Path(main_module.__file__).resolve().parents[2] / "scripts" / "bot_control.py"
    spec = importlib.util.spec_from_file_location("audit_bot_control", script)
    control = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(control)
    interpreter = settings.root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    interpreter.parent.mkdir(parents=True)
    interpreter.write_bytes(b"synthetic interpreter, never executed")
    old = {"running": False, "status": "ready", "instance": "a" * 32, "pid": 111}
    current = {"running": True, "status": "ready", "instance": "b" * 32, "pid": 222}
    states = iter([old, {**old, "running": True}, current])
    monkeypatch.setattr(control, "process_status", lambda settings: next(states))
    child = SimpleNamespace(pid=222, poll=lambda: None)
    monkeypatch.setattr(control.subprocess, "Popen", lambda *args, **kwargs: child)
    clock = [0.0]
    monkeypatch.setattr(control.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(control.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    shown = []
    monkeypatch.setattr(control, "show_status", lambda settings, state=None: shown.append(state))
    assert control.start_bot(settings, timeout=1) == 0
    assert shown == [current]


async def test_cli_and_single_command_bypass_running_qq_lock(settings, monkeypatch):
    application = AsyncMock(return_value=0)
    monkeypatch.setattr(main_module, "_run_application", application)
    async with BotRuntime(settings):
        assert await main_module._run(settings, SimpleNamespace(cli=True, command=None)) == 0
        assert await main_module._run(settings, SimpleNamespace(cli=False, command="战况")) == 0
    assert application.await_count == 2
