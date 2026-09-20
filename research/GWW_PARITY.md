# Galactic Wide Web 功能对照矩阵

上游参考快照核对日期：2026-09-16；本项目功能状态修订：2026-09-20。基准为公开上游 README 与固定提交源码。本文覆盖 README 命令表、示例中的 `/help` 和介绍段落中的通知、界面、多语言能力；“覆盖”不表示与 Discord 版逐项完全等价。

本轮代码与命令接线已完成，自动化集成验证通过；真实公共 API 查询及星图/长报告 JPEG 已复验。
下表的“已实现”指代码、离线集成和数据查询，不等于所有命令均已由真实 QQ 客户端逐项触发；
群聊和新增主动通知的实际送达仍以平台权限和后续回包为准。完整验收见 [V02_ACCEPTANCE.md](V02_ACCEPTANCE.md)。

## README 命令逐项对照

| GWW 功能 | 本项目入口 | 状态 | 当前内容与边界 |
| --- | --- | --- | --- |
| `/check_missing_translations` | 无 | 未移植 | 上游翻译文件维护工具。当前项目面向简体中文，不维护其九语言翻译目录或开发者检查命令。 |
| `/community_servers` | 无 | 不适用当前 QQ 范围 | 上游 Discord 社区服务器目录不等于 QQ 群目录；未复制邀请列表、推广内容或外部社区注册机制。 |
| `/control_centre` | `控制中心 [战役编号]`；`/control_centre` | 已实现 | 使用公开 Episode 数据列出战役目录、当前/最近战役、阶段目标和状态、战役及阶段奖励；指定编号查该战役完整阶段。保留上游实际提供的英文正文，不假造中文翻译。没有复制上游图像、按钮与下拉组件。 |
| `/dispatches` | `新闻` / `战报`；`/dispatches` | 已实现 | 最近 25 条分页、每页 5 条，支持按新闻 ID 查看详情；没有复制 Discord 下拉组件。 |
| `/dss` | `DSS` / `空间站`；`/dss` | 已实现 | 公开空间站位置、战术行动、筹备进度和迁移选举票数；未知状态不猜测含义。 |
| `/dss_votes` | `DSS票数`；`/dss_votes` | 已实现 | 从公开 SpaceStation 的 `currentElectionId` 动态请求公开 `ElectionV2/{warId}/{electionId}`，显示选项、票数和百分比；`订阅 DSS` 可推送票数变化。 |
| `/global_events` | `公告 [事件编号]` / `全球事件`；`/global_events` | 已实现 | 公开全球事件标题、正文、所属阵营、结束时间、关联星球/指令和效果编号。与游戏新闻和 Steam 公告分别查询；没有可靠说明的效果不自行补写数值效果。 |
| `/help` | `帮助`；`/help` | 已实现 | QQ 中文命令清单和示例。不是 Discord 的交互式逐命令帮助菜单。 |
| `/major_order` | `主线`；`/major_order` | 已实现 | 主要指令、期限、任务目标、进度、奖励及可解析星球目标。未知任务类型保留已知信息；星图只标记可明确解析的星球目标，不声称覆盖所有全阵营/星区条件。 |
| `/map` | `星图 [星球名称或编号]` / `地图`；`/map` | 已实现 | 银河总览与选定星球局部图。公开坐标投影、控制方颜色、活跃战役、防守、明确主线目标、不可部署状态、无向补给连接；最多 12 个优先星球编号对应下方详情。缺失或无效坐标明确说明，不补造位置。HTML 内嵌 SVG 经 Chromium 输出 JPEG；是查询时快照，没有 Discord 交互地图或消息内实时刷新。 |
| `/personal_order` | `个人指令`；`/personal_order` | 缺少授权数据，未实现 | 上游依赖私有认证服务；命令只说明数据源尚未接入。 |
| `/planet` | `星球 <中文/英文名称/编号>`；`/planet` | 已实现 | 星球控制方、部署状态、在线、解放/防守进度、环境等公开数据；`补给线 <星球>`补充相邻连接；`区域 [星球]`查询公开区域状态。搜索歧义会返回候选，不随意选星球。 |
| `/setup` | `开启推送`、`绑定推送 <一次性码>`、`订阅 …` | QQ 本地实现 | 群由管理员显式开启；普通私聊由指定用户配对；文字子频道和频道私信暂不支持主动订阅。 |
| `/steam` | `更新 [新闻ID]`、`补丁 [新闻ID]`、`Steam在线`；`/steam` | 已实现 | Steam appid 553850 官方公告最新三条、最近 50 条内按新闻 ID 查详情、补丁筛选及 Steam 在线人数。补丁依据 `patchnotes` 标签或明确补丁标题识别，普通新闻不冒称补丁；在线人数只代表 Steam。列表/短正文用文字，长正文可以生成 HTML/JPEG 卡片。 |
| `/subfaction` | `特殊部队`；`/subfaction` | 已实现 | 依据公开星球战场效果和已核对的效果/资源标识，列出特殊部队名称、所在星球、效果编号；无法识别的相关效果保留未知编号。不是实时兵力、单位数量或战力估算；没有原版图片/下拉选择器。 |
| `/superstore` | `超级商店`；`/superstore` | 缺少授权数据，未实现查询 | 上游使用私有商店索引/轮换服务。本项目未持有所需数据源凭据，命令不返回虚构库存、价格或轮换倒计时。 |
| `/warbonds` | `战争债券 [名称]`；`/warbonds` | 已实现静态目录 | 使用本地 Wiki 快照显示费用、页码和关联装备；不提供账号解锁进度或实时商店轮换。 |
| `/warfront` | `战线 [虫族/机器人/光能族]`；`/warfront` | 已实现 | 汇总阵营控制星球数量、进攻/防守战区和在线人数，逐战区显示状态及明确主线标记；未过期的紧急解放优先并附期限，其他非战区控制星球也完整列出。没有移植基于额外规则的 gambit 战略推断。 |

