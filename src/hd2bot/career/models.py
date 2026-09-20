import re
from dataclasses import dataclass
from datetime import UTC, datetime

FIELDS = (
    "totalBugKills", "totalAutomatonKills", "totalIlluminateKills", "totalEnemyKills",
    "totalFriendlyKills", "totalGrenadeKills", "totalMeleeKills", "totalEagleKills",
    "totalShotsFired", "totalShotsHit", "totalOrbitalsUsed", "totalDefensiveStratagemsUsed",
    "totalEagleStratagemsUsed", "totalSupplyStratagemsUsed", "totalReinforceStratagemsUsed",
    "totalStratagemsUsed", "totalSquadSaves", "totalDeaths", "totalMissionTime",
    "totalObjectivesCompleted", "totalSuccesfulExtractions", "totalMissionsCompleted",
    "totalMissionsWon", "totalStarsFromMissions", "totalSamplesCollected",
    "totalCreditsSpent", "totalCreditsEarned",
)
SNAPSHOT_RETENTION_DAYS = 3
SNAPSHOT_RETENTION_SECONDS = SNAPSHOT_RETENTION_DAYS * 86400


class CareerError(Exception):
    """Only a known code is exposed; never retain backend messages or credentials."""

    def __init__(self, code: str, retry_after: int | None = None):
        super().__init__(code)
        self.code = code
        self.retry_after = retry_after


@dataclass(frozen=True)
class CareerStats:
    values: dict[str, int]
    queried_at: datetime
    cached: bool = False
    local_cached: bool = False
    refreshed: bool = False
    mock: bool = False
    snapshot: bool = False
    account_scope: str = "configured_account"
    share_id: str | None = None
    player_name: str | None = None
    steam_id: str | None = None
    expires_at: datetime | None = None


ERROR_MESSAGES = {
    "snapshot_expired": "该快照已超过 3 天并自动删除，请私聊重新同步。",
    "snapshot_not_found": "未找到可查询的快照：ID 无效、已关闭分享或已超过 3 天并自动删除。",

    "challenge_required": "请先下载并打开助手，再私聊发送 获取验证码。",
    "challenge_mismatch": "验证码不属于当前私聊或输入有误，请在领取验证码的同一个私聊中提交；也可发送 获取验证码 重来。",
    "challenge_expired": "验证码已超过 120 秒，请在助手已打开时发送 获取验证码 重新领取。",
    "challenge_locked": "验证码尝试次数过多，请重新领取。",
    "challenge_rate_limited": "验证码请求太快，请等 10 秒再试。",
    "replay_rejected": "这份胶囊已经使用（REPLAY_REJECTED）。先发 战绩 查看结果；需要重新同步时发送 获取验证码。",
    "submission_unknown": "提交后未收到结果，可能是连接中断或等待超时。先发送 战绩 查看快照；不要重复提交同一胶囊。",
    "challenge_unknown": "验证码请求已提交，但未收到结果。请等 10 秒后重新发送 获取验证码，并使用最新收到的验证码。",
    "share_unknown": "分享设置已提交，但未收到结果。请发送 战绩 查看当前分享 ID；旧 ID 可能已经失效。",
    "unshare_unknown": "关闭分享已提交，但未收到结果。请稍后发送 战绩 确认当前分享状态，不要重复操作。",
    "revoke_unknown": "解绑已提交，但未收到结果。请稍后发送 战绩 确认状态，不要重复操作。",
    "mock_mode": "当前为离线模拟模式，无法同步、管理真实战绩或查询真实分享 ID；发送 战绩 可查看模拟数据。",

    "snapshot_required": "还没有战绩快照，请运行助手并私聊绑定一次。",
    "snapshot_capture_failed": "本次同步未完成，原有快照仍保留；请退出游戏后重新生成参数。",
    "snapshot_capture_timeout": "同步超时，原有快照仍保留；请稍后重新生成参数。",
    "binding_required": "请先私聊发送 /获取战绩，按提示用助手生成胶囊。",
    "binding_revoked": "绑定已撤销，请重新生成并绑定。",
    "binding_busy": "绑定正在处理，请稍后重试。",
    "invalid_user_id": "无法识别当前私聊账号。",
    "invalid_binding_envelope": "胶囊不完整或已损坏。请用助手的“复制胶囊”按钮复制整串 HD2v1: 内容后发送，不要只发验证码。",
    "binding_code_expired": "胶囊已超过 120 秒。请发送 获取验证码，回到助手重新生成后立即提交。",
    "binding_code_used": "这份绑定参数已经使用，请重新生成。",
    "account_already_bound": "该游戏账号已绑定其他私聊账号，请先在原账号解绑。",
    "account_mismatch": "票据账号核验未通过，请重新生成绑定参数。",
    "playfab_expired": "临时登录票据已失效，请运行助手重新绑定。",
    "playfab_unavailable": "票据核验服务暂时不可用，请稍后重试。",
    "arrowhead_http_403": "登录票据未被接受，请运行助手重新绑定。",
    "private_message_required": "此命令只接受机器人私聊。",

    "not_configured": "战绩服务尚未配置。请联系机器人维护者。",
    "invalid_config": "战绩服务配置不可用。请联系机器人维护者检查连接配置。",
    "authentication_failed": "战绩服务连接凭据已失效或被拒绝，请联系机器人维护者更新。",
    "binding_forbidden": "战绩服务未允许访问当前配置的账号。请联系机器人维护者。",
    "timeout": "战绩查询超时，请稍后重试。",
    "unavailable": "战绩服务暂时无法连接，请稍后重试。",
    "rate_limited": "战绩服务请求较多，请稍后重试。",
    "too_many_pending_requests": "战绩服务正在处理其他查询，请稍后重试。",
    "refresh_cooldown": "游戏会话更新正在冷却，请稍后重试。",
    "valid_playfab_session_required": "当前游戏会话需要重新绑定，请联系机器人维护者。",
    "stored_account_mismatch": "战绩服务的账号绑定不一致，请联系机器人维护者。",
    "backend_timeout": "战绩后端查询超时，请稍后重试。",
    "refresh_timeout": "游戏会话更新超时，请稍后重试。",
    "schema_error": "战绩服务返回的数据不完整或格式已变化，请稍后重试。",
    "backend_unavailable": "战绩后端暂时不可用，请稍后重试。",
}


