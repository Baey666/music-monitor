"""一轮监控的执行流水线：发现曲目 → 增量比对 → 按目标音质择优 → 交给引擎下载 → 记录结果。"""
from __future__ import annotations

import asyncio
import difflib
import logging
import re
from typing import Any

from . import quality as q
from .charts import resolve_chart
from .config import settings
from .db import Database, fingerprint, now_iso
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


def _reject_candidate(candidate: dict[str, Any], reference_duration: int = 0) -> str:
    """返回淘汰原因；空字符串表示候选可继续探测。"""
    text = " ".join(str(candidate.get(k) or "") for k in ("name", "album"))
    if _VERSION_MARKERS.search(text):
        return "标题或专辑标记为现场/演唱会/试听/混音版本"

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
    primary_rejection = _reject_candidate(primary, reference_duration)
    if primary_rejection:
        log_lines.append(f"  原始源 {primary['source']} 跳过：{primary_rejection}")
        primary_eval = {"valid": False, "satisfies": False, "bitrate": "-", "size": "", "kbps": None}
    else:
        primary_eval = q.evaluate(quality, await engine.inspect(primary))
        log_lines.append(
            f"  原始源 {primary['source']} 探测：{'有效' if primary_eval['valid'] else '无效'} "
            f"{primary_eval['bitrate']} {primary_eval['size']}"
        )
    if primary_eval["satisfies"]:
        return primary, primary_eval

    candidates: list[tuple[dict[str, Any], dict[str, Any]]] = []
    if not primary_rejection:
        candidates.append((primary, primary_eval))

    # 1) 让引擎在其它平台找最接近的可用版本（自带相似度 + 时长 + 可播放校验）
    try:
        alt = await engine.switch_source(
            song.get("name", ""), song.get("artist", ""), song.get("source", ""), duration=int(song.get("duration") or 0)
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("switch_source 异常: %s", exc)
        alt = None
    if alt and alt.get("id") and alt.get("source") and alt.get("source") != primary["source"]:
        cand = _as_candidate(alt, primary=False, score=float(alt.get("score") or 0.0))
        rejection = _reject_candidate(cand, reference_duration)
        if rejection:
            log_lines.append(f"  换源候选 {cand['source']} 跳过：{rejection}")
        elif cand["similarity"] >= 0.75:
            ev = q.evaluate(quality, await engine.inspect(cand))
            log_lines.append(f"  换源候选 {cand['source']} 相似度{cand['similarity']}：{ev['bitrate']} {ev['size']}")
            if ev["valid"]:
                candidates.append((cand, ev))
                if ev["satisfies"]:
                    return cand, ev

    # 2) 仍然不达标：在允许的平台里搜索同名曲目，逐个探测
    wanted = [s for s in (sources or []) if s and s != primary["source"]]
    if wanted:
        queried = f"{song.get('name', '')} {song.get('artist', '')}".strip()
        try:
            found = await engine.search_songs(queried, wanted, exact_artist=song.get("artist", ""))
        except Exception as exc:  # noqa: BLE001
            log.warning("跨平台搜索失败 %s: %s", queried, exc)
            found = []

        ranked: list[tuple[float, dict[str, Any]]] = []
        for f in found:
            score = similarity(song.get("name", ""), song.get("artist", ""), f.get("name", ""), f.get("artist", ""))
            if score >= 0.72:
                ranked.append((score, f))
        ranked.sort(key=lambda x: (-x[0], q.source_rank(x[1].get("source", ""))))

        seen_src: set[str] = {c["source"] for c, _ in candidates}
        checked = 0
        for score, f in ranked:
            if checked >= 4:
                break
            src = f.get("source", "")
            if src in seen_src:
                continue
            cand = _as_candidate(f, primary=False, score=score)
            rejection = _reject_candidate(cand, reference_duration)
            if rejection:
                log_lines.append(f"  搜索候选 {src} 跳过：{rejection}")
                continue
            ev = q.evaluate(quality, await engine.inspect(cand))
            checked += 1
            log_lines.append(f"  搜索候选 {src} 相似度{score}：{ev['bitrate']} {ev['size']}")
            if ev["valid"]:
                seen_src.add(src)
                candidates.append((cand, ev))
                if ev["satisfies"]:
                    return cand, ev

    # 3) 没有任何候选达标
    valid_only = [(c, e) for c, e in candidates if e["valid"]]
    if not valid_only:
        return None, None
    # 挑实际码率最高的一个，交给上层按 fallback 策略决定用不用
    best_cand, best_eval = max(
        valid_only,
        key=lambda ce: (ce[1]["kbps"] or 0, 0 if ce[0]["is_primary"] else 1, -q.source_rank(ce[0]["source"])),
    )
    return best_cand, best_eval


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
        db_fingerprints = db.downloaded_fingerprints()

        quality = q.normalize_quality(mon.get("quality"))
        sources = mon.get("sources") or []
        max_downloads = min(int(mon.get("max_downloads") or 30), settings.max_downloads_per_run)
        auto_download = bool(mon.get("auto_download"))

        todo: list[dict[str, Any]] = []
        for s in songs:
            key = (str(s.get("source", "")), str(s.get("id", "")))
            if key in existing:
                # 已见过：只刷新命中次数与最后出现时间
                db.touch_track(mon["id"], s)
                skipped += 1
                continue
            if not _match_keywords(s, include, exclude):
                db.upsert_track(mon["id"], s, status="skipped", error="被关键词规则过滤")
                skipped += 1
                continue
            if fingerprint(s.get("name", ""), s.get("artist", "")) in db_fingerprints:
                db.upsert_track(mon["id"], s, status="downloaded", quality_actual="已在库中", error="")
                skipped += 1
                continue
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
