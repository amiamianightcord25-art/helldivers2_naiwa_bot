"""Local process ownership and cooperative shutdown, independent of QQ transport."""

import asyncio
import contextvars
import errno
import json
import logging
import os
import time
import uuid
from pathlib import Path

from hd2bot.hd2.models import utcnow

logger = logging.getLogger(__name__)
_current = contextvars.ContextVar("bot_runtime", default=None)


class AlreadyRunningError(Exception):
    pass


class InstanceLock:
    """OS-owned lock. A dead process releases it even if its JSON/PID remains."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._file = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        if self.path.stat().st_size == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                return False
            raise
        self._file = handle
        return True

    def release(self):
        if self._file is not None:
            handle, self._file = self._file, None
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()


def runtime_paths(settings):
    directory = settings.database_path.parent
    return directory / "bot.lock", directory / "runtime.json"


def read_state(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def process_status(settings) -> dict:
    lock_path, state_path = runtime_paths(settings)
    lock = InstanceLock(lock_path)
    running = not lock.acquire()
    if not running:
        lock.release()
    return {**read_state(state_path), "running": running}


def stop_path(settings, instance: str) -> Path:
    # Never trust a PID or arbitrary path from a stale/tampered JSON record.
    if not isinstance(instance, str):
        raise ValueError("Invalid runtime instance")
    parsed = uuid.UUID(instance)
    if parsed.hex != instance:
        raise ValueError("Invalid runtime instance")
    return settings.database_path.parent / f".stop-{instance}"


def request_stop(settings) -> bool:
    state = process_status(settings)
    if not state["running"]:
        return False
    path = stop_path(settings, state.get("instance", ""))
    path.write_text("stop", encoding="ascii")
    return True


def notify_ready() -> None:
    runtime = _current.get()
    if runtime is not None:
        runtime.ready()


def notify_disconnected() -> None:
    runtime = _current.get()
    if runtime is not None and runtime.state["status"] == "ready":
        runtime.state["status"] = "starting"
        runtime._write()


class BotRuntime:
    def __init__(self, settings):
        self.settings = settings
        lock_path, self.state_path = runtime_paths(settings)
        self.lock = InstanceLock(lock_path)
        self.instance = uuid.uuid4().hex
        self.stop_file = stop_path(settings, self.instance)
        self.state = {
            "instance": self.instance, "pid": os.getpid(),
            "entry": str(settings.root / "run.py"),
            "started_at": utcnow().isoformat(), "status": "starting",
            "backend": settings.bot_backend,
            "transport": "onebot_v11" if settings.bot_backend == "napcat" else settings.qq_transport,
            "sandbox": settings.qq_sandbox if settings.bot_backend == "official" else None,
        }
        self._token = None

    def _write(self):
        temporary = self.state_path.with_name(f".runtime-{self.instance}.tmp")
        try:
            temporary.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf8")
            # Antivirus or a reader can briefly hold the target on Windows.
            for attempt in range(5):
                try:
                    temporary.replace(self.state_path)
                    break
                except PermissionError:
                    if attempt == 4:
                        raise
                    time.sleep(0.02)
        finally:
            temporary.unlink(missing_ok=True)

    async def __aenter__(self):
        if not self.lock.acquire():
            raise AlreadyRunningError("本项目机器人已在运行，请使用查看状态或关闭脚本。")
        try:
            self._write()
            self._token = _current.set(self)
            return self
        except BaseException:
            self.lock.release()
            raise

    async def __aexit__(self, exc_type, exc, traceback):
        try:
            self.state["status"] = "failed" if exc_type or self.state.get("exit_code", 0) else "stopped"
            self.state["stopped_at"] = utcnow().isoformat()
            if exc_type:
                self.state["error_type"] = exc_type.__name__
            self._write()
        finally:
            try:
                self.stop_file.unlink(missing_ok=True)
            finally:
                if self._token is not None:
                    _current.reset(self._token)
                self.lock.release()

    def ready(self):
        if self.state["status"] not in {"starting", "ready"}:
            return
        self.state["status"] = "ready"
        self.state["ready_at"] = utcnow().isoformat()
        self._write()

    async def _wait_stop(self):
        while not self.stop_file.exists():
            await asyncio.sleep(0.25)

    async def run(self, application):
        task = asyncio.create_task(application)
        stop = asyncio.create_task(self._wait_stop())
        try:
            done, _ = await asyncio.wait((task, stop), return_when=asyncio.FIRST_COMPLETED)
            if task in done:
                result = await task
                self.state["exit_code"] = result
                return result
            self.state["status"] = "stopping"
            self._write()
            logger.info("event=local_shutdown_requested")
            task.cancel()
            result, = await asyncio.gather(task, return_exceptions=True)
            if isinstance(result, asyncio.CancelledError) or result is None:
                result = 0
            elif isinstance(result, BaseException):
                raise result
            self.state["exit_code"] = result
            return result
        finally:
            for pending in (task, stop):
                if not pending.done():
                    pending.cancel()
            await asyncio.gather(task, stop, return_exceptions=True)
