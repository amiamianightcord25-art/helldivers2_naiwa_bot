"""Passive OneBot V11 replies; no requests, approvals or automatic group messages."""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from copy import copy
from dataclasses import replace

import nonebot
from nonebot.adapters.onebot.v11 import Adapter, Bot, Message, MessageSegment
from nonebot.adapters.onebot.v11.event import GroupMessageEvent, PrivateMessageEvent
from nonebot.drivers import Timeout

from hd2bot.commands.parser import DISABLED_COMMANDS, parse_command
from hd2bot.config import Settings, validate_bot_settings
from hd2bot.hd2.errors import CommandError
from hd2bot.presentation import ChatContext, CommandReply
from hd2bot.runtime import notify_disconnected, notify_ready
from hd2bot.transports.boundaries import ReplayWindow, split_reply
from hd2bot.transports.common import configure_sdk_logging, driver_lifespan

logger = logging.getLogger(__name__)
COMMAND_TIMEOUT = 180
CONNECT_TIMEOUT = 90
HEALTH_INTERVAL = 30


class NapCatAdapter(Adapter):
    @asynccontextmanager
    async def websocket(self, setup):
        request = copy(setup)
        # NapCat sends heartbeats; don't expire a quiet session after 30 seconds.
        request.timeout = Timeout(total=None, connect=15, read=120, close=5)
        async with super().websocket(request) as connection:
            yield connection


def addressed_message(event) -> bool:
    if isinstance(event, PrivateMessageEvent):
        return event.sub_type == "friend" and event.user_id != event.self_id
    if isinstance(event, GroupMessageEvent) and event.user_id != event.self_id:
        # to_me also accepts quoted replies; require an actual @ in the original message.
        return any(seg.type == "at" and str(seg.data.get("qq")) == str(event.self_id)
                   for seg in event.original_message)
    return False


def command_content(event) -> str | None:
    # No OCR, attachment reads or hidden JSON/XML commands.
    if any(seg.type not in {"text", "at", "reply"} for seg in event.message):
        return None
    content = event.get_plaintext().strip()
    return content if content and len(content) <= 8192 else None


def chat_context(event) -> ChatContext:
    prefix = f"onebot:{event.self_id}:"
    group = isinstance(event, GroupMessageEvent)
    role = event.sender.role if group and event.sender.role in {"owner", "admin"} else "member"
    display = event.sender.card or event.sender.nickname or ""
    return ChatContext(
        "group" if group else "c2c",
        prefix + str(event.group_id if group else event.user_id),
        prefix + str(event.user_id), role, display,
    )


def plain_reply(reply: CommandReply, context: ChatContext) -> str:
    text = reply.text.replace("点击下方按钮，或直接发送对应指令：", "发送以下指令：")
    text = text.replace("；按钮填入指令后点击发送", "")
    text = text.replace("可使用下方按钮继续查询。", "可发送指令继续查询。")
    commands = []
    for row in reply.keyboard:
        for button in row:
            if button.user_ids and context.user_id not in button.user_ids:
                continue
            try:
                command = parse_command(button.command)
            except CommandError:
                continue
            if command.name in DISABLED_COMMANDS:
                continue
            if button.command not in text and button.command not in commands:
                commands.append(button.command)
    if commands:
        text += "\n可发送：" + " / ".join(commands)
    return text


