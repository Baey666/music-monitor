"""进程内共享的运行时对象。

放在单独模块，避免 `api` 与 `main` 之间循环导入。
"""
from __future__ import annotations

from .config import settings
from .db import Database
from .engine import Engine
from .scheduler import Scheduler

db = Database(str(settings.db_path))
engine = Engine()
scheduler = Scheduler(db, engine)

DEFAULTS: dict[str, object] = {
    "default_quality": settings.default_quality,
    "default_interval_minutes": 360,
    "default_max_downloads": 30,
    "default_embed": 1,
    "default_fallback": "best_effort",
    "engine_username": "",
    "engine_password": "",
}


def seed_defaults() -> None:
    for key, value in DEFAULTS.items():
        if db.get_setting(key) is None:
            db.set_setting(key, value)
