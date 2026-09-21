"""Only friend messages and explicitly mentioned group messages reach the router."""

from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot
from nonebot.adapters.onebot.v11.event import Event

from hd2bot.napcat.adapter import addressed_message

_dispatcher = None


def configure(dispatcher):
    global _dispatcher
    _dispatcher = dispatcher


def directed_message(event: Event) -> bool:
    return addressed_message(event)


commands = on_message(rule=directed_message, priority=10, block=True)


@commands.handle()
async def query(bot: Bot, event: Event):
    if _dispatcher is not None:
        await _dispatcher.process(event, bot)
