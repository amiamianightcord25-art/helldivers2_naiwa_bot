# 研究记录阅读方式

根目录 [README](../README.md) 和专题文档描述当前用法；本目录保存来源、设计依据和分阶段验收。带日期的记录里，“当前”“本轮”、测试数量及实机状态只对应当次核验，不会自动随代码升级。本文不是新的联网核验报告。

## 当前功能参考

| 内容 | 文档 |
| --- | --- |
| QQ 场景、菜单、签到、小游戏 | [社区功能](../COMMUNITY_FEATURES_CN.md) |
| 游戏订阅与机器人更新日志 | [主动推送](../GROUP_PUSH_CN.md) |
| 可选二维码与欢迎事件 | [欢迎提示](../WELCOME_QR_CN.md) |
| Linux 安装及迁移 | [部署](../DEPLOYMENT_CN.md) |
| 配置模板与公开分发范围 | [空白配置](../DISTRIBUTION_CN.md) |
| GWW 功能在 QQ 中的实现状态 | [功能对照](GWW_PARITY.md) |
| Wiki 目录与日常同步 | [Wiki 说明](WIKI_CATALOG_NOTES.md) |

## 来源与阶段证据

- [API 研究](API_NOTES.md)、[GWW 初次接入](GWW_FEATURE_NOTES.md)、[Tenko 渲染研究](TENKO_RENDER_NOTES.md)：所注明日期或固定提交的技术依据。
- [原生菜单 API](qq_menu_20260919.md)、[Markdown 限制](qq_markdown_limits_20260919.md)：当次读取的腾讯文档，平台可能更新；应用实际能力还需验证。
- [称号最终核对](rank_verification_20260919.md) 为中文称号依据；此前 [低等级初查](rank_verification_20260919_low.md)、[高等级初查](rank_verification_20260919_high.md) 和 [签到初稿](checkin_feature_20260919.md) 中的候选译名已经被最终核对取代。[英文等级对照](rank_verification_20260919_levels.md) 记录等级与英文名。
- [小贴士来源](loading_tips_sources_20260919.md)、[中文整理](loading_tips_cn_20260919.md)、[战备英雄玩法](stratagem_hero_20260919.md)：资料来源与聊天版取舍。
- [静态目录来源](DATA_SOURCES.md)、[Wiki 初次导入](WIKI_CATALOG.md)、[费用补全](WIKI_UNLOCK_NOTES.md)：来源、许可、当时数量；实际数量以当前资料状态为准。
- [初版验收](ACCEPTANCE.md)、[阶段验收](V02_ACCEPTANCE.md)、[里程碑](MILESTONES.md) 不是当前测试报告。当前自动化结果以仓库 Checks 为准，真实 QQ 收发需另行验证。
- [旧 SDK](QQ_SDK_NOTES.md) 与[首次 NoneBot 迁移](NONEBOT_MIGRATION_NOTES.md) 保留历史设计背景，不用于配置现行机器人。

公开研究保留技术结论和公共来源，不包含运营者路径、真实连接配置、账号资料或运行回执。本机交接文档使用被 Git 忽略的 `.private.md` 文件。
