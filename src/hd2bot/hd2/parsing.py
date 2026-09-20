"""Small type guards shared by provider parsers; optional data stays unknown."""

import logging
import math
from datetime import UTC, datetime

from hd2bot.hd2.errors import HD2SchemaError

logger = logging.getLogger(__name__)


def number(value) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            parsed = float(value)
        except OverflowError:
            return None
        if math.isfinite(parsed):
            return parsed
    return None


def integer(value) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    parsed = number(value)
    return int(parsed) if parsed is not None and parsed.is_integer() else None


def text(value) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def object_value(value) -> dict:
    return value if isinstance(value, dict) else {}


def list_value(value) -> list:
    return value if isinstance(value, list) else []


def require_object(value, fields: tuple[str, ...] = ()) -> dict:
    if not isinstance(value, dict) or any(key not in value for key in fields):
        raise HD2SchemaError("Required response structure missing")
    return value


def require_list(value) -> list:
    if not isinstance(value, list):
        raise HD2SchemaError("Expected response array")
    return value


def date(value) -> datetime | None:
    try:
        if isinstance(value, str):
            parsed = datetime.fromisoformat(value)
            return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed
        if number(value) is not None:
            return datetime.fromtimestamp(value, UTC)
    except (ValueError, OverflowError, OSError):
        pass
    return None


def progress(health, max_health) -> float | None:
    current, maximum = number(health), number(max_health)
    if current is None or maximum is None or maximum <= 0:
        return None
    return max(0.0, min(100.0, 100 * (1 - current / maximum)))


def sum_known(values) -> int | None:
    items = list(values)
    return sum(items) if items and all(value is not None for value in items) else None
