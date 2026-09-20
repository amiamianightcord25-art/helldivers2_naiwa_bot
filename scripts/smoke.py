"""Opt-in live provider check. Unit tests never call this script."""

import argparse
import asyncio
import json
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hd2bot.application import create_service  # noqa: E402
from hd2bot.config import Settings  # noqa: E402
from hd2bot.hd2.errors import HD2APIError  # noqa: E402


async def smoke(provider: str) -> int:
    async with create_service(replace(Settings.load(), provider=provider)) as service:
        for method in ("get_war", "get_major_order", "get_planets", "get_campaigns", "get_statistics"):
            try:
                result = await getattr(service, method)()
            except HD2APIError as exc:
                print(json.dumps({"resource": method, "error": type(exc).__name__}))
                return 1
            value = result.value
            print(json.dumps({
                "resource": method, "source": result.source,
                "fetched_at": result.fetched_at.isoformat(), "stale": result.stale,
                **({"count": len(value)} if isinstance(value, list) else {"players": value.players}),
            }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("auto", "community", "captured", "mock"), default="auto")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(smoke(args.provider)))
