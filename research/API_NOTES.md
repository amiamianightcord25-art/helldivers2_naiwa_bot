> 历史记录：该日期的公开 API 响应与测试。下文“当前”“本轮”、测试数量和运行状态均指记录时点，不是现行部署说明。当前使用方法见 [项目 README](../README.md)，文档范围见 [研究索引](README.md)。

# HELLDIVERS 2 API 研究记录

研究日期：2026-09-11（Asia/Hong_Kong）；在线验证时间：2026-09-10 16:21–16:23 UTC。本文保留可公开的接口观测、社区 OpenAPI/源码，以及只读 GET 验证结论。

## 已验证结论

- 官方源站 `api.live.prod.thehelldiversgame.com` 的 WarID、WarInfo、Status、银河统计和主要指令均已匿名 GET 成功，HTTP 200。没有发送 Authorization、Cookie、Steam 凭据或捕获令牌。
- `api.helldivers2.dev` 的 `/api/v1/war`、`/planets`、`/campaigns`、`/assignments` 均 HTTP 200。
- 官方本地化须使用 `Accept-Language: zh-Hans`（简体）；`zh-CN` 实测让 Status 和 Assignment 返回 400，错误码 13018019。不要把这种配置错误当作认证失效。
- 社区必须同时提供 `X-Super-Client`、`X-Super-Contact`；缺少后者实测 400。经用户授权，本地测试使用诚实声明 `HD2-QQ-Bot local development; contact not configured`，服务接受并返回 200。它不是可联系地址，不代表提供了真实联系方式；之后长期使用应通过环境配置补充真实联系方式。
- 用户随后明确指定本地配置 `X-Super-Contact=example@example.com`；这是用户选择的示例地址，不能宣称为已验证可联系邮箱。上述 200 记录仍准确对应当时发送的声明文本，不改写历史证据。
- 当前 warId 为 801；应优先读取 WarID，避免永久写死当前赛季。
- “玩家”支持全服/星球聚合人数和银河累计统计；没有验证到按任意账号查询个人资料的公开服务。

建议 `auto` 首选官方公开只读 provider，失败时尝试社区 provider；`mock` 必须明确标为模拟。网络失败不能自动伪装成 mock 实时数据。

## 证据来源

本次只读检查：

