"""Select exactly one live transport; business services stay independent."""


async def run_bot(settings, router) -> int:
    if settings.bot_backend == "napcat":
        from hd2bot.napcat.adapter import run_napcat
        return await run_napcat(settings, router)
    from hd2bot.qq.adapter import run_qq
    return await run_qq(settings, router)
