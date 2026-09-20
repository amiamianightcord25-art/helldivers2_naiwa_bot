"""Opt-in actual career query; emit metadata only, never credentials or career values."""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from hd2bot.application import create_career_service  # noqa: E402
from hd2bot.career.models import CareerError  # noqa: E402
from hd2bot.config import Settings  # noqa: E402


async def smoke() -> int:
    async with create_career_service(Settings.load()) as service:
        try:
            result = await service.query()
            second = await service.query()
        except CareerError as exc:
            print(json.dumps({'ok': False, 'status': exc.code}))
            return 1
    print(json.dumps({
        'ok': True, 'field_count': len(result.values), 'queried_at': result.queried_at.isoformat(),
        'server_cached': result.cached, 'session_refreshed': result.refreshed,
        'second_query_local_cached': second.local_cached,
        'timestamp_preserved': result.queried_at == second.queried_at,
        'mock': result.mock,
    }))
    return 0


if __name__ == '__main__':
    raise SystemExit(asyncio.run(smoke()))
