"""运行期配置。全部可通过环境变量覆盖。"""
from __future__ import annotations

import os
from pathlib import Path


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


class Settings:
    def __init__(self) -> None:
        # 上游引擎
        self.engine_url: str = os.getenv("ENGINE_URL", "http://music-dl:8080").rstrip("/")
        self.engine_prefix: str = "/" + os.getenv("ENGINE_PREFIX", "/music").strip("/")

        # 调度
        self.tick_seconds: int = max(10, _int("TICK_SECONDS", 60))
        # 下载默认串行，避免上游同时提交整批任务；需要更快时可用环境变量调大。
        self.download_concurrency: int = max(1, _int("DOWNLOAD_CONCURRENCY", 1))
        self.download_retries: int = max(0, _int("DOWNLOAD_RETRIES", 2))
        self.default_quality: str = os.getenv("DEFAULT_QUALITY", "lossless").strip().lower()

        # 配置库所在目录（**容器内路径**）。
        # 注意变量名不要叫 MONITOR_DATA_DIR —— .env 里那个是「宿主机路径」，
        # 两者同名很容易被误传进容器，导致把宿主路径当成容器路径用。
        self.data_dir: Path = Path(os.getenv("MONITOR_DB_DIR", "/app/data"))
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path: Path = self.data_dir / "monitor.db"

        # HTTP
        self.http_timeout: float = float(os.getenv("HTTP_TIMEOUT", "30"))
        self.user_agent: str = os.getenv(
            "HTTP_USER_AGENT",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        )

        # 单次运行的安全上限，避免误配置把磁盘写爆
        self.max_songs_per_playlist: int = max(10, _int("MAX_SONGS_PER_PLAYLIST", 500))
        self.max_downloads_per_run: int = max(1, _int("MAX_DOWNLOADS_PER_RUN", 50))


settings = Settings()
