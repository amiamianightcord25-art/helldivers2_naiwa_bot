"""Carry actual fetch times across concurrent reads within one provider call."""

from contextvars import ContextVar
from datetime import datetime

observations: ContextVar[dict[str, datetime] | None] = ContextVar("hd2_observations", default=None)


def record_observation(key: str, fetched_at: datetime) -> None:
    current = observations.get()
    if current is not None:
        previous = current.get(key)
        current[key] = min(previous, fetched_at) if previous is not None else fetched_at
