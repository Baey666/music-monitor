"""SQLite 持久化层。表结构简单，直接写 SQL，避免引入 ORM 依赖。"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from typing import Any

from .config import settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS monitors (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT    NOT NULL,
    kind              TEXT    NOT NULL,              -- chart | playlist | favorites
    enabled           INTEGER NOT NULL DEFAULT 1,
    sources           TEXT    NOT NULL DEFAULT '[]', -- JSON: 参与的平台列表
    target            TEXT    NOT NULL DEFAULT '{}', -- JSON: 榜单/链接/收藏夹的具体目标
    quality           TEXT    NOT NULL DEFAULT 'lossless',
    fallback          TEXT    NOT NULL DEFAULT 'best_effort', -- best_effort | skip
    auto_download     INTEGER NOT NULL DEFAULT 1,
    embed             INTEGER NOT NULL DEFAULT 1,
    interval_minutes  INTEGER NOT NULL DEFAULT 360,
    max_downloads     INTEGER NOT NULL DEFAULT 30,
    include_kw        TEXT    NOT NULL DEFAULT '',
    exclude_kw        TEXT    NOT NULL DEFAULT '',
    last_run_at       TEXT,
    next_run_at       TEXT,
    created_at        TEXT    NOT NULL,
    updated_at        TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS tracks (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    monitor_id     INTEGER NOT NULL,
    source         TEXT    NOT NULL,
    song_id        TEXT    NOT NULL,
    name           TEXT    NOT NULL DEFAULT '',
    artist         TEXT    NOT NULL DEFAULT '',
    album          TEXT    NOT NULL DEFAULT '',
    duration       INTEGER NOT NULL DEFAULT 0,
    cover          TEXT    NOT NULL DEFAULT '',
    extra          TEXT    NOT NULL DEFAULT '{}',
    status         TEXT    NOT NULL DEFAULT 'pending', -- pending|downloaded|skipped|failed|missing
    quality_actual TEXT    NOT NULL DEFAULT '',
    bitrate        TEXT    NOT NULL DEFAULT '',
    file_path      TEXT    NOT NULL DEFAULT '',
    error          TEXT    NOT NULL DEFAULT '',
    hit_count      INTEGER NOT NULL DEFAULT 1,
    first_seen     TEXT    NOT NULL,
    last_seen      TEXT    NOT NULL,
    updated_at     TEXT    NOT NULL,
    UNIQUE(monitor_id, source, song_id)
);
CREATE INDEX IF NOT EXISTS idx_tracks_monitor ON tracks(monitor_id);
CREATE INDEX IF NOT EXISTS idx_tracks_status  ON tracks(status);
CREATE INDEX IF NOT EXISTS idx_tracks_name    ON tracks(name, artist);

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    monitor_id  INTEGER NOT NULL,
    started_at  TEXT    NOT NULL,
    finished_at TEXT,
    status      TEXT    NOT NULL DEFAULT 'running', -- running|ok|partial|error
    found       INTEGER NOT NULL DEFAULT 0,
    new_items   INTEGER NOT NULL DEFAULT 0,
    downloaded  INTEGER NOT NULL DEFAULT 0,
    skipped     INTEGER NOT NULL DEFAULT 0,
    failed      INTEGER NOT NULL DEFAULT 0,
    message     TEXT    NOT NULL DEFAULT '',
    log         TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_runs_monitor ON runs(monitor_id, id DESC);
"""


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


