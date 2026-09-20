"""Private backend access through the maintainer's existing SSH configuration."""
import asyncio
import os
import re
import socket
import subprocess

from hd2bot.career.models import CareerError


class LocalTunnel:
    def __init__(self, host: str, remote_port: int):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", host):
            raise CareerError("invalid_config")
        if type(remote_port) is not int or not 1024 <= remote_port <= 65535:
            raise CareerError("invalid_config")
        self.host, self.remote_port = host, remote_port
        self.process = None
        self.port = None

    async def ensure(self):
        if self.process is not None and self.process.returncode is None:
            return self.port
        await self.close()
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reserved:
            reserved.bind(("127.0.0.1", 0))
            self.port = reserved.getsockname()[1]
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        self.process = await asyncio.create_subprocess_exec(
            "ssh", "-4", "-N", "-T", "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=yes", "-o", "ExitOnForwardFailure=yes",
            "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=30",
            "-o", "ServerAliveCountMax=2", "-L",
            f"127.0.0.1:{self.port}:127.0.0.1:{self.remote_port}", self.host,
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL, **options,
        )
        try:
            async with asyncio.timeout(12):
                while self.process.returncode is None:
                    try:
                        _, writer = await asyncio.open_connection("127.0.0.1", self.port)
                        writer.close()
                        await writer.wait_closed()
                        return self.port
                    except OSError:
                        await asyncio.sleep(0.1)
            raise CareerError("unavailable")
        except BaseException:
            await self.close()
            raise

    async def close(self):
        process, self.process = self.process, None
        if process is not None and process.returncode is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(process.wait(), 3)
            except TimeoutError:
                process.kill()
                await process.wait()
