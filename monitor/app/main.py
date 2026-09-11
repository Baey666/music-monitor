"""监控服务入口。

    uvicorn app.main:app --host 0.0.0.0 --port 9090
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api import router
from .runtime import db, engine, scheduler, seed_defaults

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
)
log = logging.getLogger("monitor")

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


@asynccontextmanager
async def lifespan(app: FastAPI):
    seed_defaults()
    log.info("引擎地址: %s%s", engine.base, engine.prefix)
    health = await engine.healthz()
    if health["ok"]:
        log.info("引擎连接正常")
    else:
        log.warning("暂时连不上引擎（%s），调度会在引擎就绪后自动恢复", health["detail"])

    # 如果之前保存过引擎管理员账号，尝试恢复会话（用于代写 Cookie / 改设置）
    username = db.get_setting("engine_username") or ""
    password = db.get_setting("engine_password") or ""
    if username:
        result = await engine.login(str(username), str(password))
        log.info("引擎登录：%s", result.get("detail"))

    await scheduler.start()
    try:
        yield
    finally:
        await scheduler.stop()
        await engine.aclose()


app = FastAPI(title="music-monitor", version=__version__, lifespan=lifespan)
app.include_router(router)


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return JSONResponse({}, status_code=204)


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(WEB_DIR / "index.html")


if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")
