> 历史记录：初版 botpy 验收。下文“当前”“本轮”、测试数量和运行状态均指记录时点，不是现行部署说明。当前使用方法见 [项目 README](../README.md)，文档范围见 [研究索引](README.md)。

# 本地版本验收

> 本文下方为 2026-09-11 初版验收。2026-09-16 已迁移至 NoneBot2、加入 HTML/JPEG、银河新闻/补给线及主动订阅。
> 当前验收请参阅 [V02_ACCEPTANCE.md](V02_ACCEPTANCE.md)，早期 botpy / Webhook 条目仅作历史记录。

日期：2026-09-11；Windows 环境验证。

| 验收项 | 结果与证据 |
| --- | --- |
| 项目、虚拟环境、Git | PASS，Python 3.14.4，声明支持 Python 3.11+ |
| 抓包研究 | PASS，读取已有脱敏分析，整理 Host/Method/Path/Query/Header 名称/字段/认证/刷新证据 |
| 真实官方 API | PASS，动态 WarID、Status、WarInfo、Assignment、Stats 共五类匿名 GET |
| 社区 provider | PASS，war/planets/campaigns/assignments 实测 HTTP 200；联系头按用户指定设置 |
| 自动 fallback | PASS，合成故障覆盖 timeout、403/404 等不可用、schema、429、5xx；不回退为 mock |
| CLI 六类命令 | PASS，战况、主线、星球、进攻、防守、玩家真实查询通过，交互退出码 0 |
| 请求复用 | PASS，完整六类 CLI 查询仅 5 次官方 HTTP，原始采样时间跨命令保留 |
| Mock | PASS，离线六类指令成功，HTTP 请求 0，输出明确标注模拟 |
| 错误恢复 | PASS，用户看不到 traceback，坏指令/网络故障后仍接受后续指令 |
| TTL / stale | PASS，合并并发请求、剩余 TTL、旧缓存窗口、最早采样时间和恢复已测试 |
| QQ SDK 适配器 | PASS，群/C2C 被动回复、分段、签名、去重、关闭和并发边界本地验证 |
| 本地 Webhook 协议 | PASS，真实回环 HTTP → SDK → Router → Mock → 本地回复；未调用 QQ 发消息接口 |
| 真实 QQ 凭据 | PASS，用户凭据仅存于被 Git 忽略的 `.env`；Token API、机器人资料 API 均 HTTP 200 |
| 真实 WebSocket | PASS，沙箱 Gateway API HTTP 200，实际连接收到 READY，最终版本在本地后台运行 |
| 真实 QQ C2C 消息回复 | PASS，01:13:08 收到 C2C 事件，官方战况请求 HTTP 200，01:13:11 成功调用 QQ API 发出 1 段回复 |
| 真实 QQ 群聊回复 | 待人工联调：尚未观察到沙箱群 @机器人事件；客户端展示效果仍以实际 QQ 客户端为准 |
| SQLite | PASS，三表实际初始化，订阅隔离/唯一性/回滚/指纹跨重启验证 |
| 自动化测试 | PASS，261 项 pytest 通过；Ruff、pip check 通过 |
| Secrets | PASS，`.env`/抓包/日志/数据被忽略；当前可提交文件及已有 Git 历史未发现实际 AppSecret |
| 跨平台准备 | PASS，业务路径使用 pathlib、配置使用 .env、源码通过 Python 3.11 语法解析；尚未 Linux 实机验证 |
| 服务器部署 | 当前范围外，未配置 SSH、Docker、HTTPS、反向代理、服务管理或公网回调 |

## 当前运行状态

- 传输：`websocket`；QQ 沙箱：`true`；HD2 数据：`auto`。
- 后台连接 READY 日志时间：2026-09-11 01:08:25 +08:00。
- 真实 C2C 验证：2026-09-11 01:13:08 接收，01:13:11 回复 API 成功，1 段。
- 本地运行信息：`data/runtime.json`；日志：`logs/bot.log`、`logs/console.err.log`。
- 空闲 Python 业务进程工作集约 54 MiB（一次本机观测，不是上限）。
- 查看实际收发时，只记录 `qq_message_received` / `qq_reply_sent` 的场景与分段数，不记录正文、QQ目标标识或凭据。

## 使用与复验

```powershell
# 在项目根目录执行
.venv\Scripts\python run.py --cli
.venv\Scripts\python run.py --cli --provider mock
.venv\Scripts\python -m pytest -q
.venv\Scripts\python scripts/qq_local_probe.py
```

后台机器人已启动时无需重复启动另一个 QQ 实例。单独运行 CLI 不需要停止 QQ。
后续在沙箱测试群 `@机器人 战况` 完成群聊验收；C2C 已观察到真实消息与成功回复 API。

## 实际限制

- 部分未知任务/奖励类型、环境名称或 localization key 保留未知或源文本，不捏造语义。
- 主动订阅推送只准备了数据结构和 service，没有启动调度或主动发消息。
- QQ 事件去重保存在内存，进程重启后重置；当前负载上限为 32 条在途、4 条并行。
- 超出 QQ 被动回复分段额度时会明确提示缩略，不自动改成主动消息。
- SDK 日志正文被抑制以保护动态凭据；诊断保留事件、组件、状态和异常类型。
