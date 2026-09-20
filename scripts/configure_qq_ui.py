"""Read, plan, or apply the official QQ native menu and command panel configuration."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hd2bot.config import PROJECT_ROOT, Settings  # noqa: E402
from hd2bot.qq.native_ui import (  # noqa: E402
    NativeUIClient,
    NativeUIError,
    load_config,
    plan_changes,
)


def output(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


async def run(args):
    config = load_config(args.config) if args.action != "read" else None
    if args.action == "validate":
        output({"valid": True, "scopes": list(config["panels"]),
                "menu_mode": config["menu_mode"]})
        return 0
    settings = Settings.load()
    async with NativeUIClient(settings) as client:
        snapshot = await client.snapshot()
        if args.action == "read":
            output(snapshot)
            return 0
        changes = plan_changes(config, snapshot)
        output({"environment": "sandbox" if settings.qq_sandbox else "production",
                "mode": args.action, "changes": [change.as_dict() for change in changes]})
        if args.action == "apply":
            if args.backup:
                args.backup.parent.mkdir(parents=True, exist_ok=True)
                # An exclusive backup avoids replacing the earlier rollback reference.
                with args.backup.open("x", encoding="utf-8") as handle:
                    json.dump(snapshot, handle, ensure_ascii=False, indent=2)
            results = await client.apply(changes)
            remaining = plan_changes(config, await client.snapshot())
            output({"applied": results, "verified": not remaining,
                    "remaining_changes": [change.as_dict() for change in remaining]})
            return 1 if remaining else 0
        return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("validate", "read", "plan", "apply"), default="plan",
                        nargs="?", help="默认 plan；只有 apply 会写入 QQ 菜单/面板")
    private_config = PROJECT_ROOT / "data/qq_native_ui.json"
    parser.add_argument("--config", type=Path, default=(private_config if private_config.is_file()
                                                     else PROJECT_ROOT / "qq_native_ui.example.json"))
    parser.add_argument("--backup", type=Path, help="apply 前保存远端界面备份，不覆盖已有文件")
    args = parser.parse_args()
    try:
        return asyncio.run(run(args))
    except NativeUIError as exc:
        output({"error": str(exc)})
        return 1
    except (OSError, ValueError):
        output({"error": "配置或备份文件不可用，请检查路径及环境配置"})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
