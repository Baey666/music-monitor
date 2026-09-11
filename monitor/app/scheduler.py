"""轮询式调度器。

不引入复杂的 job store：每 tick 秒扫一次「到点的监控」，用信号量控制并发。
好处是重启后状态天然正确（到点就跑），不会因为错过某个时刻而丢任务。
"""
from __future__ import annotations

import asyncio
import logging
import time

from . import pipeline
from .config import settings
from .db import Database
from .engine import Engine

log = logging.getLogger("monitor.scheduler")

MAX_PARALLEL_MONITORS = 3


class Scheduler:
    def __init__(self, db: Database, engine: Engine) -> None:
        self.db = db
        self.engine = engine
        self._loop_task: asyncio.Task | None = None
        self._stopping = asyncio.Event()
        self._running: dict[int, asyncio.Task] = {}
        self._sem = asyncio.Semaphore(MAX_PARALLEL_MONITORS)
        self.last_tick_at: float | None = None
        self.tick_count = 0
        self.last_error: str = ""

    # ------------------------------------------------------------------ 生命周期
    async def start(self) -> None:
        self._stopping.clear()
        self._loop_task = asyncio.create_task(self._loop(), name="monitor-scheduler")
        log.info("调度器已启动，心跳 %ss", settings.tick_seconds)

    async def stop(self) -> None:
        self._stopping.set()
        if self._loop_task is not None:
            self._loop_task.cancel()
            await asyncio.gather(self._loop_task, return_exceptions=True)
            self._loop_task = None
        for task in list(self._running.values()):
            task.cancel()
        if self._running:
            await asyncio.gather(*self._running.values(), return_exceptions=True)

    # ------------------------------------------------------------------ 循环
    async def _loop(self) -> None:
        while not self._stopping.is_set():
            try:
                await self.tick()
                self.last_error = ""
            except Exception as exc:  # noqa: BLE001
                self.last_error = str(exc)
                log.exception("调度 tick 异常")
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=settings.tick_seconds)
            except asyncio.TimeoutError:
                pass

    async def tick(self) -> None:
        self.last_tick_at = time.time()
        self.tick_count += 1
        # 清理已结束的任务句柄
        for mid, task in list(self._running.items()):
            if task.done():
                self._running.pop(mid, None)
        slots = MAX_PARALLEL_MONITORS - len(self._running)
        if slots <= 0:
            return
        for mon in self.db.due_monitors(limit=slots):
            if mon["id"] in self._running:
                continue
            self.spawn(mon["id"])

    def spawn(self, monitor_id: int) -> bool:
        """把某个监控丢进后台执行。已在本轮运行中则忽略。"""
        if monitor_id in self._running:
            return False
        task = asyncio.create_task(self._run(monitor_id), name=f"monitor-{monitor_id}")
        self._running[monitor_id] = task
        return True

    async def _run(self, monitor_id: int) -> None:
        async with self._sem:
            mon = self.db.get_monitor(monitor_id)
            if mon is None:
                return
            # 先占位，避免长任务在运行期间被反复选中
            self.db.set_next_run(monitor_id, interval_minutes=int(mon.get("interval_minutes") or 360))
            log.info("开始执行监控 #%s %s", monitor_id, mon.get("name"))
            try:
                result = await pipeline.run_monitor(self.db, self.engine, mon)
                log.info("监控 #%s 完成: %s", monitor_id, result)
            except Exception:  # noqa: BLE001
                log.exception("监控 #%s 执行失败", monitor_id)

    # ------------------------------------------------------------------ 状态
    def status(self) -> dict:
        now = time.time()
        return {
            "running_monitors": sorted(self._running.keys()),
            "parallel_limit": MAX_PARALLEL_MONITORS,
            "tick_seconds": settings.tick_seconds,
            "tick_count": self.tick_count,
            "last_tick_ago": round(now - self.last_tick_at, 1) if self.last_tick_at else None,
            "last_error": self.last_error,
            "download_concurrency": settings.download_concurrency,
        }
