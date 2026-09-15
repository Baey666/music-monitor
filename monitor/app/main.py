"""监控服务入口。

    uvicorn app.main:app --host 0.0.0.0 --port 9090
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, Response
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
        # 先把凭据交给引擎记住：就算这次登录失败（比如引擎还没就绪），
        # 之后 /api/engine/session 和各受保护调用也能自动补登录
        engine.set_credentials(str(username), str(password))
        result = await engine.login(str(username), str(password))
        if result.get("ok"):
            log.info("已用保存的账号恢复引擎会话：%s", username)
        else:
            # 不要吞掉失败 —— 以前这里无论真假都写「引擎登录：已登录」，
            # 结果 Cookie 代写一直 401，看上去像「账号密码固化不下来」
            log.warning(
                "用保存的账号 %s 恢复引擎会话失败：%s（可在「设置 → 引擎连接」重新登录）",
                username, result.get("detail"),
            )

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
    # 204 按 HTTP 规范不能带 body。之前返回 JSONResponse({}, 204) 会带 2 字节 body，
    # uvicorn 每次都在 send 阶段抛 RuntimeError: Response content longer than Content-Length
    # （浏览器仍收到合法的 204，但那条 keep-alive 连接会被打断，表现像「点了没反应」）。
    return Response(status_code=204)


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(WEB_DIR / "index.html")


if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")
