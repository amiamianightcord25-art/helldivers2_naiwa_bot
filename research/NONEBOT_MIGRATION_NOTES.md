> 历史记录：初次 NoneBot 迁移，后续增加频道私信、子频道和群管理。下文“当前”“本轮”、测试数量和运行状态均指记录时点，不是现行部署说明。当前使用方法见 [项目 README](../README.md)，文档范围见 [研究索引](README.md)。

# NoneBot2 官方 QQ 迁移依据

研究日期：2026-09-16。本文只记录公开源码与本地离线验证，不记录凭据、消息正文或用户标识。

## 固定版本与来源

- NoneBot2 `2.5.0`：https://github.com/nonebot/nonebot2/tree/v2.5.0
- QQ 官方适配器 `nonebot-adapter-qq==1.7.2`：https://github.com/nonebot/adapter-qq/tree/v1.7.2
- 对照当前环境已安装 wheel 的源码，而不是仅使用 GitHub master。同一版本号尚未递增的 master 已删除 `BotInfo.token` 和沙箱配置并更换 API 域名，不能据此配置 1.7.2 发布版。
- 两项目声明 Python `>=3.10,<4`；本项目最低 Python 3.11。实际 Windows Python 3.14 已验证导入、aiohttp 驱动与生命周期。Python 3.11 尚未在本次机器上实测。使用 Pydantic 2 避免 Python 3.14 下 Pydantic 1 的兼容问题。

## 框架、配置与事件

使用 `nonebot.init(driver="~aiohttp", ...)`，这是仅有 HTTP/WS 客户端的驱动，不监听 HTTP 端口，不要求域名或 HTTPS 服务。

1.7.2 配置是 `qq_is_sandbox`、`qq_bots`。每个 bot 填 `id`、`secret`、`token`、`use_websocket=True`、`intent`。`token` 是发布版模型仍要求的遗留字段，可以为空字符串；实际 `get_access_token()` 用 AppID + AppSecret 换取访问令牌，随后用 `QQBot` 鉴权头。无需个人 QQ 登录。

只启用 `c2c_group_at_messages`（位 `1 << 25`），关闭频道、论坛等无关 intents。订阅并处理 `C2CMessageCreateEvent` 与 `GroupAtMessageCreateEvent`，其它事件不进入业务路由。NoneBot 插件通过 `on_message` / rule / handler 分发，再调用独立的 `QQDispatcher` 和 `CommandRouter`。

适配器新模型要求 `author.id`、`author.user_openid` 或 `author.member_openid` 同时存在。旧官方事件可能只有 openid；项目在 QQAdapter 子类的解析边界补充同义字段。群聊缺失 `group_id` 时使用已有 `group_openid`。缺失 group author metadata 按非机器人、普通成员补齐，业务不依赖该角色进行授权。消息 ID、正文、接收目标仍须是有效字段。

## 被动图片回复

发布版真实接口：

```python
uploaded = await bot.post_c2c_files(
    openid=target, file_type=1, file_data=jpeg_bytes, srv_send_msg=False
)
await bot.post_c2c_messages(
    openid=target, msg_type=7,
    msg_id=original_message_id, msg_seq=1,
    media=Media(file_info=uploaded.file_info),
)
```

群聊接口换成 `post_group_files(group_openid=...)`、`post_group_messages(group_openid=...)`。注意发送函数名是复数 `messages`，不同于旧 botpy 的单数方法。

`post_*_files` 接受 `bytes`，SDK 内部自动 base64 编码；`file_type=1` 是图片。设置 `srv_send_msg=False` 只上传，然后用收到的消息 ID 被动发送。无需给图片配置公网 URL。`file_info` 为空时应转文本，而不是发送无效媒体。上传成功不等于 QQ 图片已送达。

`MessageSegment.file_image(bytes)` + `bot.send(event, message)` 也是适配器内置方案；本项目直接调用上述 API，便于精确约束被动回复 ID、序号、降级和测试。大于 10 MiB 时高层 API 才转分片上传，本项目 JPEG 远小于该阈值。

渲染或上传失败使用原文本；图片发送阶段失败可能实际已被平台接受，因此不盲目重发图片，文本降级从下一个 `msg_seq` 开始，并计入单条原消息的总回复限制。

## 生命周期与隐私

已有 `asyncio.run`、`BotRuntime`、启动/关闭脚本继续使用。`nonebot.run()` 自己创建 event loop，不能嵌套调用。项目仅在一个隔离函数内使用固定版本驱动的 `driver._lifespan`；其进入/退出必须在同一任务，退出触发 Adapter shutdown 并取消 WebSocket/消息任务。由于这是私有接口，升级 NoneBot 时必须重新验证。

本地探针已验证：接收外部 `CancelledError` 时在 lifespan context 内暂存取消，正常退出 context 后再传播取消，确保 shutdown hook 执行且 AnyIO task group 清空。直接让取消异常穿过 context 可能跳过清空字段。

只有适配器收到真实 READY 并调用 `on_bot_connect` 后才标记本地运行状态 ready。驱动启动完成不代表机器人登录成功。

NoneBot 默认 SUCCESS 日志记录完整事件正文，DEBUG 日志可能打印配置，异常包含请求/响应或凭据。必须在初始化之前替换 Loguru sink，只保留 WARNING 以上的组件名称、等级摘要；应用另行记录连接状态、scope、格式、字节数、异常类型等结构化状态。不能输出原始 SDK message 或 exception。

所有本地集成验证使用合成事件及 mock QQ API；真实平台权限、审核状态和图片送达需要用户实际发送查询后验证。

## 本地验证结果

- `pytest tests/test_qq.py`：35 项通过，覆盖真正 NoneBot matcher 分发、旧官方事件兼容、非 @ 群消息不处理、JPEG 上传与 base64 HTTP JSON、降级序号、回复配额、并发限制、防重复、隐私日志、取消关闭和 READY 标记。上游适配器 `Media.dict()` 有 2 条 Pydantic 弃用提示；当前序列化通过，项目固定 Pydantic `<3`。
- `python scripts/qq_local_probe.py`：C2C、群聊各走“帮助”文字与“战况”图片，真实 Chromium HTML → JPEG，每张 100,938 字节，合计捕获 4 条被动回复，重复事件未重复回复。QQ 网络调用 0。
- 修改的 QQ 代码、插件、测试与 probe 均通过 Ruff。
- QQ 网络 Worker 首次 90 秒未连接成功则退出并正常关闭 renderer；业务命令总期限 180 秒，最多 4 个执行/32 个排队。业务关闭 hook 在 Adapter 网络关闭前执行。
