"""Generate a one-time code; the designated QQ user sends it privately to the bot."""

import hashlib
import json
import secrets
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hd2bot.config import Settings  # noqa: E402

if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf8")
    settings = Settings.load()
    if not settings.qq_push_user:
        raise SystemExit("请先在 .env 设置 QQ_PUSH_USER。")
    code = secrets.token_hex(6)
    path = settings.database_path.parent / "push_pairing.private.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({
        "qq_number": settings.qq_push_user, "digest": hashlib.sha256(code.encode()).hexdigest(),
        "expires_at": time.time() + 3600,
    }), encoding="utf8")
    temporary.replace(path)
    print(f"请使用 QQ {settings.qq_push_user} 私信机器人：绑定推送 {code}")
    print("绑定码仅可用一次，1小时内有效。请勿在群里发送。")
