"""Bounded replay memory and UTF-8 message chunks for a local query bot."""

import time
from collections import OrderedDict


class ReplayWindow:
    def __init__(self, *, ttl=3600, capacity=4096, clock=time.monotonic):
        self.ttl, self.capacity, self.clock = ttl, capacity, clock
        self._seen: OrderedDict[str, float] = OrderedDict()

    def claim(self, key: str) -> bool:
        now = self.clock()
        while self._seen:
            first = next(iter(self._seen))
            if self._seen[first] > now:
                break
            self._seen.popitem(last=False)
        if key in self._seen:
            return False
        self._seen[key] = now + self.ttl
        while len(self._seen) > self.capacity:
            self._seen.popitem(last=False)
        return True


TRUNCATED = "\n【内容较长，已缩略；请发送 星球 <名称> 查询具体星球。】"


def split_reply(content: str, *, max_parts: int, byte_limit: int = 1800) -> list[str]:
    """Keep Unicode characters intact and state explicitly when the quota truncates text."""
    if max_parts < 1 or byte_limit < len(TRUNCATED.encode("utf-8")):
        raise ValueError("Reply limits are too small")
    if not content:
        return []
    parts = []
    remainder = content
    while remainder and len(parts) < max_parts:
        encoded = remainder.encode("utf-8")
        if len(encoded) <= byte_limit:
            parts.append(remainder)
            break
        if len(parts) == max_parts - 1:
            budget = byte_limit - len(TRUNCATED.encode("utf-8"))
            parts.append(encoded[:budget].decode("utf-8", errors="ignore") + TRUNCATED)
            break
        prefix = encoded[:byte_limit].decode("utf-8", errors="ignore")
        newline = prefix.rfind("\n")
        if newline > len(prefix) // 2:
            prefix = prefix[:newline + 1]
        parts.append(prefix)
        remainder = remainder[len(prefix):]
    return parts
