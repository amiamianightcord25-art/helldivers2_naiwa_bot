# HELLDIVERS 2 QQ 机器人

Python 3.11+ 项目，使用 NoneBot2 2.5.0，支持 QQ 官方平台和 NapCat（OneBot V11）两种接入。支持 Windows 开发与 Linux 服务器运行。

v0.3.0 新增 NapCat 私聊和群内 @ 查询。通过私有配置中的 BOT_BACKEND 选择 official 或 napcat，默认保留官方接入。NapCat 连接登录账号已加入的群，菜单使用文字指令。安装与接入步骤见 [NapCat 配置](NAPCAT_CN.md)。
CLI、NoneBot 命令和 HTML 图片共用同一数据核心。当前版本 0.3.0。
当前使用说明以本 README 和根目录专题文档为准；`research/` 保存注明日期的研究与验收证据，阅读方式见 [研究记录索引](research/README.md)。

服务器安装、字体与 emoji、运行参数及 systemd 管理见 [Linux 部署说明](DEPLOYMENT_CN.md)。机器人使用出站 WebSocket，无需公网 Webhook。

新增签到升级、四场景原生快捷指令与战备英雄小游戏。发送 `菜单`、`签到`、`战备英雄` 即可使用；完整规则与官方菜单配置见 [签到与小游戏说明](COMMUNITY_FEATURES_CN.md)。

发送 `小贴士` 随机查看游戏加载提示，或 `小贴士 <编号>` 指定一条，支持“再来一条”按钮。

