"""Opt-in, persistent and quota-bounded notifications; no QQ-specific objects."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time
from dataclasses import dataclass
from datetime import UTC

from hd2bot.commands.search import search_planets
from hd2bot.hd2.errors import HD2UnavailableError
from hd2bot.hd2.models import Faction, Planet
from hd2bot.services.localization import clean_text, faction_name, metadata_for
from hd2bot.services.subscriptions import SubscriptionService, _fingerprint, _scope
from hd2bot.storage.database import Database, canonical_json

logger = logging.getLogger(__name__)
TOPICS = {"主线": "major_order", "防守": "defense", "星球": "planet", "战况": "war",
          "新闻": "news", "公告": "announcement", "DSS": "dss", "战役": "campaign",
          "区域": "region", "补丁": "patch"}
LABELS = {value: key for key, value in TOPICS.items()}
_CURRENT_SUBSCRIPTION = (
    "id = ? AND enabled = ? AND interval_minutes = ? AND baseline_json IS ? "
    "AND next_due = ? AND retry_at = ? AND failures = ?"
)


def _subscription_state(sub) -> tuple:
    return tuple(sub[key] for key in (
        "id", "enabled", "interval_minutes", "baseline_json", "next_due", "retry_at", "failures",
    ))


class PermanentPushError(Exception):
    """Sender reported unavailable permission/quota; an explicit resubscribe resumes."""

    def __init__(self, code: str):
        self.code = code if re.fullmatch(r"[A-Za-z0-9_-]{1,40}", str(code)) else "rejected"
        super().__init__(self.code)


class RetryablePushError(RuntimeError):
    """Sender proved that no message was accepted, so a later retry is safe."""

    def __init__(self, code: str):
        self.code = code if re.fullmatch(r"[A-Za-z0-9_-]{1,40}", str(code)) else "temporary"
        super().__init__(self.code)


class UncertainPushError(RuntimeError):
    """Sender failed without a response, so external acceptance is unknown."""

    def __init__(self, code: str = "outcome_unknown"):
        self.code = code if re.fullmatch(r"[A-Za-z0-9_-]{1,40}", str(code)) else "outcome_unknown"
        super().__init__(self.code)


@dataclass(frozen=True)
class _Snapshot:
    state: dict
    text: str


def _number(value) -> str:
    return "暂无数据" if value is None else f"{value:,}"


def _active(planet: Planet, now: float) -> bool:
    event = planet.event
    if event is None or event.event_type != 1 or planet.disabled:
        return False
    def timestamp(value):
        if value is None:
            return None
        return value.replace(tzinfo=value.tzinfo or UTC).timestamp()
    starts = timestamp(event.starts_at)
    ends = timestamp(event.ends_at)
    return (starts is None or starts <= now) and (ends is None or ends > now)


def _ascii_decimal(value: str, *, maximum_digits: int = 6) -> bool:
    return (isinstance(value, str) and value.isascii() and value.isdecimal()
            and 1 <= len(value) <= maximum_digits)


class NotificationManager:
    """Each target gets at most 5 successful pushes/rolling 24h, at least 5min apart.

    Change topics establish a baseline first. Timed war summaries wait one full
    interval. Transient errors back off; permanent platform rejection pauses the
    subscription. All state survives restart and no target IDs are logged.
    """

    MAX_PER_DAY = 5
    MIN_GAP = 300

    def __init__(self, db: Database, hd2service, *, steam=None, poll_interval=60,
                 clock=time.time, access=None, release_notices=None):
        if not math.isfinite(poll_interval) or poll_interval <= 0:
            raise ValueError("poll_interval must be positive")
        self.db, self.service = db, hd2service
        self.steam = steam
        self.subscriptions = SubscriptionService(db)
        self.access = access
        self.release_notices = release_notices
        self.poll_interval, self.clock = poll_interval, clock
        self._tick_lock = asyncio.Lock()

    async def handle_command(self, name: str, argument: str, scope: str, target_id: str) -> str:
        _scope(scope, target_id)
        where = "当前群" if scope == "group" else "当前私聊"
        name = {"subscribe": "订阅", "unsubscribe": "取消订阅",
                "subscriptions": "订阅列表"}.get(name, name)
        argument = argument.strip()
        group_settings = scope == "group" and self.access is not None and callable(getattr(self.access, "can_manage", None))
        public_status = group_settings and name == "订阅列表"
        if name != "取消订阅" and not public_status and not await self._access_allowed(scope, target_id):
            return ("本群推送尚未开启，请群主/管理员先发送 开启推送，再按需订阅。" if group_settings
                    else "主动推送目前仅向指定测试账号的私聊开放。")
        daily, gap, default_interval = await self._limits(scope, target_id)
        quota_text = f"每处每天最多 {daily} 条，相邻至少 {gap // 60} 分钟"
        if name == "订阅列表":
            rows = await self.subscriptions.list_subscriptions(scope, target_id)
            if not rows:
                return (f"{where}暂无订阅。发送：订阅 主线 / 防守 / 新闻 / 公告 / DSS / 战役 / 区域 / 补丁；"
                        "星球示例：订阅 星球 Meridia；定时示例：订阅 战况 60。")
            lines = [f"【{where}的订阅】"]
            for row in rows:
                label = LABELS[row.topic]
                if row.planet_index is not None:
                    label += f" #{row.planet_index}"
                if row.topic == "war":
                    label += f" · 每 {row.interval_minutes} 分钟"
                status = "启用" if row.enabled else f"已暂停（{row.pause_reason}）"
                lines.append(f"• {label} · {status}")
            lines.append(quota_text + "；再次订阅可恢复暂停项。")
            return "\n".join(lines)
        if name == "取消订阅" and not argument:
            count = await self.subscriptions.unsubscribe(scope, target_id)
            return f"已取消{where}的全部订阅（{count} 项）。"
        if name not in {"订阅", "取消订阅"}:
            return "未知订阅指令。"
        label, _, value = argument.partition(" ")
        if label.casefold() == "dss":
            label = "DSS"
        topic = TOPICS.get(label)
        if topic is None:
            return ("用法：订阅 主线 / 防守 / 新闻 / 公告 / DSS / 战役 / 区域 / 补丁；"
                    "订阅 星球 Meridia / 订阅 战况 60；退订请发送 取消订阅 <主题>。")
        if topic == "patch" and name == "订阅" and self.steam is None:
            return "补丁推送服务尚未配置，请稍后重试。"
        interval = default_interval
        planet_index = None
        if topic == "planet":
            if not value.strip():
                return "请指定星球，例如：订阅 星球 Meridia。"
            # Numeric cancellation works even while the upstream is unavailable.
            numeric = value.strip().removeprefix("#")
            if name == "取消订阅" and _ascii_decimal(numeric, maximum_digits=9):
                planet_index = int(numeric)
            else:
                try:
                    result = await self.service.get_planets()
                    if result.stale:
                        return "星球数据暂未更新，请稍后重试订阅。"
                    found = search_planets(value.strip(), result.value)
                except Exception:
                    return "星球数据暂时无法取得，请稍后重试。"
                if not found.match:
                    if found.candidates:
                        return "请指定唯一星球：" + "、".join(
                            f"{clean_text(p.name)} #{p.index}" for p in found.candidates[:5]
                        )
                    return "未找到该星球，请使用中文名、英文名或编号。"
                planet_index = found.match.index
                label += f" {clean_text(found.match.name)}"
        elif value:
            if topic != "war" or not _ascii_decimal(value.strip(), maximum_digits=4):
                return "仅战况支持分钟间隔，例如：订阅 战况 60。"
            interval = int(value.strip())
            if not 30 <= interval <= 1440:
                return "战况推送间隔须为 30 至 1440 分钟。"
        if name == "取消订阅":
            count = await self.subscriptions.unsubscribe(scope, target_id, topic, planet_index)
            return f"已取消{where}的{label}订阅（{count} 项）。"
        sub = await self.subscriptions.subscribe(scope, target_id, topic, planet_index, interval)
        now = self.clock()
        await self.db.execute(
            "UPDATE subscriptions SET baseline_json = NULL, next_due = ?, retry_at = 0, "
            "failures = 0 WHERE id = ?", (now + interval * 60 if topic == "war" else 0, sub.id),
        )
        if topic != "war":
            try:
                raw = await self.db.fetch_one("SELECT * FROM subscriptions WHERE id = ?", (sub.id,))
                snapshot = await self._snapshot(raw, now, {}) if raw else None
                if snapshot:
                    await self._set_baseline(raw, snapshot.state)
            except Exception:
                # The opt-in remains valid; the first fresh poll establishes the baseline.
                pass
        mode = f"每 {interval} 分钟摘要，首次将在间隔到期后推送" if topic == "war" else (
            "建立基线后仅在有变化时推送，不会立即补发历史消息"
        )
        return (f"已订阅{label}，推送到{where}。\n{mode}。\n"
                f"{quota_text}；QQ 平台权限/额度不足时将自动暂停。\n"
                "发送 订阅列表 查看，发送 取消订阅 关闭全部。")

    async def _access_allowed(self, scope: str, target_id: str) -> bool:
        if self.access is None:
            return True
        return await self.access.allowed(scope, target_id)

    async def _limits(self, scope, target_id):
        if self.access is not None and callable(getattr(self.access, "limits", None)):
            return await self.access.limits(scope, target_id)
        return self.MAX_PER_DAY, self.MIN_GAP, 60

    async def _update_if_current(self, sub, assignments: str, parameters: tuple) -> None:
        # Data retrieval and QQ sends can finish after a command has changed the
        # subscription. Only the state that started this work may be updated.
        await self.db.execute(
            f"UPDATE subscriptions SET {assignments} WHERE {_CURRENT_SUBSCRIPTION}",
            (*parameters, *_subscription_state(sub)),
        )

    async def _set_baseline(self, sub, state: dict) -> None:
        await self._update_if_current(sub, "baseline_json = ?", (canonical_json(state),))

    async def _snapshot(self, sub, now: float, cache: dict) -> _Snapshot | None:
        async def fetch(method):
            if method not in cache:
                source = self.steam if method == "get_patch_notes" else self.service
                if source is None:
                    raise HD2UnavailableError("Steam notifications are not configured")
                cache[method] = await getattr(source, method)()
            return cache[method]

        topic = sub["topic"]
        method = {"war": "get_war", "major_order": "get_major_order",
                  "defense": "get_planets", "planet": "get_planets", "news": "get_dispatches",
                  "announcement": "get_global_events", "dss": "get_space_stations",
                  "campaign": "get_campaigns", "region": "get_planet_regions",
                  "patch": "get_patch_notes"}[topic]
        result = await fetch(method)
        if result.stale:
            return None
        state: dict = {}
        if topic == "war":
            war = result.value
            status = war.status
            if war.ended_at and war.ended_at.replace(tzinfo=war.ended_at.tzinfo or UTC).timestamp() <= now:
                status = "已结束"
            elif war.started_at and war.started_at.replace(tzinfo=war.started_at.tzinfo or UTC).timestamp() > now:
                status = "尚未开始"
            state = {"war_id": war.war_id, "players": war.players, "status": status}
            text = (f"【定期战况】\n战争状态：{clean_text(status)}\n"
                    f"全服在线：{_number(war.players)}\n发送 战况 查看详细战区。")
        elif topic == "major_order":
            summaries = []
            for order in sorted(result.value, key=lambda order: order.id):
                completed = [None if task.progress is None or task.target is None
                             or task.target <= 0 else task.progress >= task.target
                             for task in order.tasks]
                state[str(order.id)] = completed
                known = sum(value is True for value in completed)
                unknown = sum(value is None for value in completed)
                summary = f"{known}/{len(completed)} 项已达目标" if completed else "任务详情暂缺"
                if unknown:
                    summary += f"（其中 {unknown} 项进度未知）"
                summaries.append(f"{clean_text(order.title, f'主要指令 #{order.id}')}："
                                 f"{summary}")
            text = "【主线变化】\n" + ("\n".join(summaries[:4]) or "暂无当前主线。")
            if len(summaries) > 4:
                text += f"\n另有 {len(summaries) - 4} 项。"
            text += "\n发送 主线 查看完整目标。"
        elif topic == "defense":
            if any(p.event and p.event.event_type is None and not p.disabled for p in result.value):
                return None
            active = [p for p in result.value if _active(p, now)]
            if any(p.event.id is None for p in active):
                return None  # Unknown event IDs must not look like an ended defense.
            state = {str(p.index): p.event.id for p in sorted(active, key=lambda p: p.index)}
            names = "、".join(clean_text(p.name) for p in active[:8]) or "暂无进行中的防守"
            text = f"【防守变化】\n当前 {len(active)} 处：{names}。\n发送 防守 查看详情。"
        elif topic == "region":
            return self._region_snapshot(sub, result)
        elif topic == "patch":
            return self._patch_snapshot(sub, result)
        elif topic in {"news", "announcement", "dss", "campaign"}:
            return await self._intelligence_snapshot(sub, now, fetch, result)
        else:
            planet = next((p for p in result.value if p.index == sub["planet_index"]), None)
            if planet is None:
                return None
            if planet.owner != Faction.UNKNOWN:
                state["owner"] = planet.owner.value
            if planet.event is None:
                state["event"] = None
            elif planet.event.event_type is not None and not _active(planet, now):
                state["event"] = None
            elif _active(planet, now) and planet.event.id is not None:
                state["event"] = planet.event.id
            progress = planet.liberation
            if progress is not None and math.isfinite(progress):
                state["bucket"] = math.floor(min(100, max(0, progress)) / 10)
            progress_label = "暂无数据" if progress is None else f"{progress:.1f}%"
            text = (f"【星球变化】{clean_text(planet.name)}\n归属：{faction_name(planet.owner)}\n"
                    f"解放进度：{progress_label}\n防守：{'进行中' if _active(planet, now) else '无'}\n"
                    f"发送 星球 {planet.index} 查看详情。")
        return self._dated_snapshot(state, text, result)

    @staticmethod
    def _dated_snapshot(state, text, *results):
        stamp = min(r.fetched_at for r in results).astimezone().strftime("%m-%d %H:%M")
        if any(r.source == "mock" for r in results):
            text += "\n模拟数据 · 非真实战况"
        return _Snapshot(state, text + f"\n数据时间：{stamp}")

    def _region_snapshot(self, sub, result):
        previous = json.loads(sub["baseline_json"]) if sub["baseline_json"] else {}
        state = {}
        for region in result.value:
            if any(type(value) is not int or value < 0
                   for value in (region.planet_index, region.index)):
                return None
            key = f"{region.planet_index}:{region.index}"
            fields = {}
            if region.faction != Faction.UNKNOWN:
                fields["owner"] = region.faction.value
            if type(region.available) is bool:
                fields["available"] = region.available
            state[key] = fields

        def name(key):
            planet, region = map(int, key.split(":"))
            label = clean_text(metadata_for(planet).get("name"), f"星球 #{planet}")
            return f"{label} · 区域 #{region}"

        lines = ["【区域状态变化】"]
        lines.extend(f"新增区域：{name(key)}" for key in sorted(set(state) - set(previous))[:6])
        lines.extend(f"区域记录已结束或撤下：{name(key)}"
                     for key in sorted(set(previous) - set(state))[:6])
        for key in sorted(set(state) & set(previous)):
            old, current = previous[key], state[key]
            if "owner" in old and "owner" in current and old["owner"] != current["owner"]:
                lines.append(f"{name(key)}：{faction_name(Faction(old['owner']))}"
                             f" → {faction_name(Faction(current['owner']))}")
            if ("available" in old and "available" in current
                    and old["available"] != current["available"]):
                label = "可进入" if current["available"] else "当前不可进入"
                lines.append(f"{name(key)}：{label}")
        lines.append("发送 区域 查看当前区域；记录消失不代表战斗胜负。")
        return self._dated_snapshot(state, "\n".join(lines), result)

    def _patch_snapshot(self, sub, result):
        previous = json.loads(sub["baseline_json"]) if sub["baseline_json"] else None
        items = tuple(item for item in result.value if item.patch)
        # Steam GIDs are identifiers, not chronological sequence numbers.
        published = {item.gid: item.published_at.replace(
            tzinfo=item.published_at.tzinfo or UTC,
        ).timestamp() for item in items}
        cutoff = (previous["latest_published"] if previous is not None
                  else result.fetched_at.timestamp())
        state = {"ids": published, "latest_published": max([cutoff, *published.values()])}
        old_ids = previous.get("ids", {}) if previous else published
        since = previous.get("latest_published", float("inf")) if previous else float("inf")
        new = sorted((item for item in items if item.gid not in old_ids
                      and published[item.gid] >= since), key=lambda item: item.published_at)
        lines = ["【Steam 官方补丁更新】"]
        for item in new[:3]:
            lines.extend([clean_text(item.title), f"详情：更新 {item.gid}", f"官方原文：{item.url}"])
        if len(new) > 3:
            lines.append(f"另有 {len(new) - 3} 条新补丁。")
        lines.append("发送 补丁 查看最近的官方补丁与热修复。")
        return self._dated_snapshot(state, "\n".join(lines), result)

    async def _intelligence_snapshot(self, sub, now, fetch, result):
        """Compare stable identities; prose edits and steadily changing counters stay quiet."""
        topic = sub["topic"]
        previous = json.loads(sub["baseline_json"]) if sub["baseline_json"] else {}
        items = result.value
        if any(type(item.id) is not int or item.id < 0 for item in items):
            return None
        if topic == "news":
            latest = max((item.id for item in items), default=-1)
            state = {"latest_id": latest}
            new = sorted((item for item in items if item.id > previous.get("latest_id", latest)),
                         key=lambda item: item.id)
            lines = ["【银河新闻更新】"]
            for item in new[:3]:
                lines.append(f"战报 #{item.id}：{clean_text(item.message)}")
            if len(new) > 3:
                lines.append(f"另有 {len(new) - 3} 条新战报。")
            lines.append("发送 新闻 查看最近战报。")
        elif topic == "announcement":
            state = {}
            for event in items:
                if event.expires_at is not None and event.expires_at.replace(
                    tzinfo=event.expires_at.tzinfo or UTC,
                ).timestamp() <= now:
                    continue
                state[str(event.id)] = clean_text(event.title or event.message,
                                                  f"银河公告 #{event.id}")
            added, removed = set(state) - set(previous), set(previous) - set(state)
            lines = ["【银河公告变化】"]
            lines.extend(f"新公告：{state[key]}" for key in sorted(added, key=int)[:4])
            lines.extend(f"公告已结束或撤下：{previous[key]}"
                         for key in sorted(removed, key=int)[:4])
            lines.append("发送 公告 查看当前公告。")
        elif topic == "dss":
            from hd2bot.galactic_features import tactical_status

            state = {}
            lines = ["【DSS 状态变化】"]
            for station in sorted(items, key=lambda item: item.id):
                key = str(station.id)
                old = previous.get(key, {})
                status = {}
                if type(station.planet_index) is int and station.planet_index >= 0:
                    status["planet"] = station.planet_index
                    if "planet" in old and old["planet"] != station.planet_index:
                        lines.append(f"空间站 #{station.id}：星球 #{old['planet']}"
                                     f" → 星球 #{station.planet_index}")
                actions = {}
                for action in station.tactical_actions:
                    if type(action.id) is not int or action.id < 0:
                        return None
                    if type(action.status) is int and action.status in {0, 1, 2, 3}:
                        action_key = str(action.id)
                        actions[action_key] = action.status
                        before = old.get("actions", {}).get(action_key)
                        if before is not None and before != action.status:
                            name = clean_text(action.name, f"战术行动 #{action.id}")
                            lines.append(f"{name}：{tactical_status(before)}"
                                         f" → {tactical_status(action.status)}")
                status["actions"] = actions
                election = getattr(station, "election", None)
                if election is not None:
                    election_id = clean_text(election.id)
                    if election_id:
                        status["election_id"] = election_id
                    votes = {}
                    vote_names = {}
                    for option in election.options:
                        if (type(option.meta_id) is not int or option.meta_id < 0
                                or type(option.count) is not int or option.count < 0):
                            return None
                        vote_key = str(option.meta_id)
                        votes[vote_key] = option.count
                        vote_names[vote_key] = clean_text(
                            option.text,
                            clean_text(metadata_for(option.meta_id).get("name"),
                                       f"星球 #{option.meta_id}"),
                        )
                    if votes:
                        status["votes"] = votes
                        status["vote_names"] = vote_names
                        total_votes = sum(votes.values())
                        status["vote_total"] = total_votes
                        status["vote_leader"] = max(votes, key=lambda item: (votes[item], -int(item)))
                        status["vote_buckets"] = {
                            vote_key: (votes[vote_key] * 20 // total_votes if total_votes else 0)
                            for vote_key in votes
                        }
                        old_votes = old.get("votes", {})
                        for vote_key in sorted(set(votes) & set(old_votes), key=int):
                            if votes[vote_key] != old_votes[vote_key]:
                                label = vote_names.get(vote_key, f"星球 #{vote_key}")
                                lines.append(f"迁移投票 {label}：{old_votes[vote_key]:,}"
                                             f" 票 → {votes[vote_key]:,} 票")
                        if (old.get("election_id") is not None
                                and status.get("election_id") != old["election_id"]):
                            lines.append("DSS 迁移投票已切换至新一轮。")
                        elif old_votes and set(votes) != set(old_votes):
                            lines.append("DSS 迁移投票候选已更新。")
                        elif (old.get("vote_leader") is not None
                              and status["vote_leader"] != old["vote_leader"]):
                            leader = vote_names.get(status["vote_leader"],
                                                    f"星球 #{status['vote_leader']}")
                            lines.append(f"DSS 迁移投票领先目标变为：{leader}。")
                state[key] = status
            lines.append("发送 DSS票数 查看当前票数；发送 DSS 查看空间站状态。")
        else:
            planets = await fetch("get_planets")
            if planets.stale:
                return None
            ids = {str(item.id): item.planet.index for item in items}
            owners = {str(p.index): p.owner.value for p in planets.value
                      if p.owner != Faction.UNKNOWN}
            names = {str(p.index): clean_text(p.name) for p in planets.value}
            state = {"ids": ids, "owners": owners, "names": names}
            old_ids = previous.get("ids", {})
            old_names = previous.get("names", {})
            lines = ["【战役与控制方变化】"]
            for identity in sorted(set(ids) - set(old_ids), key=int)[:5]:
                index = str(ids[identity])
                lines.append(f"新增战役 #{identity}：{names.get(index, '星球 #' + index)}")
            for identity in sorted(set(old_ids) - set(ids), key=int)[:5]:
                index = str(old_ids[identity])
                name = names.get(index, old_names.get(index, "星球 #" + index))
                lines.append(f"战役 #{identity} 已结束或撤下：{name}（不据此判定胜负）")
            for index, owner in owners.items():
                old_owner = previous.get("owners", {}).get(index)
                if old_owner is not None and old_owner != owner:
                    name = names.get(index, "星球 #" + index)
                    lines.append(f"{name}：{faction_name(Faction(old_owner))}"
                                 f" → {faction_name(Faction(owner))}")
            lines.append("发送 进攻 / 防守 / 星图 查看当前态势。")
            return self._dated_snapshot(state, "\n".join(lines), result, planets)
        return self._dated_snapshot(state, "\n".join(lines), result)

    @staticmethod
    def _compare(topic: str, baseline: dict, current: dict) -> tuple[bool, dict]:
        if topic == "region":
            changed = set(baseline) != set(current)
            merged = {}
            for key, fields in current.items():
                old = baseline.get(key, {})
                changed = changed or any(field in old and old[field] != value
                                         for field, value in fields.items())
                merged[key] = {**old, **fields}
            return changed, merged
        if topic == "patch":
            seen, current_ids = baseline.get("ids", {}), current["ids"]
            since = baseline["latest_published"]
            changed = any(gid not in seen and published >= since
                          for gid, published in current_ids.items())
            return changed, {"ids": {**seen, **current_ids},
                             "latest_published": max(since, current["latest_published"])}
        if topic == "news":
            old, new = baseline.get("latest_id", -1), current.get("latest_id", -1)
            return new > old, {"latest_id": max(old, new)}
        if topic == "announcement":
            return set(baseline) != set(current), current
        if topic == "dss":
            changed = False
            merged = dict(baseline)
            for key, status in current.items():
                old = baseline.get(key, {})
                if "planet" in old and "planet" in status and old["planet"] != status["planet"]:
                    changed = True
                actions = status.get("actions", {})
                old_actions = old.get("actions", {})
                if any(key in old_actions and value != old_actions[key]
                       for key, value in actions.items()):
                    changed = True
                if ("election_id" in old and "election_id" in status
                        and old["election_id"] != status["election_id"]):
                    changed = True
                votes = status.get("votes", {})
                old_votes = old.get("votes", {})
                if (set(votes) != set(old_votes)
                        or (old.get("vote_leader") is not None
                            and status.get("vote_leader") != old["vote_leader"])
                        or any(vote_key in old.get("vote_buckets", {})
                               and value != old["vote_buckets"][vote_key]
                               for vote_key, value in status.get("vote_buckets", {}).items())):
                    changed = True
                merged[key] = {
                    **old, **status,
                    "actions": {**old_actions, **actions},
                    **({"votes": {**old_votes, **votes}} if votes else {}),
                    **({"vote_names": {**old.get("vote_names", {}),
                                        **status.get("vote_names", {})}}
                       if status.get("vote_names") else {}),
                }
            return changed, merged
        if topic == "campaign":
            old_owners, owners = baseline.get("owners", {}), current["owners"]
            changed = (baseline.get("ids", {}) != current["ids"] or
                       any(key in old_owners and old_owners[key] != value
                           for key, value in owners.items()))
            return changed, {**current, "owners": {**old_owners, **owners},
                             "names": {**baseline.get("names", {}), **current["names"]}}
        if topic == "planet":
            changed = any(key in baseline and baseline[key] != value
                          for key, value in current.items())
            return changed, {**baseline, **current}
        if topic == "major_order":
            changed = set(baseline) != set(current)
            merged = {}
            for key, values in current.items():
                old = baseline.get(key, [])
                if key in baseline and len(values) != len(old):
                    changed = True
                merged[key] = []
                for index, value in enumerate(values):
                    previous = old[index] if index < len(old) else None
                    if previous is not None and value is not None and value != previous:
                        changed = True
                    merged[key].append(previous if value is None else value)
            return changed, merged
        return baseline != current, current

    async def _target_allowed(self, sub, now: float) -> bool:
        row = await self.db.fetch_one(
            "SELECT COUNT(*) AS count, MAX(sent_at) AS latest FROM notification_deliveries "
            "WHERE target_type = ? AND target_id = ? AND sent_at > ?",
            (sub["target_type"], sub["target_id"], now - 86400),
        )
        daily, gap, _ = await self._limits(sub["target_type"], sub["target_id"])
        return row["count"] < daily and (
            row["latest"] is None or now - row["latest"] >= gap
        )

    async def tick(self, sender) -> None:
        """Inspect fresh states once; sender(scope, target_id, text) confirms success."""
        async with self._tick_lock:
            if self.release_notices is not None:
                await self.release_notices.tick(sender)
            rows = await self.db.fetch_all("SELECT * FROM subscriptions WHERE enabled = 1 ORDER BY id")
            if not rows:
                return
            now = self.clock()
            cache = {}
            await self.db.execute("DELETE FROM notification_deliveries WHERE sent_at <= ?",
                                  (now - 86400,))
            for sub in rows:
                if not await self._access_allowed(sub["target_type"], sub["target_id"]):
                    continue
                if sub["retry_at"] > now or sub["next_due"] > now:
                    continue
                if sub["topic"] == "war" and sub["next_due"] == 0:
                    await self._update_if_current(
                        sub, "next_due = ?", (now + sub["interval_minutes"] * 60,),
                    )
                    continue
                try:
                    snapshot = await self._snapshot(sub, now, cache)
                    if snapshot is None:
                        continue
                    state = snapshot.state
                    if sub["topic"] != "war":
                        if sub["baseline_json"] is None:
                            await self._set_baseline(sub, state)
                            continue
                        baseline = json.loads(sub["baseline_json"])
                        changed, state = self._compare(sub["topic"], baseline, state)
                        if not changed:
                            if baseline != state:
                                await self._set_baseline(sub, state)
                            continue
                    if not await self._target_allowed(sub, now):
                        continue
                    # Commands can cancel/pause while data retrieval is in flight.
                    current = await self.db.fetch_one(
                        "SELECT * FROM subscriptions WHERE id = ?",
                        (sub["id"],),
                    )
                    if current is None or _subscription_state(current) != _subscription_state(sub):
                        continue
                    if not await self._access_allowed(sub["target_type"], sub["target_id"]):
                        continue
                    await sender(sub["target_type"], sub["target_id"], snapshot.text)
                    await self._mark_success(sub, state, self.clock())
                except PermanentPushError as exc:
                    await self._update_if_current(
                        sub, "enabled = 0, pause_reason = ?",
                        (f"QQ 权限/额度限制：{exc.code}",),
                    )
                    logger.warning("event=proactive_push_paused reason=platform_rejected")
                except Exception as exc:
                    delay = min(3600, 60 * (2 ** min(sub["failures"], 6)))
                    await self._update_if_current(
                        sub, "failures = failures + 1, retry_at = ?", (now + delay,),
                    )
                    logger.warning("event=proactive_push_retry reason=%s", type(exc).__name__)

    async def _mark_success(self, sub, state: dict, now: float) -> None:
        scope = f"{sub['target_type']}:{sub['target_id']}"
        event = f"subscription:{sub['id']}"
        _, gap, _ = await self._limits(sub["target_type"], sub["target_id"])
        due = now + (sub["interval_minutes"] * 60 if sub["topic"] == "war" else gap)
        async with self.db.transaction() as connection:
            await connection.execute(
                "INSERT INTO notification_state(scope, event_key, fingerprint) VALUES (?, ?, ?) "
                "ON CONFLICT(scope, event_key) DO UPDATE SET fingerprint = excluded.fingerprint, "
                "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')", (scope, event, _fingerprint(state)),
            )
            await connection.execute(
                "UPDATE subscriptions SET baseline_json = ?, next_due = ?, failures = 0, "
                f"retry_at = 0 WHERE {_CURRENT_SUBSCRIPTION}",
                (canonical_json(state), due, *_subscription_state(sub)),
            )
            await connection.execute(
                "INSERT INTO notification_deliveries(target_type, target_id, sent_at) VALUES (?, ?, ?)",
                (sub["target_type"], sub["target_id"], now),
            )

    async def run(self, sender) -> None:
        while True:
            try:
                await self.tick(sender)
            except Exception as exc:
                logger.warning("event=notification_poll_failed reason=%s", type(exc).__name__)
            await asyncio.sleep(self.poll_interval)
