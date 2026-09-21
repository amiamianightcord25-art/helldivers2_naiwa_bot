# NapCat / OneBot V11 接入（v0.3.0）

本版本通过 NoneBot2 的 OneBot V11 适配器连接 NapCat。NapCat 保持 QQ 登录并提供正向 WebSocket 服务，Bot 主动连接它，复用现有数据查询、图片、签到、百科和方向小游戏。

## 功能和交互

| 场景 | 行为 |
| --- | --- |
| 好友私聊 | 发送文字指令即可查询 |
| 群聊 | 登录账号加入群后，@该账号并发送指令 |
| 未 @ 的群消息、自己的消息 | 忽略 |
| 好友申请、邀请入群、进群通知 | 不自动审批，也不自动发送欢迎消息 |
| 菜单和后续操作 | 返回文字指令；官方原生面板与按钮不适用于 OneBot |
| 图片回复 | 通过标准 image 段传输图片，跨主机无需共享图片文件路径 |
| 图片渲染失败 | 返回文字备用内容 |
| 发送结果不明 | 不自动重发，避免重复消息 |
| 连接断开 | 适配器尝试重连；持续失联或健康检查连续失败后进程失败退出 |

只回复触发查询的私聊或群聊，不自动群发。群里引用旧回复不能代替 @。每次启动只启用一种接入；新入口没有恢复已下线能力。

## 安装

在项目虚拟环境中安装本项目：

```shell
python -m pip install -e ".[dev]"
python scripts/install_browser.py
```

依赖固定为 NoneBot2 2.5.0 和 nonebot-adapter-onebot 2.4.6。NapCat 单独安装，版本和安装方法以 [NapCat 文档](https://napneko.github.io/) 为准。

## NapCat 设置

1. 在 NapCat 中登录用于机器人的 QQ 账号。
2. 在网络配置中添加并启用 **WebSocket Server（正向 WebSocket）**。此处是 NapCat 提供服务，不是 NapCat 向 Bot 发起反向连接。
3. 使用能同时处理事件和 API 的 WebSocket 服务，消息格式选择 **Array（消息段数组）**，开启心跳（建议 30 秒）。
4. 配置独立的访问令牌，并记录服务地址。Bot 与 NapCat 同机时优先监听回环地址；跨主机使用私有网络或受保护的连接。
5. 需要的群由该 QQ 账号正常加入；群加入权限由 QQ 和群管理设置决定。

本版本仅提供正向 WebSocket 接入，没有配置 HTTP 回调或反向 WebSocket 监听器。

## Bot 私有配置

从 `.env.example` 创建本机 `.env`，自行填写以下字段；分发模板始终为空：

| 字段 | 填写方法 |
| --- | --- |
| `BOT_BACKEND` | 使用 NapCat 时填写 `napcat`；留空使用 `official` |
| `NAPCAT_WS_URL` | 填写 NapCat 正向 WebSocket 地址，使用 `ws` 或 `wss` |
| `NAPCAT_ACCESS_TOKEN` | 与 NapCat 服务配置的令牌一致，必填 |
| `NAPCAT_ALLOWED_GROUPS` | 可选，逗号分隔的允许群号；留空允许已加入群内的 @ 查询 |
| `DATABASE_PATH` | 本地数据库路径，留空使用项目默认 |
| `HD2_RENDER_CONCURRENCY` | 小内存服务器建议设为 `1` |

URL 不携带用户名、密码或令牌查询参数；访问令牌通过 Authorization 请求头发送。NapCat 模式不要求 `QQ_APP_ID`、`QQ_APP_SECRET`。这些官方凭据可以留在已有私有文件中，当前入口不会用它们登录官方平台。

登录状态、QQ 数据目录、NapCat 配置、访问令牌、二维码和 Bot 数据库均不进入 Git。菜单等运营配置仍为本地私有数据。

## 启动与验证

Windows 使用现有启动、关闭和状态脚本。手动运行：

```shell
python run.py
```

日志出现 `event=napcat_ready` 表示已建立 OneBot 连接，并通过 `get_status` 确认 QQ 在线。仅看到进程运行或 WebSocket 连接不视为完成登录。

Linux 继续使用 `hd2bot.service`，将所需变量填入服务器私有环境文件。切换前停止当前实例，安装新依赖后再启动：

```shell
sudo systemctl stop hd2bot.service
sudo /opt/hd2bot/.venv/bin/python -m pip install -e /opt/hd2bot
sudoedit /etc/hd2bot/bot.env
sudo systemctl start hd2bot.service
sudo journalctl -u hd2bot.service -n 30 --no-pager
```

手动验收：好友私聊发送 `帮助`；群里 @登录账号发送 `菜单`、`战况`、`签到`。普通群消息不应得到回复；断开再恢复 NapCat 后，应重新出现 `napcat_ready`。本仓库自动测试只使用本机 OneBot 测试服务，不代表特定 QQ 账号已完成实测。

## 数据与运行边界

- 签到、小游戏和分页状态使用接入类型、登录账号和会话身份隔离。官方 Bot 的 OpenID 不能自动映射为个人 QQ 号；保留旧数据库，新入口独立积累，不按昵称合并。
- 同一群不同成员的分页与小游戏仍独立。可选允许群列表在执行命令前检查。
- 保留消息去重、并发上限和关闭清理；健康检查仅查询 NapCat 在线状态。
- 不在聊天或日志中输出连接地址、令牌、完整原始消息和异常详情。成功回复日志仅记录场景和文字/图片类型。
- NapCat 图片不带旧官方 Bot 的运营二维码。
- 模式切换只需停止服务后更改 `BOT_BACKEND`；保留两种入口的私有配置即可切回。Bot 不管理 NapCat 的 QQ 登录、好友或群权限。

协议参考：[OneBot V11](https://github.com/botuniverse/onebot-11)、[NoneBot OneBot 适配器](https://github.com/nonebot/adapter-onebot)。
