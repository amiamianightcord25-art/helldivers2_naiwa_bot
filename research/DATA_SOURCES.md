# 静态目录来源

`src/hd2bot/assets/planets.json` 是本地离线名称/别名/环境目录，2026-09-11 整理。
不含动态战况、个人资料、账号标识或凭据；运行时读取本项目内置目录，不依赖运营者的历史工作目录。

- index/settingsHash 关联来自官方公开 `WarInfo`。
- 简体、繁体、英文星球名称和星区名选自
  [Stonemercy/Galactic-Wide-Web](https://github.com/Stonemercy/Galactic-Wide-Web)
  的 `data/json/planets/planets.json`、`sectors.json`，按 index 重排并增加常用中文别名。
- 该来源为 GPL-3.0；保留其 [许可证文本](licenses/Galactic-Wide-Web.txt)，该派生目录沿用 GPL-3.0。
- 环境及灾害名称来自公开社区 `/api/v1/planets` 响应。
- 游戏名称与内容归其各自权利人。没有复制第三方应用代码。

新星球若没有本地名字，展示 API 名或 `星球 #index`。静态目录不负责推断实时控制方。
