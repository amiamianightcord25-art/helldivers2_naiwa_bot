"""Parallel reads finish or clean up together before their caller returns."""

import asyncio
from collections.abc import Awaitable
from typing import TypeVar

T = TypeVar("T")


async def gather_cancel_on_error(*awaitables: Awaitable[T]) -> list[T]:
    tasks = [asyncio.ensure_future(awaitable) for awaitable in awaitables]
    try:
        return await asyncio.gather(*tasks)
    except BaseException:
        # gather propagates the first error without cancelling its siblings.
        # Preserve that error, but release sibling HTTP sessions/cache locks first.
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
