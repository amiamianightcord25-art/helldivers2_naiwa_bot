"""Environment configuration; relative paths resolve against the project, not cwd."""

import math
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CAPTURED_PATHS = {
    "war_id": "/api/WarSeason/current/WarID",
    "war_info": "/api/WarSeason/{war_id}/WarInfo",
    "status": "/api/WarSeason/{war_id}/Status",
    "assignments": "/api/v2/Assignment/War/{war_id}",
    "statistics": "/api/Stats/war/{war_id}/summary",
    "election": "/api/ElectionV2/{war_id}/{election_id}",
}


def _path(value: str, root: Path) -> Path:
    result = Path(value).expanduser()
    return result if result.is_absolute() else root / result


def _boolean(value: str, name: str = "QQ_SANDBOX") -> bool:
    lowered = value.strip().lower()
    if lowered not in {"1", "true", "yes", "0", "false", "no"}:
        raise ValueError(f"{name} 必须为 true/false")
    return lowered in {"1", "true", "yes"}


@dataclass(frozen=True)
class Settings:
    root: Path = PROJECT_ROOT
    provider: str = "auto"
    community_base_url: str = "https://api.helldivers2.dev"
    captured_base_url: str = "https://api.live.prod.thehelldiversgame.com"
    captured_paths: dict[str, str] = field(default_factory=lambda: dict(CAPTURED_PATHS))
    super_client: str = "HD2-QQ-Bot"
    super_contact: str = "example@example.com"
    timeout: float = 12
    retries: int = 2
    cache_ttl: float = 20
    order_ttl: float = 30
    statistics_ttl: float = 30
    static_ttl: float = 21600
    stale_ttl: float = 900
    log_level: str = "INFO"
    log_dir: Path = PROJECT_ROOT / "logs"
    database_path: Path = PROJECT_ROOT / "data/bot.db"
    qq_app_id: str = ""
    qq_app_secret: str = field(default="", repr=False)
    qq_sandbox: bool = True
    qq_transport: str = "websocket"
    qq_push_user: str = ""
    qq_listen_host: str = "127.0.0.1"
    qq_listen_port: int = 8080
    qq_callback_path: str = "/qq/events"
    career_config_path: Path | None = None
    career_timeout: float = 90
    career_cache_ttl: float = 60
    image_enabled: bool = True
    image_width: int = 1200
    image_quality: int = 82
    image_max_bytes: int = 900000
    image_timeout: float = 20
    render_concurrency: int = 2
    image_qr_path: Path | None = None
    image_browser_path: Path | None = None
    image_browser_channel: str | None = None
    wiki_catalog_path: Path = PROJECT_ROOT / "data/wiki_catalog.json"
    wiki_sync_enabled: bool = True
    wiki_sync_interval_hours: float = 24

    @classmethod
    def load(cls, root: Path = PROJECT_ROOT) -> "Settings":
        load_dotenv(root / ".env", override=False)
        def get(name, default=None):
            value = os.getenv(name)
            return default if value is None or not value.strip() else value
        settings = cls(
            root=root, provider=get("HD2_PROVIDER", "auto").lower(),
            community_base_url=get("HD2_COMMUNITY_BASE_URL", cls.community_base_url).rstrip("/"),
            captured_base_url=get("HD2_CAPTURED_BASE_URL", cls.captured_base_url).rstrip("/"),
            super_client=get("HD2_SUPER_CLIENT", cls.super_client),
            super_contact=get("HD2_SUPER_CONTACT", cls.super_contact),
            timeout=float(get("HD2_TIMEOUT", "12")), retries=int(get("HD2_RETRIES", "2")),
            cache_ttl=float(get("HD2_CACHE_TTL", "20")),
            order_ttl=float(get("HD2_ORDER_TTL", "30")),
            statistics_ttl=float(get("HD2_STATISTICS_TTL", "30")),
            static_ttl=float(get("HD2_STATIC_TTL", "21600")),
            stale_ttl=float(get("HD2_STALE_TTL", "900")),
            log_level=get("LOG_LEVEL", "INFO").upper(),
            log_dir=_path(get("LOG_DIR", "logs"), root),
            database_path=_path(get("DATABASE_PATH", "data/bot.db"), root),
            qq_app_id=get("QQ_APP_ID", ""), qq_app_secret=get("QQ_APP_SECRET", ""),
            qq_sandbox=_boolean(get("QQ_SANDBOX", "true")),
            qq_transport=get("QQ_TRANSPORT", "websocket").lower(),
            qq_push_user=get("QQ_PUSH_USER", "").strip(),
            qq_listen_host=get("QQ_LISTEN_HOST", "127.0.0.1"),
            qq_listen_port=int(get("QQ_LISTEN_PORT", "8080")),
            qq_callback_path=get("QQ_CALLBACK_PATH", "/qq/events"),
            career_config_path=_path(get("HD2_CAREER_CONFIG", ""), root)
            if get("HD2_CAREER_CONFIG", "").strip() else None,
            career_timeout=float(get("HD2_CAREER_TIMEOUT", "90")),
            career_cache_ttl=float(get("HD2_CAREER_CACHE_TTL", "60")),
            image_enabled=_boolean(get("HD2_IMAGE_ENABLED", "true"), "HD2_IMAGE_ENABLED"),
            image_width=int(get("HD2_IMAGE_WIDTH", "1200")),
            image_quality=int(get("HD2_IMAGE_QUALITY", "82")),
            image_max_bytes=int(get("HD2_IMAGE_MAX_BYTES", "900000")),
            image_timeout=float(get("HD2_IMAGE_TIMEOUT", "20")),
            render_concurrency=int(get("HD2_RENDER_CONCURRENCY", "2")),
            image_qr_path=_path(get("HD2_QR_IMAGE_PATH", "data/qq_experience_qr.png"), root)
            if get("HD2_QR_IMAGE_PATH", "data/qq_experience_qr.png").strip() else None,
            image_browser_path=_path(get("HD2_IMAGE_BROWSER_PATH", ""), root)
            if get("HD2_IMAGE_BROWSER_PATH", "").strip() else None,
            image_browser_channel=get("HD2_IMAGE_BROWSER_CHANNEL", "").strip() or None,
            wiki_catalog_path=_path(get("HD2_WIKI_CATALOG_PATH", "data/wiki_catalog.json"), root),
            wiki_sync_enabled=_boolean(get("HD2_WIKI_SYNC_ENABLED", "true"), "HD2_WIKI_SYNC_ENABLED"),
            wiki_sync_interval_hours=float(get("HD2_WIKI_SYNC_INTERVAL_HOURS", "24")),
        )
        if settings.provider not in {"auto", "community", "captured", "mock"}:
            raise ValueError("HD2_PROVIDER 必须为 auto/community/captured/mock")
        if (not math.isfinite(settings.timeout) or settings.timeout <= 0
                or not 0 <= settings.retries <= 5):
            raise ValueError("HD2_TIMEOUT 必须大于 0，HD2_RETRIES 范围为 0..5")
        if any(not math.isfinite(value) or value < 0 for value in (
            settings.cache_ttl, settings.order_ttl, settings.statistics_ttl,
            settings.static_ttl, settings.stale_ttl,
        )):
            raise ValueError("缓存时间必须是有限的非负数")
        if settings.qq_transport not in {"webhook", "websocket"}:
            raise ValueError("QQ_TRANSPORT 必须为 webhook/websocket")
        if settings.qq_push_user and (
            not settings.qq_push_user.isascii() or not settings.qq_push_user.isdecimal()
            or not 5 <= len(settings.qq_push_user) <= 12
        ):
            raise ValueError("QQ_PUSH_USER 应为指定用户QQ号或留空禁用推送")
        if not 1 <= settings.qq_listen_port <= 65535:
            raise ValueError("QQ_LISTEN_PORT 必须是有效端口")
        if not settings.qq_callback_path.startswith("/"):
            raise ValueError("QQ_CALLBACK_PATH 必须以 / 开头")
        if not math.isfinite(settings.career_timeout) or not 1 <= settings.career_timeout <= 110:
            raise ValueError("HD2_CAREER_TIMEOUT 范围为 1..110 秒")
        if not math.isfinite(settings.career_cache_ttl) or not 1 <= settings.career_cache_ttl <= 300:
            raise ValueError("HD2_CAREER_CACHE_TTL 范围为 1..300 秒")
        if not 800 <= settings.image_width <= 1600 or not 40 <= settings.image_quality <= 95:
            raise ValueError("图片宽度范围800..1600，JPEG质量范围40..95")
        if not 50000 <= settings.image_max_bytes <= 4000000:
            raise ValueError("JPEG大小上限范围50000..4000000字节")
        if not math.isfinite(settings.image_timeout) or not 1 <= settings.image_timeout <= 40:
            raise ValueError("图片渲染超时范围1..40秒")
        if not 1 <= settings.render_concurrency <= 4:
            raise ValueError("HD2_RENDER_CONCURRENCY 范围1..4")
        if settings.image_browser_channel not in {None, "chrome", "msedge"}:
            raise ValueError("浏览器channel只能为chrome/msedge或留空使用Playwright Chromium")
        if (not math.isfinite(settings.wiki_sync_interval_hours)
                or not 6 <= settings.wiki_sync_interval_hours <= 720):
            raise ValueError("百科同步间隔范围6..720小时")
        return settings
