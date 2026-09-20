"""QQ native C2C menu and four-scope command panels, using the official REST API."""

from __future__ import annotations

import copy
import json
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

import aiohttp

from hd2bot.config import Settings

SCOPES = ("c2c", "group", "channel", "dm")
API_BASE = "https://api.bot.qq.com"
TOKEN_URL = API_BASE + "/app/getAppAccessToken"


class NativeUIError(RuntimeError):
    """A configuration or remote error containing no request credentials."""


def _length(value: str) -> int:
    return sum(1 if ord(character) < 128 else 2 for character in value)


def _text(value: Any, limit: int, field: str, *, empty: bool = False) -> str:
    if (not isinstance(value, str) or (not empty and not value.strip())
            or _length(value) > limit or any(ord(c) < 32 for c in value)):
        raise NativeUIError(f"{field} 格式不正确或超出 {limit} 字符（中文计 2）")
    return value


def _https(value: Any, field: str) -> None:
    if not isinstance(value, str):
        raise NativeUIError(f"{field} 必须是 HTTPS 链接")
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise NativeUIError(f"{field} 必须是 HTTPS 链接")


def _menu_item(item: Any, *, child: bool = False) -> None:
    if not isinstance(item, dict):
        raise NativeUIError("菜单项必须是对象")
    _text(item.get("name"), 14 if child else 10, "菜单名称")
    kind = item.get("type")
    allowed = {"send_message", "link"} if child else {"switch", "send_message", "link", "menu"}
    if kind not in allowed:
        raise NativeUIError("菜单项类型不正确")
    fields = {"name", "type"}
    if kind == "send_message":
        fields.add("send_message")
        _text(item.get("send_message"), 1000, "菜单发送内容")
    elif kind == "link":
        fields.add("link")
        _https(item.get("link"), "菜单链接")
    elif kind == "menu":
        fields.add("sub_menu_items")
        children = item.get("sub_menu_items")
        if not isinstance(children, list) or not 1 <= len(children) <= 5:
            raise NativeUIError("子菜单需要 1 到 5 项")
        for sub_item in children:
            _menu_item(sub_item, child=True)
    else:
        fields.add("switch")
        switch = item.get("switch")
        if not isinstance(switch, dict) or set(switch) != {"switch_id", "default"}:
            raise NativeUIError("switch 需要 switch_id 和 default")
        _text(switch["switch_id"], 100, "switch_id")
        if not isinstance(switch["default"], bool):
            raise NativeUIError("switch.default 必须是布尔值")
    if set(item) - fields:
        raise NativeUIError("菜单项含有该类型不支持的字段")