英文别名只替换命令名称，参数使用本项目的空格文本形式；例如 `/planet Meridia`，不是 Discord 参数格式 `/planet planet:124-BORE ROCK public:Yes`。私有数据命令的兼容回复不是功能已实现的证据。

## README 通知与看板能力

| README 提到的能力 | 本项目对应 | 状态和限制 |
| --- | --- | --- |
| 每 15 分钟自动刷新战略看板 | `战况`、`星图`、`订阅 战况 [分钟]` | 查询快照与定时推送已实现。不会每 15 分钟编辑同一条 Discord 看板消息；当前订阅间隔为 30～1440 分钟，并受单目标发送上限限制。 |
| Major Orders 更新 | `订阅 主线` | 已实现。首次建立基线；后续比较指令身份和目标达成状态，不因每次百分比跳动广播。 |
| Dispatches | `订阅 新闻` | 已实现。按新增新闻 ID 通知，内容编辑或回退到旧 ID 不反复广播。 |
| Global Events | `订阅 公告` | 已实现。关注新增、结束/撤下的公开事件；普通正文编辑不会制造新公告通知。 |
| DSS movements, Tactical Action and vote updates | `订阅 DSS` | 已实现。关注驻留星球、战术行动状态、选举轮换、领先目标和投票占比 5% 档位变化，不将微小票数或筹备计数每次增长都广播。 |
| Planetary Region changes | `区域 [星球]`、`订阅 区域` | 已实现。查询全部或指定星球的区域；订阅只关注区域出现/结束、已知控制方/可进入状态变化，人数和每次血量变化不触发。 |
| Campaign wins and losses | `订阅 战役`、`进攻`、`防守`、`控制中心` | 战役/控制方变化通知已实现。战役从列表消失只能报告“结束或撤下”，不据此推断胜负；控制中心只显示 Episode 公开状态。没有把推测结果包装成官方战役胜负历史。 |
| Steam patch notes | `更新` / `补丁`、`订阅 补丁` | 已实现。仅官方补丁新增触发；首次建立基线，普通公告、旧补丁和正文编辑均不推送。Steam GID 不按大小假定发布时间。 |
| 星球状态/防守变化 | `订阅 星球 <名称>` / `订阅 防守` | 本项目已有对应订阅，本轮保留并整合；与区域级通知分别订阅。 |

