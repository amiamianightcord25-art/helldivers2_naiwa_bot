# 2026-09-19 称号等级独立联网核对

核对对象：`src/hd2bot/assets/rank_titles.json`。本报告重新访问了在线资料，未把项目已有研究记录当作事实依据。未修改业务文件。

## 结论

- 36 项英文称号及其解锁等级，与当日在线 Helldivers Wiki 的 Levels 表逐项一致。
- 最高 300 级有 Arrowhead 在 Steam 发布的官方公告支持。
- 项目所记 `1.007.000（2026-08-12）` 与 Wiki 变更记录一致；Steam 同日公告标题为 `Devoid of Liberty: 7.0.0`。
- 140 级 `Private`、150 级 `Super Private` 确实位于高等级区段，不能按现实军衔常识移到低等级。
- 英文和等级正确，不等于项目简体中文名称已经取得官方本地化证据。该 JSON 当前也明确写着“简体中文参考译名；尚未逐项核实游戏客户端官方简中文本”。本轮等级核对无法将这些参考译名认定为官方简中。

## 官方来源

1. Steam 官方社区公告 `Devoid of Liberty: 7.0.0`
   - URL: https://steamstore-a.akamaihd.net/news/externalpost/steam_community_announcements/1840944183773413
   - 经 Steam News API 当日重新读取：`feedname=steam_community_announcements`，`date=1786525290`，即 **2026-08-12 09:01:30 UTC**。
   - “Level Cap Increased”段原文：
     > We've expanded the Helldiver rank system beyond Level 150, adding 15 new ranks and titles that carry Helldivers all the way up to Level 300.
2. Steam 官方社区公告 `HELLDIVERS 2 Is Raising the Level Cap to 300`
   - URL: https://steamstore-a.akamaihd.net/news/externalpost/steam_community_announcements/1840310314341623
   - 时间 **2026-08-06 12:01:00 UTC**。
   - 此篇为上限调整预告；实际更新日期应引用 8 月 12 日公告。
3. 使用的公开 API：
   - https://api.steampowered.com/ISteamNews/GetNewsForApp/v0002/?appid=553850&count=100&maxlength=0&format=json

## 英文称号和等级来源

在线页面：https://helldivers.wiki.gg/wiki/Levels

通过浏览器当日读取，页面页脚显示最近编辑于 **2026-09-09 09:28**。普通 Python HTTP 获取返回 403，但浏览器页面正常，完整表格可读。

页面导语仍残留“21 titles”的旧描述；以下完整等级表已含 36 项。核对采用完整表格，并与官方新增 15 项公告交叉验证，不采用已过时的导语数量。

| 等级 | 在线英文称号 | 项目英文及等级 |
|---:|---|---|
| 1 | Cadet | 一致 |
| 5 | Space Cadet | 一致 |
| 10 | Sergeant | 一致 |
| 15 | Master Sergeant | 一致 |
| 20 | Chief | 一致 |
| 25 | Space Chief Prime | 一致 |
| 30 | Death Captain | 一致 |
| 35 | Marshal | 一致 |
| 40 | Star Marshal | 一致 |
| 45 | Admiral | 一致 |
| 50 | Skull Admiral | 一致 |
| 60 | Fleet Admiral | 一致 |
| 70 | Admirable Admiral | 一致 |
| 80 | Commander | 一致 |
| 90 | Galactic Commander | 一致 |
| 100 | Hell Commander | 一致 |
| 110 | General | 一致 |
| 120 | 5-Star General | 一致 |
| 130 | 10-Star General | 一致 |
| 140 | Private | 一致 |
| 150 | Super Private | 一致 |
| 160 | 5-Star Super Private | 一致 |
| 170 | 10-Star Super Private | 一致 |
| 180 | Ranger | 一致 |
| 190 | Ranger, Eagle Class | 一致 |
| 200 | Ranger, Bald Eagle Class | 一致 |
| 210 | Divemaster | 一致 |
| 220 | First Divemaster | 一致 |
| 230 | Divemaster Major | 一致 |
| 240 | Command Divemaster Major | 一致 |
| 250 | Master Divemaster | 一致 |
| 260 | Master Divemaster Major Omega | 一致 |
| 270 | Max Rank | 一致 |
| 280 | Max Rank Infinity | 一致 |
| 290 | Max Rank Infinity +1 | 一致 |
| 300 | Rank 300 | 一致 |

## 简中一手资料渠道探索

GitHub 仓库搜索 `helldivers 2 localization`、`helldivers2 strings chinese`、`helldivers localization` 均未返回仓库。Google 搜索 `helldivers 2 localization strings github` 找到社区 API 和 `helldivers-2/json`，但搜索结果本身未展示游戏官方简中称号文本。未找到可确认为客户端官方简中文字典的完整公开来源；不据此臆造称号，也不把社区译名当成官方文本。

要逐字确认简中，应以游戏客户端“军械库 → 角色 → 称号”画面或可核对版本的游戏本地化文本为直接证据。此项由主任务继续搜寻中文资料。