本项目源代码公开于 [GitHub](https://github.com/amiamianightcord25-art/helldivers2_naiwa_bot)，机器人内发送 `开源项目` 或 `源码` 可获取地址。分发版本不包含运营者配置、二维码或用户数据库；先按示例填写自己的配置。提交检查使用 Gitleaks，启用钩子：`git config core.hooksPath .githooks`。

## 一键启动、关闭和查看状态（Windows）

虚拟环境和 `.env` 已配置后，直接双击项目根目录中的脚本：

| 脚本 | 作用 |
| --- | --- |
| `start_bot.cmd` | 后台启动机器人，等待 QQ READY；已有实例时显示状态，不重复启动 |
| `stop_bot.cmd` | 请求正常退出，等待 QQ、HTTP、数据库清理完成 |
| `status_bot.cmd` | 查看是否运行、业务进程 PID、传输模式和最近 READY 时间 |

窗口显示操作结果后按任意键关闭；关闭这个结果窗口不会停止后台机器人。
默认沿用 `.env` 的 WebSocket / 沙箱配置。重启时先关闭再启动。
在 PowerShell 中可避免脚本末尾暂停：

```powershell
.\start_bot.cmd --no-pause
.\status_bot.cmd --no-pause
.\stop_bot.cmd --no-pause
```

脚本按自身所在目录定位项目，从其他目录调用也可以。启动调用项目 `.venv`，
不传递账号凭据作为命令行参数。所有 `run.py` QQ 入口共用系统文件锁，CLI 可以独立并行使用。
运行状态位于 `DATABASE_PATH` 同目录的 `runtime.json`，锁为 `bot.lock`。
停止请求绑定每次运行的随机实例标识，旧 PID / 停止文件不会误关其他程序。

日志在可配置 `LOG_DIR` 内：`bot.log` 自动轮转，`console.out.log` 和 `console.err.log` 追加保留。
READY 表示最近成功建立连接，状态脚本不向 QQ 发送探测消息。
脚本默认等待 45 秒；若还未 READY 会提示检查状态，机器人启动后 90 秒仍无法连接则退出。
关闭超时不会按 PID 强制杀进程。
需要调整等待时间可运行 `.venv\Scripts\python scripts\bot_control.py start --timeout 90`，
`stop` 同样支持 `--timeout`。这些 Windows 脚本不安装开机自启；Linux 的 systemd 方案见部署说明。迁出工作站的本地标记会阻止启动脚本再开实例。

## Windows 本地环境

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python scripts/install_browser.py
Copy-Item .env.example .env
.venv\Scripts\python run.py --cli
.venv\Scripts\python -m pytest
```

依赖已安装时，不要重复覆盖自己的 `.env`。也可先执行
`.venv\Scripts\Activate.ps1`，再输入 `python run.py --cli`。

## CLI 与数据模式

```powershell
# 真实数据，优先官方公开接口，失败尝试社区
.venv\Scripts\python run.py --cli

# 不联网、不需要 QQ 凭据的模拟演习
.venv\Scripts\python run.py --cli --provider mock

# 执行单条指令（同样无需 QQ 凭据）
.venv\Scripts\python run.py --command "星球 Meridia"
```

进入后可输入：

```text
HD2> 战况
HD2> 主线
HD2> 星球 Meridia
HD2> 星球 天使之愿
HD2> 进攻
HD2> 防守
HD2> 玩家
HD2> 新闻
HD2> 新闻 列表 2
HD2> 新闻 12345
HD2> 补给线 天使之愿
HD2> 看板
HD2> 星图
HD2> 星图 Meridia
HD2> 战线 机器人
HD2> 控制中心
HD2> DSS
HD2> 公告
HD2> 区域
HD2> 特殊部队
HD2> 更新
HD2> 补丁
HD2> Steam在线
HD2> 百科
HD2> 武器
HD2> 武器 解放者
HD2> 武器 喷子
HD2> 战争债券
HD2> 战争债券 铁血老兵
HD2> 选择 2
HD2> 帮助
HD2> 退出
```

命令可带 `/`，星球支持中英文、别名、编号、子串与相似候选。
回复显示真实采样时间与数据源；模拟数据明确标注。API 故障时继续接受输入。
有旧缓存时会显示“数据暂时无法更新，以下为最近缓存”，超过保留期限不返回。

`.env` 中 `HD2_PROVIDER` 支持 `auto`、`captured`、`community`、`mock`。
`captured` 在此项目中只使用已经匿名验证的官方公共 GET，不重放游戏客户端会话。
`auto` 不会把网络故障伪装成模拟数据。社区联系头默认为占位值 `example@example.com`，部署时通过 `HD2_SUPER_CONTACT` 填写自己的联系信息。

缓存默认：战况/星球/战役 20 秒，主线/统计 30 秒，静态信息 6 小时，旧缓存再保留 15 分钟。
同资源并发请求合并，跨命令保留原始采样时间。429 遵守 Retry-After，长等待直接降级。

## 数据来源与边界

| Provider | 使用的公开 endpoint |
| --- | --- |
| 官方公开 | `/api/WarSeason/current/WarID` |
| 官方公开 | `/api/WarSeason/{warId}/Status`、`/WarInfo` |
| 官方公开 | `/api/Stats/war/{warId}/summary` |
| 官方公开 | `/api/v2/Assignment/War/{warId}` |
| 官方公开 | `/api/NewsFeed/{warId}`，最近一周窗口，返回后支持最近 25 条分页与按 ID 查看 |
| 社区 | `/api/v1/war`、`/planets`、`/campaigns`、`/assignments` |
| 社区新闻 | `/api/v2/dispatches` |

官方接口以星球编号和战争相对秒数返回状态；社区补齐名称/环境并改为日期时间字符串。
本项目统一模型并用离线名称目录支持中文/英文搜索。详见
[API 研究](research/API_NOTES.md)与[静态目录来源和许可](research/DATA_SOURCES.md)。

防守进度使用事件自身血量；regen 是源站 HP/秒恢复量，不是每小时净解放速度。
未知枚举、未本地化文本、缺失字段保留为未知，不捏造数字。

## 图片回复与文字回复

| 查询 | QQ 回复方式 |
| --- | --- |
| 战况 | 战区、在线人数、银河公告卡，JPEG |
| 星图 / 星图 <星球> | 银河总览或局部补给星图，JPEG；搜索失败/坐标缺失用文字 |
| 星球 <名称> | 星球详情卡，JPEG；查不到或候选列表用文字 |
| 进攻 | 至少 5 个进攻战役时用 JPEG，短列表用文字 |
| 主线 / 防守 / 补给线 | 较长结果用 JPEG，简短或空结果用文字 |
| 新闻 / 战报 | 最近 25 条分页或按 ID 查询；较长正文用 JPEG，短结果用文字 |
| 玩家 / 帮助 / 错误提示 | 文字 |
| DSS / DSS票数 | 空间站或迁移投票图卡；无有效数据时用文字 |
| 战线 / 控制中心 / 公告 / 区域 / 特殊部队 | 较长报告用 JPEG，简短或空结果用文字 |
| 装饰详情 / 装饰候选列表 | 图卡；其他百科列表通常用文字 |
| 更新 / 补丁 / Steam在线 | 列表、在线人数用文字；按新闻 ID 查询长正文可生成 JPEG |

生成流程为 Jinja2 HTML 模板 → Playwright 无头 Chromium 截图 → Pillow JPEG 压缩。
中间截图仅在内存保留，最终采用 RGB、渐进 JPEG、优化编码。默认宽 1200 像素、质量 82、
上限 900 KB；必要时降低质量/等比缩小，超长或无法保持可读性时退回文字，不截掉表格行。
浏览器仅在第一次需要图片时启动，复用单浏览器，每张图独立上下文，默认最多并发 2 张，`HD2_RENDER_CONCURRENCY` 可设 1～4，小内存服务器建议 1。
查询内容 HTML 自动转义，页面不执行脚本、不加载远程图片/字体。浏览器缓存放在项目 `.cache`。

渲染、上传失败或平台明确拒绝图片时回退文字；发送结果不明时不盲目补发，避免重复回复。上传使用 QQ 官方富媒体接口的 Base64 文件数据，
`srv_send_msg=false`，随后携带原始消息 ID 被动发送；不提供公网图片服务器。
真实发送仍受 QQ 应用权限限制，代码不会用个人 QQ 协议替代。

`.env` 可设置：

```dotenv
HD2_IMAGE_ENABLED=true
HD2_IMAGE_WIDTH=1200
HD2_IMAGE_QUALITY=82
HD2_IMAGE_MAX_BYTES=900000
HD2_IMAGE_TIMEOUT=20
HD2_IMAGE_BROWSER_PATH=
HD2_IMAGE_BROWSER_CHANNEL=
```

关闭图片可设 `HD2_IMAGE_ENABLED=false`。浏览器缺失时也会自动返回文字。
默认浏览器由 `scripts/install_browser.py` 安装；也可指定现有浏览器可执行文件，
或 `HD2_IMAGE_BROWSER_CHANNEL=chrome` / `msedge`。参数改变后重启机器人。
CLI 继续直接显示文字；需要本地导出图片时：

```powershell
.venv\Scripts\python scripts/render_query.py 战况 --output tmp/war.jpg
```

模板参考 Tenko 历史版的信息组织方式，采用本项目重新编写的 HTML/CSS，未复制游戏图片或字体。
历史 BF1 成品使用 Pillow，HTML 草稿与通用 HTML 渲染工具分别存在，详见
[Tenko 历史核对](research/TENKO_RENDER_NOTES.md)。

## 银河新闻与补给线

借鉴 Galactic-Wide-Web 的公开功能：

- `新闻` 或 `战报`：最近 25 条新闻分页，支持 `新闻 <战报ID>` 查看单条详情；保留正文和发布时间。官方战争相对时间按同次 Status 采样换算。
- `补给线 <中文名/英文名/编号>`：查询正向、反向 waypoint 相邻关系、控制方和在线人数。
  数据没有相邻连接时会明确提示；联通不代表当前可以进攻或部署。

新闻也支持显式订阅后的新增消息通知。实现及来源见 [GWW 功能参考](research/GWW_FEATURE_NOTES.md)。

## 星图与银河情报

继续参考 Galactic-Wide-Web 的公开查询功能，命令沿用 QQ 官方入口和本地 CLI：

| 命令 | 内容 |
| --- | --- |
| `星图` / `地图` | 银河坐标、阵营颜色、活跃战役、防守、明确主线目标和补给连接 |
| `星图 <中文名/英文名/编号>` | 以指定星球为中心的局部图，包含正反向连接的邻星 |
| `战线 [虫族/机器人/光能族]` | 阵营进攻、防守、紧急解放、战区人数及其他控制星球 |
| `控制中心 [战役编号]` | 战役目录、当前/最近战役、目标阶段、结果和奖励；编号查询完整阶段 |
| `DSS` / `空间站` | 民主空间站位置、战术行动状态、期限、公开物资筹备进度和公开迁移票数 |
| `DSS票数` | 读取当前空间站 `currentElectionId`，查询公开 ElectionV2 选项、票数和百分比 |
| `公告 [事件编号]` / `全球事件` | 完整全球事件正文、关联星球和主要指令、结束时间 |
| `区域 [星球名称/编号]` | 当前公开星球区域的控制方、可进入状态、在线人数和解放进度 |
| `特殊部队` | 按公开战场效果识别的特殊部队和所在星球；不估算兵力 |
| `更新 [新闻ID]` | 最新三条 Steam 官方公告，或最近 50 条范围内的公告全文 |
| `补丁 [新闻ID]` | 按官方 patchnotes 标签或明确补丁标题筛选；指定 ID 查看正文 |
| `Steam在线` | 仅 Steam 平台的实时在线人数，与“玩家”的全平台数据区分 |
| `看板` / `战略看板` / `dashboard` | 聚合战争状态、主线、重点进攻/防守、DSS 和银河事件；可选资源失败时保留其余内容 |

兼容 `/map`、`/planet Meridia`、`/warfront Automaton`、`/control_centre`、`/dss`、
`/global_events`、`/subfaction`、`/dispatches`、`/major_order`、`/steam` 等英文名称。
使用空格文本参数，不接受 Discord 的 `planet:`、`public:` 参数语法。

星图由本地 HTML 内嵌 SVG 绘制，经现有 Chromium 流程压缩为 JPEG。
默认宽 1200 像素、最多 900 KB；编号优先标出最多 12 个主线/防守/热门星球，下方列全名。
地图保持原始坐标方向与比例，局部图自动包含相邻连接；无效或缺失坐标不补造。
线条只代表 waypoint 相邻关系，不代表进攻方向或可部署。没有精确边界资料时不绘制星区领土，
不复制上游底图、游戏图标或特殊事件素材。数据来源、采样时间、旧缓存和模拟标识会随图显示。

五类新增游戏资源（DSS、控制中心、全球事件、区域、特殊部队）由官方公共 API 提供；
社区 provider 暂不提供对应资源，`auto` 也不会把上游故障伪装成 mock。
Steam 使用独立的公开只读 API 和缓存，不需要 Steam 登录，中文介绍之外保留上游实际原文。
控制中心的部分正文上游只返回英文。所有新命令在 `--provider mock` 中可离线验证。

个人指令和超级商店在参考项目中依赖额外数据服务，当前没有接入其数据源；DSS 票数已通过公开 ElectionV2 接口接入；
`战争债券` 已使用本地 Wiki 快照提供静态目录、费用、页码和关联装备，但不表示实时轮换或账号解锁进度。
其余兼容命令会明确说明不可用。Discord 社区服务器目录、频道管理面板、原生组件及原消息自动刷新，
以及九语言翻译维护功能未移植。QQ 版已有自己的分类菜单、原生指令面板和消息按钮，见 [社区功能说明](COMMUNITY_FEATURES_CN.md)。
逐项覆盖和具体边界见 [GWW 功能对照](research/GWW_PARITY.md)。

## 装备百科与 Wiki 定期同步

`百科` 显示武器、战略配备、盔甲、强化资源、装饰五类入口。资料存储在本地，普通查询不访问 Wiki。
当前快照收录 **782 条**：武器 137、战略配备 114、盔甲 109、强化资源 18、装饰 404；
137 条武器均已配本地图片。数量会随 Wiki 同步变化。

| 用法 | 行为 |
| --- | --- |
| `武器` / `武器 列表` | 每页 8 条，展示原名、型号和可选序号，让用户先认识装备名称 |
| `武器 分类` / `武器 突击步枪` | 查看子类或按装备类型浏览 |
| `武器 AR23` / `武器 解放者` / `武器 Liberator` | 按型号、中英文名字、别名查询；可省略型号中的连字符 |
| `武器 AR23 详参` | 详细伤害、耐久伤害、弹道、散布、后坐力、热量等参数，保留弹体/爆炸/状态分项 |
| `武器 AR23 配件` | 配件名称、武器等级要求、解锁花费与效果 |
| `武器 喷子` / `武器 breakr` | 返回多项或拼写近似候选；不悄悄选错武器 |
| `选择 2` / `2` | 选择当前页第 2 项；`下一页` / `上一页` 翻页 |
| `战略配备 飞鹰` / `盔甲 重甲` / `强化资源` / `装饰 披风` | 其他分类使用同样的列表与搜索方式 |
| `战争债券` / `战争债券 铁血老兵` | 浏览本地静态债券目录、费用、页数和关联装备；支持分页及序号选择 |
| `百科 <名称或资料ID>` | 跨分类检索；可直接复制资料 ID 精确定位 |
| `资料状态` | 查看收录数、来源、资料快照时间 |

忽略名称中的大小写、全半角、空格、标点，识别维护过的中文社区别名与部分名。
基础型号 `AR23` / `AR-23` 返回同系列候选，具体变体如 `AR-23P` 可直达；缩写、不完整型号、近似拼写和重名需要选择确认。
候选 5 分钟有效，同群每位用户独立；群聊回复序号同样需按平台规则 @机器人。
资料更新或机器人重启后旧候选失效，重新查询即可。

武器详情带 Wiki 原图的本地副本、公开属性、简介和出处，用 HTML→JPEG 输出。装饰详情与纯装饰候选列表也用图卡；其他目录和候选通常用文字，图片不可用时保留文字。
默认卡片先展示单件解锁费用、获取途径、战争债券费用、页码和可确认的累计奖章门槛。
累计门槛表示需先在债券中花费的奖章，不与单件兑换费用或超级货币混为同一标价。
137 件武器均记录费用或初始/赠送/付费版本/现场拾取性质；AR-11 原页面未公布价格，显示未知。
132 件有详细统计，43 件有配件表，缺表时明确说明。补全来自 Wiki 原文，并随每日同步更新。
图片读取/渲染/上传失败时退回文字。优先采用中文 Wiki 已有的装备名与内容；英文站补缺属性和说明通过维护的中文词表/译文展示，英文原名继续供检索与核查。
未公开的属性不补造。修订号、资料 ID、译名说明和 Wiki 过时标记保留在本地资料与状态页，
查询卡片只保留简短来源与日期。武器卡使用较宽的双列，文字按实际字体宽度选择同行或上下排版，
型号、数值单位和短列表项保持完整；长正文及来源链接允许正常换行。

QQ 模式默认每天同步，使用限速、HTTP 条件请求、缓存和原子替换；失败保留上一份完整资料，
约一小时后再试。CLI / `--provider mock` 不启动后台同步。首次运行自带快照，电脑休眠、关闭或
机器人停止期间不更新。以下展示通用默认值，并非分发模板中已经填写的值：

```dotenv
HD2_WIKI_CATALOG_PATH=data/wiki_catalog.json
HD2_WIKI_SYNC_ENABLED=true
HD2_WIKI_SYNC_INTERVAL_HOURS=24
```

图片位于快照旁的 `wiki_images/`，日志 `logs/wiki-sync.log`，HTTP 缓存 `.cache/wiki/`。
关闭同步只需设 `HD2_WIKI_SYNC_ENABLED=false`；仍可离线查询。手动同步：

```powershell
.venv\Scripts\python scripts/sync_wiki_catalog.py --output data/wiki_catalog.json --cache-dir .cache/wiki --image-dir data/wiki_images --refresh
```

人工别名文件为 `src/hd2bot/assets/wiki_aliases.json`；中文术语和说明译文为 `wiki_zh_terms.json`、`wiki_zh_texts.json`，同步不会覆盖这些维护文件。未来新说明尚无对应中文译文时会明确提示待整理。
英文 Wiki 资料及对应译文保留 CC BY-NC-SA 4.0；中文 Wiki 原文保留 CC BY-SA 4.0，分别标明来源。游戏图片原作权利另行保留。
资料范围、同步边界和维护细节见 [Wiki 资料说明](research/WIKI_CATALOG_NOTES.md)。

## 主动消息

主动消息、订阅和版本公告当前下线。Bot 运行入口不会启动主动通知任务，也不会创建或发送主动推送。

## QQ 官方平台接入

本节用于 BOT_BACKEND=official；NapCat 无需填写官方应用凭据，使用 [NapCat 配置](NAPCAT_CN.md)。

在 `.env` 本地填写：

```dotenv
QQ_APP_ID=
QQ_APP_SECRET=
QQ_SANDBOX=true
QQ_TRANSPORT=websocket
```

AppID/AppSecret 从 QQ 机器人开放平台应用管理处取得，不是个人 QQ 账号或登录密码。
通过平台配置相应群聊/C2C事件、沙箱成员与权限；群聊消息以平台允许的 @机器人事件为准。
配置好凭据后运行 `.venv\Scripts\python run.py`。

本版本已经用 NoneBot2 的真实 matcher 和 `nonebot-adapter-qq` 完全替代 botpy。
事件路径为官方 QQ Gateway → NoneBot matcher → CommandRouter.respond → 数据核心 → 文字或图片。
接收普通 QQ 私聊、群 @、文字子频道 @ 和频道私信；保持 WebSocket 出站连接，不需公网回调或监听 HTTP 端口。主动订阅只支持群和已绑定的普通 QQ 私聊，频道查询能力不等于频道推送能力。
当前只支持 `QQ_TRANSPORT=websocket`，旧 Webhook 实现已删除，不会启动备用旧框架。
平台控制台中的实际权限优先；`.env` 中的 `QQ_LISTEN_HOST/PORT/CALLBACK_PATH` 为遗留兼容字段，当前入口不会据此启动 HTTP 服务。

`QQ_SANDBOX` 留空默认沙箱 `true`；生产部署须显式设置 `false`。公开文档不指定某个运营者当前的环境。READY 和离线测试不代表所有客户端场景均已实测送达。
新适配器接口、事件兼容和版本差异见 [NoneBot2 迁移说明](research/NONEBOT_MIGRATION_NOTES.md)。
早期 botpy 验收笔记仅作为历史记录保留。

本地 NoneBot 事件与 HTML/JPEG 复验（合成凭据和模拟 QQ API，不连接 QQ）：

```powershell
.venv\Scripts\python scripts/qq_local_probe.py
```

适配器限制每段 1800 UTF-8 字节，群最多 5 段、C2C 最多 4 段；超限明确提示缩略。
普通查询使用原始消息 ID 被动回复并去重；游戏状态只按显式订阅主动发送，版本更新日志按上述目标条件发送。
并发处理最多 4 个，内存中最多等待/处理 32 条，超出记录繁忙并丢弃。重启会清空内存去重。

## 可移植性

Linux 本地命令等价于：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/install_browser.py
cp .env.example .env
.venv/bin/python run.py --cli
```

所有业务路径使用 pathlib，相对目录按项目根目录解析，可设置 `DATABASE_PATH` 和 `LOG_DIR`。
代码不依赖注册表、GUI 或 Windows 业务绝对路径；服务器部署脚本位于 `deploy/`。
HTTP 客户端可遵循环境代理，TLS 验证保持开启。
Linux 图片渲染还需 Chromium 系统运行库和中文字体（例如 Noto CJK）；缺少时文字查询仍可用。

## 本地存储

CLI 或 QQ 正常启动时自动初始化 `DATABASE_PATH`（默认 `data/bot.db`）：

| 表 | 当前用途 |
| --- | --- |
| `subscriptions` | 保存群和普通私聊订阅；发送前检查群开关或指定用户绑定 |
| `bot_settings` | 本地设置、推送配对、群开关及公告发送状态 |
| `checkin_profiles` / `checkin_members` | 签到经验、称号所需等级和会话成员记录 |
| `stratagem_hero_sessions` / `stratagem_hero_records` | 小游戏进度及历史最高分 |
| `notification_state` | 通知事件指纹和上次成功通知时间 |
| `notification_deliveries` | 按目标记录成功推送时间，用于限额与最小间隔 |

核心订阅库从 v1/v2/v3 迁移到 v4；签到和小游戏表由各自服务按需创建。欢迎去重另存于运行数据目录中的数据库。群与 C2C 目标隔离，同一订阅不能重复创建。
基线与成功通知记录分离，仅平台接口成功返回后才推进成功指纹、次数额度和下次发送时间。
正常重启会保留订阅、基线、暂停原因和额度。发送成功但来不及写库即崩溃的极端窗口仍可能重复一次。

## 测试与复验

```powershell
.venv\Scripts\python -m pytest -q
.venv\Scripts\python -m ruff check src tests scripts run.py
.venv\Scripts\python -m pip check
# 手动真实请求检查，不属于 pytest
.venv\Scripts\python scripts/smoke.py --provider auto
```

测试临时文件位于 `.pytest-tmp`。HTTP 单元测试使用 mock，不访问游戏/QQ 服务。
`requirements.lock.txt` 记录本机验证过的版本；精确复现可先安装此文件，再安装项目。
`.env`、原始抓包、数据库和日志不进入 Git。

## 进度

里程碑与实际验证记录见 [research/MILESTONES.md](research/MILESTONES.md)。
上述历史记录中的测试数量和部署状态只代表对应日期；当前回归结果查看仓库 Checks，具体部署的收发与权限单独验证。

## 当前目录

```text
Helldivers2QQBot/
├── run.py
├── src/hd2bot/
│   ├── main.py, config.py, logging_setup.py
│   ├── application.py, cli.py, router.py, formatter.py, presentation.py
│   ├── commands/                 # 解析与星球搜索
│   ├── hd2/                      # 模型、服务、HTTP、数据来源
│   │   └── providers/            # captured / community / mock / fallback
│   ├── qq/                       # NoneBot2官方QQ适配器、matcher、消息边界
│   ├── rendering/                # HTML模板、Chromium渲染、JPEG压缩
│   ├── intelligence.py           # 新闻与补给线格式化
│   ├── services/                 # cache / localization / subscriptions
│   ├── storage/                  # SQLite
│   └── assets/planets.json        # 离线中文/英文名称目录
├── tests/
├── scripts/smoke.py
├── scripts/qq_local_probe.py
├── research/                     # API / SDK / 验收 / 来源许可
│   └── private/                  # 新抓包放这里，不提交 Git
├── data/bot.db                   # 本地生成，不提交 Git
├── logs/                         # 本地日志，不提交 Git
├── tmp/, .cache/, .venv/          # 本地文件，不提交 Git
├── .env                          # 本地凭据，不提交 Git
├── .env.example, .gitignore
└── pyproject.toml, requirements.txt, requirements.lock.txt
```
