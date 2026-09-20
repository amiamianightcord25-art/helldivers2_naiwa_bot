"""Offline NoneBot events -> real HTML/JPEG -> mocked official QQ HTTP requests."""

import asyncio
import base64
import io
import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nonebot.adapters.qq import Bot  # noqa: E402
from nonebot.adapters.qq.models import Dispatch, User  # noqa: E402
from nonebot.drivers import Response  # noqa: E402
from PIL import Image  # noqa: E402

from hd2bot.config import Settings  # noqa: E402
from hd2bot.hd2.providers.fallback import FallbackProvider  # noqa: E402
from hd2bot.hd2.providers.mock import MockProvider  # noqa: E402
from hd2bot.hd2.service import HD2Service  # noqa: E402
from hd2bot.qq.adapter import QQAdapter, QQDispatcher, initialize_nonebot  # noqa: E402
from hd2bot.rendering.renderer import HtmlRenderer  # noqa: E402
from hd2bot.router import CommandRouter  # noqa: E402


async def probe() -> int:
    # Deliberately do not load .env; this script cannot use real QQ credentials.
    settings = Settings(provider="mock", qq_app_id="offline-probe",
                        qq_app_secret="SYNTHETIC_LOCAL_PROBE_NOT_A_REAL_CREDENTIAL")
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(settings.root / ".cache/ms-playwright"))
    router = CommandRouter(HD2Service(FallbackProvider([MockProvider()]), settings))
    renderer = HtmlRenderer(width=settings.image_width, jpeg_quality=settings.image_quality,
                            max_bytes=settings.image_max_bytes, timeout=settings.image_timeout)
    dispatcher = QQDispatcher(settings, router, renderer)
    driver = initialize_nonebot(settings, dispatcher)
    adapter = driver._adapters["QQ"]
    bot = Bot(adapter, settings.qq_app_id, adapter.qq_config.qq_bots[0])
    bot._self_info = User(id="offline-bot", username="Offline", bot=True)
    requests = []
    jpeg_sizes = []

    async def record_request(request):
        # Any accidental auth, gateway, history or other request fails locally.
        assert request.method == "POST"
        assert request.url.path.endswith(("/files", "/messages"))
        requests.append(request)
        if request.url.path.endswith("/files"):
            assert request.json["file_type"] == 1
            assert request.json["srv_send_msg"] is False
            jpeg = base64.b64decode(request.json["file_data"], validate=True)
            with Image.open(io.BytesIO(jpeg)) as picture:
                assert picture.format == "JPEG" and picture.width == settings.image_width
                picture.verify()
            assert len(jpeg) <= settings.image_max_bytes
            jpeg_sizes.append(len(jpeg))
            response = {"file_info": "offline-image-reference"}
        else:
            assert request.json["msg_id"].startswith("offline-")
            assert request.json["msg_seq"] == 1
            response = {"id": "offline-reply"}
        return Response(200, content=json.dumps(response).encode())

    adapter.request = record_request
    bot.get_access_token = AsyncMock(return_value="OFFLINE_NO_NETWORK_TOKEN")
    try:
        for scope in ("c2c", "group"):
            for command in ("帮助", "战况", "星图", "武器 AR23"):
                message_id = f"offline-{scope}-{command}"
                data = {"id": message_id, "content": command,
                        "timestamp": "2026-09-16T00:00:00Z"}
                if scope == "group":
                    data.update(group_openid="offline-group", author={"member_openid": "offline"})
                else:
                    data["author"] = {"user_openid": "offline-user"}
                event = QQAdapter.payload_to_event(Dispatch.model_validate({
                    "op": 0, "s": 1, "id": "event-" + message_id,
                    "t": "GROUP_AT_MESSAGE_CREATE" if scope == "group" else "C2C_MESSAGE_CREATE",
                    "d": data,
                }))
                # Real NoneBot matcher pipeline; a duplicate must not add a reply.
                await bot.handle_event(event)
                await bot.handle_event(event)
        messages = [r for r in requests if r.url.path.endswith("/messages")]
        assert len(messages) == 8 and len(jpeg_sizes) == 6
        assert [r.json["msg_type"] for r in messages] == [0, 7, 7, 7, 0, 7, 7, 7]
        assert all("帮助" in r.json["content"] for r in messages if r.json["msg_type"] == 0)
        print("PASS: real NoneBot C2C/group matcher, text help, HTML/JPEG war, star map, wiki weapon,")
        print("      JPEG compression, base64 upload, passive message IDs/sequences, deduplication")
        print(f"QQ network calls: 0; captured replies: 8; JPEG bytes: {jpeg_sizes}")
        return 0
    finally:
        await dispatcher.close()
        await renderer.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(probe()))
