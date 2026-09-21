"""Executable local OneBot peer; no QQ login, token exchange or external messages."""

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aiohttp import web  # noqa: E402
from nonebot.adapters.onebot.v11 import adapter as sdk  # noqa: E402

import hd2bot.napcat.adapter as transport  # noqa: E402
from hd2bot.application import create_service  # noqa: E402
from hd2bot.config import Settings  # noqa: E402
from hd2bot.router import CommandRouter  # noqa: E402
from hd2bot.services.checkin import CheckinService  # noqa: E402
from hd2bot.storage.database import Database  # noqa: E402
from hd2bot.transports.common import driver_lifespan  # noqa: E402

TOKEN = "SYNTHETIC_NAPCAT_TOKEN"


def message(ident, text, *, group=False, mention=True, user=20002):
    segments = ([{"type": "at", "data": {"qq": "10001"}}] if group and mention else [])
    segments += [{"type": "text", "data": {"text": text}}]
    data = {"time": 1, "self_id": 10001, "post_type": "message", "message_id": ident,
            "message_type": "group" if group else "private",
            "sub_type": "normal" if group else "friend", "user_id": user,
            "message": segments, "raw_message": text, "font": 0,
            "sender": {"user_id": user, "nickname": "合成用户", "role": "member"}}
    if group:
        data["group_id"] = 30003
    return data


async def main():
    root = Path(sys.argv[1])
    seen, ready, connections = [], [], []
    done = asyncio.Event()
    test_errors = []

    async def peer(request):
        if request.headers.get("Authorization") != "Bearer " + TOKEN:
            return web.Response(status=401)
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        connections.append(ws)
        number = len(connections)
        await ws.send_json({"time": 1, "self_id": 10001, "post_type": "meta_event",
                            "meta_event_type": "lifecycle", "sub_type": "connect"})
        injected = False
        async for incoming in ws:
            if incoming.type != web.WSMsgType.TEXT:
                continue
            action = json.loads(incoming.data)
            if action["action"] == "get_status":
                data = {"online": True, "good": True}
            elif action["action"] in {"send_private_msg", "send_group_msg"}:
                seen.append(action)
                data = {"message_id": 9000 + len(seen)}
            else:
                test_errors.append(action["action"])
                data = {}
            await ws.send_json({"status": "ok", "retcode": 0, "data": data, "echo": action["echo"]})
            if action["action"] == "get_status" and not injected:
                injected = True
                if number == 1:
                    for row in (
                        message(1, "签到"), message(2, "帮助", group=True),
                        message(3, "战况", group=True),
                        message(4, "战况", group=True, mention=False),
                        message(5, "帮助", user=10001),
                    ):
                        await ws.send_json(row)
                    await ws.send_json({"time": 1, "self_id": 10001, "post_type": "request",
                                        "request_type": "friend", "user_id": 20003,
                                        "comment": "ignored", "flag": "test"})
                else:
                    await ws.send_json(message(1, "签到"))
                    await ws.send_json(message(6, "菜单", group=True))
            if number == 1 and len(seen) == 3:
                await ws.close()
                break
            if number == 2 and len(seen) >= 4:
                done.set()
        return ws

    app = web.Application()
    app.router.add_get("/onebot", peer)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    settings = Settings(root=root, provider="mock", bot_backend="napcat",
                        napcat_ws_url=f"ws://127.0.0.1:{port}/onebot",
                        napcat_access_token=TOKEN, database_path=root / "loopback.db")
    sdk.RECONNECT_INTERVAL = 0.01
    transport.notify_ready = lambda: ready.append(True)
    renderer = AsyncMock()
    renderer.render.return_value = b"synthetic-jpeg"
    watch = None
    try:
        async with Database(settings.database_path) as db, create_service(settings) as war:
            checkin = CheckinService(db)
            router = CommandRouter(war, checkin=checkin, career_enabled=False, proactive_enabled=False)
            dispatcher = transport.NapCatDispatcher(settings, router, renderer)
            driver = transport.initialize_napcat(settings, dispatcher)
            assert set(driver._adapters) == {"OneBot V11"}
            async with driver_lifespan(driver):
                watch = asyncio.create_task(transport.watch_connection(
                    dispatcher, interval=0.03, connect_timeout=3))
                try:
                    await asyncio.wait_for(done.wait(), 8)
                    await asyncio.sleep(0.05)
                finally:
                    watch.cancel()
                    await asyncio.gather(watch, return_exceptions=True)
            assert len(connections) == 2 and len(ready) >= 2
            assert not test_errors and len(seen) == 4
            assert len([x for x in seen if x["action"] == "send_private_msg"]) == 1
            private = next(x for x in seen if x["action"] == "send_private_msg")
            assert private["params"]["user_id"] == 20002
            assert "签到成功" in json.dumps(private, ensure_ascii=False)
            images = [x for x in seen if any(s["type"] == "image" for s in x["params"]["message"])]
            assert len(images) == 1
            assert images[0]["params"]["group_id"] == 30003
            assert images[0]["params"]["message"][0]["data"]["file"].startswith("base64://")
            assert await db.fetch_one("SELECT total_days FROM checkin_profiles")
            assert not dispatcher._requests
    finally:
        for ws in connections:
            await ws.close()
        await runner.cleanup()
    print("NAPCAT_LOOPBACK_OK: auth, health, private, mention, image, dedup, reconnect, shutdown")


if __name__ == "__main__":
    asyncio.run(main())
