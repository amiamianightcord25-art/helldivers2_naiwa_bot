import argparse
import asyncio
import sqlite3
from dataclasses import replace

from hd2bot import __version__
from hd2bot.config import Settings
from hd2bot.logging_setup import configure_logging
from hd2bot.runtime import AlreadyRunningError, BotRuntime


async def _run(settings: Settings, args) -> int:
    if not args.cli and args.command is None:
        if not (settings.qq_app_id and settings.qq_app_secret):
            print("QQ 凭据尚未配置。请在 .env 设置 QQ_APP_ID 和 QQ_APP_SECRET。\n"
                  "本地查询请运行：python run.py --cli（离线可加 --provider mock）。")
            return 2
        async with BotRuntime(settings) as runtime:
            return await runtime.run(_run_application(settings, args))
    return await _run_application(settings, args)


async def _run_application(settings: Settings, args) -> int:
    from hd2bot.application import create_career_service, create_service
    from hd2bot.cli import run_cli
    from hd2bot.router import CommandRouter
    from hd2bot.services.checkin import CheckinService
    from hd2bot.services.menu import MenuService
    from hd2bot.services.stratagem_hero import StratagemHeroService
    from hd2bot.steam import SteamService
    from hd2bot.storage.database import Database
    from hd2bot.wiki.service import CatalogService

    async with (Database(settings.database_path) as db, create_service(settings) as service,
                create_career_service(settings) as career, SteamService(settings) as steam):
        checkin = CheckinService(db)
        hero = StratagemHeroService(db)
        await checkin.initialize()
        await hero.initialize()
        router = CommandRouter(service, career, notifications=None, steam=steam,
                               career_enabled=False, proactive_enabled=False,
                               catalog=CatalogService(settings.wiki_catalog_path),
                               checkin=checkin, hero=hero,
                               menu=MenuService(settings.database_path.parent / "menu.json"))
        if args.command is not None:
            print(await router.handle(args.command))
            return 0
        if args.cli:
            return await run_cli(router)
        from hd2bot.qq.adapter import run_qq
        from hd2bot.wiki.sync import WikiSyncManager
        sync = WikiSyncManager(settings)
        sync.start()
        try:
            return await run_qq(settings, router)
        finally:
            await sync.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="HELLDIVERS 2 QQ Bot")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--cli", action="store_true", help="启动交互式本地终端")
    parser.add_argument("--command", help="执行一条指令后退出，无需 QQ 配置")
    parser.add_argument("--provider", choices=("auto", "captured", "community", "mock"))
    args = parser.parse_args()
    try:
        settings = Settings.load()
        if args.provider:
            settings = replace(settings, provider=args.provider)
        configure_logging(settings)
        return asyncio.run(_run(settings, args))
    except AlreadyRunningError as exc:
        print(str(exc))
        return 2
    except (ValueError, OSError):
        print("配置或本地路径不可用，请检查 .env 与日志/数据目录权限。")
        return 2
    except sqlite3.Error:
        print("本地数据库暂时不可用，请检查 DATABASE_PATH、文件权限或数据库占用情况。")
        return 2
    except KeyboardInterrupt:
        print("\n通讯终端已关闭。")
        return 0
