"""Editable command menus, with text and official inline-button representations."""

import json
import logging
from pathlib import Path

from hd2bot.commands.parser import parse_command
from hd2bot.hd2.errors import CommandError
from hd2bot.presentation import ChatContext, CommandButton, CommandReply
from hd2bot.project_info import OPEN_SOURCE_BRIEF

logger = logging.getLogger(__name__)
DEFAULT_PATH = Path(__file__).resolve().parents[1] / "assets" / "command_menu.json"


class MenuService:
    def __init__(self, path: Path | None = None):
        self.path = path
        self._pages = self._parse(DEFAULT_PATH.read_bytes())
        self._signature = None

    @staticmethod
    def _parse(raw: bytes) -> dict:
        if len(raw) > 32_768:
            raise ValueError("menu too large")
        value = json.loads(raw.decode("utf-8-sig"))
        if (not isinstance(value, dict) or set(value) != {"schema", "pages"}
                or type(value["schema"]) is not int or value["schema"] != 1):
            raise ValueError("invalid menu schema")
        pages = value["pages"]
        if not isinstance(pages, dict) or "首页" not in pages or not 1 <= len(pages) <= 8:
            raise ValueError("invalid menu pages")
        for name, page in pages.items():
            if (not isinstance(name, str) or not 1 <= len(name) <= 8
                    or any(c.isspace() or ord(c) < 32 for c in name)
                    or not isinstance(page, dict) or set(page) != {"title", "rows"}):
                raise ValueError("invalid menu page")
            title, rows = page["title"], page["rows"]
            if (not isinstance(title, str) or not 1 <= len(title) <= 40
                    or any(ord(c) < 32 for c in title)
                    or not isinstance(rows, list) or not 1 <= len(rows) <= 5):
                raise ValueError("invalid menu rows")
            for row in rows:
                if not isinstance(row, list) or not 1 <= len(row) <= 3:
                    raise ValueError("invalid menu row")
                for button in row:
                    if not isinstance(button, dict) or set(button) != {"label", "command"}:
                        raise ValueError("invalid menu button")
                    label, command = button["label"], button["command"]
                    if (not isinstance(label, str) or not 1 <= len(label) <= 10
                            or not isinstance(command, str) or not 1 <= len(command) <= 80
                            or any(ord(c) < 32 for c in label + command)):
                        raise ValueError("invalid menu text")
                    try:
                        parsed = parse_command(command)
                    except CommandError as exc:
                        raise ValueError("unknown menu command") from exc
                    if parsed.name == "菜单" and parsed.argument not in {"", *pages}:
                        raise ValueError("unknown menu page")
        return pages

    def _refresh(self):
        if self.path is None:
            return
        try:
            stat = self.path.stat()
            signature = (stat.st_mtime_ns, stat.st_size)
            if signature == self._signature:
                return
            self._signature = signature
            self._pages = self._parse(self.path.read_bytes())
        except FileNotFoundError:
            return
        except (OSError, ValueError, TypeError):
            logger.warning("event=command_menu_invalid action=keep_last_valid")

    def reply(self, page: str = "", *, context: ChatContext | None = None) -> CommandReply:
        self._refresh()
        selected = self._pages.get(page or "首页")
        if selected is None:
            return CommandReply("菜单分类：" + "、".join(self._pages) + "。例如：菜单 娱乐")
        keyboard = tuple(tuple(CommandButton(**item) for item in row)
                         for row in selected["rows"])
        lines = ["🦅 " + selected["title"], "点击下方按钮，或直接发送对应指令："]
        for row in keyboard:
            lines.append(" · ".join(
                item.label if item.label == item.command else f"{item.label}（{item.command}）"
                for item in row
            ))
        if context is not None and not context.is_private:
            lines.append("群聊和频道使用时请 @机器人；按钮填入指令后点击发送。")
        lines.append("签到经验和小游戏分数仅属于本机器人。")
        lines.append(OPEN_SOURCE_BRIEF)
        return CommandReply("\n".join(lines), keyboard=keyboard)
