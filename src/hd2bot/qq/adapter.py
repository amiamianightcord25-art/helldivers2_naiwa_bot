"""NoneBot2 + official QQ adapter, with passive replies and bounded work."""

import asyncio
import html
import logging
import os
import re
import time
import unicodedata
from contextlib import asynccontextmanager
from copy import copy
from dataclasses import replace
from datetime import datetime, timezone

import nonebot
from nonebot.adapters.qq import Adapter, Bot
from nonebot.adapters.qq.config import Intents
from nonebot.adapters.qq.event import (
    AtMessageCreateEvent,
    C2CMessageCreateEvent,
    DirectMessageCreateEvent,
    FriendAddEvent,
    GroupAddRobotEvent,
    GroupAtMessageCreateEvent,
)
from nonebot.adapters.qq.models import Media, MessageKeyboard, MessageMarkdown
from nonebot.adapters.qq.utils import unescape
from nonebot.drivers import Timeout
from nonebot.log import logger as nonebot_logger  # noqa: F401

from hd2bot.config import Settings
from hd2bot.presentation import ChatContext, CommandButton
from hd2bot.qq.boundaries import ReplayWindow, split_reply
from hd2bot.qq.welcome import GROUP_WELCOME, PRIVATE_WELCOME, WelcomeStore
from hd2bot.router import CommandRouter
from hd2bot.runtime import notify_ready
from hd2bot.transports.common import configure_sdk_logging as configure_sdk_logging
from hd2bot.transports.common import driver_lifespan as driver_lifespan
from hd2bot.transports.common import safe_nonebot_log as safe_nonebot_log

logger = logging.getLogger(__name__)
COMMAND_TIMEOUT = 180
CONNECT_TIMEOUT = 90
_SNAPSHOT_ID = r"HD2-[A-HJ-NP-Z2-9]{12}"
_DEFINITE_REJECTION = {400, 401, 403, 404, 422, 429}
_URL_REJECTION_CODES = {304003, 40054010}
_FORMAT_REJECTION_CODES = {50037, 50056, 304037, 304036, *_URL_REJECTION_CODES}
_ACTION_PROMPT = "可使用下方按钮继续查询。"


def snapshot_copy_text(content: str) -> str:
    """Return a short copyable line when a career reply contains a snapshot ID."""
    if not isinstance(content, str):
        return ""
    ids = list(dict.fromkeys(re.findall(
        rf"(?:快照 ID：|群聊可用：战绩\s+)({_SNAPSHOT_ID})", content,
    )))
    return "\n".join(
        line for snapshot_id in ids
        for line in (f"快照 ID：{snapshot_id}", f"群聊可用：战绩 {snapshot_id}")
    )


def safe_error_numbers(exc):
    """Only retain bounded integer status/error codes, never remote bodies."""
    result = []
    for name in ("status_code", "code"):
        try:
            value = getattr(exc, name, None)
        except Exception:
            value = None
        result.append(value if type(value) is int and 0 <= value <= 2**31 - 1 else None)
    return tuple(result)


def _without_rejected_urls(content):
    # Preserve link labels and surrounding prose when QQ explicitly refuses URLs.
    content = re.sub(r"\[([^\]\n]+)\]\(https?://[^\s)]+\)", r"\1", content)

    def omitted(match):
        value = match.group()
        trimmed = value.rstrip(".,!?;:)]}")
        return "[链接已省略]" + value[len(trimmed):]

    return re.sub(r"https?://[^\s<>，。！？；：、（）【】《》]+", omitted, content)


