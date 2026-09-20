> 历史记录：最初新闻与补给线接入，后续已加入 DSS、公开票数及静态战争债券。下文“当前”“本轮”、测试数量和运行状态均指记录时点，不是现行部署说明。当前使用方法见 [项目 README](../README.md)，文档范围见 [研究索引](README.md)。

# Galactic Wide Web 功能参考

> 本文记录最初的新闻/补给线研究。后续已根据 README 扩展星图、战线、DSS、控制中心、
> 全球事件、区域、特殊部队、Steam 查询及变化通知；现行范围见 [GWW_PARITY.md](GWW_PARITY.md)。
> 下文“本轮”“暂缓”描述仅代表最初阶段，不代表当前实现状态。

核对日期：2026-09-16。依据公开上游固定提交进行只读源码核对。参考快照 [`b28045bf2653bcc427029ff8d48e63880936add1`](https://github.com/Stonemercy/Galactic-Wide-Web/tree/b28045bf2653bcc427029ff8d48e63880936add1)，提交日期 2026-09-09。

## 本轮采用

| 功能 | 上游对应能力 | 本项目实现边界 |
| --- | --- | --- |
| 新闻 / 战报 | `/dispatches`，默认最新消息，可选 ID 与下拉列表 | 最近三条银河新闻，中文原文清理、发布时间、来源/缓存信息；按用户请求查询 |
| 补给线 星球 | Planet 的 waypoint/nearby 与地图连接线 | 对查询星球列出正向、反向、双向记录，邻星控制方和在线人数；保留未解析的星球编号 |

本轮不接入上游的主动订阅广播、Discord 组件、DSS 投票私有数据或其它账号认证。

## 新闻数据依据

- [命令入口](https://github.com/Stonemercy/Galactic-Wide-Web/blob/b28045bf2653bcc427029ff8d48e63880936add1/cogs/dispatches.py#L131)：读取整理后的最新 dispatch；上游还支持按 ID 和最近 25 条下拉选择。
- [公开 HTTP 客户端](https://github.com/Stonemercy/Galactic-Wide-Web/blob/b28045bf2653bcc427029ff8d48e63880936add1/utils/api_wrapper/clients/helldivers_client.py#L16)：`GET /api/NewsFeed/{war_id}?maxEntries=1024&fromTimestamp=...`，按 `Accept-Language` 请求语言。
- [采集窗口](https://github.com/Stonemercy/Galactic-Wide-Web/blob/b28045bf2653bcc427029ff8d48e63880936add1/utils/api_wrapper/services/data_service.py#L93)：以 `Status.time - 7 天` 获取最近消息，最终保存最近 25 条。
- [消息模型](https://github.com/Stonemercy/Galactic-Wide-Web/blob/b28045bf2653bcc427029ff8d48e63880936add1/utils/api_wrapper/models/dispatch.py#L10)：`id`、`published`、`message`；正文可能包含 `<i=3>` 标题或其它游戏格式标签。
- [战争时间锚点](https://github.com/Stonemercy/Galactic-Wide-Web/blob/b28045bf2653bcc427029ff8d48e63880936add1/utils/api_wrapper/formatters/data_formatter.py#L174)：使用采样 UTC 时间减 `Status.time`，然后加消息的 `published` 秒数。**不能把 WarInfo.startDate 当作此相对计时的可靠起点。**

本项目已实测官方 NewsFeed HTTP 200，最近一周窗口返回四条消息，包括 ID 3922、3923，字段为 `id,published,type,tagIds,message`。`published` 为战争相对秒数。请求头 `Accept-Language: zh-Hans` 返回中文；尝试 `zh-CN` 时 Status 返回 400，沿用项目既有的 `zh-Hans`。

额外确认：`fromTimestamp=0&maxEntries=3` 返回的是历史最早三条，而不是最新三条。因此实现使用最近一周窗口，再按 ID 降序选择最近三条；窗口内无消息时明确提示暂无近期新闻。若时钟字段不可用，则请求较大的历史上限并保留发布时间未知，避免伪造日期。

社区备用接口 [GET /api/v2/dispatches](https://api.helldivers2.dev/api/v2/dispatches) 已实测 HTTP 200，返回数组（当次 1048 条），`id` 为整数、`message` 为中文字符串、`published` 为 ISO 8601 字符串（例如以 `Z` 结尾的 UTC 时间）。社区路径不把数值型 published 当成 Unix 秒解读，遇到未知形状保留时间未知。

本项目用固定新闻缓存键保留一次采样的日期换算结果，避免缓存命中后发布时间随查询时间滑动，也避免每个动态 fromTimestamp 都占一个缓存键。传输、回退、旧缓存标记复用已有 HD2 数据层。

## 补给线数据依据

- [Planet 初始化](https://github.com/Stonemercy/Galactic-Wide-Web/blob/b28045bf2653bcc427029ff8d48e63880936add1/utils/api_wrapper/models/planet.py#L44)：从 WarInfo 的 `waypoints` 建立 `nearby` 列表。
- [补全反向相邻关系](https://github.com/Stonemercy/Galactic-Wide-Web/blob/b28045bf2653bcc427029ff8d48e63880936add1/utils/api_wrapper/formatters/data_formatter.py#L619)：若 A 的 waypoints 包含 B，也把 A 加入 B 的 nearby。
- [地图绘制](https://github.com/Stonemercy/Galactic-Wide-Web/blob/b28045bf2653bcc427029ff8d48e63880936add1/utils/maps.py#L149)：基于 nearby 绘制连接线。

本项目已有 `Planet.waypoints`、中英文星球搜索、控制方和在线人数，无需新增 API。实现采用查询星球正向 waypoint 与所有星球反向引用的并集，去重并排除自身。方向只表示源数据如何存储该连接，不能据此断言可进攻、可部署、必定能切断补给等游戏规则。

不存在的目标编号显示“星球 #编号，详情暂无数据”；没有连接记录显示“当前数据未提供相邻连接”，不能声称该星球已断路。上游没有同名的独立 `/补给线` 文本命令，本项目是将其地图连接信息整理为适合 QQ 的查询形式。

## 暂缓项目

[DSS 命令](https://github.com/Stonemercy/Galactic-Wide-Web/blob/b28045bf2653bcc427029ff8d48e63880936add1/cogs/dss.py#L20) 展示空间站位置和战术行动。公开链为 Status.spaceStations[].id32 → [SpaceStation/{war_id}/{station_id}](https://github.com/Stonemercy/Galactic-Wide-Web/blob/b28045bf2653bcc427029ff8d48e63880936add1/utils/api_wrapper/clients/helldivers_client.py#L36)，已只读确认接口有效。可显示 planetIndex、选举截止的战争时间、tacticalActions 的状态和资源进度；票数另依赖上游私有认证客户端。本轮优先完成新闻与补给线，未增加 DSS 查询命令。

战争累计统计已有本项目 `GlobalStatistics` 和“玩家”输出覆盖任务胜负、阵营击杀与阵亡，不重复建立相同功能。

## 许可与验证

参考仓库 [LICENSE](https://github.com/Stonemercy/Galactic-Wide-Web/blob/b28045bf2653bcc427029ff8d48e63880936add1/LICENSE) 为 GNU GPL v3。本项目参考公开 API 行为与功能组织，独立编写模型、解析器和文字 formatter；未复制上游代码、Discord UI、图片、数据资源或凭据。

无网络定向测试：`test_intelligence.py`、`test_captured.py`、`test_community.py`、`test_freshness.py` 共 79 项通过，覆盖相对日期、缓存不漂移、未知时间、错误 JSON、社区回退、去标签、双向连接及缺失目标。相关新增/修改文件的 Ruff 检查通过。完整集成与 QQ 发送验证由主任务完成。
