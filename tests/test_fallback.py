from unittest.mock import AsyncMock

import pytest

from hd2bot.hd2.errors import HD2SchemaError, HD2UnavailableError
from hd2bot.hd2.models import WarStatus
from hd2bot.hd2.providers.fallback import FallbackProvider


async def test_fallback_recovers_and_retains_actual_source():
    captured = AsyncMock(name="captured")
    captured.name = "captured"
    captured.get_war.side_effect = HD2SchemaError("shape changed")
    community = AsyncMock()
    community.name = "community"
    community.get_war.return_value = WarStatus(players=123)
    fallback = FallbackProvider([captured, community])
    result = await fallback.fetch("get_war")
    assert result.source == "community"
    assert result.value.players == 123
    await fallback.fetch("get_war")
    assert captured.get_war.await_count == 1


async def test_no_mock_substitution_when_every_provider_fails():
    failed = AsyncMock()
    failed.name = "captured"
    failed.get_war.side_effect = HD2UnavailableError("down")
    with pytest.raises(HD2UnavailableError):
        await FallbackProvider([failed]).fetch("get_war")
