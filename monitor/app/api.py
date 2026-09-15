"""HTTP 接口层。前端是一个零构建的单页应用，直接消费这些 JSON。"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from . import __version__, charts, pipeline, quality
from .config import settings
from .engine import EngineError
from .runtime import db, engine, scheduler

log = logging.getLogger("monitor.api")
router = APIRouter(prefix="/api")


# --------------------------------------------------------------------------- 模型
class MonitorIn(BaseModel):
    name: str
    kind: str = Field(pattern="^(chart|playlist|favorites|artist)$")
    enabled: bool = True
    sources: list[str] = Field(default_factory=list)
    target: dict[str, Any] = Field(default_factory=dict)
    quality: str = "lossless"
    fallback: str = "best_effort"
    auto_download: bool = True
    embed: bool = True
    interval_minutes: int = 360
    max_downloads: int = 30
    include_kw: str = ""
    exclude_kw: str = ""


class MonitorPatch(BaseModel):
    name: str | None = None
    enabled: bool | None = None
    sources: list[str] | None = None
    target: dict[str, Any] | None = None
    quality: str | None = None
    fallback: str | None = None
    auto_download: bool | None = None
    embed: bool | None = None
    interval_minutes: int | None = None
    max_downloads: int | None = None
    include_kw: str | None = None
    exclude_kw: str | None = None


class PreviewIn(BaseModel):
    kind: str
    sources: list[str] = Field(default_factory=list)
    target: dict[str, Any] = Field(default_factory=dict)
    include_kw: str = ""
    exclude_kw: str = ""


class LoginIn(BaseModel):
    username: str
    password: str


class CookiesIn(BaseModel):
    cookies: dict[str, str]


class RetryIn(BaseModel):
    track_ids: list[int]


# --------------------------------------------------------------------------- 总览
@router.get("/health")
async def health() -> dict[str, Any]:
    eng = await engine.healthz()
    return {
        "version": __version__,
        "engine": {
            "url": engine.base + engine.prefix,
            # 「有账号」不等于「会话有效」，两者分开报，免得又出现「看着登录成功其实没登上」
            "logged_in": engine.logged_in,
            "session_ok": await engine.session_ok() if engine.logged_in else False,
            **eng,
        },
        "scheduler": scheduler.status(),
        "tracks": db.track_stats(),
        "monitors": len(db.list_monitors()),
    }


@router.get("/platforms")
async def platforms() -> dict[str, Any]:
    items = await engine.sources()
    return {"items": items, "quality_levels": [
        {"key": k, "label": v["label"]} for k, v in quality.QUALITY_LEVELS.items()
    ]}


# --------------------------------------------------------------------------- 榜单
@router.get("/charts")
async def chart_list() -> dict[str, Any]:
    return {"groups": charts.CHART_GROUPS, "index": charts.chart_index()}


@router.get("/charts/resolve")
async def chart_resolve(key: str = "", platform: str = "", id: str = "", link: str = "", limit: int = 50) -> dict[str, Any]:
    entry: dict[str, Any] = {}
    if key:
        found = charts.chart_index().get(key)
        if not found:
            raise HTTPException(404, f"未知榜单：{key}")
        entry = dict(found)
    else:
        entry = {"platform": platform, "id": id, "link": link}
    result = await charts.resolve_chart(engine, entry)
    songs = result["songs"]
    return {
        "ok": result["ok"],
        "message": result["message"],
        "resolved": result["resolved"],
        "count": len(songs),
        "songs": [
            {
                "id": s.get("id"), "source": s.get("source"), "name": s.get("name"), "artist": s.get("artist"),
                "album": s.get("album"), "duration": s.get("duration"), "cover": s.get("cover"),
            }
            for s in songs[:limit]
        ],
    }


@router.post("/charts/verify")
async def chart_verify(payload: dict[str, Any]) -> dict[str, Any]:
    """批量校验榜单可用性。请求体：{"keys": ["netease_hot", ...]} 或 {"entries": [{...}]}"""
    index = charts.chart_index()
    entries: list[dict[str, Any]] = []
    for key in payload.get("keys") or []:
        if key in index:
            entries.append(dict(index[key]))
    entries.extend(payload.get("entries") or [])
    if not entries:
        entries = [dict(c) for c in index.values()]

    out = []
    for entry in entries[:40]:
        result = await charts.resolve_chart(engine, entry)
        out.append({
            "key": entry.get("key", ""),
            "name": entry.get("name") or entry.get("link") or entry.get("id"),
            "platform": entry.get("platform", ""),
            "ok": result["ok"],
            "count": len(result["songs"]),
            "message": result["message"],
            "resolved": result["resolved"],
        })
    return {"items": out}


# --------------------------------------------------------------------------- 歌单 / 收藏夹
@router.get("/playlists/resolve")
async def playlist_resolve(link: str, limit: int = 60) -> dict[str, Any]:
    if not link.strip():
        raise HTTPException(400, "缺少歌单链接")
    playlists = await engine.search_playlists(link.strip(), None)
    if not playlists:
        return {"ok": False, "message": "未能识别该链接，或该平台不支持歌单解析", "playlists": [], "songs": []}
    first = playlists[0]
    songs = await engine.playlist_songs(first["id"], first["source"], link=first.get("link") or "")
    return {
        "ok": bool(songs),
        "message": "" if songs else "歌单识别成功但取不到曲目（可能需要登录 Cookie）",
        "playlists": playlists,
        "picked": first,
        "count": len(songs),
        "songs": [
            {
                "id": s.get("id"), "source": s.get("source"), "name": s.get("name"), "artist": s.get("artist"),
                "album": s.get("album"), "duration": s.get("duration"),
            }
            for s in songs[:limit]
        ],
    }


@router.post("/favorites/list")
async def favorites_list(payload: dict[str, Any]) -> dict[str, Any]:
    """列出已登录平台的个人歌单 / 收藏夹，供用户勾选要监控哪些。"""
    sources = payload.get("sources") or []
    playlists = await engine.user_playlists(sources)
    return {
        "ok": bool(playlists),
        "message": "" if playlists else "没有读到个人歌单，请先在 go-music-dl 里为该平台登录/配置 Cookie",
        "items": [
            {**pl, "key": f"{pl['source']}:{pl['id']}"} for pl in playlists
        ],
    }


# --------------------------------------------------------------------------- 监控 CRUD
@router.get("/monitors")
async def monitor_list() -> dict[str, Any]:
    return {"items": db.list_monitors(), "running": scheduler.status()["running_monitors"]}


@router.post("/monitors")
async def monitor_create(payload: MonitorIn) -> dict[str, Any]:
    data = payload.model_dump()
    if not data["name"].strip():
        raise HTTPException(400, "监控名称不能为空")
    mid = db.create_monitor(data)
    return {"id": mid, "monitor": db.get_monitor(mid)}


@router.get("/monitors/{mid}")
async def monitor_get(mid: int) -> dict[str, Any]:
    mon = db.get_monitor(mid)
    if mon is None:
        raise HTTPException(404, "监控不存在")
    return {
        "monitor": mon,
        "runs": db.recent_runs(mid, limit=20),
        "stats": _monitor_stats(mid),
    }


@router.patch("/monitors/{mid}")
async def monitor_patch(mid: int, payload: MonitorPatch) -> dict[str, Any]:
    if db.get_monitor(mid) is None:
        raise HTTPException(404, "监控不存在")
    changes = payload.model_dump(exclude_none=True)
    db.update_monitor(mid, changes)
    if "interval_minutes" in changes:
        db.set_next_run(mid, interval_minutes=int(changes["interval_minutes"]))
    return {"monitor": db.get_monitor(mid)}


@router.delete("/monitors/{mid}")
async def monitor_delete(mid: int) -> dict[str, Any]:
    db.delete_monitor(mid)
    return {"ok": True}


@router.post("/monitors/{mid}/run")
async def monitor_run(mid: int) -> dict[str, Any]:
    if db.get_monitor(mid) is None:
        raise HTTPException(404, "监控不存在")
    started = scheduler.spawn(mid)
    return {"ok": True, "started": started, "message": "已开始执行" if started else "该监控正在执行中"}


@router.post("/monitors/{mid}/preview")
async def monitor_preview(mid: int, limit: int = 60) -> dict[str, Any]:
    mon = db.get_monitor(mid)
    if mon is None:
        raise HTTPException(404, "监控不存在")
    return await pipeline.preview_monitor(engine, mon, limit=limit)


@router.post("/preview")
async def draft_preview(payload: PreviewIn) -> dict[str, Any]:
    """新建监控前先干跑一次，确认榜单/歌单/收藏夹能解析出曲目。"""
    draft = payload.model_dump()
    draft["name"] = "preview"
    draft["fallback"] = "best_effort"
    draft["embed"] = True
    return await pipeline.preview_monitor(engine, draft, limit=60)


@router.get("/monitors/{mid}/runs")
async def monitor_runs(mid: int, limit: int = 30) -> dict[str, Any]:
    return {"items": db.recent_runs(mid, limit=limit)}


@router.get("/runs")
async def run_list(limit: int = 50) -> dict[str, Any]:
    return {"items": db.recent_runs(None, limit=limit)}


# --------------------------------------------------------------------------- 曲目记录
@router.get("/tracks")
async def track_list(monitor_id: int | None = None, status: str | None = None, limit: int = 100, offset: int = 0) -> dict[str, Any]:
    limit = max(1, min(limit, 500))
    return {
        "items": db.list_tracks(monitor_id=monitor_id, status=status, limit=limit, offset=offset),
        "stats": db.track_stats(),
    }


@router.post("/tracks/retry")
async def track_retry(payload: RetryIn) -> dict[str, Any]:
    results = []
    for tid in payload.track_ids[:20]:
        row = db.one("SELECT * FROM tracks WHERE id = ?", (tid,))
        if row is None:
            results.append({"id": tid, "ok": False, "message": "记录不存在"})
            continue
        track = dict(row)
        mon = db.get_monitor(track["monitor_id"])
        if mon is None:
            results.append({"id": tid, "ok": False, "message": "监控已删除"})
            continue
        song = {
            "id": track["song_id"], "source": track["source"], "name": track["name"],
            "artist": track["artist"], "album": track["album"], "duration": track["duration"],
            "cover": track["cover"], "extra": _loads(track["extra"]),
        }
        try:
            res = await pipeline.redownload(db, engine, mon, song)
        except Exception as exc:  # noqa: BLE001
            res = {"ok": False, "message": str(exc)}
        results.append({"id": tid, **res})
    return {"items": results}


# --------------------------------------------------------------------------- 引擎侧设置
@router.get("/engine/settings")
async def engine_settings() -> dict[str, Any]:
    return {"settings": await engine.settings()}


@router.post("/engine/login")
async def engine_login(payload: LoginIn) -> dict[str, Any]:
    """登录引擎。

    ⚠️ 只有**真正拿到并验证过会话**才落库。上游失败时返回的是 200 + 登录页，
    以前这里把「cookie jar 非空」当成功，于是乱输的账号密码也会显示「登录成功」
    并覆盖掉原本正确的凭据 —— 这就是「账号密码固化不下来」的根因。
    """
    result = await engine.login(payload.username, payload.password)
    if result["ok"]:
        db.set_setting("engine_username", payload.username)
        db.set_setting("engine_password", payload.password)
        # 顺手报一下这个账号在引擎里配好了哪些平台 Cookie，方便判断会员音质能不能拿到
        result["configured"] = sorted((await engine.get_cookies()).keys())
    else:
        # 失败时不动已保存的凭据（原来的可能是对的），让用户看清原因后重试
        result["saved_username"] = db.get_setting("engine_username") or ""
    return result


@router.post("/engine/logout")
async def engine_logout() -> dict[str, Any]:
    """退出引擎登录：丢掉本地会话与已保存的账号密码。

    没有这个出口的话，一个错误的旧会话会一直挂在连接池里，
    后续接口拿它去请求都会「看起来能用」，错误无法暴露也无法清除。
    """
    db.set_setting("engine_username", "")
    db.set_setting("engine_password", "")
    return await engine.logout()


@router.get("/engine/session")
async def engine_session() -> dict[str, Any]:
    """引擎会话现状：是否持有凭据、此刻是否仍然有效。

    顺手做一次自愈：会话丢了但设置里存过账号密码，就用它自动重登 ——
    这样刷新页面看到的不再是「要重新登录」，而是已经恢复好的会话。
    """
    await engine.ensure_session()
    logged_in = engine.logged_in
    return {
        "logged_in": logged_in,
        "session_ok": await engine.session_ok() if logged_in else False,
        "saved_username": db.get_setting("engine_username") or "",
    }


@router.get("/engine/cookies")
async def engine_cookies() -> dict[str, Any]:
    cookies = await engine.get_cookies()
    return {
        "ok": bool(cookies),
        "configured": sorted(cookies.keys()),
        "message": "" if cookies else "尚未登录引擎或引擎未配置任何平台 Cookie",
    }


@router.post("/engine/cookies")
async def engine_set_cookies(payload: CookiesIn) -> dict[str, Any]:
    try:
        return await engine.push_cookies(payload.cookies)
    except EngineError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/engine/settings")
async def engine_save_settings(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return await engine.save_settings(payload)
    except EngineError as exc:
        raise HTTPException(400, str(exc)) from exc


# --------------------------------------------------------------------------- 本服务设置
@router.get("/settings")
async def settings_get() -> dict[str, Any]:
    data = db.all_settings()
    # 引擎管理员密码只用于服务端自动重登，不回传给浏览器
    saved_password = data.pop("engine_password", "")
    return {"settings": data, "engine_password_saved": bool(saved_password), "limits": {
        "tick_seconds": settings.tick_seconds,
        "download_concurrency": settings.download_concurrency,
        "download_batch": settings.download_batch,
        "download_batch_gap": settings.download_batch_gap,
        "download_timeout": settings.download_timeout,
        "max_downloads_per_run": settings.max_downloads_per_run,
        "engine_url": engine.base + engine.prefix,
    }}


@router.post("/settings")
async def settings_post(payload: dict[str, Any]) -> dict[str, Any]:
    for key, value in (payload or {}).items():
        db.set_setting(key, value)
    return {"settings": db.all_settings()}


@router.post("/settings/engine-login")
async def settings_engine_login() -> dict[str, Any]:
    """用已保存的引擎管理员账号重新登录（引擎重启后会话会失效）。"""
    username = db.get_setting("engine_username") or ""
    password = db.get_setting("engine_password") or ""
    if not username:
        raise HTTPException(400, "尚未保存引擎管理员账号")
    result = await engine.login(str(username), str(password))
    if not result["ok"]:
        # 保存的凭据已经不可用，直接清掉，免得下次开机又拿它去撞
        db.set_setting("engine_username", "")
        db.set_setting("engine_password", "")
        result["detail"] = f"已保存的引擎账号已失效并清除：{result['detail']}"
    return result


# --------------------------------------------------------------------------- 工具
def _monitor_stats(mid: int) -> dict[str, int]:
    rows = db.query("SELECT status, COUNT(*) AS c FROM tracks WHERE monitor_id = ? GROUP BY status", (mid,))
    stats = {r["status"]: r["c"] for r in rows}
    stats["total"] = sum(stats.values())
    return stats


def _loads(raw: str) -> dict[str, Any]:
    import json

    try:
        value = json.loads(raw or "{}")
        return value if isinstance(value, dict) else {}
    except ValueError:
        return {}