群和普通私聊分别检查群开关与配对记录；默认每目标滚动 24 小时 5 条、间隔 5 分钟，群可单独设置。群公告要求至少一个启用订阅，已配对普通私聊可没有游戏订阅。具体条件见 [推送说明](../GROUP_PUSH_CN.md)。离线集成不等于真实 QQ 客户端已送达。

## 平台与呈现方式

| 上游形态 | 本项目处理 |
| --- | --- |
| Discord / Disnake；服务器安装、用户安装、频道管理员配置 | 使用 NoneBot2 和官方 QQ 适配器。保留官方机器人，不使用个人号协议；用户/群/消息身份由 QQ 适配层提供。 |
| Slash Commands、按钮、下拉菜单、富嵌入、仅调用者可见回复 | 以中文命令、英文别名、文字、JPEG 卡片、QQ 原生面板和消息按钮实现查询。未承诺 Discord 组件和 ephemeral 语义在 QQ 中同样存在。 |
| PostgreSQL 设置库 | 当前使用 SQLite 保存本项目的绑定、订阅、通知去重和发送状态，不迁入上游数据库。 |
| Pillow / OpenCV 星图 | 独立编写 Jinja 模板内的 SVG 星图，用现有 Chromium 渲染为 JPEG；不引入 OpenCV，不复制上游地图底图、图标、字体或星球装饰素材。 |
| 英/法/德/意/葡萄牙巴西/俄/西/繁体中文/土耳其语 | 当前简体中文界面及英文命令别名；必要时展示上游英文原文。未移植九语言目录、自动翻译或翻译完整性检查。 |
| 邀请链接、支持服务器、贡献/捐赠入口、部署规模徽章 | 属于上游社区运营内容，不作为本 QQ 机器人的功能或部署成绩引用。 |

复杂星图和较长多字段报告可以使用 HTML/JPEG；帮助、错误、短查询、列表、无数据提示等保留文字。JPEG 走已有大小上限、质量调整和失败回退文字路径，文字结果仍是完整备用入口；长正文超出明确大小保护时会提示去官方原文查看。

## 本轮读取的数据与实现依据

| 来源 | 用途 | 认证/边界 |
| --- | --- | --- |
| Arrowhead 公开 `WarSeason/current/WarID`、`WarInfo`、`Status` | 战局、星球坐标/waypoints、战役、全球事件、区域和星球战场效果 | 只读公共数据；不回放抓包中的游戏账号认证头。原始字段不足时显示未知，不补造数据。 |
| 公开 `v2/Assignment/War/{war_id}` | 主要指令 | 不能据此提供所有人的 Personal Orders。 |
| 公开 `NewsFeed/{war_id}` | 银河新闻 | 保持战争相对时间语义；按有效时间锚点换算，不能直接当 Unix 时间。 |
| 公开 `SpaceStation/{war_id}/{station_id}` + `ElectionV2/{war_id}/{election_id}` | DSS 位置、战术行动及公开迁移票数 | 先读取 `currentElectionId`，再读取公开 ElectionV2。 |
| 公开 `Episode/{war_id}` | 控制中心战役、阶段和结果 | 按公开状态展示；未知状态不归为成功或失败。 |
| 公开 `WarSeason/GalacticWarEffects` 及 Status 的效果标识 | 特殊部队分布 | 只识别已核对的标识语义；不读取私有游戏会话或推断真实兵力。 |
| `api.helldivers2.dev` | 项目已有社区备用数据源 | 只在对应资源确有实现时回退；不能暗示所有新增资源都有社区备用实现。 |
| Steam `ISteamNews/GetNewsForApp/v2/` | appid 553850、`steam_community_announcements` 官方公告 | 固定官方 feed，拒绝不符合应用/来源的记录。Steam CDN 公告 URL 经严格主机及路径验证后规范化到官方商店地址；不访问正文中的外部链接。 |
| Steam `ISteamUserStats/GetNumberOfCurrentPlayers/v1/` | Steam 当前在线数 | appid 553850，单独标注 Steam 平台，不能当全平台总数。 |
| 本地 `mock` provider | 不联网开发与演示 | 所有模拟结果明确标注，不能作为实时数据或真实 QQ 发送验收。 |

