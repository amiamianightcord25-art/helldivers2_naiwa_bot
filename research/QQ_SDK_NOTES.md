> 历史记录：旧 botpy SDK，现行入口使用 NoneBot2。下文“当前”“本轮”、测试数量和运行状态均指记录时点，不是现行部署说明。当前使用方法见 [项目 README](../README.md)，文档范围见 [研究索引](README.md)。

# QQ 官方平台接入研究

> 历史记录：2026-09-16 按用户新要求迁移至 NoneBot2 + 官方 QQ 适配器，已移除 botpy 依赖和旧 Webhook 实现。
> 当前实现与验证见 [NONEBOT_MIGRATION_NOTES.md](NONEBOT_MIGRATION_NOTES.md)。下文保留早期研究事实，不代表现行代码。

研究日期：2026-09-11。范围：本地已安装的 `qq-botpy-sdk==2.0.3`、QQ 机器人开放平台公开文档，以及不联网发消息的 SDK 行为验证。本文不代表 QQ 线上或沙箱联调已通过。

## 结论

- 使用用户指定的 `qq-botpy-sdk`，导入名为 `botpy`，调用 QQ 官方开放平台 REST API 和事件协议。此发行包现由 Teahouse Studios 社区维护，源于腾讯 `botpy`；应称为“官方平台机器人”，不能把这个发行包描述成当前腾讯官方维护的 SDK。
- 2.0.3 原生支持 `webhook` 和 `websocket`。项目显式选择 transport，按用户要求当前默认 `websocket`；已有账号实际通过 Gateway READY 验证。Webhook 作为可配置适配器保留，监听 `127.0.0.1` 用于本地验证。
- 当前无需公网部署。仅在 loopback 启动 Webhook 并不能收到 QQ 平台事件；可用本地签名请求检验接收协议和适配器。没有 AppID/AppSecret 时继续使用 CLI/mock，QQ 启动应给出明确配置提示。
- SDK 能验签并立即 ACK，但没有时间戳新鲜度检查、重复事件去重或有界业务队列。本项目要在接入边界处理重复消息，并把耗时业务和消息 ACK 分离。
- 普通文本回复建议直接调用 `post_group_message` / `post_c2c_message` 并始终带原始 `msg_id` 和递增 `msg_seq`。不要依赖 SDK `Client.send_text()` 的自动被动/主动消息切换。

## 实际 SDK 构造与生命周期

已读源码：`botpy/client.py`、`http.py`、`message.py`、`api.py`、`flags.py`、`logging.py`、`connection.py`、`gateway.py`、`protocol/transport/*`、`protocol/reply.py`、`protocol/events.py`。

主要签名（仅列本项目需要的参数）：

```python
botpy.Client(
    intents=botpy.Intents(public_messages=True),
    timeout=5,
    is_sandbox=False,
    transport="webhook",        # SDK 默认实际是 websocket
    webhook_host="127.0.0.1",    # SDK 默认实际是 0.0.0.0
    webhook_port=8080,
    webhook_path="/qq/events",
    webhook_server=None,         # 可注入 WebhookServerAdapter
    log_level=logging.WARNING,
    bot_log=None,
    ext_handlers=False,
)

client.run(appid=appid, secret=secret)  # 阻塞式入口，内部管理事件循环
await client.start(appid=appid, secret=secret, ret_coro=False)
await client.close()                  # 幂等关闭
```

项目已经使用 asyncio 时，在同一事件循环内构造 Client，并使用：

```python
async with client:
    await client.start(appid=appid, secret=secret)
```

`__aenter__` 初始化运行循环和 ready event；`__aexit__` 调用 `close()`。不要在运行中的 asyncio 循环内调用阻塞式 `run()`。`ret_coro=True` 仍然先完成 token 获取、账号登录和传输准备，再返回协程，不能当作“无凭据构造模式”。

无论 Webhook 或 WebSocket，`start()` 都先请求 token，再调用 `/users/@me`；Webhook 模式只是不请求 Gateway 接入点。`close()` 关闭 transport、流式会话、WebSocket、session store、声明式配置、HTTP/token session 和上传缓存。

SDK `_schedule_event()` 创建的普通回调 Task 没有统一加入 Client 关闭集合。项目应跟踪自身在途任务并在关闭时取消/收拢，避免业务在 HTTP session 关闭后才尝试回复。使用 `asyncio.run()` 管理最外层生命周期能在退出时收拢剩余 Task，但不等于业务已持久化。

## 事件与回复

