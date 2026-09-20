"""Outgoing, authenticated WS client for the documented career query API."""

import asyncio
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp

from hd2bot.career.models import CareerError, CareerStats, parse_reply
from hd2bot.career.tunnel import LocalTunnel

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ConnectionConfig:
    url: str
    token: str = field(repr=False)
    binding: str = "owner"
    ssh_host: str | None = None

    @classmethod
    def load(cls, path: Path) -> "ConnectionConfig":
        try:
            if path.stat().st_size > 16384:
                raise ValueError()
            value = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(value, dict):
                raise ValueError()
            url, token, binding = value["url"], value["token"], value.get("bindingId", "owner")
            if not all(isinstance(item, str) for item in (url, token, binding)):
                raise ValueError()
            parsed = urlsplit(url)
            if (parsed.scheme not in {"ws", "wss"} or not parsed.hostname
                    or parsed.username is not None or parsed.password is not None
                    or parsed.query or parsed.fragment or any(c.isspace() for c in url)):
                raise ValueError()
            if not 32 <= len(token) <= 128 or any(not 33 <= ord(c) <= 126 for c in token):
                raise ValueError()
            if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", binding) is None:
                raise ValueError()
            _ = parsed.port  # Validate an explicit port before attempting the handshake.
            ssh_host = value.get("sshHost")
            if ssh_host is not None:
                if (not isinstance(ssh_host, str)
                        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", ssh_host)
                        or parsed.scheme != "ws" or parsed.hostname != "127.0.0.1"
                        or not parsed.port or not 1024 <= parsed.port <= 65535):
                    raise ValueError()
            return cls(url, token, binding, ssh_host)
        except (OSError, ValueError, KeyError, TypeError):
            raise CareerError("invalid_config") from None