class NapCatDispatcher:
    def __init__(self, settings: Settings, router, renderer=None):
        self.settings, self.router, self.renderer = settings, router, renderer
        self.connected = asyncio.Event()
        self.changed = asyncio.Event()
        self.bot = None
        self.generation = 0
        self._closing = False
        self._requests = set()
        self._slots = asyncio.Semaphore(4)
        self._messages = ReplayWindow(ttl=3600, capacity=4096)

    def connected_bot(self, bot):
        self.bot = bot
        self.generation += 1
        self.connected.set()
        self.changed.set()

    def disconnected_bot(self, bot):
        if self.bot is bot:
            self.bot = None
            self.connected.clear()
            self.changed.set()
            notify_disconnected()
            logger.warning("event=napcat_disconnected")

    async def process(self, event, bot: Bot):
        if self._closing or not addressed_message(event) or str(event.self_id) != bot.self_id:
            return
        if (isinstance(event, GroupMessageEvent) and self.settings.napcat_allowed_groups
                and str(event.group_id) not in self.settings.napcat_allowed_groups):
            return
        content = command_content(event)
        if content is None:
            return
        context = chat_context(event)
        if len(self._requests) >= 32:
            logger.warning("event=napcat_busy status=dropped")
            return
        if not self._messages.claim(
                f"{event.self_id}:{context.scope}:{context.target_id}:{event.message_id}"):
            return
        task = asyncio.current_task()
        self._requests.add(task)
        logger.info("event=napcat_message_received scope=%s", context.scope)
        try:
            async with asyncio.timeout(COMMAND_TIMEOUT), self._slots:
                reply = await self.router.respond(content, context=context)
                await self.reply(bot, event, reply, context)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # A send timeout is ambiguous; don't duplicate the message with a retry.
            logger.warning("event=napcat_reply_failed scope=%s reason=%s",
                           context.scope, type(exc).__name__)
        finally:
            self._requests.discard(task)

    async def reply(self, bot, event, reply, context):
        text = plain_reply(reply, context)
        image = None
        if reply.card is not None and self.settings.image_enabled and self.renderer is not None:
            try:
                # Do not reuse the official application's QR in a personal-account reply.
                image = await self.renderer.render(reply.card)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("event=napcat_render_failed reason=%s", type(exc).__name__)
        messages = []
        if image:
            # Bytes become a base64 image; no shared file paths needed between hosts.
            payload = Message(MessageSegment.image(image))
            if reply.keyboard:
                hint = plain_reply(replace(reply, text="", card=None), context).strip()
                if hint:
                    payload += MessageSegment.text("\n" + hint)
            messages.append(payload)
        else:
            # Explicit text segments prevent CQ-code execution from upstream/user text.
            messages = [Message(MessageSegment.text(part))
                        for part in split_reply(text, max_parts=3, byte_limit=3000)]
        for message in messages:
            if isinstance(event, GroupMessageEvent):
                await bot.send_group_msg(group_id=event.group_id, message=message)
            else:
                await bot.send_private_msg(user_id=event.user_id, message=message)
            logger.info("event=napcat_reply_accepted scope=%s kind=%s",
                        context.scope, "image" if image else "text")

    async def close(self):
        self._closing = True
        tasks = [task for task in self._requests if task is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


def initialize_napcat(settings, dispatcher):
    validate_bot_settings(settings)
    configure_sdk_logging()
    # Override unselected adapter settings rather than inheriting unrelated .env values.
    nonebot.init(
        driver="~aiohttp", _env_file=None, log_level="WARNING", api_timeout=settings.timeout,
        nickname=[], onebot_v11_ws_urls=[settings.napcat_ws_url],
        onebot_v11_access_token=settings.napcat_access_token,
        onebot_v11_api_roots={}, onebot_v11_secret=None,
    )
    driver = nonebot.get_driver()
    driver.register_adapter(NapCatAdapter)
    driver.on_shutdown(dispatcher.close)
    plugin = (nonebot.get_plugin_by_module_name("hd2bot.napcat.plugin")
              or nonebot.load_plugin("hd2bot.napcat.plugin"))
    if plugin is None:
        raise RuntimeError("OneBot command plugin unavailable")
    plugin.module.configure(dispatcher)

    @driver.on_bot_connect
    async def connected(bot):
        if isinstance(bot, Bot):
            dispatcher.connected_bot(bot)

    @driver.on_bot_disconnect
    async def disconnected(bot):
        if isinstance(bot, Bot):
            dispatcher.disconnected_bot(bot)

    return driver


async def watch_connection(dispatcher, *, connect_timeout=CONNECT_TIMEOUT,
                           interval=HEALTH_INTERVAL, max_failures=3):
    failures, ready_generation = 0, None
    while True:
        await asyncio.wait_for(dispatcher.connected.wait(), connect_timeout)
        dispatcher.changed.clear()
        bot, generation = dispatcher.bot, dispatcher.generation
        try:
            async with asyncio.timeout(dispatcher.settings.timeout):
                status = await bot.get_status()
            if not isinstance(status, dict) or status.get("online") is not True or status.get("good") is not True:
                raise ConnectionError("napcat_not_online")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            notify_disconnected()
            failures += 1
            logger.warning("event=napcat_health_failed reason=%s", type(exc).__name__)
            if failures >= max_failures:
                raise ConnectionError("napcat_health_unavailable") from None
        else:
            if dispatcher.bot is bot and dispatcher.generation == generation:
                if ready_generation != generation or failures:
                    notify_ready()
                    logger.info("event=napcat_ready transport=onebot_v11 scopes=c2c,group")
                ready_generation, failures = generation, 0
        try:
            await asyncio.wait_for(dispatcher.changed.wait(), interval)
        except TimeoutError:
            pass


async def run_napcat(settings, router) -> int:
    validate_bot_settings(settings)
    from hd2bot.rendering.renderer import HtmlRenderer

    # Keep retired features off even for a caller constructing a router directly.
    router.career_enabled = False
    router.proactive_enabled = False
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(settings.root / ".cache/ms-playwright"))
    renderer = HtmlRenderer(
        browser_path=settings.image_browser_path, browser_channel=settings.image_browser_channel,
        width=settings.image_width, jpeg_quality=settings.image_quality,
        max_bytes=settings.image_max_bytes, timeout=settings.image_timeout,
        cache_dir=settings.root / ".cache/renders", max_concurrency=settings.render_concurrency,
        qr_path=None, include_qr=False,
    )
    dispatcher = NapCatDispatcher(settings, router, renderer)
    try:
        driver = initialize_napcat(settings, dispatcher)
        async with driver_lifespan(driver):
            await watch_connection(dispatcher)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("event=napcat_connection_failed reason=%s", type(exc).__name__)
        print("NapCat 服务不可用，请检查连接配置和 QQ 登录状态。")
        return 1
    finally:
        await dispatcher.close()
        await renderer.close()
    return 0
