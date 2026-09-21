"""Start, stop and inspect this checkout's local QQ bot. No credentials in argv."""

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from hd2bot.config import Settings, validate_bot_settings  # noqa: E402
from hd2bot.runtime import process_status, request_stop  # noqa: E402


def show_status(settings, state=None):
    state = state if state is not None else process_status(settings)
    if not state["running"]:
        print("机器人未运行。")
        return
    labels = {"starting": "正在连接", "ready": "已收到 READY", "stopping": "正在关闭"}
    print(f"机器人运行中：{labels.get(state.get('status'), '启动状态待确认')}，PID {state.get('pid', '待确认')}")
    print(f"传输：{state.get('transport', '待确认')}；沙箱：{state.get('sandbox', '待确认')}")
    if state.get("ready_at"):
        print(f"最近 READY：{state['ready_at']}")
    print(f"日志目录：{settings.log_dir}")


def start_bot(settings, timeout: float) -> int:
    if (settings.database_path.parent / "deployment_target.json").is_file():
        print("本机已标记为迁移到服务器，请通过服务器 systemd 管理机器人，避免重复启动。")
        return 2
    state = process_status(settings)
    if state["running"]:
        print("已存在本项目实例，不会重复启动。")
        show_status(settings, state)
        return 0
    previous_instance = state.get("instance")
    try:
        validate_bot_settings(settings)
    except ValueError as exc:
        print(str(exc))
        return 2
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    interpreter = settings.root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not interpreter.is_file():
        print("项目虚拟环境不存在，请按 README 安装依赖。")
        return 2
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
    # Append preserves earlier startup/stop evidence; application bot.log rotates.
    with (settings.log_dir / "console.out.log").open("ab") as output, \
            (settings.log_dir / "console.err.log").open("ab") as errors:
        child = subprocess.Popen(
            [str(interpreter), "-u", str(settings.root / "run.py")], cwd=settings.root,
            stdin=subprocess.DEVNULL, stdout=output, stderr=errors, close_fds=True, **options,
        )
    print("正在启动，等待 QQ 连接…", flush=True)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = process_status(settings)
        if (state["running"] and state.get("status") == "ready"
                and state.get("instance") != previous_instance):
            show_status(settings, state)
            return 0
        if child.poll() is not None and not state["running"]:
            print(f"启动失败（退出码 {child.returncode}）。请查看 {settings.log_dir / 'console.err.log'}")
            return 1
        time.sleep(0.25)
    show_status(settings)
    print("等待 READY 超时。进程保留运行，可用查看状态脚本检查，或用关闭脚本结束。")
    return 1


def stop_bot(settings, timeout: float) -> int:
    if not request_stop(settings):
        print("机器人已停止，无需重复关闭。")
        return 0
    print("已请求正常关闭，等待 QQ 连接和数据库清理…", flush=True)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not process_status(settings)["running"]:
            print("机器人已关闭。")
            return 0
        time.sleep(0.25)
    print("正常关闭尚未完成，请查看日志并稍后重试；没有强制结束其他进程。")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "stop", "status"))
    parser.add_argument("--timeout", type=float, default=45)
    args = parser.parse_args()
    if not 0 < args.timeout <= 120:
        parser.error("timeout must be between 0 and 120 seconds")
    try:
        settings = Settings.load(PROJECT_ROOT)
        if args.action == "start":
            return start_bot(settings, args.timeout)
        if args.action == "stop":
            return stop_bot(settings, args.timeout)
        show_status(settings)
        return 0
    except (OSError, ValueError) as exc:
        print(f"操作失败（{type(exc).__name__}），请检查 .env 配置及项目目录权限。")
        return 2


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf8", errors="replace")
    raise SystemExit(main())
