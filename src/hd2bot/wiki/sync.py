"""Daily local snapshot refresh, isolated from QQ's event loop and query service."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)


def _read(path: Path) -> dict:
    try:
        if path.stat().st_size > 30_000_000:
            return {}
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _stamp(value) -> float:
    try:
        if not isinstance(value, str):
            return 0
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return stamp.replace(tzinfo=stamp.tzinfo or UTC).timestamp()
    except (ValueError, OverflowError):
        return 0


class WikiSyncManager:
    """Own just the importer child process; never stop a process obtained from a PID file."""

    def __init__(self, settings, *, clock=time.time, timeout=1800):
        self.settings, self.clock, self.timeout = settings, clock, timeout
        self.interval = settings.wiki_sync_interval_hours * 3600
        self.status_path = settings.wiki_catalog_path.with_name("wiki_sync_status.json")
        self._task = None
        self._child = None
        self._lock = asyncio.Lock()

    def next_due(self) -> float:
        """Use the bundled snapshot on first launch; do not re-download it immediately."""
        now = self.clock()
        snapshots = [self.settings.wiki_catalog_path,
                     self.settings.root / "src/hd2bot/assets/wiki_catalog.json"]
        stamps = []
        for path in snapshots:
            data = _read(path)
            if (data.get("schema_version") == 1 and isinstance(data.get("entries"), list)
                    and data["entries"]):
                stamp = _stamp(data.get("synced_at"))
                if 0 < stamp <= now + 300:
                    stamps.append(min(stamp, now))
        due = max(stamps, default=0) + self.interval
        status = _read(self.status_path)
        retry = status.get("retry_at", 0)
        if type(retry) in (int, float) and math.isfinite(retry) and 0 < retry <= now + self.interval:
            due = max(due, retry)
        return due

    def _status(self, **changes):
        state = {**_read(self.status_path), **changes}
        self.status_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.status_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf8")
        temporary.replace(self.status_path)

    async def _stop_child(self):
        child = self._child
        if child is None or child.returncode is not None:
            return
        try:
            child.terminate()
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(child.wait(), timeout=5)
        except TimeoutError:
            try:
                child.kill()
            except ProcessLookupError:
                return
            await child.wait()

    async def run_once(self, *, force=False) -> bool:
        if not self.settings.wiki_sync_enabled or self.settings.provider == "mock":
            return False
        async with self._lock:
            if not force and self.next_due() > self.clock():
                return False
            started = self.clock()
            self._status(status="running", last_attempt=started)
            log_path = self.settings.log_dir / "wiki-sync.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
                # No shell, credentials, or chat input participates in this command.
                with log_path.open("ab") as output:
                    self._child = await asyncio.create_subprocess_exec(
                        sys.executable, str(self.settings.root / "scripts/sync_wiki_catalog.py"),
                        "--output", str(self.settings.wiki_catalog_path),
                        "--cache-dir", str(self.settings.root / ".cache/wiki"), "--refresh",
                        "--image-dir", str(self.settings.wiki_catalog_path.parent / "wiki_images"),
                        cwd=self.settings.root, stdout=output, stderr=output,
                        stdin=asyncio.subprocess.DEVNULL, **options,
                    )
                    code = await asyncio.wait_for(self._child.wait(), timeout=self.timeout)
                data = _read(self.settings.wiki_catalog_path)
                if (code != 0 or data.get("schema_version") != 1
                        or not isinstance(data.get("entries"), list) or not data["entries"]
                        or not started - 60 <= _stamp(data.get("synced_at")) <= self.clock() + 300):
                    raise ValueError("Wiki importer did not produce a complete refreshed snapshot")
                self._status(status="ok", last_success=self.clock(), retry_at=0,
                             entries=len(data["entries"]), error_type=None)
                logger.info("event=wiki_sync status=ok entries=%s", len(data["entries"]))
                return True
            except asyncio.CancelledError:
                await self._stop_child()
                self._status(status="cancelled", retry_at=self.clock() + 3600)
                raise
            except Exception as exc:
                await self._stop_child()
                self._status(status="failed", error_type=type(exc).__name__,
                             retry_at=self.clock() + 3600)
                logger.warning("event=wiki_sync status=failed reason=%s", type(exc).__name__)
                return False
            finally:
                self._child = None

    async def _run(self):
        while True:
            try:
                await self.run_once()
                delay = max(1, self.next_due() - self.clock())
            except Exception as exc:
                logger.warning("event=wiki_sync_worker reason=%s", type(exc).__name__)
                delay = 3600
            # Sleep is asynchronous; QQ and local stop signals remain responsive.
            await asyncio.sleep(min(delay, 3600))

    def start(self):
        if (self.settings.wiki_sync_enabled and self.settings.provider != "mock"
                and self._task is None):
            self._task = asyncio.create_task(self._run(), name="hd2-wiki-sync")
            logger.info("event=wiki_sync_scheduled interval_hours=%s next_due=%s",
                        self.settings.wiki_sync_interval_hours,
                        datetime.fromtimestamp(max(self.clock(), self.next_due()), UTC).isoformat())

    async def close(self):
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