class Database:
    def __init__(self, path: str) -> None:
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, timeout=30, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    # ---------------- 通用 ----------------
    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, params).fetchall())

    def one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    # ---------------- settings ----------------
    def get_setting(self, key: str, default: Any = None) -> Any:
        row = self.one("SELECT value FROM settings WHERE key = ?", (key,))
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except (TypeError, ValueError):
            return row["value"]

    def set_setting(self, key: str, value: Any) -> None:
        self.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value, ensure_ascii=False)),
        )

    def all_settings(self) -> dict[str, Any]:
        return {r["key"]: self.get_setting(r["key"]) for r in self.query("SELECT key FROM settings")}

    # ---------------- monitors ----------------
    def create_monitor(self, data: dict[str, Any]) -> int:
        ts = now_iso()
        cur = self.execute(
            """INSERT INTO monitors
               (name, kind, enabled, sources, target, quality, fallback, auto_download, embed,
                interval_minutes, max_downloads, include_kw, exclude_kw,
                last_run_at, next_run_at, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,?,?,?)""",
            (
                data["name"],
                data["kind"],
                int(data.get("enabled", 1)),
                json.dumps(data.get("sources", []), ensure_ascii=False),
                json.dumps(data.get("target", {}), ensure_ascii=False),
                data.get("quality", "lossless"),
                data.get("fallback", "best_effort"),
                int(data.get("auto_download", 1)),
                int(data.get("embed", 1)),
                int(data.get("interval_minutes", 360)),
                int(data.get("max_downloads", 30)),
                data.get("include_kw", ""),
                data.get("exclude_kw", ""),
                ts,  # next_run_at（随即被 set_next_run 覆盖）
                ts,  # created_at
                ts,  # updated_at
            ),
        )
        mid = int(cur.lastrowid)
        self.set_next_run(mid, immediately=True)
        return mid

    def update_monitor(self, mid: int, data: dict[str, Any]) -> None:
        fields, params = [], []
        for key in (
            "name", "kind", "quality", "fallback", "include_kw", "exclude_kw",
        ):
            if key in data:
                fields.append(f"{key} = ?")
                params.append(data[key])
        for key in ("enabled", "auto_download", "embed", "interval_minutes", "max_downloads"):
            if key in data:
                fields.append(f"{key} = ?")
                params.append(int(data[key]))
        for key in ("sources", "target"):
            if key in data:
                fields.append(f"{key} = ?")
                params.append(json.dumps(data[key], ensure_ascii=False))
        if not fields:
            return
        fields.append("updated_at = ?")
        params.append(now_iso())
        params.append(mid)
        self.execute(f"UPDATE monitors SET {', '.join(fields)} WHERE id = ?", tuple(params))

    def delete_monitor(self, mid: int) -> None:
        self.execute("DELETE FROM tracks WHERE monitor_id = ?", (mid,))
        self.execute("DELETE FROM runs WHERE monitor_id = ?", (mid,))
        self.execute("DELETE FROM monitors WHERE id = ?", (mid,))

    def get_monitor(self, mid: int) -> dict[str, Any] | None:
        row = self.one("SELECT * FROM monitors WHERE id = ?", (mid,))
        return self._row_to_monitor(row) if row else None

    def list_monitors(self) -> list[dict[str, Any]]:
        rows = self.query("SELECT * FROM monitors ORDER BY id DESC")
        return [self._row_to_monitor(r) for r in rows]

    def due_monitors(self, limit: int = 5) -> list[dict[str, Any]]:
        rows = self.query(
            "SELECT * FROM monitors WHERE enabled = 1 AND (next_run_at IS NULL OR next_run_at <= ?) "
            "ORDER BY COALESCE(next_run_at, '') ASC LIMIT ?",
            (now_iso(), limit),
        )
        return [self._row_to_monitor(r) for r in rows]

    def set_next_run(self, mid: int, *, immediately: bool = False, interval_minutes: int | None = None) -> None:
        if interval_minutes is None:
            mon = self.get_monitor(mid)
            interval_minutes = int(mon["interval_minutes"]) if mon else 360
        nxt = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() + interval_minutes * 60))
        if immediately:
            nxt = now_iso()
        self.execute("UPDATE monitors SET next_run_at = ? WHERE id = ?", (nxt, mid))

    @staticmethod
    def _row_to_monitor(row: sqlite3.Row) -> dict[str, Any]:
        m = dict(row)
        m["sources"] = json.loads(m.get("sources") or "[]")
        m["target"] = json.loads(m.get("target") or "{}")
        m["enabled"] = bool(m.get("enabled"))
        m["auto_download"] = bool(m.get("auto_download"))
        m["embed"] = bool(m.get("embed"))
        return m

    # ---------------- runs ----------------
    def recover_running_runs(self) -> int:
        """容器重启后收尾上次未完成的运行，避免网页永久显示 running。"""
        cur = self.execute(
            "UPDATE runs SET finished_at = ?, status = 'error', message = ?, log = CASE WHEN log = '' THEN ? ELSE log END "
            "WHERE status = 'running'",
            (now_iso(), "服务重启，中断了上次运行；可重新执行监控", "服务重启，中断了上次运行"),
        )
        return cur.rowcount

    def start_run(self, monitor_id: int) -> int:
        cur = self.execute(
            "INSERT INTO runs(monitor_id, started_at, status) VALUES(?,?,'running')",
            (monitor_id, now_iso()),
        )
        return int(cur.lastrowid)

    def finish_run(self, run_id: int, **kw: Any) -> None:
        self.execute(
            """UPDATE runs SET finished_at = ?, status = ?, found = ?, new_items = ?,
               downloaded = ?, skipped = ?, failed = ?, message = ?, log = ? WHERE id = ?""",
            (
                now_iso(),
                kw.get("status", "ok"),
                kw.get("found", 0),
                kw.get("new_items", 0),
                kw.get("downloaded", 0),
                kw.get("skipped", 0),
                kw.get("failed", 0),
                kw.get("message", "")[:500],
                kw.get("log", "")[:20000],
                run_id,
            ),
        )

    def recent_runs(self, monitor_id: int | None = None, limit: int = 50) -> list[dict[str, Any]]:
        if monitor_id:
            rows = self.query("SELECT * FROM runs WHERE monitor_id = ? ORDER BY id DESC LIMIT ?", (monitor_id, limit))
        else:
            rows = self.query("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    # ---------------- tracks ----------------
    def existing_song_keys(self, monitor_id: int) -> set[tuple[str, str]]:
        rows = self.query("SELECT source, song_id FROM tracks WHERE monitor_id = ?", (monitor_id,))
        return {(r["source"], r["song_id"]) for r in rows}

    def downloaded_tracks(self, monitor_id: int) -> dict[tuple[str, str], str]:
        rows = self.query(
            "SELECT source, song_id, file_path FROM tracks "
            "WHERE monitor_id = ? AND status = 'downloaded'",
            (monitor_id,),
        )
        return {(r["source"], r["song_id"]): (r["file_path"] or "") for r in rows}

    def upsert_track(self, monitor_id: int, song: dict[str, Any], status: str, **kw: Any) -> None:
        ts = now_iso()
        self.execute(
            """INSERT INTO tracks
               (monitor_id, source, song_id, name, artist, album, duration, cover, extra,
                status, quality_actual, bitrate, file_path, error, hit_count, first_seen, last_seen, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(monitor_id, source, song_id) DO UPDATE SET
                 status = excluded.status,
                 quality_actual = excluded.quality_actual,
                 bitrate = excluded.bitrate,
                 file_path = CASE WHEN excluded.file_path <> '' THEN excluded.file_path ELSE tracks.file_path END,
                 error = excluded.error,
                 hit_count = tracks.hit_count + 1,
                 last_seen = excluded.last_seen,
                 updated_at = excluded.updated_at""",
            (
                monitor_id,
                song.get("source", ""),
                str(song.get("id", "")),
                song.get("name", ""),
                song.get("artist", ""),
                song.get("album", ""),
                int(song.get("duration") or 0),
                song.get("cover", ""),
                json.dumps(song.get("extra") or {}, ensure_ascii=False),
                status,
                kw.get("quality_actual", ""),
                kw.get("bitrate", ""),
                kw.get("file_path", ""),
                kw.get("error", ""),
                1,   # hit_count
                ts,  # first_seen
                ts,  # last_seen
                ts,  # updated_at
            ),
        )

    def touch_track(self, monitor_id: int, song: dict[str, Any]) -> None:
        """已见过的歌：只刷新命中次数与最后出现时间，不改状态。"""
        ts = now_iso()
        self.execute(
            "UPDATE tracks SET hit_count = hit_count + 1, last_seen = ?, updated_at = ? "
            "WHERE monitor_id = ? AND source = ? AND song_id = ?",
            (ts, ts, monitor_id, song.get("source", ""), str(song.get("id", ""))),
        )

    # 跨监控的「已下载」指纹，用于避免同一首歌被多个监控重复下载
    def downloaded_fingerprints(self) -> set[str]:
        rows = self.query("SELECT name, artist FROM tracks WHERE status = 'downloaded'")
        return {_fingerprint(r["name"], r["artist"]) for r in rows}

    def list_tracks(
        self,
        monitor_id: int | None = None,
        status: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM tracks WHERE 1=1"
        params: list[Any] = []
        if monitor_id:
            sql += " AND monitor_id = ?"
            params.append(monitor_id)
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        return [dict(r) for r in self.query(sql, tuple(params))]

    def track_stats(self) -> dict[str, int]:
        rows = self.query("SELECT status, COUNT(*) AS c FROM tracks GROUP BY status")
        stats = {r["status"]: r["c"] for r in rows}
        stats["total"] = sum(stats.values())
        return stats


def _fingerprint(name: str, artist: str) -> str:
    """歌名+歌手的归一化指纹，用于跨监控去重。"""
    def norm(s: str) -> str:
        s = (s or "").lower()
        return "".join(ch for ch in s if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")

    return f"{norm(name)}|{norm(artist)}"


fingerprint = _fingerprint