- [社区 API OpenAPI](https://helldivers-2.github.io/api/openapi/Helldivers-2-API.json)，本次 HTTP 200，Last-Modified 为 2026-09-09 07:17:58 GMT。
- [社区整理的官方上游 OpenAPI](https://helldivers-2.github.io/api/openapi/Helldivers-2-API_arrowhead.json)，同样 HTTP 200。这是社区文档，不能称为 Arrowhead 正式发布的开发者合同。
- [社区 API 固定源码](https://github.com/helldivers-2/api/tree/ec187b8c01c3e37138592c1f44f030ccde8a32fe)：`ArrowHeadApiService.cs` 构建 GET 与 Accept-Language，`ServiceCollectionExtensions.cs` 指向官方主机，没有配置游戏认证。

公开响应及探测状态暂存在项目忽略目录 `tmp/research_results/`；不依赖这些临时文件启动程序。未修改原始 SAZ、旧项目或抓包报告。以下未携带私人凭据的成功结果只证明本次这些公共路由的匿名可用性，不推广到登录、好友、大厅或其他接口。

## 官方公开战局接口

共同 Host：`api.live.prod.thehelldiversgame.com`；Method：GET；实际请求头名称：`Accept`、`Accept-Language`、`User-Agent`。本次采用 `Accept: application/json`、`Accept-Language: zh-Hans`（前期探测部分使用 zh-CN）、诚实本地应用 User-Agent。没有正文。下表 Query 为本次成功请求实际使用值。

| Path | Query | 返回结构与字段含义 | 本次认证/状态 | 抓包刷新观测 |
| --- | --- | --- | --- | --- |
| `/api/WarSeason/current/WarID` | 无 | `{id: integer}`，当前战争编号 | 无凭据，200，id=801 | 原抓包未覆盖，适合较长缓存 |
| `/api/WarSeason/{warId}/WarInfo` | 无 | `warId,startDate,endDate,layoutVersion,minimumClientVersion,planetInfos[],homeWorlds[],capitalInfos[],planetPermanentEffects[],planetRegions[]` | 无凭据，200 | 原抓包未覆盖，静态信息可长缓存 |
| `/api/WarSeason/{warId}/Status` | 无 | `warId,time,impactMultiplier,storyBeatId32,planetStatus[],planetAttacks[],campaigns[],communityTargets[],jointOperations[],planetEvents[],planetActiveEffects[],planetRegions[],activeElectionPolicyEffects[],globalEvents[],superEarthWarResults[],spaceStations[],globalResources[],layoutVersion` | 无凭据；zh-Hans 200；zh-CN 400 | 约 5 分钟 |
| `/api/Stats/war/{warId}/summary` | 无 | `{galaxy_stats: object, planets_stats: object[]}`，银河/星球累计统计 | 无凭据，200 | 约 60 秒；32 次正文都变化 |
| `/api/v2/Assignment/War/{warId}` | 无 | `Assignment[]`，主要指令与任务、进度和奖励 | 无凭据；zh-Hans 200；zh-CN 400 | 约 5 分钟 |

刷新观测来自 2026-09-08 23:13:10–23:44:35 本地捕获，只代表客户端当时轮询间隔；不是源站刷新 SLA 或允许请求频率。本项目建议状态/任务集中缓存 60 秒，静态目录缓存更久，并处理超时、429、5xx 与陈旧缓存标识。

### Raw 字段与映射

`WarInfo.planetInfos[]`：`index` 是关联键；`settingsHash`/`planetNameId32`/`planetBiomeId32` 是静态标识；`position{x,y}` 是坐标；`waypoints[]` 为连接目标；`sector` 是区域编号；`maxHealth` 最大生命值；`disabled` 禁用标志；`initialOwner` 初始阵营。官方这个接口不直接给出可读星球名称，名称需要有来源的静态目录或社区接口。切勿把哈希当名称。

`Status.planetStatus[]`：`index,owner,health,regenPerSecond,players,position{x,y}`。`players` 是星球聚合在线人数；`health` 与 `WarInfo.maxHealth` 联合使用。已知阵营码 1=Humans（超级地球）、2=Terminids（终结族）、3=Automatons（机器人）、4=Illuminate（光能者），未知值保留为未知。

`Status.campaigns[]`：`id,planetIndex,type,count,race`；战役索引与星球关联。不要仅凭存在 campaign 判断进攻：本次包括 `race=1` 的记录。

`Status.planetEvents[]`：`id,planetIndex,eventType,race,health,maxHealth,startTime,expireTime,campaignId,jointOperationIds[],potentialBuildUp`。防守事件与星球关联；事件血量和最大血量是独立尺度，不能用星球最大血量计算事件进度。本次 eventType=1 出现在星球 158，敌方 race=3。剩余秒数可按同一 Status 的 `expireTime-time` 计算并截到不小于零；不能把 raw 战争相对时间直接当 Unix 时间。静态进度比例只能表达当前完成度，不能凭单快照推断每小时净进度或预计获胜时间。

`Summary.galaxy_stats` 与 `planets_stats[]`：`missionsWon,missionsLost,missionTime,bugKills,automatonKills,illuminateKills,bulletsFired,bulletsHit,timePlayed,deaths,revives,friendlies,missionSuccessRate,accurracy`；星球统计另含 `planetIndex`。社区把 `bugKills` 重命名为 `terminidKills`、上游拼写 `accurracy` 改为 `accuracy`。`friendlies` 是友伤统计，不是好友；这些是全局累计值。源站命中累计口径存在 `bulletsHit>bulletsFired`，应展示源站比率，不自行按普通公式解释。

`Assignment[]`：`id32,startTime,progress[],expiresIn,setting`。`setting` 包含 `type,overrideTitle,overrideBrief,taskDescription,tasks[],rewards[],reward,flags`；任务包含 `type,values[],valueTypes[]`，奖励包含 `type,id32,amount`。`progress[i]` 对应任务 i；`expiresIn` 为剩余秒数。数值任务类型/参数语义不能无证据猜测，未知任务应仍显示官方描述和原始进度。本次任务 type=11，参数 `values=[1,1,156]` / `[1,1,253]` 与 `valueTypes=[3,11,12]`，描述要求解放指定星球。中文文本内可能有游戏 `<i=...>` 等标签，formatter 要清理展示标签但保留正文。实际奖励 amount=45；如未校验奖励枚举，显示“奖励数量 45”，不要擅自断定币种。

当前成功官方 Status 样本：273 条 `planetStatus`、34 条 campaign、1 条 planetEvent，聚合玩家数 71,223。该数是采样快照，不应写成长期固定期望值。Summary 和 WarInfo 覆盖数量可能不同，按 index 合并而非位置 zip，允许单边缺项及未知新字段。

## 社区 fallback 接口

共同 Host：`api.helldivers2.dev`；Method：GET；下列实际请求 Query 均无；实际头名称：`Accept`、`Accept-Language`、`User-Agent`、`X-Super-Client`、`X-Super-Contact`。普通请求不需要游戏凭据；社区自己的可选高限额认证与游戏 Token 不同。这里 `/raw` 是登记的固定缓存路由，不是任意路径透传。

| Path | Response Schema | 本次状态 |
| --- | --- | --- |
| `/api/v1/war` | `started,ended,now,clientVersion,factions[],impactMultiplier,statistics` | 200 |
| `/api/v1/planets` | `Planet[]`，星球及状态、事件与统计 | 200 |
| `/api/v1/planets/{index}` | `Planet`；整数路径参数 | 仅当前 OpenAPI，未单独请求 |
| `/api/v1/campaigns` | `Campaign[]`：`id,type,count,faction,planet` | 200 |
| `/api/v1/assignments` | `Assignment[]`：`id,progress[],title,briefing,description,tasks[],reward,rewards[],expiration,flags` | 200 |
| `/raw/api/WarSeason/current/WarID` | raw WarID | 当前 OpenAPI，未请求 |
| `/raw/api/WarSeason/801/Status` | raw Status | 当前 OpenAPI，未请求 |
| `/raw/api/WarSeason/801/WarInfo` | raw WarInfo | 当前 OpenAPI，未请求 |
| `/raw/api/Stats/war/801/summary` | raw Summary | 当前 OpenAPI，未请求 |
| `/raw/api/v2/Assignment/War/801` | raw Assignment[] | 当前 OpenAPI，未请求 |
| `/raw/api/NewsFeed/801` | `NewsFeedItem[]`：`id,published,type,message` | 当前 OpenAPI，未请求；无 Query 声明 |

`Planet`：`index,name,sector,biome{name,description},hazards[],hash,position,waypoints[],maxHealth,health,disabled,initialOwner,currentOwner,regenPerSecond,event,statistics,attacking[],regions[]`。`statistics.playerCount` 来源于对应星球 raw players；`event` 可以为 null，存在时含 `id,eventType,faction,health,maxHealth,startTime,endTime,campaignId,jointOperationIds[]`。使用模型容忍后续新增字段、null 和缺失可选字段。社区按 Accept-Language 翻译星球名，英文命令别名须保留英文目录，不能假定中文 `name` 能直接匹配 `Meridia`。

`War.statistics`：银河累计统计及 `playerCount`，不同接口并非原子快照，同一时刻拉取可能有小幅差异。社区 `started/ended/now/expiration` 等是日期时间字符串，与官方相对战争秒数分开解析。

限流：源码默认 5 请求/10 秒；本次响应 `X-RateLimit-Limit: 5`，剩余额度依次变动，验证服务器实际给出限流头。处理 `X-RateLimit-Remaining` 与 `Retry-After`，共享缓存，避免 QQ 每条命令重复拉取四个路由。源码同步循环默认完成一次同步后等待 20 秒，不等于每 20 秒严格更新。缓存可用时返回注明采样时间的数据；所有 provider 失败时输出可理解错误并保持 CLI 循环运行。

## 抓包覆盖但本阶段不接入的接口

共同游戏 Host 为 `api.live.prod.thehelldiversgame.com`，除表中另列 PlayFab。抓包里的游戏 GET 均曾携带 `Authorization`，但这只能证明客户端发送；本次公共路由匿名成功已澄清“携带”和“必需”的区别。以下私有/写入接口未做网络探测。

| Host / Method / Path | 已观察 Query / JSON 参数 | 关键响应字段 | 头名称与限制 |
| --- | --- | --- | --- |
| 游戏 GET `/api/Episode/801/status` | `maxEntries=1` | `episodes[].episodeId32,latestPhaseId32` | Authorization；约 30 秒；未匿名验证；与 WarSeason Status 不同 |
| 游戏 GET `/api/WarSeason/801/timeSinceStart` | 无 | `secondsSinceStart` | Authorization；约 5 分钟；与 Status.time 基准不能混用 |
| 游戏 GET `/api/NewsFeed/801` | `maxEntries,fromTimeStamp`（报告观察名称） | 本样本空数组 | Authorization；约 5 分钟；空数组不能证明所有新闻字段 |
| 游戏 GET `/api/FriendsV2/{Request,Block,Recent}` | `offset=0,limit=100` | `totalEntries,currentLimit,currentOffset,entries[]` | Authorization；Request 活跃时约 15.3 秒；非公开玩家查询 |
| 游戏 POST `/api/lobby/accounts` | `ids:string[]` | `accountLobbies[],lobbies[]` | Authorization,X-Signature,key_id；只读目的仍是认证 POST，未重放 |
| 游戏 PUT `/api/lobby` | lobbyId,players[],memberAccountIds[] 与状态字段 | HTTP202，无响应正文 | Authorization,X-Signature,key_id；players 等级/经验为客户端上报，不是查询 API |
| 游戏 POST `/api/lobby/{id}/member` | JSON null | HTTP202，无业务正文 | Authorization,X-Signature,key_id；约90秒；不接入维持/上报 |
| 游戏 POST `/api/Mail/inbox` | limit,offset,orderDateAsc,unclaimedOnly,sources[] | totalMailCount,entries[] 与分页 | Authorization,X-Signature,key_id；约10分钟 |
| 游戏 POST `/api/Pes/GetConfiguration` | 语言与会话时长 | isValid,debugResults | Authorization,X-Signature,key_id；样本 isValid=false 不是网络失败 |
| 游戏 GET `/api/Configuration/GameClient` | 无 | pollingConfiguration[],featureConfiguration[],onlineOverrideConfiguration[] 与匹配配置 | Authorization；约15分钟；数字 id32 含义未确认 |
| 游戏 GET `/api/WarSeason/GalacticWarEffects` | 无 | 效果定义数组 | Authorization；约15分钟；未匿名验证 |
| `974a9.playfabapi.com` POST `/Client/GetFriendsList` | ExternalPlatformFriends | data.Friends[] 含基础好友信息 | X-Authorization；认证会话范围，不是任意玩家 |
| `974a9.playfabapi.com` POST `/Lobby/GetLobby` | LobbyId | data.Lobby：Owner,Members[],MaxPlayers,AccessPolicy,SearchData,LobbyData,ChangeNumber | X-EntityToken；未重放 |
| `974a9.playfabapi.com` POST `/pubsub/negotiate` | 会话协商字段 | URL,accessToken | X-EntityToken；真实值不保存进项目 |
| SignalR 动态区域主机 POST `/client/negotiate` | 协议协商参数 | 连接标识及传输方式 | Bearer 与 X-EntityToken；不用于战况查询 |
| `974a9.playfabapi.com` POST `/Event/WriteTelemetryEvents` | Events[] | AssignedEventIds[] 与 code | SDK 遥测；部分 X-ReportErrorAsSuccess |

不复制这些接口的凭据、账号 ID、好友、大厅 ID 或原始私人响应进入源码/fixture/Git。后续新增抓包导入应默认提取 Host/Method/脱敏 Path/Query 名称/Headers 名称/schema，敏感值保留在用户原始文件，不写日志。

## M1 验证记录

- 已读取两轮已有脱敏报告，并核对捕获频率、请求形态和公开数据边界。
- 已下载当前两份公开 OpenAPI 并成功 JSON 解析。
- 官方五个核心读取接口 5/5 HTTP 200；另记录 zh-CN 的明确失败及 zh-Hans 修复。
- 社区四个整理接口 4/4 HTTP 200；另记录缺少联系头 400。
- 所有本次在线访问均为公共战局/公开文档 GET，间隔约 3 秒，不发送游戏/Steam/QQ secrets。
- M1 不以数据值固定断言稳定性；可运行 provider、CLI、错误恢复和 pytest 在后续里程碑验证。

## 新增公共银河情报与 Steam 资源（2026-09-16）

在原有公开接口上，增加以下查询。均为只读 GET，不使用游戏账号 Cookie、Steam 票据或私有服务凭据。

| 资源 | 字段/行为 | 缓存 |
| --- | --- | --- |
| `Status.globalEvents` | `eventId`、title/message、race、关联星球/任务、`expireTime` | 复用 Status |
| `Status.spaceStations` → `/api/SpaceStation/{war_id}/{id32}` | `planetIndex`、flags、`currentElectionId`、投票截止、战术行动 status/期限/筹备 cost | 动态 TTL |
| `/api/ElectionV2/{war_id}/{election_id}` | 公开选举状态、选项、目标星球 `metaId`、票数 | 动态 TTL |
| `/api/Episode/{war_id}` | 战役、phases、公开状态、开始/结束、阶段/战役奖励 | 主线 TTL |
| `Status.planetRegions` + `WarInfo.planetRegions` | `(planetIndex, regionIndex)` 合并当前控制方/HP/在线和静态最大HP/伤害倍率 | 复用 Status/WarInfo |
| `Status.planetActiveEffects` + `/api/WarSeason/GalacticWarEffects` | 当前战场效果与公开特殊部队标识 | 动态 Status + 静态 TTL |
| Steam `ISteamNews/GetNewsForApp/v2/` | appid 553850、官方 feed、最近 50 条，列表显示 3 条；可按 ID 查详情 | 至少 60 秒 |
| Steam `ISteamUserStats/GetNumberOfCurrentPlayers/v1/` | appid 553850；只代表 Steam 在线 | 至少 30 秒 |

时间统一使用同次 Status 采样 UTC 时刻减 `Status.time` 再加战争相对秒数；数值 0 或缺失的
截止时间不伪装成真实日期。部分 Episode 英文不属于本地化键，保留原文。未知状态/奖励/效果
保留编号；缺少整个必需字段视为 schema 故障，与真实空数组区分。

真实匿名联调：DSS 1 站/3 行动、全球事件 1 条、Episode 7 个、动态区域 41 条、特殊部队 11 类；
星图实际 273 星球、336 去重连接。数目仅为当次快照，不能用作常量或固定验收断言。
Steam 官方 feed、在线接口和公开 ElectionV2 均成功；个人指令、商店暂未取得授权数据。
