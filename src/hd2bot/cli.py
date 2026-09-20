"""Interactive terminal adapter; the exact same router is used by QQ."""

import asyncio

from hd2bot.router import CommandRouter


async def run_cli(router: CommandRouter, *, input_func=input, output_func=print) -> int:
    output_func("HELLDIVERS 2 通讯终端。输入 帮助 查看命令，退出 / exit 结束。")
    while True:
        try:
            content = await asyncio.to_thread(input_func, "HD2> ")
        except EOFError:
            break
        if content.strip().lower().lstrip("/") in {"退出", "exit", "quit", "q"}:
            break
        if not content.strip():
            continue
        output_func(await router.handle(content))
    return 0
