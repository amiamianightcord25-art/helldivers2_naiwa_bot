> 更新：2026-09-19 用户登录后，已用实际 1920×1080 游戏画面重新核对全部称号并修正 21 项中文；当前结论以 [高清核对报告](rank_verification_20260919.md) 为准。以下保留初次研究记录。

# 签到与等级功能核验（2026-09-19）

## 参考的成熟签到实现

实际在线读取了真寻机器人的 README 和内置签到实现：

- 项目与功能目录：https://github.com/zhenxun-org/zhenxun_bot
- 签到命令、个人签到、排行榜与奖励上限配置：
  https://github.com/zhenxun-org/zhenxun_bot/blob/main/zhenxun/builtin_plugins/sign_in/__init__.py
- 日期和数据处理：
  https://github.com/zhenxun-org/zhenxun_bot/blob/main/zhenxun/builtin_plugins/sign_in/_data_source.py

核验到其代码按 `Asia/Shanghai` 获取日期、比较最近签到记录日期；提供 `签到`、
`我的签到`、当前群排行、总排行入口，配置中包含额外奖励上限。本项目参考这些交互设计，
独立实现经验、连签和数据库事务，不移植其随机道具、商店和全局排行。

## 游戏称号与最新等级上限

已在线读取：

- https://helldivers.wiki.gg/wiki/Levels?oldid=134866
- https://helldivers.wiki.gg/wiki/Cosmetics#Titles
- https://helldivers.wiki.gg/zh/wiki/装饰?variant=zh-cn

英文等级页 `Change History` 明确记录：`1.007.000`、`2026-08-12`、
`Raised the cap from 150 to 300`。该页的等级表逐项列出 1–300 级经验与解锁称号。
本地 `wiki_catalog.json` 现有的 36 个 `Titles / Level Earned` 条目与线上表一致。

因此本功能采用 300 级上限，而不是旧资料中的 150 级。普通等级称号依据数字解锁等级
升序排列，严格排除 `Super Citizen` 以及所有战争债券/其他特殊称号。
140 级 `Private` 和 150 级 `Super Private` 保持游戏原本的位置；这是游戏称号设计，
不应按现实军衔把它们移到低等级。

**中文来源限制：** 核验时中文 Wiki 的装饰页虽然存在，但该等级称号表仍为英文。
没有把它当成官方中文译名的证据。`rank_titles.json` 保存每项英文名、解锁等级、来源 URL，
并明确将全简体中文名称标为“参考译名”；`等级表` 也显示该说明。尚未逐项核实当前游戏
客户端的官方简中语言文本。获得官方文本后只需修订 JSON 中的 `title`，等级顺序不变。

## 本项目规则

- `签到`：北京时间每天 00:00 换日，每个身份领域内用户每日最多发放一次。
- 每次基础 100 经验；连续第二天 110，依此类推，第七天及以后每天 160。
- 漏签后连续天数归零；再次签到记为第一天，已有累计经验和签到天数保留。
- 1 级初始经验为 0；从 L 级升一级需要 `100 + 2 * (L - 1)` 经验。
- 300 级封顶经验为 119002；满级后仍可签到累计天数，不再增加经验。
- 此经验曲线是机器人签到进度，不是游戏账号的经验/等级同步。
- `我的等级`：当前称号、累计经验、连签、累计签到、下一级及下一称号。
- `等级表`：按解锁等级排列的完整 36 个简中称号与规则。
- `签到排行`：当前群/频道前十；私聊仅显示自己，不跨私聊展示其他用户。

## 身份、事务与接入

`CheckinService(db, clock=time.time)` 复用已有 `Database`，调用 `initialize()` 或首次
业务调用时建立独立的 `checkin_profiles` 和 `checkin_members` 两张表。

`checkin_profiles` 以 `(scope, user_id)` 唯一标识，支持 `c2c/group/dms/channel`。
不合并不同聊天类型的用户 ID；在相同类型中同一 ID 换到另一个群/频道不会再次发奖。
`checkin_members` 以 `(scope, target_id, user_id)` 记录在该会话签到的成员与平台提供的昵称。
群/频道排行只读取当前会话成员；昵称缺失如实显示“未提供昵称”，本人额外标“（你）”。
任何对外回复均不拼接用户 ID 或群 ID。

签到在 `Database.transaction()` 的 `BEGIN IMMEDIATE` 内完成日期读取、重复判定、
昵称登记、经验与天数写入。跨 SQLite 连接的并发调用仍只有一个事务发奖，事务失败全部
回滚。若时钟回拨到已签到的日期之前，不发放额外经验。

根路由接入：

```python
checkin = CheckinService(database)
reply = await checkin.handle(command.name, context)
```

`handle` 返回 `CommandReply`；业务方法还包括 `check_in(context)`、`profile(context)`、
`leaderboard(context, limit=10)`，分别返回不可变结果对象。

## 验证

```text
python -X utf8 -m pytest -q tests/test_checkin.py --basetemp=tmp/pytest-checkin-feature --tb=short
25 passed

python -m ruff check src/hd2bot/services/checkin.py tests/test_checkin.py --output-format concise
All checks passed!
```

覆盖日切、连签封顶、断签、满级、数据库重开、跨连接并发、时钟回拨、四种聊天场景、
跨群重复领奖、不同身份领域隔离、群榜单隔离、私聊榜单限制、昵称缺失与更新、所有等级
经验边界、36 个称号与本地 Wiki 对照、写入失败回滚。

只修改本功能的独立 service/asset/tests/research 文件；没有启动机器人、实际发送消息
或修改生产签到数据。
