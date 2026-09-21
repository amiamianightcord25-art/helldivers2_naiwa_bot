# Linux 服务器部署

支持官方 QQ 与 NapCat 两种入口，默认 BOT_BACKEND=official。NapCat 的 WebSocket、登录状态和 systemd 接入步骤见 [NapCat 配置](NAPCAT_CN.md)。更换入口前先停止当前服务，同一份工作区只运行一个 Bot 实例；账号和连接配置继续放在私有环境文件中。


以下是 Ubuntu 24.04、Python 3.12、2 核 / 2 GB 内存的通用部署方案，不代表每位部署者已有同样环境。机器人由独立 `hd2bot` 用户运行；管理操作先 SSH 登录，再执行 `sudo -i`。

| 内容 | 默认路径 |
| --- | --- |
| 程序与虚拟环境 | `/opt/hd2bot`、`/opt/hd2bot/.venv` |
| 实际环境配置 | `/etc/hd2bot/bot.env` |
| 数据库与动态配置 | `/var/lib/hd2bot/state` |
| 图片浏览器与 Wiki 缓存 | `/var/cache/hd2bot` |
| 应用日志 | `/var/log/hd2bot` |
| systemd 服务 | `hd2bot.service` |

## 新安装

先将公开源码放入 `/opt/hd2bot`，在 root 会话中执行：

```bash
cd /opt/hd2bot
bash deploy/bootstrap_ubuntu.sh
```

脚本安装依赖、字体、Chromium、运行用户、目录及 systemd 服务文件，不创建真实配置、不连接运行目录、不启动服务。接着为全新安装创建空白配置：

```bash
if [ ! -e /etc/hd2bot/bot.env ]; then
  install -o root -g hd2bot -m 640 .env.example /etc/hd2bot/bot.env
fi
```

编辑 `/etc/hd2bot/bot.env`，填写自己的 `QQ_APP_ID`、`QQ_APP_SECRET` 和环境选择。`QQ_SANDBOX` 空白默认 `true`；正式环境显式设为 `false`。`QQ_TRANSPORT` 仅支持 `websocket`。小内存服务器建议渲染并发 1、超时 40 秒；在配置中设置 `HD2_RENDER_CONCURRENCY=1`、`HD2_IMAGE_TIMEOUT=40`。

在程序目录建立以下链接。命令用于全新源码目录；若同名文件或目录已存在，先按下方迁移步骤处理，不覆盖已有运行数据。

```bash
ln -s /etc/hd2bot/bot.env .env
ln -s /var/lib/hd2bot/state data
ln -s /var/log/hd2bot logs
ln -s /var/cache/hd2bot .cache
```

确认 `hd2bot` 能读取配置及代码、写入运行目录，再启动：

```bash
systemctl enable --now hd2bot.service
systemctl status hd2bot.service
sudo -u hd2bot /opt/hd2bot/.venv/bin/python /opt/hd2bot/scripts/bot_control.py status
```

`active` 仅说明服务进程存在；状态脚本显示“已收到 READY”才说明最近成功连接 QQ。真实收发和按钮展示需在应用获准的聊天场景中验证。

## 资源、字体与图片

服务内存软限 1100 MB、硬限 1400 MB、CPU 配额 150%，这些是上限设置，不是预计常驻占用。脚本不创建 swap；根据主机实际情况配置。默认卡片宽度 1200 像素、JPEG 上限 900 KB。应用日志约 2 MB 一份、保留 3 份轮转备份；systemd 标准输出另由 journal 管理。

服务设置 `LimitCORE=0`，不生成进程 core dump。

安装字体为 `Noto Sans CJK SC`、`Noto Sans`、`Noto Color Emoji`。服务通过 `PLAYWRIGHT_BROWSERS_PATH` 使用专用浏览器目录；运行交互式导图命令时也应设置相同变量。Windows 字体回退使用微软雅黑和 Segoe UI Emoji。

公开版没有运营者二维码。可将自己的 PNG 放入运行数据目录并设置 `HD2_QR_IMAGE_PATH`；没有二维码仍可正常渲染，见 [二维码说明](WELCOME_QR_CN.md)。

## 迁移与更新

迁移须先停止旧实例，再复制完整运行数据目录，包含签到、小游戏、订阅、欢迎去重、动态资料、自定义菜单、推送设置、公告及发送状态。SQLite 复制前应正常停机或使用数据库备份方式。复制后检查文件归属、数据库完整性与配置中的路径，再启动新实例；不要用旧主机数据库覆盖新实例运行后产生的数据。


工作站迁出后，可在其 `data/deployment_target.json` 写入本地迁移记录，启动脚本会拒绝再次启动。此文件不提交到仓库，也不复制给新服务器；直接运行 `run.py` 不检查该标记，因此维护时仍须避免双实例。

日常更新先测试并检查待发布文件，再 commit、push；服务器在工作区干净时执行 `git pull --ff-only`，必要时安装新增依赖。运行代码、模板或配置变化后重启并确认 READY；单纯文档更新无需重启。

```bash
sudo systemctl restart hd2bot.service
sudo systemctl status hd2bot.service
sudo journalctl -u hd2bot.service -n 40 --no-pager
```

真实配置、用户数据、缓存及迁移记录始终保留在运行目录，不进入 Git，模板清单见 [空白分发配置](DISTRIBUTION_CN.md)。
