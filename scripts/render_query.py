"""Export a query card locally; short replies are printed as text without a browser."""

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dataclasses import replace  # noqa: E402

from hd2bot.application import create_career_service, create_service  # noqa: E402
from hd2bot.config import Settings  # noqa: E402
from hd2bot.rendering.renderer import HtmlRenderer  # noqa: E402
from hd2bot.router import CommandRouter  # noqa: E402
from hd2bot.steam import SteamService  # noqa: E402
from hd2bot.wiki.service import CatalogService  # noqa: E402


async def render(args) -> int:
    settings = Settings.load()
    if args.provider:
        settings = replace(settings, provider=args.provider)
    async with (create_service(settings) as service, create_career_service(settings) as career,
                SteamService(settings) as steam):
        reply = await CommandRouter(service, career, steam=steam,
                                    catalog=CatalogService(settings.wiki_catalog_path)).respond(args.command)
        if reply.card is None:
            print(reply.text)
            print("此回复使用文字，无需生成图片。")
            return 0
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(settings.root / ".cache/ms-playwright"))
        renderer = HtmlRenderer(browser_path=settings.image_browser_path,
            browser_channel=settings.image_browser_channel, width=settings.image_width,
            jpeg_quality=settings.image_quality, max_bytes=settings.image_max_bytes,
            timeout=settings.image_timeout, max_concurrency=settings.render_concurrency,
            qr_path=settings.image_qr_path)
        try:
            encoded = await renderer.render(reply.card)
        finally:
            await renderer.close()
        output = Path(args.output).resolve()
        if output.suffix.lower() not in {".jpg", ".jpeg"}:
            raise ValueError("Output suffix must be .jpg or .jpeg")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(encoded)
        print(f"JPEG: {output} ({len(encoded):,} bytes)")
        return 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command")
    parser.add_argument("--provider", choices=("auto", "mock", "captured", "community"))
    parser.add_argument("--output", default="tmp/query.jpg")
    raise SystemExit(asyncio.run(render(parser.parse_args())))