class QQAdapter(Adapter):
    """Accept legacy QQ payloads still emitted by the official gateway.

    New adapter models require duplicate IDs and group author metadata that old
    QQ payloads omit. Missing role defaults to the least privileged role so that
    group push management never assumes administrative permission.
    """

    @asynccontextmanager
    async def websocket(self, setup):
        # Adapter 1.7.2 supplies scalar timeout=30. aiohttp maps that to
        # ws_receive, which expires before a quiet QQ gateway's next heartbeat.
        # Keep HTTP request timeouts unchanged and allow heartbeat gaps on WS.
        request = copy(setup)
        request.timeout = Timeout(total=None, connect=15, read=120, close=5)
        async with super().websocket(request) as connection:
            yield connection

    @staticmethod
    def payload_to_event(payload):
        if payload.type in {"C2C_MESSAGE_CREATE", "DIRECT_MESSAGE_CREATE",
                            "GROUP_AT_MESSAGE_CREATE", "GROUP_MESSAGE_CREATE", "AT_MESSAGE_CREATE"}:
            # Record only the event family. No IDs, text, capsules or access tokens.
            logger.info("event=qq_gateway_event kind=%s", str(payload.type))
        if payload.type in {"C2C_MESSAGE_CREATE", "GROUP_AT_MESSAGE_CREATE"}:
            data = dict(payload.data) if isinstance(payload.data, dict) else {}
            author = data.get("author")
            if isinstance(author, dict):
                author = dict(author)
                key = "user_openid" if payload.type == "C2C_MESSAGE_CREATE" else "member_openid"
                if author.get(key):
                    author.setdefault("id", author[key])
                if payload.type == "GROUP_AT_MESSAGE_CREATE":
                    author.setdefault("bot", False)
                    author.setdefault("member_role", "member")
                data["author"] = author
            if payload.type == "GROUP_AT_MESSAGE_CREATE" and data.get("group_openid"):
                data.setdefault("group_id", data["group_openid"])
            payload = payload.model_copy(update={"data": data})
        return Adapter.payload_to_event(payload)