class CareerClient:
    def __init__(self, config: ConnectionConfig, *, timeout: float = 90, retries: int = 2,
                 session: aiohttp.ClientSession | None = None):
        self.config, self.timeout, self.retries = config, timeout, retries
        self._session = session
        self._owns_session = session is None
        self._connection = None
        self._lock = asyncio.Lock()
        self._closed = False
        self._tunnel = LocalTunnel(config.ssh_host, urlsplit(config.url).port) if config.ssh_host else None

    async def _disconnect(self):
        connection, self._connection = self._connection, None
        if connection is not None:
            try:
                await asyncio.wait_for(connection.close(), timeout=2)
            except (TimeoutError, aiohttp.ClientError, OSError):
                pass

    async def close(self):
        self._closed = True
        await self._disconnect()
        if self._owns_session and self._session is not None:
            await self._session.close()
        if self._tunnel is not None:
            await self._tunnel.close()

    async def _connect(self):
        url = self.config.url
        if self._tunnel is not None:
            port = await self._tunnel.ensure()
            url = f"ws://127.0.0.1:{port}{urlsplit(url).path}"
        if self._session is None:
            # Match the supplied client: direct outbound connection, no Windows proxy dependence.
            self._session = aiohttp.ClientSession(
                trust_env=False, timeout=aiohttp.ClientTimeout(total=15),
            )
        self._connection = await self._session.ws_connect(
            url,
            headers={"Authorization": "Bearer " + self.config.token},
            heartbeat=30, autoping=True, compress=0, max_msg_size=65536,
            timeout=aiohttp.ClientWSTimeout(ws_close=2),
        )

    async def query(self) -> CareerStats:
        started = time.monotonic()
        request_id = uuid.uuid4().hex
        status = "cancelled"
        try:
            # Includes the lock queue, connect, response, retry delay and reconnect budget.
            async with asyncio.timeout(self.timeout):
                async with self._lock:
                    try:
                        result = await self._exchange(request_id)
                    except BaseException:
                        # Clean up before releasing the lock. A cancelled waiter never closes
                        # another request's active connection or consumes its late response.
                        await self._disconnect()
                        raise
                status = "ok"
                return result
        except TimeoutError:
            status = "timeout"
            raise CareerError("timeout") from None
        except asyncio.CancelledError:
            raise
        except CareerError as exc:
            status = exc.code
            raise
        finally:
            logger.info("event=career_query status=%s latency=%.3f", status, time.monotonic() - started)

    async def _exchange(self, request_id: str) -> CareerStats:
        if self._closed:
            raise CareerError("unavailable")
        for attempt in range(self.retries + 1):
            try:
                if self._connection is None or self._connection.closed:
                    await self._connect()
                await self._connection.send_json({
                    "requestId": request_id, "action": "career.query",
                    "bindingId": self.config.binding,
                })
                message = await self._connection.receive()
                if message.type in {aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSE,
                                    aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.ERROR}:
                    raise ConnectionError()
                if message.type != aiohttp.WSMsgType.TEXT:
                    raise CareerError("schema_error")
                try:
                    payload = json.loads(message.data)
                except (ValueError, UnicodeError):
                    raise CareerError("schema_error") from None
                return parse_reply(payload, request_id, self.config.binding)
            except aiohttp.WSServerHandshakeError as exc:
                code = "authentication_failed" if exc.status in {401, 403} else "unavailable"
                raise CareerError(code) from None
            except (aiohttp.ClientError, OSError, TimeoutError):
                await self._disconnect()
                if attempt == self.retries:
                    raise CareerError("unavailable") from None
                # Retrying the same operation reuses its id to avoid duplicate backend work.
                await asyncio.sleep(min(4, 2 ** attempt))

    async def request_user(self, action: str, user_id: str, envelope: str | None = None, *, share_id=None, chat_type="private"):
        """Send trusted C2C identity separately from user-supplied command text."""
        if not re.fullmatch(r"[A-Za-z0-9_:@.\-]{1,128}", user_id):
            raise CareerError("invalid_user_id")
        request_id = uuid.uuid4().hex
        request = {"requestId": request_id, "action": action,
                   "userId": user_id, "chatType": chat_type}
        if share_id is not None:
            request["shareId"] = share_id
        if envelope is not None:
            request["envelope"] = envelope
        # These actions change one-time state and the backend does not deduplicate
        # them by requestId. A lost response must never trigger a second mutation.
        uncertain_code = {
            "binding.import": "submission_unknown",
            "binding.challenge": "challenge_unknown",
            "binding.share": "share_unknown",
            "binding.unshare": "unshare_unknown",
            "binding.revoke": "revoke_unknown",
        }.get(action)
        submitted = False
        try:
            async with asyncio.timeout(self.timeout), self._lock:
                try:
                    for attempt in range(self.retries + 1):
                        submitted = False
                        try:
                            if self._closed:
                                raise CareerError("unavailable")
                            if self._connection is None or self._connection.closed:
                                await self._connect()
                            submitted = True
                            await self._connection.send_json(request)
                            message = await self._connection.receive()
                            if message.type in {aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSE,
                                                aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.ERROR}:
                                raise ConnectionError()
                            if message.type != aiohttp.WSMsgType.TEXT:
                                raise CareerError("schema_error")
                            try:
                                payload = json.loads(message.data)
                            except ValueError:
                                raise CareerError("schema_error") from None
                            if not isinstance(payload, dict) or payload.get("requestId") not in (None, request_id):
                                raise CareerError("schema_error")
                            if payload.get("ok") is not True:
                                # Reuse the established allowlist for failures.
                                parse_reply(payload, request_id, "")
                            if payload.get("requestId") != request_id:
                                raise CareerError("schema_error")
                            if action in {"career.query", "snapshot.query"}:
                                binding = payload.get("bindingId")
                                pattern = r"HD2-[A-HJ-NP-Z2-9]{12}" if action == "snapshot.query" else r"b_[A-Za-z0-9_-]{24}"
                                if not isinstance(binding, str) or not re.fullmatch(pattern, binding) or (action == "snapshot.query" and binding != share_id):
                                    raise CareerError("schema_error")
                                return parse_reply(payload, request_id, binding)
                            if action == "binding.challenge":
                                code = payload.get("challenge")
                                if (payload.get("status") != "challenge_issued"
                                        or not isinstance(code, str)
                                        or not re.fullmatch(r"[A-HJ-NP-Z2-9]{6}", code)
                                        or payload.get("expiresInSeconds") != 120):
                                    raise CareerError("schema_error")
                                return {"challenge": code, "expiresInSeconds": 120}
                            if action in {"binding.share", "binding.unshare"}:
                                expected = "share_enabled" if action == "binding.share" else "share_disabled"
                                share = payload.get("shareId")
                                if payload.get("status") != expected or (action == "binding.share" and (
                                        not isinstance(share, str) or not re.fullmatch(r"HD2-[A-HJ-NP-Z2-9]{12}", share))):
                                    raise CareerError("schema_error")
                                return {"ok": True, "shareId": share}
                            expected = "bound" if action == "binding.import" else "revoked"
                            if payload.get("status") != expected:
                                raise CareerError("schema_error")
                            if action == "binding.import" and (payload.get("snapshot") is not True
                                    or payload.get("credentialsRetained") is not False):
                                raise CareerError("schema_error")
                            return {"ok": True, "status": expected}
                        except aiohttp.WSServerHandshakeError as exc:
                            raise CareerError("authentication_failed" if exc.status in (401, 403) else "unavailable") from None
                        except (aiohttp.ClientError, OSError, TimeoutError):
                            await self._disconnect()
                            if submitted and uncertain_code:
                                raise CareerError(uncertain_code) from None
                            if attempt == self.retries:
                                raise CareerError("unavailable") from None
                            await asyncio.sleep(min(4, 2 ** attempt))
                        except CareerError as exc:
                            if submitted and uncertain_code and exc.code == "schema_error":
                                raise CareerError(uncertain_code) from None
                            raise
                except BaseException:
                    await self._disconnect()
                    raise
        except TimeoutError:
            raise CareerError(uncertain_code if submitted and uncertain_code else "timeout") from None
