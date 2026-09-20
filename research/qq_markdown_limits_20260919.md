# QQ Markdown 与消息按钮限制核对

读取日期：2026-09-19。直接读取腾讯官方文档正文；未依赖第三方教程或从 SDK 常量反推平台上限。本文件只记录资料，不更改机器人行为。

## 适用于本次改动的结论

- C2C 和群聊可发送原生 `markdown.content`，当前官方称已向所有机器人开放；频道仍需内邀。频道私信 API 继承子频道参数，不应把频道私信当普通 C2C，未找到其单独免开通承诺。
- `markdown.content` 的最大字符数、最大 UTF-8 字节数：**本次读取的现行官方消息页和 Markdown 格式页均未公开定值**。不要把 4,096 / 5,000 / 20,000 等库或产品分块阈值标注为腾讯平台硬上限。
- 当前 C2C/group 参数表规定按钮 `label` 最多 10 字符；未说明中文算 1 还是 2，也未说明按 UTF-8 字节计算。`visited_label` 未另列长度定值，可采用相同 10 字符应用约束，但要明确这是本项目保守选择。
- 当前 API-v2 `rows`/`buttons` 表没有写具体最大数，但会返回“内联键盘行/列超限”。2022 年官方 SDK 说明为最多 5 行、每行最多 5 个按钮；可当兼容上限，不应宣称这是现行 API-v2 参数表重新确认过的值。
- `action.enter` 明文为**仅单聊可用**，群聊/频道不得承诺“点击即自动发送”。群指令按钮是填入 `@bot data`，用户仍需发送。
- Markdown 与 `content/ark` 互斥；全局错误码还明确带 Markdown 的消息只支持 Markdown 本身或与 keyboard 组合。当前资料没有证据支持 `media + markdown + keyboard` 混发。图片保留普通图片消息时，按钮使用后一条简短 Markdown 消息，每条使用不同回复序号，并计算进被动回复条数。
- Markdown 内图片是另一种方式：图片必须有可公网读取的 URL，平台下载转存；不能把本地图片路径、图片字节或 `file_info` 直接塞进 Markdown 图片 URL。

## 官方来源与日期

