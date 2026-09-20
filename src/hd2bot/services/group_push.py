"""Explicit per-group opt-in, role checks and editable operator policy."""
import asyncio
import hashlib
import json
import time
from pathlib import Path

DEFAULT_POLICY = {
    "enabled": True,
    "allowed_groups": [],
    "manager_user_ids": [],
    "default_daily_limit": 5,
    "default_min_gap_minutes": 5,
    "default_interval_minutes": 60,
}


class GroupPushAccess:
    def __init__(self, private_access, db, policy_file: Path, app_id: str, *, clock=time.time):
        self.private = private_access
        self.db, self.policy_file, self.app_id = db, Path(policy_file), app_id
        self.clock = clock
        self._settings_lock = asyncio.Lock()

    def policy(self):
        try:
            raw = self.policy_file.read_bytes()
            if len(raw) > 65536:
                raise ValueError()
            supplied = json.loads(raw)
            if not isinstance(supplied, dict) or set(supplied)-set(DEFAULT_POLICY):
                raise ValueError()
            result = {**DEFAULT_POLICY, **supplied}
            if type(result['enabled']) is not bool:
                raise ValueError()
            for name in ('allowed_groups', 'manager_user_ids'):
                if (not isinstance(result[name], list) or len(result[name]) > 1000
                        or any(not isinstance(v, str) or not 1 <= len(v) <= 128 for v in result[name])):
                    raise ValueError()
            for name, low, high in (('default_daily_limit', 1, 50),
                                    ('default_min_gap_minutes', 1, 1440),
                                    ('default_interval_minutes', 30, 1440)):
                if type(result[name]) is not int or not low <= result[name] <= high:
                    raise ValueError()
            return result
        except (OSError, ValueError, TypeError):
            return {**DEFAULT_POLICY, 'enabled': False}

    def key(self, target):
        digest = hashlib.sha256(json.dumps([self.app_id, target]).encode()).hexdigest()
        return 'group_push:' + digest

    async def state(self, target):
        value = await self.db.get_setting(self.key(target), {})
        return value if isinstance(value, dict) else {}

    async def available(self, target):
        config = self.policy()
        return config['enabled'] and (not config['allowed_groups'] or target in config['allowed_groups'])

    async def allowed(self, scope, target_id):
        if scope != 'group':
            return await self.private.allowed(scope, target_id)
        return await self.available(target_id) and (await self.state(target_id)).get('enabled') is True

    async def can_manage(self, context):
        if context.scope != 'group' or not context.user_id:
            return False
        return context.group_role in {'owner', 'admin'} or context.user_id in self.policy()['manager_user_ids']

    async def limits(self, scope, target):
        if scope != 'group':
            return 5, 300, 60
        config, state = self.policy(), await self.state(target)
        values = []
        for name, fallback, low, high in (
            ('daily_limit', config['default_daily_limit'], 1, 50),
            ('min_gap_minutes', config['default_min_gap_minutes'], 1, 1440),
            ('interval_minutes', config['default_interval_minutes'], 30, 1440),
        ):
            value = state.get(name, fallback)
            values.append(value if type(value) is int and low <= value <= high else fallback)
        return values[0], values[1]*60, values[2]

    async def configure(self, command, argument, context):
        async with self._settings_lock:
            return await self._configure(command, argument, context)

    async def _configure(self, command, argument, context):
        if context.scope != 'group':
            return '请在目标QQ群内 @机器人 使用群推送设置。'
        target = context.target_id
        if command == '推送设置' and not argument:
            daily, gap, interval = await self.limits('group', target)
            on = await self.allowed('group', target)
            return (f'本群推送：{"已开启" if on else "已暂停或尚未开启"}\n'
                    f'每日上限：{daily} 条；最短间隔：{gap//60} 分钟；默认战况间隔：{interval} 分钟。\n'
                    '群主/管理员：开启推送 → 订阅 主线 / 防守 / 新闻，或 订阅 战况 60。\n'
                    '推送设置 每日上限 10 · 推送设置 最短间隔 5 · 推送设置 默认间隔 60\n'
                    '订阅列表 · 暂停推送 · 恢复推送 · 取消订阅。')
        if not await self.can_manage(context):
            return '群推送由群主、管理员或维护者配置的管理用户操作；当前消息未提供可用的管理权限。'
        if command not in {'暂停推送'} and not await self.available(target):
            return '维护者尚未允许本群主动推送，请检查 data/group_push.json 的开关与允许群列表。'
        state = await self.state(target)
        if command in {'开启推送', '恢复推送'}:
            # Require new opt-in; resumption establishes a fresh baseline without a historical burst.
            state['enabled'] = True
            await self.db.execute(
                "UPDATE subscriptions SET baseline_json=NULL, next_due=CASE WHEN topic='war' "
                "THEN ? + interval_minutes*60 ELSE 0 END, failures=0, retry_at=0 "
                "WHERE target_type='group' AND target_id=?", (self.clock(), target))
            await self.db.set_setting(self.key(target), state)
            return '本群推送已开启。请按需发送 订阅 主线、订阅 防守、订阅 新闻 或 订阅 战况 60；未订阅的内容不会推送。'
        if command == '暂停推送':
            state['enabled'] = False
            await self.db.set_setting(self.key(target), state)
            return '已暂停本群所有主动推送，保留订阅设置；发送 恢复推送 可重新启用。'
        if command == '推送设置':
            label, _, text = argument.partition(' ')
            mapping = {'每日上限': ('daily_limit', 1, 50),
                       '最短间隔': ('min_gap_minutes', 1, 1440),
                       '默认间隔': ('interval_minutes', 30, 1440)}
            if (label not in mapping or not text.isascii() or not text.isdecimal()
                    or not 1 <= len(text) <= 4):
                return '用法：推送设置 每日上限 5 / 最短间隔 5 / 默认间隔 60。'
            name, low, high = mapping[label]
            value = int(text)
            if not low <= value <= high:
                return f'{label}须为 {low}～{high}。'
            state[name] = value
            await self.db.set_setting(self.key(target), state)
            return f'已设置本群{label}为 {value}。默认间隔只影响后续未指定分钟数的战况订阅。'
        return '未知推送设置命令。'

    async def bind(self, code, scope, target):
        return await self.private.bind(code, scope, target)
