"""Download instructions never request a time-limited credential challenge."""
import json
from pathlib import Path
from urllib.parse import urlsplit

from hd2bot.career.models import SNAPSHOT_RETENTION_DAYS

DEFAULT_CONFIG = Path(__file__).resolve().parents[3] / 'data/helper_download.json'


class CareerOnboarding:
    def __init__(self, path: Path = DEFAULT_CONFIG):
        self.path = Path(path)

    def download(self):
        try:
            raw = self.path.read_bytes()
            if len(raw) > 8192:
                raise ValueError()
            config = json.loads(raw)
            if not isinstance(config, dict):
                raise ValueError()
            url, password = config.get('url', ''), config.get('password', '')
            if not isinstance(url, str) or not isinstance(password, str):
                raise ValueError()
            if (len(url) > 1000 or len(password) > 64
                    or any(c.isspace() for c in url) or any(not c.isprintable() for c in password)):
                raise ValueError()
            parsed = urlsplit(url)
            if not url:
                return None
            if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError()
            return url, password
        except (OSError, ValueError, TypeError):
            return None

    def guide(self):
        download = self.download()
        location = ('下载链接：' + download[0] + '\n提取密码：' + (download[1] or '无')) if download else (
            '下载链接尚未发布，请向维护者领取最新版 HD2Bind ZIP；已经下载的可继续第 2 步。')
        return (
            '【第一次同步战绩 · 3 步完成】\n'
            '1️⃣ 下载并解压助手\n' + location + '\n'
            '电脑下载 ZIP 后右键“全部解压”，进入解压后的文件夹，双击 HD2Bind.exe。'
            '不要只在压缩包预览里打开。需要 64 位 Windows，保持 Steam 已登录在线，并先退出游戏。\n\n'
            '2️⃣ 助手已经打开后，再领取验证码\n'
            '回到本私聊发送：获取验证码。把收到的 6 位码填入助手。'
            '现在只是下载准备，不会提前开始 120 秒倒计时。\n\n'
            '3️⃣ 生成并提交\n'
            '在助手勾选说明，点击生成，再点复制，将整串 HD2v1: 胶囊发回本私聊。'
            '领取验证码后须在 120 秒内提交，等待“同步成功”再启动游戏。\n\n'
            '以后发送 战绩 查看保存的快照，不需要再次获取凭据。'
            '每次需要获取新的战绩数据，都要重新发送 获取验证码，并生成、提交一份新的胶囊；旧凭据不能复用。'
            f'快照从采集起仅保存 {SNAPSHOT_RETENTION_DAYS} 天，到期自动删除；'
            '群聊可凭快照ID查询。遇到问题发送：同步帮助。'
        )

    @staticmethod
    def group_guide():
        return (
            '【群聊只查询已保存的战绩快照】\n'
            '已有快照ID：@机器人 查询战绩 <快照ID>。\n'
            '首次查询：点击机器人头像，进入私聊并发送 获取战绩；'
            '若未显示私聊入口，请先添加机器人，再进入聊天。\n'
            '按私聊指引完成同步后，把返回的快照ID用于群聊查询。\n'
            '验证码和凭据胶囊只在同一个私聊领取、提交；群内提交不处理。'
        )

    @staticmethod
    def challenge(code):
        return (
            f'【第 2 步：验证码 {code}】\n'
            '120 秒从现在开始，只能用一次。\n'
            '① 回到已经解压打开的 HD2Bind.exe，粘贴这 6 位码。\n'
            '② 确认 Steam 在线、游戏已退出，勾选说明，点击“生成同步胶囊”。\n'
            '③ 点击“复制胶囊”，粘贴并发回当前私聊，等待战绩图片。\n'
            '还没下载或打开助手？先发送 获取战绩 完成准备，准备好后重新领取验证码。'
            '重新领取会使旧码失效。'
        )

    @staticmethod
    def troubleshooting():
        return (
            '【同步帮助】\n'
            '• 下载链接打不开：复制链接到浏览器，用电脑下载；需要提取密码时按提示填写。\n'
            '• ZIP 已下载：右键→全部解压，再运行文件夹里的 HD2Bind.exe；两个运行文件请放在一起。\n'
            '• 生成按钮不可点：检查 Steam 在线、游戏已退出、验证码填满 6 位、说明已勾选。\n'
            '• 验证码过期/已经使用：私聊发送 获取验证码，重新生成胶囊。\n'
            '• 不要改换聊天窗口：验证码和胶囊必须在同一个机器人私聊中领取、提交。\n'
            '• 发完胶囊：等待回复；若连接中断，先发送 战绩 看是否已更新，不重复发旧胶囊。\n'
            '• 更新战绩：查看已有快照不需要凭据；每次获取新数据都要重新领取验证码并生成新胶囊。\n'
            '• 提示游戏账号已绑定其他用户：在原私聊解绑后重新同步，或联系维护者处理换号。\n'
            '• 普通查询不会登录游戏；只有重新同步才使用临时凭据。'
        )
