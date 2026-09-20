# 战备英雄聊天版：资料和适配说明

核验日期：2026-09-19。

## 已访问来源

- 在线社区复刻：[StratagemHero](https://www.stratagemhero.com/)。页面展示战备名与方向代码、Round、Score、Round Bonus、Time Bonus、Perfect Bonus、Game Over 和最终分数；页面明确声明不隶属于 Arrowhead。
- 同一复刻的公开前端实现：[stratagemhero.min.js](https://www.stratagemhero.com/js/stratagemhero.min.js?v=1)。读取确认：初始 10 秒；每关 `4 + round` 题；完整代码每个方向 5 分；答对加 1 秒且不超过初始时间；输错重置当前题输入并取消该关完美奖励；过关加 `75 + 25 * (round - 1)`、剩余时间百分比分和 100 完美分；新关刷新计时。仅核验玩法，没有复制其代码、图片或音频。
- 题目使用现有本地 [Wiki 战备目录](https://helldivers.wiki.gg/wiki/Stratagems) 的已归档代码：`src/hd2bot/assets/wiki_catalog.json`，目录修订 `133897`；每道题保留条目自己的来源链接。原始目录已有授权说明，游戏服务不复制为无来源的独立代码表。
- 已尝试网上搜索 `"Stratagem Hero" "time" "round"`、`"Stratagem Hero" "perfect" "bonus"`。当前搜索返回了无关结果，Wiki 页面返回 403，因此没有把这些结果当作玩法证据。上面的社区复刻是本次可实际读取的玩法来源，不宣称它等于当前游戏客户端的逐帧精确实现。

## 聊天适配

- 保留出题、按顺序输入、分关、错误重置当前题、限时、计分、完美奖励和最高分。
- 考虑 QQ 消息往返和输入法，每关放宽到 120 秒；每题返 8 秒，仍以 120 秒为上限。首关 5 题，后续每关加一题，最多每关 12 题，最多 100 关。
- 支持整串或逐条输入；一条消息不能同时回答两个尚未展示的题目。格式错误不会过滤掉字符后当正确答案，也不会清空已有进度。
- 支持普通箭头、emoji 箭头及 variation selector、多种 Unicode 单向箭头、快进方向符号、手指方向及肤色、全角及半角 `^v<>`、竖排 `︿﹀`、ASCII `->` / `<-`、WASD、中文方向、英文方向词、QQ 风格 `[上][下][左][右]`。对角和双向箭头含义不唯一，明确拒绝。`DSS` 保留给现有查询命令；路由应先分发已识别的普通业务指令。
- 题库只选已有中文名/中文别名、实际方向序列的战备，排除任务专属及未开放条目；本次默认得到 50 题。来源不明或没有中文名的战备不临时编造代码或名称。
- 玩家状态和纪录按 `scope + target_id + user_id` 隔离。群聊、私聊、频道/子频道、频道私信可由路由传入各自 scope；QQ 的用户标识在不同场景可能不相同，不尝试强行合并。
- 状态与最高分保存到 SQLite，机器人重启后继续使用原绝对截止时间；所有状态变更在数据库事务内，支持并发请求及多个数据库连接。
- 仅在玩家发消息或有人启动/查看记录时惰性处理到期，不自动向群或用户推送游戏消息。群聊仍需要按 QQ 平台接收规则 @机器人。
- 活跃局上限 512，记录上限 50,000，榜单最多 10 人。不默默删除最高分；达到上限时明确提示。现有玩家仍能继续游戏。

## 集成接口

```python
service = StratagemHeroService(db)
await service.initialize()  # 独立 CREATE TABLE IF NOT EXISTS，不变更主数据库 user_version
reply = await service.handle(
    plain_text,
    scope=scope,
    target_id=target_id,
    user_id=user_id,
    display_name=display_name,
)
# reply is None => 本功能未接管，继续普通路由。
```

`normalize_arrows()` 返回统一 `↑↓←→` 序列，无效输入抛 `ArrowInputError`；`is_arrow_input()` 用于识别完整的方向输入。游戏自己的帮助由 `help_text()` 返回。