`botpy.Intents(public_messages=True)` 为 `1 << 25`，对应群和 C2C 事件。Webhook 的实际监听事件还需在开放平台管理端选定；SDK intents 参数不代替管理端授权。

| 场景 | SDK 回调 | 目标字段 | 原消息字段 | 回复接口 |
| --- | --- | --- | --- | --- |
| 群内 @ 机器人 | `on_group_at_message_create(GroupMessage)` | `message.group_openid` | `message.id` | `api.post_group_message(group_openid=..., msg_id=..., msg_seq=..., content=..., msg_type=0)` |
| C2C 单聊 | `on_c2c_message_create(C2CMessage)` | `message.author.user_openid` | `message.id` | `api.post_c2c_message(openid=..., msg_id=..., msg_seq=..., content=..., msg_type=0)` |
| 频道 @ 机器人（可选） | `on_at_message_create(Message)` | `message.channel_id` | `message.id` | `api.post_message(channel_id=..., msg_id=..., content=...)` |

`GroupMessage.reply(**kwargs)` 和 `C2CMessage.reply(**kwargs)` 只负责自动填入目标和 `msg_id`，其余参数透传。它们不会自动递增 `msg_seq`。同一条原消息的每个回复分段必须使用不同的 `msg_seq`，且不要同时给 `.reply()` 再传 `msg_id`（会重复传参）。

SDK 同时提供统一的 `on_message(InboundMessage)`：`normalize_inbound_message()` 支持 C2C、群、频道、频道私信，包含 `reply_target`、`author_id` 和 `event_id`。不要同时在旧场景回调和统一回调里执行业务，否则同一事件可能回复两次。

推荐职责：QQ 回调只提取消息标识/正文，交给项目 Command Router → HD2 Service → Formatter，再经一个可 mock 的 reply 函数返回。QQ handler 不解析 HD2 JSON、不计算战况，也不保存用户凭据。

官方消息概述（页面更新时间 2026-07-21）的规则：

- 单聊被动消息有效期 60 分钟，每条原消息可回复 4 次。
- 群聊被动消息有效期 5 分钟，每条原消息可回复 5 次。
- 相同 `msg_id` 可能重复推送；相同 `msg_id + msg_seq` 重复发送会失败。

SDK `api.py` 的部分旧 docstring 仍写单聊 5 分钟，以及发送必须 WebSocket 在线；这些注释不能代替当前按场景拆分的官方规则。SDK `Client.send()` 的默认 ReplyLimiter 对群和 C2C 一律按 4 次/3600 秒处理，超过限制时会删除 `msg_id`/`event_id` 转为主动消息。本项目只做被动查询回复，直接 REST reply API 更可控；分段应限制为至多 4 段并裁剪过长结果，不能自动发送主动消息。

## Webhook 验签、ACK 与测试入口

实际接口位于 `botpy.protocol.transport.webhook`：

```python
WebhookRequest(body: bytes, headers: Mapping[str, str])
WebhookResponse(status: int, body: bytes, headers: Mapping[str, str])

WebhookTransport(app_id, app_secret, *, host="0.0.0.0", port=8080,
                 path="/", server=None, logger=None,
                 on_started=None, on_error=None)
await transport.start(async_event_handler)
await transport.handle_request(WebhookRequest(body, headers))
await transport.close()
```

自定义 server 实现 `async listen(host, port, path, handler)` 和 `async close()`，即可在真实 SDK transport 前加入本项目所需的请求限制，不必重写密码算法。默认 `AiohttpWebhookServer(max_body_size=1024*1024)` 支持 POST 路径和本地临时端口 `port=0`，`bound_port` 可用于回环集成测试。

验签工具位于 `botpy.protocol.transport.webhook_verify`：

- `derive_ed25519_seed(bot_secret)`：按 UTF-8 字节重复 secret 并截取 32 字节。
- `ed25519_sign(bot_secret, message: bytes) -> str`：返回十六进制签名，适合仅用虚构测试 secret 构造本地请求。
- `verify_webhook_signature(body=..., timestamp=..., signature=..., bot_secret=...) -> bool`：校验签名覆盖 `timestamp.encode('utf-8') + 原始请求字节`。
- `sign_validation_response(plain_token=..., event_ts=..., bot_secret=...)`：生成回调地址验证响应。

SDK 实测处理顺序：