确认依赖私有认证数据的上游位置为 [`utils/api_wrapper/clients/authed_client.py`](https://github.com/Stonemercy/Galactic-Wide-Web/blob/main/utils/api_wrapper/clients/authed_client.py)：`AuthedClient`/`AltPOAuthedClient`（个人指令）、`AltDSSVotesAuthedClient`（票数）、`AltSuperstoreAuthedClient`（商店）、`AltWarbondsAuthedClient`（战争债券）。这些是所参考上游版本的实现路径，不代表本项目必须使用同一数据源。当前 DSS 票数使用公开 ElectionV2，战争债券使用 Wiki 静态目录；个人指令和实时超级商店仍未接入。

核心参考代码范围：

- [README](https://github.com/Stonemercy/Galactic-Wide-Web/blob/main/README.md)：功能清单与交互目标。
- [公开 HD2 客户端](https://github.com/Stonemercy/Galactic-Wide-Web/blob/main/utils/api_wrapper/clients/helldivers_client.py)：公开只读路径、战争数据和控制中心数据来源。
- [Steam 客户端](https://github.com/Stonemercy/Galactic-Wide-Web/blob/main/utils/api_wrapper/clients/steam_client.py)、[`cogs/steam.py`](https://github.com/Stonemercy/Galactic-Wide-Web/blob/main/cogs/steam.py)：Steam 新闻、在线数和浏览功能组织。
- [`utils/maps.py`](https://github.com/Stonemercy/Galactic-Wide-Web/blob/main/utils/maps.py)、[`cogs/warfront.py`](https://github.com/Stonemercy/Galactic-Wide-Web/blob/main/cogs/warfront.py)：坐标、相邻连线与阵营战线组织。
- [`cogs/control_centre.py`](https://github.com/Stonemercy/Galactic-Wide-Web/blob/main/cogs/control_centre.py)、[`cogs/subfactions.py`](https://github.com/Stonemercy/Galactic-Wide-Web/blob/main/cogs/subfactions.py)：战役阶段和特殊部队查询概念。
- [`utils/api_wrapper/formatters/data_formatter.py`](https://github.com/Stonemercy/Galactic-Wide-Web/blob/main/utils/api_wrapper/formatters/data_formatter.py)：时间锚点、星球连接补全、公开状态组织。上述链接的 `main` 会随上游更新，本轮本机参考快照记录为 `b28045bf2653bcc427029ff8d48e63880936add1`；既有详细证据见 `GWW_FEATURE_NOTES.md`。

## 许可与验收口径

上游 GWW [LICENSE](https://github.com/Stonemercy/Galactic-Wide-Web/blob/main/LICENSE) 为 GPL v3；Tenko 历史 BF1 参考快照同为 GPL v3，详见 `TENKO_RENDER_NOTES.md`。本项目独立实现查询、解析、模板与订阅逻辑，没有复制这些上游项目的业务源码或界面模板。已有星球和星区名称目录包含来自 GWW 的派生数据，按 GPL-3.0 保留来源和许可，见 [静态目录来源](DATA_SOURCES.md)；Wiki 文字与装备图片的来源和许可另见 [Wiki 说明](WIKI_CATALOG_NOTES.md)。原始项目代码的许可不能覆盖这些第三方资料。

验证覆盖星图投影与无向连接、缺失坐标、HTML 转义、原有卡片回归、真实 Chromium/JPEG 星图，
以及 Steam 源验证、正文清理、缓存、错误和会话生命周期。真实查询 11/11 成功；DSS、公告、
控制中心、区域、特殊部队、Steam 查询均已接通。SQLite v1/v2/v3→v4 并发迁移、10 类订阅的基线、
变化检测、权限、额度和取消均经过测试。QQ 本地探针捕获 6 条文字/图片回复，QQ 网络请求 0；
未将这些模拟发送或公共 API 读取计为新增通知实际送达。
