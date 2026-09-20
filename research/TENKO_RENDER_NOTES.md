> 历史记录：固定上游提交的渲染研究。下文“当前”“本轮”、测试数量和运行状态均指记录时点，不是现行部署说明。当前使用方法见 [项目 README](../README.md)，文档范围见 [研究索引](README.md)。

# Tenko 历史 BF1 渲染参考

核对日期：2026-09-16。只读取上游 Git 历史与源码，未执行第三方脚本。研究仓库位于项目忽略目录 `tmp/tenko-research/`。

## 历史版本与证据

当前 Tenko 已切换架构，不能用当前默认分支判断旧版 BF1 功能。此次定位以下历史：

| 提交 | 日期 | 意义 |
| --- | --- | --- |
| [`e0123e3bc08283e761a23731c99d1fcaa73e8cda`](https://github.com/g1331/tenko/commit/e0123e3bc08283e761a23731c99d1fcaa73e8cda) | 2023-04-29 | 加入 BF1 生涯 HTML 草稿 |
| [`e49afb53efd0261eebe1dd7a7521758e61e7e352`](https://github.com/g1331/tenko/tree/e49afb53efd0261eebe1dd7a7521758e61e7e352) | 2026-08-30 | 旧 Graia 代码删除前的完整快照，本说明的主要依据 |
| [`8debbdb004d7226d1fe9f5ef59f977569caef165`](https://github.com/g1331/tenko/commit/8debbdb004d7226d1fe9f5ef59f977569caef165) | 2026-08-30 | 删除旧 Graia/BF1 实现 |

## 实际流程

必须区分两种实现：历史 BF1 项目的成品使用 Pillow 拼图；仓库也包含 BF1 HTML 草稿和可用的通用 HTML 渲染工具，不能把两者误报成已接通的 BF1 HTML 渲染。

- [BF1 HTML 草稿](https://github.com/g1331/tenko/blob/e0123e3bc08283e761a23731c99d1fcaa73e8cda/utils/bf1/draw/template/stat_template.html)：Jinja 变量配 CSS Grid 三列，分别为身份/生涯、武器、载具；背景 cover 居中；圆角、阴影、白色 0.8 alpha 面板和 blur(10px)。不少数据区域只有标题或注释。同提交的 `PlayerStatPic.draw()` 也没有完成实际绘图。
- [成熟 BF1 PlayerStatPic](https://github.com/g1331/tenko/blob/e49afb53efd0261eebe1dd7a7521758e61e7e352/utils/bf1/draw/__init__.py#L413)：整理生涯、兵种、武器、载具、头像等数据，通过 Pillow 拼到 2000×1550 画布。背景模糊，左列身份/生涯/兵种/附加信息，中、右列各类最佳装备卡片。[`draw()`](https://github.com/g1331/tenko/blob/e49afb53efd0261eebe1dd7a7521758e61e7e352/utils/bf1/draw/__init__.py#L1368) 负责最终组合。
- [BF1 命令处理](https://github.com/g1331/tenko/blob/e49afb53efd0261eebe1dd7a7521758e61e7e352/modules/self_contained/bf1_info/__init__.py#L421)：并行取得数据后生成图片并发送，也保留文字分支；成功发送后删除临时图片。
- [通用 template2img](https://github.com/g1331/tenko/blob/e49afb53efd0261eebe1dd7a7521758e61e7e352/utils/text2img/__init__.py#L75)：Jinja `Template.render(params)` → `html2img()` → `graiax.text2img.playwright.HTMLRenderer.render()`。默认 viewport 为 1000×10，device scale factor 为 1.5；返回图像 bytes。

## JPEG 策略

- [通用 HTML 截图默认值](https://github.com/g1331/tenko/blob/e49afb53efd0261eebe1dd7a7521758e61e7e352/utils/text2img/__init__.py#L22)：`type="jpeg"`、`quality=80`、`scale="device"`。
- [BF1 成品保存](https://github.com/g1331/tenko/blob/e49afb53efd0261eebe1dd7a7521758e61e7e352/utils/bf1/draw/__init__.py#L1466)：`format="JPEG", quality=95`。历史临时文件仍使用 `.png` 后缀，内容实际为 JPEG；本项目不应继承这种格式/后缀不一致。
- 未发现上述路径提供按目标字节数自动调质量的逻辑，不能把压缩尺寸上限误称为旧版原有能力。

## 本项目的采用边界

参考其“分组面板、一屏生涯概览、模板与数据分离、JPEG 输出”思路，独立编写 HD2 的 HTML/CSS 模板与渲染器。建议用黑金配色及数据层实际拥有的 27 项统计，保留来源、获取时间和账号范围说明，不虚构 BF1 特有武器/载具/等级数据。

运行链应为 HD2 数据模型 → 显式模板上下文 → Jinja 自动转义 → 本地 Chromium/Playwright 截图 → JPEG 大小检查/压缩 → QQ 官方图片接口。渲染限时、并发限制、阻止远程页面资源及图片失败退回文字由本项目独立实现。

密集统计默认输出图片；帮助、错误、简单状态和短查询保留文本。CLI 保持文字输出，同时可以通过单独的预览命令验证 JPEG。

## 许可证与素材

核对的历史快照 [LICENSE](https://github.com/g1331/tenko/blob/e49afb53efd0261eebe1dd7a7521758e61e7e352/LICENSE) 为 GNU GPL v3。此次只研究流程和布局，不复制上游 Python、HTML、游戏背景、图标或字体到业务代码；原创模板使用本地系统字体/CSS 装饰。以后若实际复制其代码或模板，应单独评估并履行相关许可证义务，不能把引用链接当作许可证替代。
