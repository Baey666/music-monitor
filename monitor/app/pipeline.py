"""一轮监控的执行流水线：发现曲目 → 增量比对 → 按目标音质择优 → 交给引擎下载 → 记录结果。"""
from __future__ import annotations

import asyncio
import difflib
import logging
import os
import re
from pathlib import Path
from typing import Any

from . import quality as q
from .charts import resolve_chart
from .config import settings
from .db import Database, now_iso
from .engine import Engine, EngineError

log = logging.getLogger("monitor.pipeline")


# --------------------------------------------------------------------------- 工具
def _norm(text: str) -> str:
    text = (text or "").lower()
    text = re.sub(r"[\(\[（【].*?[\)\]）】]", "", text)          # 去掉 (Live) / [Remix] 之类后缀
    text = re.sub(r"\b(feat|ft|remaster(ed)?|version|live|explicit|hq|hires)\b", "", text)
    return "".join(ch for ch in text if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")


def similarity(name_a: str, artist_a: str, name_b: str, artist_b: str) -> float:
    """歌名 70% + 歌手 30% 的相似度，口径与上游换源逻辑接近。"""
    title = difflib.SequenceMatcher(None, _norm(name_a), _norm(name_b)).ratio()
    a1, a2 = _norm(artist_a), _norm(artist_b)
    if not a1 or not a2:
        artist = 0.5
    else:
        artist = difflib.SequenceMatcher(None, a1, a2).ratio()
        # 只要有一方包含另一方（"周杰伦" vs "周杰伦/杨瑞代"）就给高分
        if a1 in a2 or a2 in a1:
            artist = max(artist, 0.9)
    return round(title * 0.7 + artist * 0.3, 4)


def _keywords(raw: str) -> list[str]:
    return [k.strip().lower() for k in re.split(r"[,，;；\s]+", raw or "") if k.strip()]


def _file_exists(file_path: str) -> bool:
    if not file_path:
        return False
    path = Path(file_path)
    if path.is_absolute():
        candidates = [path]
        marker = "/home/appuser/data/downloads/"
        if marker in path.as_posix():
            candidates.append(Path("/downloads") / path.as_posix().split(marker, 1)[1])
        return any(candidate.is_file() for candidate in candidates)
    # go-music-dl 返回的通常是 data/downloads/...，对应 monitor 的只读 /downloads。
    if str(path).startswith("data/downloads/"):
        return (Path("/downloads") / path.relative_to("data/downloads")).is_file()
    return False


def _match_keywords(song: dict[str, Any], include: list[str], exclude: list[str]) -> bool:
    haystack = f"{song.get('name', '')} {song.get('artist', '')} {song.get('album', '')}".lower()
    if include and not any(k in haystack for k in include):
        return False
    if exclude and any(k in haystack for k in exclude):
        return False
    return True


def _as_candidate(song: dict[str, Any], *, primary: bool, score: float) -> dict[str, Any]:
    return {
        "id": str(song.get("id", "")),
        "source": str(song.get("source", "")),
        "name": song.get("name", ""),
        "artist": song.get("artist", ""),
        "album": song.get("album", ""),
        "duration": int(song.get("duration") or 0),
        "cover": song.get("cover", ""),
        "extra": song.get("extra") or {},
        "is_primary": primary,
        "similarity": score,
    }


# 试听片段和现场录音经常能被搜索接口返回，但不应混入正式歌曲下载。
_VERSION_MARKERS = re.compile(
    r"(?:live|现场|演唱会|演唱會|跨年|音乐会|音樂會|演出|acoustic|demo|试听|試聽|片段|snippet|remix|dj|混音|加长版|加長版|伴奏|instrumental|广播剧|廣播劇|radio edit|sped up|slowed)",
    re.IGNORECASE,
)


def _candidate_text(candidate: dict[str, Any]) -> str:
    return " ".join(str(candidate.get(k) or "") for k in ("name", "album"))


def _is_variant(candidate: dict[str, Any]) -> bool:
    return bool(_VERSION_MARKERS.search(_candidate_text(candidate)))


def _is_preview(candidate: dict[str, Any]) -> bool:
    text = _candidate_text(candidate)
    return bool(re.search(r"(?:demo|试听|試聽|片段|snippet)", text, re.IGNORECASE)) or (
        0 < int(candidate.get("duration") or 0) <= 75
    )


def _reject_candidate(
    candidate: dict[str, Any], reference_duration: int = 0, *, allow_variant: bool = True
) -> str:
    """返回原因；正式版优先，改编版只能由调用方作为兜底。"""
    if _is_preview(candidate):
        return "疑似试听片段"
    if _is_variant(candidate) and not allow_variant:
        return "改编版本仅允许作为正式版不可用时的兜底"

    duration = int(candidate.get("duration") or 0)
    reference = int(reference_duration or 0)
    if duration and duration <= 75:
        return f"时长仅 {duration} 秒，疑似试听片段"
    if duration and reference and abs(duration - reference) > max(10, round(reference * 0.15)):
        return f"时长 {duration} 秒与原曲 {reference} 秒不匹配"
    return ""


# --------------------------------------------------------------------------- 发现
async def discover(engine: Engine, mon: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """按监控类型抓取本轮的全部曲目。返回 (songs, warnings)。"""
    songs: list[dict[str, Any]] = []
    warnings: list[str] = []
    kind = mon["kind"]
    target = mon.get("target") or {}
    sources = mon.get("sources") or []

    if kind == "chart":
        entries = target.get("charts") or []
        if not entries:
            warnings.append("未选择任何榜单")
        for entry in entries:
            res = await resolve_chart(engine, entry)
            label = entry.get("name") or entry.get("key") or entry.get("link") or entry.get("id") or "未知榜单"
            if not res["ok"]:
                warnings.append(f"榜单「{label}」解析失败：{res['message']}")
                continue
            for s in res["songs"][: settings.max_songs_per_playlist]:
                s["_origin"] = label
                songs.append(s)

    elif kind == "playlist":
        items = target.get("playlists") or []
        if not items:
            warnings.append("未配置任何歌单链接")
        for item in items:
            label = item.get("name") or item.get("link") or item.get("id") or "未知歌单"
            try:
                if item.get("id") and item.get("source"):
                    cur = await engine.playlist_songs(item["id"], item["source"], link=item.get("link", ""))
                else:
                    found = await engine.search_playlists(item.get("link", ""), None)
                    cur = []
                    for pl in found:
                        cur = await engine.playlist_songs(pl["id"], pl["source"], link=pl.get("link") or "")
                        if cur:
                            break
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"歌单「{label}」解析失败：{exc}")
                continue
            if not cur:
                warnings.append(f"歌单「{label}」没有解析到曲目（链接可能已失效或需要登录）")
                continue
            for s in cur[: settings.max_songs_per_playlist]:
                s["_origin"] = label
                songs.append(s)

    elif kind == "favorites":
        try:
            playlists = await engine.user_playlists(sources)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"读取个人收藏失败：{exc}（请先在引擎里配置该平台 Cookie）")
            playlists = []
        if not playlists:
            warnings.append("没有读到任何个人歌单/收藏夹，请确认已在 go-music-dl 里登录对应平台")
        selected = set(target.get("playlist_ids") or [])
        for pl in playlists:
            key = f"{pl['source']}:{pl['id']}"
            if selected and key not in selected:
                continue
            try:
                cur = await engine.playlist_songs(pl["id"], pl["source"])
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"收藏夹「{pl.get('name')}」解析失败：{exc}")
                continue
            for s in cur[: settings.max_songs_per_playlist]:
                s["_origin"] = pl.get("name") or key
                songs.append(s)
    else:
        warnings.append(f"未知的监控类型：{kind}")

    return _dedupe_list(songs), warnings


