from hd2bot.career.models import (
    ERROR_MESSAGES,
    SNAPSHOT_RETENTION_DAYS,
    CareerError,
    CareerStats,
)

REFRESH_NOTICE = (
    "查看已有快照不需要凭据；每次获取新的战绩数据，都需要重新发送 获取验证码，"
    "并生成、提交一份新的胶囊，旧凭据不能复用。"
)

GROUPS = (
    ("击杀", (
        ("totalEnemyKills", "敌人总击杀"), ("totalBugKills", "终结族"),
        ("totalAutomatonKills", "机器人"), ("totalIlluminateKills", "光能族"),
        ("totalFriendlyKills", "友军"), ("totalGrenadeKills", "手雷"),
        ("totalMeleeKills", "近战"), ("totalEagleKills", "飞鹰"),
    )),
    ("射击与生存", (
        ("totalShotsFired", "射击次数"), ("totalShotsHit", "命中次数"),
        ("totalDeaths", "死亡次数"), ("totalSquadSaves", "小队救援累计"),
    )),
    ("战略配备", (
        ("totalStratagemsUsed", "使用总次数"), ("totalOrbitalsUsed", "轨道支援"),
        ("totalDefensiveStratagemsUsed", "防御配备"), ("totalEagleStratagemsUsed", "飞鹰配备"),
        ("totalSupplyStratagemsUsed", "补给配备"), ("totalReinforceStratagemsUsed", "增援配备"),
    )),
    ("任务与资源", (
        ("totalMissionsCompleted", "完成任务"), ("totalMissionsWon", "获胜任务"),
        ("totalObjectivesCompleted", "完成目标"), ("totalSuccesfulExtractions", "成功撤离"),
        ("totalStarsFromMissions", "任务星数累计"), ("totalSamplesCollected", "收集样本"),
        ("totalCreditsEarned", "获得点数"), ("totalCreditsSpent", "花费点数"),
        ("totalMissionTime", "任务时长累计（原值，单位待核实）"),
    )),
)


def account_label(stats: CareerStats) -> str:
    if stats.account_scope == "shared_snapshot":
        return "分享 ID 对应的玩家快照"
    return "当前私聊用户绑定的游戏账号" if stats.account_scope == "bound_user" else "当前配置账号"


def data_status(stats: CareerStats) -> str:
    if stats.snapshot:
        return "已保存的战绩快照（本次未连接游戏接口）"
    sources = []
    if stats.local_cached:
        sources.append("机器人本地缓存")
    if stats.cached:
        sources.append("服务端缓存")
    return " / ".join(sources) if sources else "本次查询返回"


def data_notice(stats: CareerStats) -> str:
    if stats.snapshot:
        return (f"凭据已清除，查询不重新登录；快照从采集起仅保留 {SNAPSHOT_RETENTION_DAYS} 天，"
                "到期自动删除，查看不会续期。"
                + REFRESH_NOTICE)
    return "按当前账号查询；数据归属以绑定记录为准。"


def format_career(stats: CareerStats) -> str:
    lines = []
    if stats.mock:
        lines.append("【模拟数据 · 非真实战绩】")
    lines.extend(["【个人生涯战绩 · 27 项】", "玩家：" + (stats.player_name or "未记录 Steam 昵称"),
                  "SteamID64：" + (stats.steam_id or "旧快照未记录，重新同步后补齐"), "账号：" + account_label(stats)])
    if stats.share_id:
        lines.extend(["快照 ID：" + stats.share_id, "群聊可用：战绩 " + stats.share_id])
    if stats.expires_at:
        lines.append("到期时间：" + stats.expires_at.astimezone().isoformat(timespec="seconds"))
    for title, fields in GROUPS:
        lines.append("\n" + title)
        lines.extend(f"{label}：{stats.values[key]:,}" for key, label in fields)
    sources = []
    if stats.local_cached:
        sources.append("机器人本地缓存")
    if stats.cached:
        sources.append("服务端缓存")
    lines.extend([
        "", "数据状态：" + data_status(stats),
        ("快照采集时间：" if stats.snapshot else "原始查询时间：") + stats.queried_at.astimezone().isoformat(timespec="seconds"),
        data_notice(stats),
    ])
    return "\n".join(lines)


def format_career_error(exc: CareerError) -> str:
    text = "🚨 " + ERROR_MESSAGES.get(exc.code, ERROR_MESSAGES["backend_unavailable"])
    if exc.retry_after:
        text += f"\n建议 {exc.retry_after} 秒后再试。"
    return text
