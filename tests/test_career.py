"""Career contract, live loopback WS transport, isolation and presentation tests."""

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import aiohttp
import pytest
from aiohttp import web
from nonebot.adapters.qq.models import Dispatch

from hd2bot.application import create_career_service, create_service
from hd2bot.career.client import CareerClient, ConnectionConfig
from hd2bot.career.formatter import GROUPS, format_career, format_career_error
from hd2bot.career.models import FIELDS, CareerError, parse_reply
from hd2bot.career.service import CareerService
from hd2bot.commands.parser import parse_command
from hd2bot.config import Settings
from hd2bot.hd2.errors import CommandError
from hd2bot.qq.adapter import QQAdapter, QQDispatcher
from hd2bot.router import HELP, CommandRouter

TOKEN = 'synthetic-local-test-token-00000000000000'
STAMP = datetime(2026, 1, 1, tzinfo=UTC)


def response(request_id='test', **updates):
    result = {'requestId': request_id, 'ok': True, 'bindingId': 'owner', 'fieldCount': 27,
              'career': {name: i for i, name in enumerate(FIELDS)},
              'queriedAt': STAMP.timestamp(), 'cached': True, 'refreshed': False}
    result.update(updates)
    return result


@asynccontextmanager
async def ws_server(on_message, *, reject=False):
    connections, requests = [], []

    async def handle(request):
        assert request.query_string == ''
        assert request.headers.get('Authorization') == 'Bearer ' + TOKEN
        if reject:
            return web.Response(status=401, text='private upstream response')
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        connections.append(ws)
        async for message in ws:
            assert message.type == aiohttp.WSMsgType.TEXT
            payload = json.loads(message.data)
            requests.append(payload)
            await on_message(ws, payload)
        return ws

    app = web.Application()
    app.router.add_get('/ws', handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    url = f'ws://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/ws'
    try:
        yield url, connections, requests
    finally:
        for ws in connections:
            await ws.close()
        await runner.cleanup()


def test_complete_response_and_unknown_private_fields_filtered():
    payload = response()
    payload['career']['sessionId'] = 'not-for-display'
    result = parse_reply(payload, 'test', 'owner')
    assert set(result.values) == set(FIELDS)
    assert result.values[FIELDS[0]] == 0
    assert result.queried_at == STAMP
    assert 'not-for-display' not in repr(result)


@pytest.mark.parametrize('invalid', [None, -1, True, 1.5, '5', 9007199254740992])
def test_invalid_or_missing_stat_never_becomes_zero(invalid):
    payload = response()
    payload['career'][FIELDS[0]] = invalid
    with pytest.raises(CareerError, match='schema_error'):
        parse_reply(payload, 'test', 'owner')


@pytest.mark.parametrize('updates', [
    {'requestId': None}, {'requestId': 'other'}, {'bindingId': 'other'}, {'fieldCount': 26},
    {'fieldCount': True}, {'career': []}, {'ok': 1}, {'cached': None}, {'refreshed': 1},
    {'queriedAt': True}, {'queriedAt': -1}, {'queriedAt': float('inf')},
    {'queriedAt': 10 ** 1000},
    {'queriedAt': (datetime.now(UTC) + timedelta(hours=1)).timestamp()},
])
def test_contract_rejects_wrong_identity_time_and_shape(updates):
    with pytest.raises(CareerError, match='schema_error'):
        parse_reply(response(**updates), 'test', 'owner')


def test_failure_keeps_only_allowlisted_code_and_cooldown():
    with pytest.raises(CareerError) as error:
        parse_reply({'ok': False, 'status': 'refresh_cooldown', 'retryAfterSeconds': 60,
                     'message': TOKEN}, 'test', 'owner')
    assert error.value.code == 'refresh_cooldown'
    assert '60' in format_career_error(error.value)
    assert TOKEN not in format_career_error(error.value)
    with pytest.raises(CareerError) as unknown:
        parse_reply({'ok': False, 'status': TOKEN}, 'test', 'owner')
    assert unknown.value.code == 'backend_unavailable'


def test_private_config_validation_and_repr(tmp_path):
    path = tmp_path/'config.json'
    path.write_text(json.dumps({'url': 'ws://127.0.0.1:8888/ws', 'token': TOKEN,
                                'bindingId': 'owner'}), encoding='utf8')
    config = ConnectionConfig.load(path)
    assert config.binding == 'owner' and config.token == TOKEN
    assert TOKEN not in repr(config)
    for url in ['http://127.0.0.1/ws', 'ws://user:password@localhost/ws',
                'ws://localhost/ws?token=secret', 'ws://localhost:wrong/ws']:
        path.write_text(json.dumps({'url': url, 'token': TOKEN}), encoding='utf8')
        with pytest.raises(CareerError, match='invalid_config'):
            ConnectionConfig.load(path)


async def test_ws_connection_reuse_new_ids_and_header_auth(caplog):
    async def reply(ws, payload):
        await ws.send_json(response(payload['requestId']))

    with caplog.at_level(logging.INFO, logger='hd2bot.career.client'):
        async with ws_server(reply) as (url, connections, requests):
            client = CareerClient(ConnectionConfig(url, TOKEN))
            try:
                first, second = await client.query(), await client.query()
                assert first.values == second.values
                assert len(connections) == 1 and len(requests) == 2
                assert requests[0]['requestId'] != requests[1]['requestId']
                assert all(set(r) == {'requestId', 'action', 'bindingId'} for r in requests)
                assert all(r['bindingId'] == 'owner' for r in requests)
            finally:
                await client.close()
    assert TOKEN not in caplog.text and 'sessionId' not in caplog.text


async def test_retry_reuses_id_after_disconnect():
    attempts = 0

    async def reply(ws, payload):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            await ws.close()
        else:
            await ws.send_json(response(payload['requestId']))

    async with ws_server(reply) as (url, connections, requests):
        client = CareerClient(ConnectionConfig(url, TOKEN), timeout=5)
        try:
            assert len((await client.query()).values) == 27
            assert len(connections) == 2
            assert requests[0]['requestId'] == requests[1]['requestId']
        finally:
            await client.close()


@pytest.mark.parametrize('mode', ['json', 'binary', 'wrong_id', 'missing_field'])
async def test_malformed_ws_replies_fail_cleanly_without_retry(mode):
    async def reply(ws, payload):
        if mode == 'json':
            await ws.send_str('{secret:bad')
        elif mode == 'binary':
            await ws.send_bytes(b'not-text')
        else:
            value = response('wrong' if mode == 'wrong_id' else payload['requestId'])
            if mode == 'missing_field':
                value['career'].pop(FIELDS[0])
            await ws.send_json(value)

    async with ws_server(reply) as (url, _, requests):
        client = CareerClient(ConnectionConfig(url, TOKEN))
        try:
            with pytest.raises(CareerError, match='schema_error'):
                await client.query()
            assert len(requests) == 1 and client._connection is None
        finally:
            await client.close()


async def test_unauthorized_ws_handshake_is_friendly():
    async with ws_server(AsyncMock(), reject=True) as (url, _, requests):
        client = CareerClient(ConnectionConfig(url, TOKEN))
        try:
            with pytest.raises(CareerError, match='authentication_failed'):
                await client.query()
            assert requests == []
        finally:
            await client.close()


async def test_timeout_discards_connection_and_next_query_recovers():
    count = 0

    async def reply(ws, payload):
        nonlocal count
        count += 1
        if count > 1:
            await ws.send_json(response(payload['requestId']))

    async with ws_server(reply) as (url, _, _):
        client = CareerClient(ConnectionConfig(url, TOKEN), timeout=0.08)
        try:
            with pytest.raises(CareerError, match='timeout'):
                await client.query()
            assert client._connection is None
            client.timeout = 2
            assert len((await client.query()).values) == 27
        finally:
            await client.close()


async def test_cancelling_waiter_does_not_close_active_request():
    entered, release = asyncio.Event(), asyncio.Event()

    async def reply(ws, payload):
        entered.set()
        await release.wait()
        await ws.send_json(response(payload['requestId']))

    async with ws_server(reply) as (url, connections, _):
        client = CareerClient(ConnectionConfig(url, TOKEN), timeout=3)
        first = asyncio.create_task(client.query())
        try:
            await asyncio.wait_for(entered.wait(), 1)
            waiter = asyncio.create_task(client.query())
            await asyncio.sleep(0)
            waiter.cancel()
            await asyncio.gather(waiter, return_exceptions=True)
            assert client._connection is not None and not connections[0].closed
            release.set()
            assert len((await first).values) == 27
        finally:
            release.set()
            await asyncio.gather(first, return_exceptions=True)
            await client.close()


async def test_cache_coalesces_without_sliding_source_time_or_masking_failure():
    clock = [100.0]
    stats = parse_reply(response(), 'test', 'owner')
    client = AsyncMock()
    client.query.return_value = stats
    service = CareerService(Settings(career_cache_ttl=60), client=client, clock=lambda: clock[0])
    results = await asyncio.gather(*(service.query() for _ in range(20)))
    assert client.query.await_count == 1
    assert all(r.queried_at == STAMP for r in results)
    assert sum(r.local_cached for r in results) == 19
    results[0].values[FIELDS[0]] = 999
    assert (await service.query()).values[FIELDS[0]] == 0
    clock[0] = 161
    client.query.side_effect = CareerError('refresh_cooldown', 60)
    with pytest.raises(CareerError):
        await service.query()
    clock[0] = 170
    with pytest.raises(CareerError):
        await service.query()
    assert client.query.await_count == 2
    clock[0] = 222
    client.query.side_effect = None
    assert (await service.query()).queried_at == STAMP
    assert client.query.await_count == 3
    await service.close()
    client.close.assert_awaited_once()


def test_formatter_all_27_fields_once_no_fabricated_ratios_or_time_units():
    assert sorted(key for _, fields in GROUPS for key, _ in fields) == sorted(FIELDS)
    result = format_career(parse_reply(response(), 'test', 'owner'))
    assert '27 项' in result and '当前配置账号' in result
    assert '服务端缓存' in result and '2026-01-01' in result
    assert '单位待核实' in result and '小时' not in result and '命中率' not in result
    assert 'totalSuccesfulExtractions' not in result  # Chinese labels, no raw payload dump.


@pytest.mark.parametrize('command', ['战绩', '查战绩', '/战绩', '<@123> /查战绩'])
async def test_router_career_commands_work_without_war_api(command):
    stats = parse_reply(response(), 'test', 'owner')
    career = AsyncMock()
    career.query.return_value = stats
    war = AsyncMock()
    result = await CommandRouter(war, career).handle(command)
    assert '27 项' in result
    career.query.assert_awaited_once()
    war.get_war.assert_not_called()
    assert parse_command(command).name == '战绩'


async def test_career_unavailable_does_not_break_other_commands(tmp_path):
    settings = Settings(provider='auto', career_config_path=tmp_path/'missing.json')
    career = CareerService(settings)
    router = CommandRouter(AsyncMock(), career)
    assert '配置不可用' in await router.handle('战绩')
    assert await router.handle('帮助') == HELP
    assert '尚未配置' in await CommandRouter(AsyncMock()).handle('战绩')
    assert '不支持' in await router.handle('战绩 76561198000000000')
    with pytest.raises(CommandError):
        parse_command('查战绩 some-other-person')


async def test_mock_career_never_reads_private_config_or_connects(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Unexpected credential or network access')
    monkeypatch.setattr(ConnectionConfig, 'load', forbidden)
    monkeypatch.setattr(aiohttp.ClientSession, 'ws_connect', forbidden)
    async with create_career_service(Settings(provider='mock')) as career:
        result = await career.query()
        assert result.mock and len(result.values) == 27
        assert '模拟数据' in format_career(result)


@pytest.mark.parametrize('operation,args', [
    ('challenge', ('qq:u',)),
    ('bind', ('qq:u', 'HD2v1:' + 'A' * 160)),
    ('unbind', ('qq:u',)),
    ('sharing', ('qq:u', True)),
    ('sharing', ('qq:u', False)),
    ('query_shared', ('HD2-ABCDEFGH2345', 'qq:u', 'private')),
])
@pytest.mark.parametrize('injected_client', [False, True])
async def test_mock_career_never_sends_real_user_operations(
    monkeypatch, operation, args, injected_client
):
    def forbidden(*args, **kwargs):
        raise AssertionError('Unexpected credential or network access')

    monkeypatch.setattr(ConnectionConfig, 'load', forbidden)
    client = AsyncMock() if injected_client else None
    service = CareerService(
        Settings(provider='mock', career_config_path='must-not-be-read.json'), client=client
    )
    try:
        with pytest.raises(CareerError, match='mock_mode'):
            await getattr(service, operation)(*args)
        if client is not None:
            client.request_user.assert_not_awaited()
    finally:
        await service.close()


@pytest.mark.parametrize('scope', ['group', 'c2c'])
async def test_career_qq_adapter_returns_complete_passive_reply(scope):
    settings = Settings(provider='mock', qq_app_id='local-test', qq_app_secret=TOKEN,
                        image_enabled=False)
    async with create_career_service(settings) as career, create_service(settings) as war:
        client = QQDispatcher(settings, CommandRouter(war, career))
        bot = AsyncMock()
        try:
            event = QQAdapter.payload_to_event(Dispatch.model_validate({
                'op': 0, 's': 1, 't': 'GROUP_AT_MESSAGE_CREATE' if scope == 'group' else 'C2C_MESSAGE_CREATE',
                'id': 'evt', 'd': {'id': 'msg', 'content': '查战绩',
                    'timestamp': '2026-09-16T00:00:00Z', 'group_openid': 'group-local',
                    'author': {'member_openid': 'member-local', 'user_openid': 'user-local'}},
            }))
            await client.process(event, bot)
            calls = (bot.post_group_messages if scope == 'group' else bot.post_c2c_messages).await_args_list
            result = ''.join(call.kwargs.get('content', '')
                             + getattr(call.kwargs.get('markdown'), 'content', '') for call in calls)
            if scope == 'group':
                assert '私聊' in result and '27 项' not in result
            else:
                assert '27 项' in result and '单位待核实' in result and '缩略' not in result
            assert all(call.kwargs['msg_id'] == 'msg' for call in calls)
            assert [call.kwargs['msg_seq'] for call in calls] == list(range(1, len(calls)+1))
            assert len(calls) <= 4
        finally:
            await client.close()


@pytest.mark.parametrize('name,value', [('HD2_CAREER_TIMEOUT', 'nan'),
    ('HD2_CAREER_TIMEOUT', '120'), ('HD2_CAREER_CACHE_TTL', '-1')])
def test_career_configuration_rejects_unbounded_timing(tmp_path, monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError):
        Settings.load(tmp_path)