class QQDispatcher:
    def __init__(self, settings: Settings, router: CommandRouter, renderer=None, notifications=None,
                 welcome_store=None, *, channel_clock=time.monotonic, channel_sleep=asyncio.sleep):
        self.settings = settings
        self.router = router
        self.renderer = renderer
        self.notifications = notifications
        self.welcome_store = welcome_store
        self._bot = None
        self._notification_task = None
        self._closing = False
        self._requests: set[asyncio.Task] = set()
        self._slots = asyncio.Semaphore(4)
        self._messages = ReplayWindow(ttl=3600, capacity=4096)
        self._channel_send_lock = asyncio.Lock()
        self._channel_next_send = 0.0
        self._channel_clock = channel_clock
        self._channel_sleep = channel_sleep
        self.connected = asyncio.Event()

    async def process(self, event, bot: Bot):
        if isinstance(event, DirectMessageCreateEvent):
            scope, target = "dms", event.guild_id
            user_id = event.author.id
            if not user_id or getattr(event.author, "bot", False):
                return
        elif isinstance(event, GroupAtMessageCreateEvent):
            scope, target = "group", event.group_openid
            user_id = event.author.member_openid
            if not user_id or event.author.bot:
                return
        elif isinstance(event, AtMessageCreateEvent):
            scope, target = "channel", event.channel_id
            user_id = event.author.id
            if not user_id or getattr(event.author, "bot", False):
                return
        elif isinstance(event, C2CMessageCreateEvent):
            scope, target = "c2c", event.author.user_openid
            user_id = target
        else:
            return
        if self._closing or not target or not event.id or not isinstance(event.content, str):
            return
        if len(self._requests) >= 32:
            logger.warning("event=qq_busy status=dropped")
            return
        if not self._messages.claim(f"{scope}:{target}:{event.id}"):
            return
        task = asyncio.current_task()
        self._requests.add(task)
        logger.info("event=qq_message_received scope=%s", scope)
        try:
            async with asyncio.timeout(COMMAND_TIMEOUT), self._slots:
                reply = await self.router.respond(unescape(event.content),
                                                  context=ChatContext(scope, target, user_id,
                                                                      event.author.member_role if scope == "group" else "member",
                                                                      getattr(event.author, "username", None) or ""))
                if scope in {"c2c", "dms"} and self.welcome_store is not None:
                    try:
                        first = await self.welcome_store.claim_first_message(scope, target)
                    except Exception as exc:
                        first = False
                        logger.warning("event=qq_welcome_state_failed scope=%s reason=%s", scope, type(exc).__name__)
                    if first:
                        card = replace(reply.card, notices=(PRIVATE_WELCOME, *reply.card.notices)) if reply.card else None
                        reply = replace(reply, text=PRIVATE_WELCOME + "\n\n" + reply.text,
                                        card=card)
                await self._reply(bot, scope, target, event.id, reply)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("event=qq_reply_failed reason=%s status=%s code=%s",
                           type(exc).__name__, *safe_error_numbers(exc))
        finally:
            self._requests.discard(task)

    async def welcome(self, event, bot: Bot):
        """Send one event-authorized welcome after bot addition, never on startup."""
        if isinstance(event, GroupAddRobotEvent):
            scope, target, content = "group", event.group_openid, GROUP_WELCOME
            send, destination = bot.post_group_messages, {"group_openid": target}
        elif isinstance(event, FriendAddEvent):
            scope, target, content = "c2c", event.openid, PRIVATE_WELCOME
            send, destination = bot.post_c2c_messages, {"openid": target}
        else:
            return
        if self._closing or not self.welcome_store or not target or not event.event_id:
            return
        age = (datetime.now(timezone.utc) - event.timestamp).total_seconds()
        if not -30 <= age <= 300 or len(self._requests) >= 32:
            return
        task = asyncio.current_task()
        self._requests.add(task)
        try:
            async with asyncio.timeout(25), self._slots:
                if not await self.welcome_store.claim_added_event(scope, target, event.event_id):
                    return
                await send(**destination, event_id=event.event_id, msg_type=0, content=content)
                logger.info("event=qq_welcome_sent scope=%s", scope)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("event=qq_welcome_failed scope=%s reason=%s status=%s code=%s",
                           scope, type(exc).__name__, *safe_error_numbers(exc))
        finally:
            self._requests.discard(task)

    async def _reply(self, bot, scope, target, message_id, reply):
        send, destination = self._reply_destination(bot, scope, target)
        keyboard = self._build_keyboard(reply.keyboard, scope) if reply.keyboard else None
        sequence = 1
        if reply.card is not None and self.settings.image_enabled and self.renderer is not None:
            try:
                jpeg = await self.renderer.render(reply.card)
                if scope in {"group", "c2c"}:
                    upload = bot.post_group_files if scope == "group" else bot.post_c2c_files
                    uploaded = await upload(**destination, file_type=1, file_data=jpeg,
                                            srv_send_msg=False)
                    if not uploaded.file_info:
                        raise ValueError("QQ upload returned no file_info")
                    payload = {"msg_seq": 1, "msg_type": 7,
                               "media": Media(file_info=uploaded.file_info)}
                else:
                    payload = {"file_image": jpeg}
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Rendering and upload do not send a message (srv_send_msg=False).
                logger.warning("event=qq_image_fallback scope=%s reason=%s status=%s code=%s",
                               scope, type(exc).__name__, *safe_error_numbers(exc))
            else:
                # Reserve this attempt even on an explicit rejection. Unknown
                # outcomes stop here: an image may already be visible to the user.
                sequence = 2
                try:
                    await self._send_reply(scope, send, **destination, msg_id=message_id, **payload)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    status, code = safe_error_numbers(exc)
                    if (status not in _DEFINITE_REJECTION
                            and not (status in {None, 200} and code in _FORMAT_REJECTION_CODES)):
                        raise
                    logger.warning("event=qq_image_fallback scope=%s reason=%s status=%s code=%s",
                                   scope, type(exc).__name__, *safe_error_numbers(exc))
                else:
                    logger.info("event=qq_reply_sent scope=%s format=jpeg bytes=%d",
                                scope, len(jpeg))
                    # A separate text/Markdown message retains a copyable ID.
                    # Failure here must never resend the already-delivered image.
                    tail = snapshot_copy_text(reply.text) or (_ACTION_PROMPT if keyboard else "")
                    if tail:
                        await self._reply_content(
                            bot, scope, target, message_id, replace(reply, text=tail, card=None),
                            keyboard=keyboard, sequence=sequence,
                        )
                    return
        await self._reply_content(bot, scope, target, message_id, reply,
                                  keyboard=keyboard, sequence=sequence)

    @staticmethod
    def _reply_destination(bot, scope, target):
        if scope == "group":
            return bot.post_group_messages, {"group_openid": target}
        if scope == "c2c":
            return bot.post_c2c_messages, {"openid": target}
        if scope == "channel":
            return bot.post_messages, {"channel_id": target}
        if scope == "dms":
            return bot.post_dms_messages, {"guild_id": target}
        raise ValueError("unsupported reply scope")

    async def _send_reply(self, scope, send, **payload):
        if scope != "channel":
            return await send(**payload)
        # One dispatcher-wide queue is more conservative than QQ's per-channel
        # allowance. Spacing covers images, Markdown and all fallback attempts.
        async with self._channel_send_lock:
            delay = self._channel_next_send - self._channel_clock()
            while delay > 0:
                await self._channel_sleep(delay)
                delay = self._channel_next_send - self._channel_clock()
            self._channel_next_send = self._channel_clock() + 0.21
            return await send(**payload)

    @staticmethod
    def _build_keyboard(rows, scope):
        try:
            if not isinstance(rows, (tuple, list)) or not 1 <= len(rows) <= 5:
                raise ValueError("invalid keyboard rows")
            for row in rows:
                if not isinstance(row, (tuple, list)) or not 1 <= len(row) <= 5:
                    raise ValueError("invalid keyboard columns")
                for button in row:
                    if (not isinstance(button, CommandButton)
                            or not isinstance(button.label, str) or not 1 <= len(button.label) <= 10
                            or not isinstance(button.command, str)
                            or not 1 <= len(button.command.encode("utf-8")) <= 1024
                            or not button.label.strip() or not button.command.strip()
                            or any(ord(char) < 32 or ord(char) == 127
                                   for char in button.label + button.command)):
                        raise ValueError("invalid keyboard button")
                    user_ids = getattr(button, "user_ids", ())
                    if (not isinstance(user_ids, (tuple, list)) or len(user_ids) > 20
                            or any(not isinstance(user_id, str) or not 1 <= len(user_id) <= 128
                                   or any(char.isspace() or unicodedata.category(char).startswith("C")
                                          for char in user_id) for user_id in user_ids)):
                        raise ValueError("invalid keyboard user IDs")
            return MessageKeyboard.model_validate({"content": {"rows": [
                {"buttons": [{
                    "id": f"command-{row_index}-{index}",
                    "render_data": {"label": button.label, "visited_label": button.label,
                                    "style": 1},
                    "action": {"type": 2, "permission": (
                        {"type": 0, "specify_user_ids": list(button.user_ids)}
                        if getattr(button, "user_ids", ()) else {"type": 2}
                    ), "data": button.command,
                               "enter": scope == "c2c", "reply": False,
                               "unsupport_tips": "请直接发送按钮上的指令"},
                } for index, button in enumerate(row)]}
                for row_index, row in enumerate(rows)
            ]}})
        except (AttributeError, TypeError, ValueError):
            logger.warning("event=qq_keyboard_invalid scope=%s action=keep_content", scope)
            return None

    async def _reply_content(self, bot, scope, target, message_id, reply, *, keyboard, sequence):
        """Bound all attempts, including format rejection, by the passive budget."""
        send, destination = self._reply_destination(bot, scope, target)
        budget = 5 if scope == "group" else 4
        remaining_text = reply.text
        omit_urls = False
        # A first explicit URL rejection may consume one extra send. Preserve
        # room for the final truncated chunk and the useful action/fallback.
        url_retry_reserve = int(bool(re.search(r"https?://", reply.text)))

        async def send_text(text):
            nonlocal sequence, omit_urls
            if sequence > budget:
                return False
            if omit_urls:
                text = _without_rejected_urls(text)
            payload = {"content": text}
            if scope in {"group", "c2c"}:
                payload.update(msg_seq=sequence, msg_type=0)
            sequence += 1
            try:
                await self._send_reply(scope, send, **destination, msg_id=message_id, **payload)
            except Exception as exc:
                status, code = safe_error_numbers(exc)
                if (code not in _URL_REJECTION_CODES
                        or status not in {None, 200, *_DEFINITE_REJECTION}):
                    raise
                without_urls = _without_rejected_urls(text)
                if without_urls == text or sequence > budget:
                    raise
                omit_urls = True
                payload["content"] = without_urls
                if scope in {"group", "c2c"}:
                    payload["msg_seq"] = sequence
                sequence += 1
                await self._send_reply(scope, send, **destination, msg_id=message_id, **payload)
            logger.info("event=qq_reply_sent scope=%s format=text parts=1", scope)
            return True

        if keyboard is not None:
            markdown_text = reply.text or _ACTION_PROMPT
            if len(html.escape(markdown_text, quote=False).encode("utf-8")) > 1800:
                # Reserve a short button message and one plain-text fallback.
                # Full long replies use the existing bounded text splitter.
                count = max(1, budget - sequence - 1 - url_retry_reserve)
                for part in split_reply(reply.text, max_parts=count):
                    if not await send_text(part):
                        break
                markdown_text = _ACTION_PROMPT
                remaining_text = "可直接发送：" + " / ".join(dict.fromkeys(
                    button.action.data for row in keyboard.content.rows for button in row.buttons
                ))
            if sequence < budget:
                sent, error_code = await self._reply_keyboard(
                    bot, scope, target, message_id, replace(reply, text=markdown_text),
                    keyboard=keyboard, sequence=sequence,
                )
                sequence += 1
                if sent:
                    return
                # Only a definite platform rejection reaches this fallback.
                missing_commands = list(dict.fromkeys(
                    button.action.data for row in keyboard.content.rows for button in row.buttons
                    if button.action.data not in markdown_text
                ))
                fallback = (markdown_text.replace(_ACTION_PROMPT, "")
                            if markdown_text == _ACTION_PROMPT else markdown_text)
                if missing_commands:
                    fallback = (fallback + "\n可直接发送：" + " / ".join(missing_commands)).strip()
                if error_code in _URL_REJECTION_CODES:
                    omit_urls = True
                    fallback = _without_rejected_urls(fallback)
                for part in split_reply(fallback, max_parts=budget - sequence + 1):
                    if not await send_text(part):
                        break
                return
        if sequence <= budget:
            count = max(1, budget - sequence + 1 - (url_retry_reserve if not omit_urls else 0))
            for part in split_reply(remaining_text, max_parts=count):
                if not await send_text(part):
                    break

    async def _reply_keyboard(self, bot, scope, target, message_id, reply, *, keyboard, sequence=1):
        payload = {"msg_id": message_id, "markdown": MessageMarkdown(
            content=html.escape(reply.text, quote=False)), "keyboard": keyboard}
        send, destination = self._reply_destination(bot, scope, target)
        if scope in {"group", "c2c"}:
            payload.update(msg_type=2, msg_seq=sequence)
        try:
            await self._send_reply(scope, send, **destination, **payload)
        except Exception as exc:
            status, code = safe_error_numbers(exc)
            logger.warning("event=qq_keyboard_failed scope=%s reason=%s status=%s code=%s",
                           scope, type(exc).__name__, status, code)
            if (status in _DEFINITE_REJECTION
                    or (status in {None, 200} and code in _FORMAT_REJECTION_CODES)):
                return False, code
            raise
        logger.info("event=qq_reply_sent scope=%s format=keyboard", scope)
        return True, None

    async def _reply_dms(self, bot, guild_id, message_id, reply, *, scope="dms"):
        """Compatibility entry point for the two guild message transports."""
        await self._reply(bot, scope, guild_id, message_id, reply)

    async def close(self):
        self._closing = True
        if self._notification_task is not None:
            self._notification_task.cancel()
            await asyncio.gather(self._notification_task, return_exceptions=True)
            self._notification_task = None
        tasks = [task for task in self._requests if task is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def connected_bot(self, bot):
        self._bot = bot
        if self.notifications is not None and self._notification_task is None:
            self._notification_task = asyncio.create_task(
                self.notifications.run(self.send_proactive), name="hd2-notifications",
            )

    def disconnected_bot(self, bot):
        if self._bot is bot:
            self._bot = None

    async def send_proactive(self, scope: str, target: str, text: str):
        """Called only for a persisted opt-in; never fake an old passive msg_id."""
        from hd2bot.services.notifications import (
            PermanentPushError,
            RetryablePushError,
            UncertainPushError,
        )
        if self._closing or self._bot is None:
            raise RetryablePushError("disconnected")
        access = getattr(self.notifications, "access", None)
        if access is not None and not await access.allowed(scope, target):
            raise PermanentPushError("recipient_not_allowed")
        if scope not in {"group", "c2c"}:
            raise PermanentPushError("invalid_scope")
        data = {"group_openid" if scope == "group" else "openid": target}
        content = split_reply(text, max_parts=1)[0]
        api = self._bot.post_group_messages if scope == "group" else self._bot.post_c2c_messages
        try:
            async with asyncio.timeout(25):
                await api(**data, msg_type=0, content=content)
        except Exception as exc:
            status, code = safe_error_numbers(exc)
            logger.warning("event=qq_push_failed scope=%s reason=%s status=%s code=%s",
                           scope, type(exc).__name__, status, code)
            # Stable request/permission failures require explicit operator action.
            # Rate limiting is temporary and remains eligible for bounded backoff.
            if status in {400, 401, 403, 404}:
                raise PermanentPushError("platform_denied") from None
            if status == 429:
                raise RetryablePushError("rate_limited") from None
            if status is not None:
                raise RetryablePushError("http_failure") from None
            raise UncertainPushError() from None
        logger.info("event=qq_push_sent scope=%s format=text", scope)


def initialize_nonebot(settings: Settings, dispatcher: QQDispatcher):
    if not settings.qq_app_id or not settings.qq_app_secret:
        raise ValueError("QQ_APP_ID 和 QQ_APP_SECRET 尚未配置，请先使用 --cli。")
    if settings.qq_transport != "websocket":
        raise ValueError("当前 NoneBot2 版本仅启用 QQ_TRANSPORT=websocket。")
    configure_sdk_logging()
    intents = {name: False for name in Intents.model_fields}
    intents["c2c_group_at_messages"] = True
    intents["direct_message"] = True
    intents["at_messages"] = True
    nonebot.init(
        driver="~aiohttp", _env_file=None, log_level="WARNING", api_timeout=settings.timeout,
        qq_is_sandbox=settings.qq_sandbox,
        qq_bots=[{"id": settings.qq_app_id, "secret": settings.qq_app_secret, "token": "",
                  "intent": intents, "use_websocket": True}],
    )
    driver = nonebot.get_driver()
    driver.register_adapter(QQAdapter)
    # Hooks run in reverse order: stop business before closing QQ networking.
    driver.on_shutdown(dispatcher.close)
    plugin = (nonebot.get_plugin_by_module_name("hd2bot.qq.plugin")
              or nonebot.load_plugin("hd2bot.qq.plugin"))
    if plugin is None:
        raise RuntimeError("QQ command plugin failed to load")
    plugin.module.configure(dispatcher)

    @driver.on_bot_connect
    async def connected(bot: Bot):
        notify_ready()
        dispatcher.connected_bot(bot)
        dispatcher.connected.set()
        logger.info("event=qq_ready transport=websocket framework=nonebot2 app_id=%s sandbox=%s scopes=c2c,group,channel,dms",
                    settings.qq_app_id, settings.qq_sandbox)

    @driver.on_bot_disconnect
    async def disconnected(bot: Bot):
        dispatcher.disconnected_bot(bot)

    return driver


async def run_qq(settings: Settings, router: CommandRouter) -> int:
    # Credentials and unsupported transports fail without spawning a browser.
    if not settings.qq_app_id or not settings.qq_app_secret:
        raise ValueError("QQ_APP_ID 和 QQ_APP_SECRET 尚未配置，请先使用 --cli。")
    if settings.qq_transport != "websocket":
        raise ValueError("当前 NoneBot2 版本仅启用 QQ_TRANSPORT=websocket。")
    from hd2bot.rendering.renderer import HtmlRenderer

    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(settings.root / ".cache/ms-playwright"))
    renderer = HtmlRenderer(
        browser_path=settings.image_browser_path, browser_channel=settings.image_browser_channel,
        width=settings.image_width, jpeg_quality=settings.image_quality,
        max_bytes=settings.image_max_bytes, timeout=settings.image_timeout,
        cache_dir=settings.root / ".cache/renders",
        max_concurrency=settings.render_concurrency,
        qr_path=settings.image_qr_path,
    )
    dispatcher = QQDispatcher(settings, router, renderer, getattr(router, "notifications", None),
                              welcome_store=WelcomeStore(settings.database_path.parent / "welcome.sqlite3", settings.qq_app_id))
    try:
        driver = initialize_nonebot(settings, dispatcher)
        async with driver_lifespan(driver):
            await asyncio.wait_for(dispatcher.connected.wait(), timeout=CONNECT_TIMEOUT)
            await asyncio.Event().wait()
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("event=qq_connection_failed reason=%s", type(exc).__name__)
        print("QQ 连接未成功，请检查机器人配置、网络和开放平台权限。CLI 仍可独立使用。")
        return 1
    finally:
        await dispatcher.close()
        await renderer.close()
    return 0
