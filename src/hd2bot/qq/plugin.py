"""NoneBot matcher boundary: official C2C, channel direct messages and group @ messages."""

from nonebot import on_message, on_notice
from nonebot.adapters.qq import Bot
from nonebot.adapters.qq.event import (
    AtMessageCreateEvent,
    C2CMessageCreateEvent,
    DirectMessageCreateEvent,
    Event,
    FriendAddEvent,
    GroupAddRobotEvent,
    GroupAtMessageCreateEvent,
)

_dispatcher = None


def configure(dispatcher):
    global _dispatcher
    _dispatcher = dispatcher


def official_query_event(event: Event) -> bool:
    return isinstance(event, (C2CMessageCreateEvent, DirectMessageCreateEvent,
                              GroupAtMessageCreateEvent, AtMessageCreateEvent))


commands = on_message(rule=official_query_event, priority=10, block=True)


@commands.handle()
async def query(bot: Bot, event: Event):
    if _dispatcher is not None:
        await _dispatcher.process(event, bot)


def official_added_event(event: Event) -> bool:
    return isinstance(event, (FriendAddEvent, GroupAddRobotEvent))


welcomes = on_notice(rule=official_added_event, priority=10, block=False)


@welcomes.handle()
async def greet(bot: Bot, event: Event):
    if _dispatcher is not None:
        await _dispatcher.welcome(event, bot)
