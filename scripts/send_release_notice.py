"""Preview or send the current release once to existing enabled group recipients."""

import argparse
import asyncio
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hd2bot.config import PROJECT_ROOT, Settings  # noqa: E402
from hd2bot.qq.native_ui import NativeUIError  # noqa: E402
from hd2bot.qq.release_sender import ReleaseSenderError, run_release_notice  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--notice", type=Path, default=PROJECT_ROOT / "data/release_notice.json")
    parser.add_argument("--send", action="store_true", help="发送一次；默认只读预览")
    args = parser.parse_args()
    try:
        result = asyncio.run(run_release_notice(Settings.load(), args.notice, send=args.send))
    except ReleaseSenderError as exc:
        result = {"scope": "group", "status": "error", "code": str(exc)}
    except NativeUIError:
        result = {"scope": "group", "status": "error", "code": "client_configuration"}
    except (OSError, ValueError, sqlite3.Error):
        result = {"scope": "group", "status": "error", "code": "local_configuration"}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    failed = result.get("status") == "error" or any(
        attempt["status"] != "sent" for attempt in result.get("attempts", [])
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