1. JSON 不合法/不是对象/没有整数 op 返回 HTTP 400。
2. `op=13` 回调地址验证独立处理；要求非空 `d.plain_token` 和 `d.event_ts` 字符串，返回签名。此路径不要求签名请求头，与官方 URL 验证示例一致。
3. 其他请求需要 `X-Signature-Timestamp` 和 `X-Signature-Ed25519`（header 名大小写不敏感），缺失或验签失败返回 HTTP 401。
4. `op=0` 使用 `asyncio.create_task()` 分发业务，立即返回 HTTP 200 和 `{"op":12,"d":0}`，不等待 HD2 请求/QQ 回复完成。
5. 通过验签的其他 opcode 也返回 ACK，不进入业务分发。

已观察到的边界：

- `timestamp="1"` 的合法签名仍被接受；SDK 没有时间窗口检查。签名证明完整性，不等于防重放。
- 同一合法请求发送两次，会分发两次。应用层应以 `(场景, 目标, 原消息ID)` 做有界 TTL 去重；拒绝过期签名的策略由应用接收层配置，并允许合理时钟偏差。
- SDK 不检查 `X-Bot-Appid` 是否匹配配置。可由接收层检查提供的 AppID，但不能取代签名校验。
- ACK 早于业务完成，进程在 ACK 后退出可能丢失该次回复。本地阶段不引入持久化消息队列；文档应明确这是最佳努力查询服务。
- SDK transport 的 `close()` 会取消其 `_dispatch_tasks`；普通 Client 回调任务仍需上述应用级关闭处理。

本次运行了一次纯内存 smoke probe，使用虚构 AppID/Secret、FakeServer、AsyncMock API，无真实 QQ 网络请求。结果：

```text
PASS: 签名完整性、立即 ACK、无签名/坏 JSON 拒绝、op13 challenge、关闭取消、群/C2C 被动回复参数映射
OBSERVED: 旧 timestamp 被接受；重复事件分发两次；op13 可无签名请求头
```

M4 回归测试建议：签名内容篡改、过期签名、重复 event/msg ID、并发重复消息、慢 HD2 服务下 ACK 不阻塞、未知命令、服务失败、回复失败、超长内容分段序号、任务关闭和无配置启动。以虚构值测试，不读取真实 QQ 凭据。

## 日志与凭据

SDK import 会执行 `logging.basicConfig()`、设置 `botpy` logger 为 INFO（命令行有 `--debug`/`-d` 时设 DEBUG），并执行空的 `os.system("")` 作为历史 Windows 控制台处理。默认 Client 还会追加位于当前工作目录的 `botpy.log`，与项目可配置日志目录冲突。

尤其是 `gateway.py` 的 DEBUG 发送日志包含完整 Identify/Resume payload，其中含动态 access token；接收 DEBUG 也包含消息正文和会话资料。不要启用 SDK DEBUG 或依赖“源码没有 secret”来判断运行日志安全。

应用应在加载 SDK 后明确禁用其默认文件 handler、清理遗留 handler、将 SDK 及其子 logger 至少设 WARNING，并把输出接入项目经过脱敏的 handler；`bot_log=None, ext_handlers=False, log_level=logging.WARNING` 是构造基础，不等于完成所有子 logger 的安全配置。`bot_log=False` 会设置 `propagate=False`，若采用向项目根 logger 传播方案，须显式重新配置。

新的 `protocol/http.py` 对常见敏感键做了脱敏，并去掉 URL query；这不能覆盖所有原始字符串或异常消息。应用异常日志只记录错误类型/本项目错误码，不转储消息对象、HTTP 请求头/响应、Token 对象或完整异常 traceback。动态 `QQBot ...`/Bearer token、Secret/Cookie/Authorization 字段仍需 handler 过滤，并写入回归测试。SDK 默认 `on_error()` 直接 `traceback.print_exc()`，应在适配器中覆盖。

Token 获取地址默认为 `https://bots.qq.com`，API 地址由 `is_sandbox` 控制为正式或 sandbox sgroup 域名。AppID/AppSecret 来自 `.env`；SDK 保持 token 在内存并负责刷新。不要把 token 或 QQ session store 放入 Git。

## 官方公开文档与适用范围

本次读取的官方页面仍同时描述 Webhook 和 WebSocket，不能据此声称所有账号必定有 Gateway 权限，也未找到足以声称 Gateway 已全面停止的官方证据。未来联调时应以具体机器人管理端权限、事件订阅和 API 响应为准。

Webhook 管理端需要平台可访问的 HTTPS 回调地址；文档允许配置端口 80、443、8080、8443。当前用户明确不部署，不创建公网回调、反向代理、穿透或证书配置。loopback 模式用于本地验签/适配器验证。

来源（2026-09-11 读取）：