def validate_config(config: Any) -> dict[str, Any]:
    """Validate the local schema before obtaining a token or changing remote state."""
    if not isinstance(config, dict) or config.get("schema") != 1:
        raise NativeUIError("原生界面配置 schema 必须为 1")
    if set(config) - {"schema", "owner", "menu_mode", "menu", "panels"}:
        raise NativeUIError("原生界面配置存在未知字段")
    owner = config.get("owner", "hd2bot")
    if not isinstance(owner, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", owner):
        raise NativeUIError("owner 只能使用 1 到 40 个英文、数字、下划线或短横线")
    if config.get("menu_mode", "merge") not in {"merge", "replace"}:
        raise NativeUIError("menu_mode 必须为 merge 或 replace")
    menu = config.get("menu")
    if menu is not None:
        if not isinstance(menu, dict) or set(menu) != {"items"}:
            raise NativeUIError("menu 必须只包含 items")
        if not isinstance(menu["items"], list) or len(menu["items"]) > 10:
            raise NativeUIError("菜单最多 10 项")
        for item in menu["items"]:
            _menu_item(item)
        names = [item["name"] for item in menu["items"]]
        if len(names) != len(set(names)):
            raise NativeUIError("菜单名称不能重复")
    panels = config.get("panels", {})
    if not isinstance(panels, dict) or set(panels) - set(SCOPES):
        raise NativeUIError("panels 场景只能为 c2c/group/channel/dm")
    for scope, panel in panels.items():
        if not isinstance(panel, dict) or set(panel) != {"items"}:
            raise NativeUIError(f"{scope} 面板必须只包含 items")
        if not isinstance(panel["items"], list) or len(panel["items"]) > 20:
            raise NativeUIError(f"{scope} 面板最多 20 项")
        for item in panel["items"]:
            if not isinstance(item, dict) or set(item) - {"name", "desc", "type", "only_admin", "link"}:
                raise NativeUIError(f"{scope} 面板元素格式不正确")
            _text(item.get("name"), 14, "面板名称")
            if "desc" in item:
                _text(item["desc"], 30, "面板描述", empty=True)
            if "only_admin" in item and not isinstance(item["only_admin"], bool):
                raise NativeUIError("only_admin 必须是布尔值")
            if item.get("type") == "link":
                _https(item.get("link"), "面板链接")
            elif item.get("type") != "command" or "link" in item:
                raise NativeUIError("面板元素仅支持 command 或 link")
    result = copy.deepcopy(config)
    result.setdefault("owner", "hd2bot")
    result.setdefault("menu_mode", "merge")
    result.setdefault("panels", {})
    return result


def load_config(path: Path) -> dict[str, Any]:
    try:
        return validate_config(json.loads(path.read_text(encoding="utf-8-sig")))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise NativeUIError("无法读取原生界面 JSON 配置") from exc


@dataclass(frozen=True)
class Change:
    method: str
    path: str
    scope: str
    before: dict[str, Any] | None
    body: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _panel_content(panel: dict[str, Any]) -> dict[str, Any]:
    # Servers may insert defaults or a version; neither changes the actual panel.
    items = []
    for item in panel.get("items", []):
        items.append({key: value for key, value in item.items()
                      if key in {"type", "name", "desc", "only_admin", "link"}
                      and value is not None and not (key == "only_admin" and value is False)
                      and not (key in {"desc", "link"} and value == "")})
    return {"items": items, "remark": panel.get("remark", "")}


def _menu_content(value: Any) -> Any:
    # GET /v2/menu decorates entries with Tencent's default icon URLs. The PUT
    # configuration does not set icons, so those generated fields are not a diff.
    if isinstance(value, list):
        return [_menu_content(item) for item in value]
    if isinstance(value, dict):
        return {key: _menu_content(item) for key, item in value.items() if key != "icon"}
    return value


def plan_changes(config: dict[str, Any], snapshot: dict[str, Any]) -> list[Change]:
    """Only touch our exact owner/scope panel remarks; never delete another panel."""
    config = validate_config(config)
    changes: list[Change] = []
    desired_menu = config.get("menu")
    current_menu = snapshot.get("menu", {}).get("menu") or {"items": []}
    if desired_menu is not None:
        merged = copy.deepcopy(desired_menu)
        if config["menu_mode"] == "merge":
            replacements = {item["name"]: item for item in desired_menu["items"]}
            existing_names = {item.get("name") for item in current_menu.get("items", [])}
            merged["items"] = [copy.deepcopy(replacements.get(item.get("name"), item))
                               for item in current_menu.get("items", [])]
            merged["items"].extend(copy.deepcopy(item) for item in desired_menu["items"]
                                   if item["name"] not in existing_names)
        if len(merged["items"]) > 10:
            raise NativeUIError("保留原有菜单后超过 10 项，请调整配置或明确选择 replace")
        if _menu_content(merged) != _menu_content(current_menu):
            changes.append(Change("PUT", "/v2/menu", "c2c", current_menu, {"menu": merged}))
    records_by_scope = snapshot.get("panels", {})
    total = sum(len(records) for records in records_by_scope.values())
    additions = 0
    for scope, configured_panel in config["panels"].items():
        remark = f"{config['owner']}:{scope}"
        panel = {**copy.deepcopy(configured_panel), "remark": remark}
        owned = [record for record in records_by_scope.get(scope, [])
                 if record.get("panel", {}).get("remark") == remark]
        if len(owned) > 1:
            raise NativeUIError(f"{scope} 有重复的本项目面板，未执行写操作")
        if owned:
            record = owned[0]
            if record.get("target_type") != "all" or record.get("scope") != scope:
                raise NativeUIError(f"{scope} 同名面板并非该场景全局面板，未执行写操作")
            panel_id = record.get("panel_id")
            if not isinstance(panel_id, str) or not panel_id:
                raise NativeUIError(f"{scope} 面板缺少 panel_id")
            if _panel_content(record["panel"]) != _panel_content(panel):
                changes.append(Change("PUT", f"/v2/panels/{quote(panel_id, safe='')}",
                                      scope, record["panel"], {"panel": panel}))
        else:
            additions += 1
            changes.append(Change("POST", "/v2/panels", scope, None,
                                  {"scope": scope, "target_type": "all", "panel": panel}))
    if total + additions > 20:
        raise NativeUIError("新增后将超过机器人 20 个面板上限，未执行写操作")
    return changes


class NativeUIClient:
    """Short-lived management client; never log or persist token/secret values."""

    def __init__(self, settings: Settings, *, session: aiohttp.ClientSession | None = None):
        self.settings = settings
        self.base = "https://sandbox.api.sgroup.qq.com" if settings.qq_sandbox else API_BASE
        self._session = session
        self._owns_session = session is None
        self._token: str | None = None
        self._expires_at = 0.0

    async def __aenter__(self) -> NativeUIClient:
        if not self.settings.qq_app_id or not self.settings.qq_app_secret:
            raise NativeUIError("QQ_APP_ID 和 QQ_APP_SECRET 尚未配置")
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25))
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._owns_session and self._session is not None:
            await self._session.close()
        self._token = None

    async def _json(self, method: str, url: str, **kwargs: Any) -> tuple[int, dict[str, Any]]:
        if self._session is None:
            raise NativeUIError("管理客户端尚未打开")
        try:
            async with self._session.request(method, url, **kwargs) as response:
                if response.status == 204:
                    return response.status, {}
                try:
                    data = await response.json(content_type=None)
                except (ValueError, UnicodeError):
                    raise NativeUIError(f"QQ API 返回非 JSON：HTTP {response.status}") from None
                if not isinstance(data, dict):
                    raise NativeUIError(f"QQ API 返回格式不正确：HTTP {response.status}")
                return response.status, data
        except (aiohttp.ClientError, TimeoutError):
            raise NativeUIError("QQ API 网络请求未完成；请重新查询远端状态再执行计划") from None

    @staticmethod
    def _check(status: int, data: dict[str, Any]) -> None:
        code = data.get("err_code", data.get("code", 0))
        if not 200 <= status < 300 or code not in (0, "0", None):
            displayed = str(code) if isinstance(code, (str, int)) and str(code).isdigit() else "未知"
            raise NativeUIError(f"QQ API 拒绝请求：HTTP {status}，code={displayed}")

    async def _access_token(self) -> str:
        if self._token is not None and time.monotonic() < self._expires_at:
            return self._token
        status, data = await self._json("POST", TOKEN_URL, json={
            "appId": self.settings.qq_app_id, "clientSecret": self.settings.qq_app_secret,
        })
        self._check(status, data)
        token = data.get("access_token")
        try:
            lifetime = int(data.get("expires_in", 0))
        except (TypeError, ValueError):
            lifetime = 0
        if not isinstance(token, str) or not token or lifetime <= 0:
            raise NativeUIError("QQ API 未返回有效访问凭证")
        self._token = token
        self._expires_at = time.monotonic() + max(1, lifetime - 60)
        return token

    async def access_token(self) -> str:
        """Return an in-memory token for another explicit official API client."""
        return await self._access_token()

    async def request(self, method: str, path: str, *, body: dict[str, Any] | None = None,
                      params: dict[str, Any] | None = None) -> dict[str, Any]:
        if method not in {"GET", "PUT", "POST"} or not re.fullmatch(
            r"/v2/(?:menu|panels(?:/[A-Za-z0-9_.%~-]+)?)", path,
        ):
            raise NativeUIError("此工具只允许菜单和面板的查询、创建、修改接口")
        token = await self._access_token()
        status, data = await self._json(method, self.base + path, params=params, json=body,
                                       headers={"Authorization": f"QQBot {token}",
                                                "X-Union-Appid": self.settings.qq_app_id})
        self._check(status, data)
        return data

    async def snapshot(self) -> dict[str, Any]:
        menu = await self.request("GET", "/v2/menu")
        panels = {}
        for scope in SCOPES:
            records: list[dict[str, Any]] = []
            cursor = ""
            seen: set[str] = set()
            while True:
                params: dict[str, Any] = {"scope": scope, "limit": 50}
                if cursor:
                    params["cursor"] = cursor
                page = await self.request("GET", "/v2/panels", params=params)
                batch = page.get("records", [])
                if not isinstance(batch, list) or any(not isinstance(r, dict) for r in batch):
                    raise NativeUIError("QQ 面板列表格式不正确")
                records.extend(batch)
                cursor = page.get("next_cursor") or ""
                if page.get("is_end") is True or not cursor:
                    break
                if not isinstance(cursor, str) or cursor in seen or len(seen) >= 20:
                    raise NativeUIError("QQ 面板分页游标异常，未执行写操作")
                seen.add(cursor)
            panels[scope] = records
        return {"menu": menu, "panels": panels}

    async def apply(self, changes: list[Change]) -> list[dict[str, Any]]:
        results = []
        for change in changes:
            data = await self.request(change.method, change.path, body=change.body)
            # Do not expose arbitrary response bodies, and never blindly retry POST.
            results.append({"method": change.method, "path": change.path, "scope": change.scope,
                            "panel_id": data.get("panel_id"), "version": data.get("version")})
        return results
