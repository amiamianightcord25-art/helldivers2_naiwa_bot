> 历史记录：初次目录导入与来源核对，后续补充装饰图片等功能。下文“当前”“本轮”、测试数量和运行状态均指记录时点，不是现行部署说明。当前使用方法见 [项目 README](../README.md)，文档范围见 [研究索引](README.md)。

# 装备百科离线目录

数据来源为 [Helldivers Wiki](https://helldivers.wiki.gg/) 公开的 HELLDIVERS 2 装备目录：
[Weapons](https://helldivers.wiki.gg/wiki/Weapons)、
[Stratagems](https://helldivers.wiki.gg/wiki/Stratagems)、
[Armor](https://helldivers.wiki.gg/wiki/Armor)、
[Boosters](https://helldivers.wiki.gg/wiki/Boosters)、
[Cosmetics](https://helldivers.wiki.gg/wiki/Cosmetics)。

范围是这五个目录中的装备记录、武器详细统计与配件表，不包括整个 Wiki、HELLDIVERS 1、战术文章全文或每次补丁历史。
支援武器会同时出现在武器和战略配备类别；同一套装的盔甲与头盔分别列入盔甲/装饰。
装饰还包括披风、玩家名片、动作/胜利姿势、武器/载具涂装、称号；相同名称但不同外观类型会保留独立候选。
`Armor` 的头盔/披风列表和 `Cosmetics` 本身并非始终一致，因此装饰类别取两者的并集，独有条目保留其实际来源。

## 来源与准确性

- 2026-09-16 读取首页、许可信息以及 robots.txt，公开文章可正常访问。
- `robots.txt` 禁止 `/api.php` 等路径用于抓取，正式同步器只访问允许的公开 `/wiki/` 文章和 `/images/` 武器插图，不使用 API、特殊查询页面、浏览器挑战或任何私有会话。
- 武器读取目录和每个武器页面的主信息框，保留原始单位与数值，不从文字猜伤害；盔甲、战略配备、强化资源和装饰的数值/获取方式读取目录表。
- `revision_source_url` 明确修订号对应的来源页；`source_url` 保留该装备的专属文章地址。没有专页的涂装/称号使用目录锚点。
- Wiki 标记 `Potentially Outdated Pages` 的武器将显示该提醒，不能把同步日期等同于游戏数值的实际更新时间。
- **真实中文入口为 [绝地潜兵中文维基](https://helldivers.wiki.gg/zh/)**；武器目录为 `/zh/wiki/武器`，不是 `/zh/wiki/Weapons`。初次只检查英文页面语言链接而漏掉这个入口的判断已纠正。同步器现每次同时校验中英文来源，中文真实名称/说明优先，英文编号和名称继续保留供检索。
- 中文站仍在建设：本次中文武器目录有 132 个中文名称；战略配备表只有 2 行；盔甲表 107 件中仅 1 个中文名称，其被动字段还存在上游模板错误；装饰目录 400 条中仅 1 个中文名称；`/zh/wiki/强化资源` 正常公开请求返回 404。目录中的红链不视作已存在的详情页；还核查了导航中指向已存在中文页的正确名称，例如目录红链 `AC-8机炮` 与真实文章 `AC-8_机炮` 的空格差异。
- 名称按完整型号匹配到稳定英文 ID，且限定类别/外观类型；保留 `name_source_url`、`name_revision`。已存在的中文详情只合并真正中文的字段值/说明，`chinese_detail_source` 标明其来源、修订号及覆盖字段；英文补缺出处保留在 `source_url`/`revision_source_url`，`source_urls` 列出中英文来源，绝不将英文值标成中文站翻译。
- `wiki_aliases.json` 是独立维护的社区俗称，与中文 Wiki 原名区分；不能当成全部官方中文翻译。别名可以一对多，例如“喷子”“筒子”“轨道炮”。
- 不执行网页脚本，不把抓取内容当成项目指令。

## 许可与图片

英文站文字及整理出的对应数据遵循 [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/)：
署名 Helldivers Wiki contributors，非商业使用，英文派生数据采用相同许可。
中文站页脚实际声明 [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/deed.zh-hans)，
因此中文来源的名称与说明保留这一许可，不能把中文内容强制改成英文站的非商业许可。
JSON 是不同来源内容的集合，`source.licenses` 分别声明许可；`name_license`、`chinese_detail_source.license` 标明中文部分，
原英文来源和英文数据许可仍独立保留，中文来源使用署名及相同方式共享条件。
这里的文本许可并不表示 Wiki 拥有游戏美术版权。武器插图仍归各自权利人所有，条目的 `image_credit` 指向对应 Wiki 文件页，`image_url` 保留取得的原始地址。

只抓武器信息框的主图，不抓整页截图或导航图标。图片必须来自已观察到的 `https://helldivers.wiki.gg/images/`，
仅接受 Pillow 核验的 PNG/JPEG/WebP，下载上限 5 MB、像素上限 25 MP，缩至最长边 800 像素后转 WebP。
`image_path` 为生成内容的 SHA-256 文件名，运行时只读取本地资源，HTML 渲染器无需访问远程图片。

## 重复同步

```powershell
.venv/Scripts/python.exe scripts/sync_wiki_catalog.py
.venv/Scripts/python.exe scripts/sync_wiki_catalog.py --refresh --output data/wiki_catalog.json --image-dir data/wiki_images --cache-dir .cache/wiki
.venv/Scripts/python.exe scripts/sync_wiki_catalog.py --offline
```

默认 bundled 数据在 `src/hd2bot/assets/wiki_catalog.json`，图片在同级 `wiki_images/`。
脚本支持 `--output`、`--image-dir`、`--cache-dir`、`--aliases`、`--refresh`、`--offline`、`--delay`。

- 串行访问，默认请求之间至少 1 秒；本地 HTTP 缓存 24 小时。
- 刷新时使用 ETag/Last-Modified 条件请求；304 使用已有正文。相同正文哈希复用解析缓存。
- 429/502/503/504 总计最多三次请求，尊重数字 Retry-After（最大等待 30 秒），其它拒绝不重复尝试。
- 中文目录和已观察到的中文装备页共用缓存/条件请求；404 缺页会记录负缓存，24 小时后重新核对，中文页面后来建成便会纳入。中文抓取失败、已有中文目录消失、名称覆盖突然下降或已有中文详情丢失，都使本次发布失败并保留旧完整快照，不静默退回全英文。
- 五个目录和所有武器详情均成功后，核对分类最低数量及上次数量下降超过 20% 的异常，再用临时文件原子替换完整目录。文字抓取/解析失败退出码为 1，旧目录不变。
- 单张新图片失败记录在 `images.failures`；已有图片仍存在时保留旧图，不因一张图片 404 把整个目录覆盖为空。
- 输出字段版本 `schema_version=1`；同步时间、分类数量、修订号、图片覆盖和错误均记录于 JSON。
- `synced_at` 来自真实的正文 HTTP 校验时刻；纯离线重建不冒充新鲜联网数据，构建时间另记 `built_at`。
- 原子发布前使用机器人实际的快照加载器校验完整 schema；字段、别名或许可错误不会覆盖有效目录。
- 人工中文别名保存在独立 JSON 中，每次同步重新叠加，不被上游内容覆盖。
- 初次运行定期同步时可复制首次导入的 HTTP/解析缓存并复制 bundled 图片到动态图片目录；路径在脚本调用中明确指定，不要移动正在使用的缓存。

## 验证

`tests/test_wiki_sync.py` 覆盖表格合并单元格/缺少装饰图标、战略配备方向码、数字前的隐藏零、HD1/外站路径排除、正文缓存与 304、限流重试上限、失败时保留旧目录，以及拒绝伪装成 PNG 的 HTML 和图片内容哈希命名。

本次快照共 **782 条**：137 武器、114 战略配备、109 盔甲、18 强化资源、404 装饰。
其中装饰包含 `Armor` 独有的 2 顶头盔和 2 件披风；来源未提供其数值价格，所以不猜测。
137 件武器全部附本地 WebP 主图，约 2.14 MB，下载错误 0；132 个武器页面被 Wiki 标记可能未跟进当前补丁，目录保留这个来源提醒。
最终实际数量和图片覆盖率以 bundled JSON 的 `coverage` 与 `images` 元数据为准。