def parse_reply(payload, request_id: str, binding: str) -> CareerStats:
    if not isinstance(payload, dict):
        raise CareerError("schema_error")
    if payload.get("requestId") not in (None, request_id):
        raise CareerError("schema_error")
    if payload.get("ok") is False:
        code = payload.get("status")
        if not isinstance(code, str) or code not in ERROR_MESSAGES:
            code = "backend_unavailable"
        wait = payload.get("retryAfterSeconds")
        raise CareerError(code, wait if type(wait) is int and 0 < wait <= 300 else None)
    if (payload.get("ok") is not True or payload.get("requestId") != request_id
            or payload.get("bindingId") != binding or type(payload.get("fieldCount")) is not int
            or payload["fieldCount"] != len(FIELDS)):
        raise CareerError("schema_error")
    stats = payload.get("career")
    if not isinstance(stats, dict) or any(
        type(stats.get(key)) is not int or not 0 <= stats[key] <= 9007199254740991
        for key in FIELDS
    ):
        raise CareerError("schema_error")
    stamp = payload.get("queriedAt")
    if (type(stamp) not in (float, int)
            or not 0 < stamp <= datetime.now(UTC).timestamp() + 60):
        raise CareerError("schema_error")
    try:
        queried_at = datetime.fromtimestamp(stamp, UTC)
    except (ValueError, OverflowError, OSError):
        raise CareerError("schema_error") from None
    if any(type(payload.get(flag)) is not bool for flag in ("cached", "refreshed")):
        raise CareerError("schema_error")
    if "snapshot" in payload and type(payload["snapshot"]) is not bool:
        raise CareerError("schema_error")
    if payload.get("snapshot") is True and (payload.get("credentialsRetained") is not False
                                           or payload.get("refreshed") is not False):
        raise CareerError("schema_error")
    share=payload.get("shareId")
    if share is not None and (not isinstance(share,str) or not re.fullmatch(r"HD2-[A-HJ-NP-Z2-9]{12}",share)):
        raise CareerError("schema_error")
    player=payload.get("player") or {}
    if not isinstance(player,dict):
        raise CareerError("schema_error")
    steam=player.get("steamId")
    name=player.get("displayName")
    if steam is not None and (not isinstance(steam,str) or not re.fullmatch(r"7656119\d{10}",steam)):
        raise CareerError("schema_error")
    if name is not None and (not isinstance(name,str) or len(name)>64 or any(not c.isprintable() for c in name)):
        raise CareerError("schema_error")
    expires=payload.get("expiresAt")
    if (expires is not None and (type(expires) not in (int, float)
                                or expires != stamp + SNAPSHOT_RETENTION_SECONDS)):
        raise CareerError("schema_error")
    return CareerStats(
        values={key: stats[key] for key in FIELDS}, queried_at=queried_at,
        cached=payload["cached"], refreshed=payload["refreshed"],
        snapshot=payload.get("snapshot") is True,
        account_scope=("shared_snapshot" if payload.get("shared") is True else "bound_user" if binding.startswith("b_") else "configured_account"),
        share_id=share,player_name=name or None,steam_id=steam,
        expires_at=datetime.fromtimestamp(expires,UTC) if expires else None,
    )