| 来源 | 页面显示的上次更新 | 用途 |
| --- | --- | --- |
| [Markdown 消息](https://bot.q.qq.com/wiki/develop/api-v2/server-inter/message/type/markdown.html) | 2026-07-22 20:51:48 | 开放范围、语法、图片 URL |
| [发送单聊消息](https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_users_user_openid_messages.post.html) | 2026-09-03 17:58:35 | C2C 参数、按钮、错误码、被动消息 |
| [发送群聊消息](https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_groups_group_openid_messages.post.html) | 2026-09-03 17:58:33 | 群参数、按钮、错误码、被动消息 |
| [消息收发概述](https://bot.q.qq.com/wiki/develop/api-v2/server-inter/message/overview.html) | 2026-07-21 23:01:44 | 四场景时效、频率、去重 |
| [发送子频道消息](https://bot.q.qq.com/wiki/develop/api-v2/server-inter/channel/message/send.html) | 2026-07-21 03:25:45 | 子频道参数、时效、消息审核 |
| [频道私信](https://bot.q.qq.com/wiki/develop/api-v2/server-inter/channel/message/dms.html) | 2026-07-21 03:25:45 | DMS 参数继承及次数 |
| [全局错误码](https://bot.q.qq.com/wiki/develop/api-v2/openapi/error/error.html) | 2026-07-21 21:50:42 | Markdown/keyboard/URL 拒绝 |
| [API 变更记录](https://bot.q.qq.com/wiki/develop/api-v2/changelog.html) | 2026-09-16 20:01:05 | 图片转存检查字段 |
| [旧官方 SDK InlineKeyboard](https://bot.q.qq.com/wiki/develop/pythonsdk/model/inline_keyboard.html) | 2022-05-19 10:40:08 | 5 行 × 5 按钮旧限制，补充证据 |
| [旧官方 SDK 带按钮消息](https://bot.q.qq.com/wiki/develop/pythonsdk/api/message/post_keyboard_message.html) | 2022-06-24 18:27:11 | 仅 Markdown 带按钮旧说明，补充证据 |

以上时间按页面显示抄录，不额外推定显示时区。

## 明确原句

### 开放范围和内容字段

Markdown 页“2026/04/23 能力更新说明”原句：

> 单聊场景、群聊场景自定义 Markdown 消息能力已开放到所有机器人均可使用，无需单独申请 Markdown 模版，频道场景目前需要内邀开通。

C2C/group `markdown` 参数原句：

> Markdown 消息。msg_type=2 时必填 注意: 填写此字段后 content/ark 必须全为空

C2C/group `content` 参数原句：

> 文本内容。msg_type=0 时为全文 注意: 传了 markdown 后此字段必须为空

现行消息字段中的 `template_id` / `custom_template_id` 均标“【已废弃】”，原生 `content` 可直接使用。旧模板仍可能产生模板专用错误，不能把所有错误解释为原生 Markdown 未开通。

### 图片

Markdown 页原句：

> 对于 markdown 消息内的图片资源，请使用可在公网访问的资源 url，开放平台会下载转存该资源。

格式示例为 `![text #208px #320px](https://...)`。

2026-08-10 变更记录原句：

> 发送 Markdown 消息：新增可选参数 force_verify_image_resource。开启后，当图片资源转存失败时，将中断消息发送并返回失败（默认关闭，保持原有行为）。

### 按钮

C2C/group 现行字段定义：

| 字段 | 原句 / 可确定含义 |
| --- | --- |
| `Keyboard.id` / `Keyboard.content` | 模板与自定义布局互斥 |
| `Button.id` | “按钮 ID。同一键盘内唯一” |
| `RenderData.label` | “按钮文字，最多 10 字符” |
| `RenderData.visited_label` | “点击后文字，不传则保持不变” |
| `RenderData.style` | “0：灰色线框，1：蓝色线框 3: 白色背景+红色字体, 4:蓝色背景+白色字体” |
| `Action.type=2` | “指令按钮：自动在输入框插入 @bot data” |
| `Action.data` | “回调数据。type=1/2 时必填” |
| `Action.enter` | “指令按钮可用，点击按钮后直接自动发送 data，仅单聊可用，默认 false。支持版本 8983” |
| `Action.reply` | “指令按钮可用，指令是否带引用回复本消息，默认 false。支持版本 8983” |
| `Action.anchor` | 设置后忽略 enter；1 唤起选图器，仅手机 8983+ 单聊，桌面不支持 |
| `Permission.type` | “0=指定用户, 1=管理员, 2=所有人” |
| `Modal.content` | “最多40个字符, 不能有URL” |
| `Modal.confirm_text` / `cancel_text` | 各最多 4 字符 |

`Action.data` / `Button.id` 的数字长度上限未在本次当前字段表中公开。`40034108` 说明实际存在指令参数长度限制，但该码并未给出上限数字。

群页自带的 Markdown 签到示例写了 `enter:true`，与同页“仅单聊可用”的字段定义不一致。本项目应以明确的场景限制为准，群按钮按填入指令设计，不能由示例反推群自动发送受支持。

旧官方 SDK InlineKeyboard 原句：

> 数组的一项代表消息按钮组件的一行,最多含有 5 行

> 数组的一项代表一个按钮，每个 InlineKeyboardRow 最多含有 5 个 Button

旧带按钮消息页原句：

> 仅 markdown 消息支持消息按钮。

这句话来自 2022 SDK 页；现行群页只说“支持文本/Markdown/富媒体等类型，可附带内嵌键盘”，未明确逐项列出 keyboard 能否与非 Markdown 组合。稳妥组合是官方现行示例里的 `msg_type=2 + markdown + keyboard`。不可把现行页笼统描述当作已确认纯图片能单消息带键盘。

## 四场景被动回复限制

| 场景 | 有效期 | 每条入站消息允许回复次数 | 备注 |
| --- | --- | --- | --- |
| 普通 QQ 单聊 C2C | 60 分钟 | 4 次 | 总览与接口开头一致；`msg_id` 字段残留“5分钟”冲突，见下文 |
| QQ 群聊 group | 5 分钟 | 5 次 | 总览、接口开头与字段一致 |
| 文字子频道 channel | 5 分钟 | 未公开固定次数 | 同一子频道每秒最多 5 条；不是每消息 5 次 |
| 频道私信 DMS | 5 分钟 | 不限条数 | DMS 页明确“被动消息没有条数限制”，仍受时效和通用 API 限频 |

C2C 页开头原句：

> 被动消息有效时间 60 分钟，每个消息最多回复 4 次

但同页 `msg_id` 行写“5 分钟内有效”。《消息收发概述》重复 60 分钟 / 4 次，因此记录为官方文档内部冲突；常规即时回复保持 5 分钟内不会触及该冲突，不应为了展示按钮延迟处理。

群页原句：

> 被动消息有效时间 5 分钟，每个消息最多回复 5 次

子频道页原句：

> 不论主动消息还是被动消息，在一个子频道中，每 1s 只能发送 5 条消息。

> 被动回复消息有效期为 5 分钟。超时会报错。

DMS 页原句：

> 私信场景下，被动消息没有条数限制。

> 和发送子频道消息参数一致。

C2C/group `msg_seq` 原句：

> 回复消息的序号，与 msg_id 联合使用，避免相同消息 id 回复重复发送，不填默认是 1。相同的 msg_id + msg_seq 重复发送会失败。

应用实现建议（非官方原句）：图片 + 独立按钮 Markdown 消耗 2 次被动回复；同一请求的兜底也必须考虑剩余额度，不能任意重置序号。发送超时无法确定消息是否已送达，不宜立即无条件补发另一格式。

## 拒绝码：区分权限、格式、时效和平台状态

以下为官方明文，不把泛化错误自动当 Markdown 权限错误。

| 错误码 | 官方描述 | 来源 |
| --- | --- | --- |
| 50037 | 带有markdown消息只支持 markdown 或者 keyboard 组合 | 全局 |
| 50054 | markdown 模版参数错误 | 全局 |
| 50055 | 无效的 markdown content | 全局 |
| 50056 | 不允许发送 markdown content | 全局 |
| 50057 | markdown 参数只支持原生语法或者模版二选一 | 全局 |
| 304036 | 没有 markdown 模板的权限 / 无Markdown模板权限 | 全局/群 |
| 304037 | 没有发消息按钮组件的权限 | 全局 |
| 304038 | 消息按钮组件不存在 | 全局 |
| 304039 | 消息按钮组件解析错误 | 全局 |
| 304040 | 消息按钮组件消息内容错误 | 全局 |
| 305007 | 键盘样式参数错误 | 群 |
| 40034008 | markdown参数有空值 | C2C/group |
| 40034009 | markdown参数有换行符 | C2C/group；勿误用为原生content不能换行，官方原生示例有换行 |
| 40034010 | 模版参数中不能含有markdown语法 | C2C/group；模板参数约束 |
| 40034011 | 无效的markdown内容 | C2C/group |
| 40034029 | 内联键盘行/列超限 | C2C/group |
| 40034106 | 消息不支持该指令类型 | C2C/group |
| 40034108 | 指令参数长度超限 | C2C/group |
| 40034109 | 指令参数解析失败 | C2C/group |
| 40034124 | markdown消息参数错误 | C2C/group |
| 40034127 | 无markdown模板权限 | C2C/group |
| 40054007 | 消息长度超限 | C2C/group；未给数字上限 |
| 40054018 | 消息过长或异常 | C2C |
| 40034004 | 富媒体信息转存失败 | C2C/group |
| 40034128 | 被动回复时间或次数超限 | C2C/group |
| 304103 / 40034005 | 消息ID已过期，不能回复 / 回复消息msg_id已过期 | C2C/group |
| 40054005 | 消息被去重 | C2C/group |
| 40034105 | 主动消息发送失败，无权限 | C2C/group；与 Markdown 不是同一含义 |

频道主动消息 `304023` / `304024` 会带 `message_audit`，表示审核相关状态；不能当作普通格式拒绝即刻重复发送。

## 消息链接与域名

当前 Markdown 语法页公开支持标准链接 `[文本](https://...)` 和 `<https://...>`。但这不构成任意域名、任何场景、任何应用均可发送的保证。

官方全局错误码原句：

> 304003 URL_NOT_ALLOWED url 未报备

现行群消息错误码原句：

> 40054010 不允许发送URL；请移除消息中的URL

本次当前消息/Markdown/介绍文档未查到完整的消息域名白名单可配置入口、名单容量、允许域名列表或“链接全部无需报备”承诺。**未公开/未核实，不猜测。** 服务器请求来源 IP 白名单是不同配置，不能用来解释消息 URL 被拒绝。

应用实现建议（非官方原句）：按钮指令值使用站内 bot 指令可避免把外链资格作为基本操作前提。明确返回 URL 不允许时可展示不含外链的简短文本，不应不断重试原请求，也不应把 URL 拒绝错报成整个 Markdown 能力不可用。