1. [QQ 官方：事件订阅与通知](https://bot.q.qq.com/wiki/develop/api-v2/dev-prepare/interface-framework/event-emit.html)，页面显示更新于 2026-07-30，覆盖 op12/op13、Webhook 配置、Gateway、Intents。
2. [QQ 官方：安全和授权](https://bot.q.qq.com/wiki/develop/api-v2/dev-prepare/interface-framework/sign.html)，覆盖 Ed25519、原始 body 与时间戳签名。
3. [QQ 官方：消息收发概述](https://bot.q.qq.com/wiki/develop/api-v2/server-inter/message/overview.html)，页面显示更新于 2026-07-21，覆盖各场景回复期限/次数与重复消息。
4. [qq-botpy-sdk 维护仓库](https://github.com/Teahouse-Studios/qq-botpy-sdk) 与本地发行包 `METADATA`，明确其社区维护身份。
5. [SDK 迁移指南](https://github.com/Teahouse-Studios/qq-botpy-sdk/blob/master/MIGRATION.md)；实现结论以本地锁定 2.0.3 源码为准，未来升级必须重跑接入回归测试。

真实账号补充验证：用户提供凭据后，仅保存至被 Git 忽略的 `.env`。2026-09-11 实测 Token、`/users/@me`、`/gateway/bot` 均 HTTP 200；沙箱 WebSocket 连接收到 READY（01:05 本地时间）。未在报告保存凭据、Token 或连接会话。

消息补充验证：01:13:08 收到真实 C2C 事件，官方战况查询 HTTP 200，01:13:11 QQ 回复接口成功发送 1 段消息。应用日志只保留场景、时间和分段数。

尚未验证：沙箱群 @消息、QQ 客户端展示效果、管理端完整事件权限、正式环境，以及公网 HTTPS callback。C2C 成功不能代替其他场景的收发验收。

## M4 本地实现与验证

上述研究已应用到 `src/hd2bot/qq/`。入口为 `await run_qq(settings, router)`，可用 `make_client(settings, router)` 构造本地测试对象。沿用已有 `QQ_TRANSPORT`、`QQ_LISTEN_HOST`、`QQ_LISTEN_PORT`、`QQ_CALLBACK_PATH` 和账号配置，无新增必填配置。

- 只实现群 @ 和 C2C 回调。业务全部交给共享 `CommandRouter.handle()`；不同时实现统一 `on_message()`，避免重复回复。
- 每条消息在第一次 await 前预留去重标记。去重内存最多 4096 条，保存 1 小时；最多接纳 32 个在途请求，4 个并行执行。总处理时间上限 120 秒（含排队），关闭时取消应用在途任务。
- 每段最多 1800 UTF-8 字节，按 Unicode 字符和行边界拆分；这属于保守的本地发送预算，不声称是平台所有消息场景的统一硬限制。群最多 5 段、C2C 最多 4 段，全部携带原 `msg_id` 和递增 `msg_seq`；超过配额时末段明确标注“已缩略”并引导具体星球查询。
- Webhook 复用 SDK Ed25519 验证，在其 HTTP server adapter 外层限制请求为 128 KiB、时间戳偏差 ±300 秒、校验提供的 `X-Bot-Appid`，并对已通过验签的重复事件返回成功 ACK 而不重复分发。URL 验证请求无需签名 header，但也检查 `event_ts` 新鲜度。
- SDK 默认 handler 被移除。低于 WARNING 的 SDK 日志不输出，WARNING 以上也只保留 logger 名称/级别结构诊断，不保留原始消息或异常正文；最终仍进入项目日志 handler。已覆盖 SDK `on_error()`，不直接打印 traceback。
- 过载请求会被丢弃，已接纳消息去重标记不会因回复失败自动撤销；这是明确的最佳努力、最多处理一次策略，避免失败重试造成重复回复和额度消耗。此版本不提供持久化队列或宕机后补发。

`tests/test_qq.py` 的 23 项离线测试通过，覆盖真实 SDK 群/C2C dispatch 入口各只调用一次 router、重复消息、UTF-8 分段、回复失败/超时后恢复、并发上限、关闭取消、challenge/错签名/内容篡改/过期或未来时间戳/错 AppID、即时 ACK 和事件去重。另有一次真实 `127.0.0.1` 临时端口 HTTP 测试，链路为签名 POST → SDK HTTP server → 真实 SDK 事件分发 → 共享 router → MockProvider → mock QQ REST reply，验证到模拟战况输出。该测试没有连接 QQ 平台、使用真实凭据或向任何 QQ 用户发消息。

M4 的本地适配器与协议回归已完成；上一节列出的 QQ 平台线上/沙箱验证项仍未完成。