def _dedupe_list(songs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for s in songs:
        key = (str(s.get("source", "")), str(s.get("id", "")))
        if not key[0] or not key[1] or key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


# --------------------------------------------------------------------------- 择优
async def _pick_best(
    engine: Engine,
    song: dict[str, Any],
    *,
    quality: str,
    sources: list[str],
    log_lines: list[str],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """返回 (选中的候选, 评估结果)。评估结果里记录了实际码率/格式。"""
    primary = _as_candidate(song, primary=True, score=1.0)
    reference_duration = primary["duration"]
    formal: list[tuple[dict[str, Any], dict[str, Any]]] = []
    variants: list[tuple[dict[str, Any], dict[str, Any]]] = []

    async def probe(cand: dict[str, Any], label: str) -> None:
        rejection = _reject_candidate(cand, reference_duration, allow_variant=True)
        if rejection and not _is_variant(cand):
            log_lines.append(f"  {label}跳过：{rejection}")
            return
        ev = q.evaluate(quality, await engine.inspect(cand))
        log_lines.append(f"  {label}{'有效' if ev['valid'] else '无效'} {ev['bitrate']} {ev['size']}")
        if not ev["valid"]:
            return
        (variants if _is_variant(cand) else formal).append((cand, ev))

    await probe(primary, f"原始源 {primary['source']} 探测：")

    try:
        alt = await engine.switch_source(
            song.get("name", ""), song.get("artist", ""), song.get("source", ""), duration=reference_duration
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("switch_source 异常: %s", exc)
        alt = None
    if alt and alt.get("id") and alt.get("source") != primary["source"]:
        cand = _as_candidate(alt, primary=False, score=float(alt.get("score") or 0.0))
        if cand["similarity"] >= 0.75:
            await probe(cand, f"换源候选 {cand['source']} 相似度{cand['similarity']}：")

    wanted = [s for s in (sources or []) if s and s != primary["source"]]
    if wanted:
        queried = f"{song.get('name', '')} {song.get('artist', '')}".strip()
        try:
            found = await engine.search_songs(queried, wanted, exact_artist=song.get("artist", ""))
        except Exception as exc:  # noqa: BLE001
            log.warning("跨平台搜索失败 %s: %s", queried, exc)
            found = []
        ranked = sorted(
            ((similarity(song.get("name", ""), song.get("artist", ""), f.get("name", ""), f.get("artist", "")), f) for f in found),
            key=lambda x: (-x[0], q.source_rank(x[1].get("source", ""))),
        )
        seen = {c["source"] for c, _ in formal + variants}
        for score, f in ranked:
            if score < 0.72 or len(seen) >= 5:
                continue
            src = f.get("source", "")
            if src in seen:
                continue
            cand = _as_candidate(f, primary=False, score=score)
            await probe(cand, f"搜索候选 {src} 相似度{score}：")
            seen.add(src)

    # 正式版永远优先；改编版只在没有任何正式版可用时兜底。
    valid = formal or variants
    if not valid:
        return None, None
    satisfying = [item for item in valid if item[1]["satisfies"]]
    pool = satisfying or valid
    return max(pool, key=lambda ce: (ce[1]["kbps"] or 0, 0 if ce[0]["is_primary"] else 1, -q.source_rank(ce[0]["source"])))


# --------------------------------------------------------------------------- 主流程
async def run_monitor(db: Database, engine: Engine, mon: dict[str, Any]) -> dict[str, Any]:
    run_id = db.start_run(mon["id"])
    log_lines: list[str] = []
    warnings: list[str] = []
    found = new_items = downloaded = skipped = failed = 0

    try:
        songs, warnings = await discover(engine, mon)
        found = len(songs)
        log_lines.append(f"发现 {found} 首曲目" + (f"，{len(warnings)} 条提示" if warnings else ""))
        for w in warnings:
            log_lines.append(f"  ! {w}")

        include = _keywords(mon.get("include_kw", ""))
        exclude = _keywords(mon.get("exclude_kw", ""))
        existing = db.existing_song_keys(mon["id"])
        downloaded_paths = db.downloaded_tracks(mon["id"])
        quality = q.normalize_quality(mon.get("quality"))
        sources = mon.get("sources") or []
        max_downloads = min(int(mon.get("max_downloads") or 30), settings.max_downloads_per_run)
        auto_download = bool(mon.get("auto_download"))

        todo: list[dict[str, Any]] = []
        for s in songs:
            key = (str(s.get("source", "")), str(s.get("id", "")))
            if key in existing and key in downloaded_paths and _file_exists(downloaded_paths[key]):
                # 只有数据库记录仍对应真实文件时才跳过；文件被删后必须重新下载。
                db.touch_track(mon["id"], s)
                skipped += 1
                continue
            if not _match_keywords(s, include, exclude):
                db.upsert_track(mon["id"], s, status="skipped", error="被关键词规则过滤")
                skipped += 1
                continue
            # 不用 monitor 数据库的历史记录拦截：文件可能已被用户删除。
            # go-music-dl 会在真正下载时按当前文件/下载记录判断是否跳过。
            todo.append(s)

        new_items = len(todo)
        log_lines.append(f"其中新曲目 {new_items} 首，跳过 {skipped} 首")

        if not todo:
            db.finish_run(run_id, status="ok", found=found, new_items=0, downloaded=0, skipped=skipped,
                          failed=0, message="无新增曲目", log="\n".join(log_lines))
            _reschedule(db, mon)
            return {"run_id": run_id, "found": found, "new": 0, "downloaded": 0, "skipped": skipped, "failed": 0, "warnings": warnings}

        budget = max_downloads
        sem = asyncio.Semaphore(settings.download_concurrency)
        lock = asyncio.Lock()

        async def handle(song: dict[str, Any]) -> None:
            nonlocal downloaded, failed, skipped, budget
            async with sem:
                async with lock:
                    if budget <= 0:
                        return
                    # 先预留名额，再离开锁；避免并发任务同时通过检查。
                    budget -= 1
                head = f"[{song.get('_origin', '')}] {song.get('name', '')} - {song.get('artist', '')}"
                local_log: list[str] = [f"- {head}"]
                try:
                    cand, ev = await _pick_best(engine, song, quality=quality, sources=sources, log_lines=local_log)
                    if cand is None or ev is None:
                        raise EngineError("所有候选源都探测失败")

                    if not ev["satisfies"] and mon.get("fallback") == "skip":
                        raise EngineError(f"未找到满足「{q.quality_label(quality)}」的源（最佳 {ev['bitrate']}），按策略跳过")

                    if not auto_download:
                        db.upsert_track(mon["id"], cand, status="pending",
                                        quality_actual=q.actual_quality_desc(ev), bitrate=ev["bitrate"])
                        local_log.append("  → 仅记录（未开启自动下载）")
                        return

                    result = None
                    last_error = ""
                    for attempt in range(settings.download_retries + 1):
                        try:
                            result = await engine.download(cand, embed=bool(mon.get("embed")))
                            break
                        except Exception as exc:  # noqa: BLE001
                            last_error = str(exc)
                            if attempt < settings.download_retries:
                                local_log.append(f"  ! 下载失败，第 {attempt + 1} 次重试：{last_error}")
                                await asyncio.sleep(min(2 * (attempt + 1), 5))
                    if result is None:
                        raise EngineError(last_error or "下载失败，未收到上游确认")

                    file_path = result.get("path") or result.get("filename") or ""
                    engine_skipped = bool(result.get("skipped"))
                    db.upsert_track(
                        mon["id"],
                        cand,
                        status="downloaded",
                        quality_actual=q.actual_quality_desc(ev) + ("（引擎已存在，跳过写入）" if engine_skipped else ""),
                        bitrate=ev["bitrate"],
                        file_path=file_path,
                    )
                    async with lock:
                        downloaded += 1
                    local_log.append(f"  → 下载完成 {q.actual_quality_desc(ev)} {file_path}")

                except Exception as exc:  # noqa: BLE001
                    async with lock:
                        failed += 1
                    db.upsert_track(mon["id"], song, status="failed", error=str(exc)[:400])
                    local_log.append(f"  × 失败（已重试 {settings.download_retries} 次）：{exc}")
                finally:
                    async with lock:
                        log_lines.extend(local_log)

        await asyncio.gather(*(handle(s) for s in todo))

        status = "ok" if failed == 0 else ("partial" if downloaded else "error")
        message = "；".join(warnings[:3])
        db.finish_run(run_id, status=status, found=found, new_items=new_items, downloaded=downloaded,
                      skipped=skipped, failed=failed, message=message, log="\n".join(log_lines))
        _reschedule(db, mon)
        return {
            "run_id": run_id, "found": found, "new": new_items, "downloaded": downloaded,
            "skipped": skipped, "failed": failed, "warnings": warnings[:10],
        }

    except Exception as exc:  # noqa: BLE001
        log.exception("监控 %s 执行异常", mon.get("name"))
        db.finish_run(run_id, status="error", found=found, new_items=new_items, downloaded=downloaded,
                      skipped=skipped, failed=failed, message=str(exc)[:400], log="\n".join(log_lines))
        _reschedule(db, mon)
        return {"run_id": run_id, "found": found, "new": new_items, "downloaded": downloaded,
                "skipped": skipped, "failed": failed, "error": str(exc)}


def s_key(song: dict[str, Any]) -> str:
    return f"{song.get('source', '')}:{song.get('id', '')}"


# --------------------------------------------------------------------------- 供 API 复用的单曲动作
async def preview_monitor(engine: Engine, mon: dict[str, Any], limit: int = 60) -> dict[str, Any]:
    """干跑：只做「发现 + 过滤」，不下载。用于新建监控前确认配置是否正确。"""
    songs, warnings = await discover(engine, mon)
    include = _keywords(mon.get("include_kw", ""))
    exclude = _keywords(mon.get("exclude_kw", ""))
    kept = [s for s in songs if _match_keywords(s, include, exclude)]
    return {
        "total": len(songs),
        "filtered": len(kept),
        "warnings": warnings,
        "songs": [
            {
                "id": s.get("id"),
                "source": s.get("source"),
                "name": s.get("name"),
                "artist": s.get("artist"),
                "album": s.get("album"),
                "duration": s.get("duration"),
                "origin": s.get("_origin", ""),
            }
            for s in kept[:limit]
        ],
    }


async def redownload(db: Database, engine: Engine, mon: dict[str, Any], song: dict[str, Any]) -> dict[str, Any]:
    """对单首歌曲（通常来自失败记录的重试）重新走一遍择优 + 下载。"""
    quality = q.normalize_quality(mon.get("quality"))
    log_lines: list[str] = []
    cand, ev = await _pick_best(engine, song, quality=quality, sources=mon.get("sources") or [], log_lines=log_lines)
    if cand is None or ev is None:
        db.upsert_track(mon["id"], song, status="failed", error="所有候选源都探测失败")
        return {"ok": False, "message": "所有候选源都探测失败", "log": log_lines}

    if not ev["satisfies"] and mon.get("fallback") == "skip":
        db.upsert_track(mon["id"], song, status="missing",
                        error=f"未找到满足「{q.quality_label(quality)}」的源", bitrate=ev["bitrate"],
                        quality_actual=q.actual_quality_desc(ev))
        return {"ok": False, "message": "未达目标音质，按策略跳过", "log": log_lines}

    result = await engine.download(cand, embed=bool(mon.get("embed")))
    db.upsert_track(
        mon["id"], cand, status="downloaded",
        quality_actual=q.actual_quality_desc(ev),
        bitrate=ev["bitrate"],
        file_path=result.get("path") or result.get("filename") or "",
    )
    return {"ok": True, "message": "重试下载成功", "song": cand, "quality": q.actual_quality_desc(ev), "log": log_lines}


def _reschedule(db: Database, mon: dict[str, Any]) -> None:
    try:
        db.execute("UPDATE monitors SET last_run_at = ? WHERE id = ?", (now_iso(), mon["id"]))
        db.set_next_run(mon["id"], interval_minutes=int(mon.get("interval_minutes") or 360))
    except Exception:  # noqa: BLE001
        log.exception("更新下次运行时间失败 monitor=%s", mon.get("id"))
