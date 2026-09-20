"""Plain-text fragments that should move to the next line as a whole."""

import re
import unicodedata

_SEPARATOR = re.compile(r"(\n|[ \t]+[·•/][ \t]+|、|[，；;]|,[ \t]+)")
_UNIT = re.compile(
    r"[（(][^()（）\n]{1,24}[）)]"
    r"|区域\s*#\d+"
    r"|(?<![A-Za-z0-9])[A-Z]{1,8}-\d+[A-Z]*(?:/[A-Z]+)?"
    r"|\d[\d,]*(?:\.\d+)?[ \t]*(?:发/分钟|发/分|HP/秒|申购点|申购单|奖章|超级货币|超级点数|"
    r"普通样本|稀有样本|实弹|射弹|弹体|爆炸|能量|热能|火焰|电弧|毒气|"
    r"小时|分钟|毫秒|秒|毫米|米|人|枚|发|%|％)"
)


def _cells(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1 for char in text)


def text_parts(value: str) -> list[tuple[str, bool]]:
    """Return escaped-by-Jinja text, never HTML; keep short phrases and units intact.

    Long paragraphs retain normal Chinese line breaking. Their model codes,
    parenthetical qualifiers and numbers with units remain indivisible.
    """
    result = []
    for part in _SEPARATOR.split(value):
        if not part:
            continue
        if part.strip() and _cells(part) <= 26:
            result.append((part, True))
            continue
        start = 0
        for match in _UNIT.finditer(part):
            if match.start() > start:
                result.append((part[start:match.start()], False))
            result.append((match[0], _cells(match[0]) <= 26))
            start = match.end()
        if start < len(part):
            result.append((part[start:], False))
    return result
