"""端到端冒烟测试：用 stub_engine 替代真实的 go-music-dl，验证完整链路。

    cd music-monitor/monitor
    ../.venv/Scripts/python.exe -m tests.smoke_test

覆盖：平台清单解析 → 榜单解析 → 干跑预览 → 创建监控 → 调度执行 →
      音质择优（含跨平台换源）→ 触发下载 → 曲目记录落库。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx
import uvicorn

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

ENGINE_PORT = 18085
MONITOR_PORT = 19090

WORKDIR = Path(tempfile.mkdtemp(prefix="music-monitor-test-"))
os.environ["MONITOR_DB_DIR"] = str(WORKDIR)
os.environ["ENGINE_URL"] = f"http://127.0.0.1:{ENGINE_PORT}"
os.environ["TICK_SECONDS"] = "15"
os.environ["HTTP_TIMEOUT"] = "10"

PASS: list[str] = []
FAIL: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASS.append(label)
        print(f"  [OK]   {label}")
    else:
        FAIL.append(f"{label} {detail}")
        print(f"  [FAIL] {label} {detail}")


def serve(app, port: int) -> uvicorn.Server:
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(60):
        try:
            httpx.get(f"http://127.0.0.1:{port}/", timeout=1)
            break
        except Exception:  # noqa: BLE001
            time.sleep(0.2)
    return server


def main() -> int:
    from tests import stub_engine  # noqa: PLC0415

    print("启动 stub 引擎 …")
    serve(stub_engine.app, ENGINE_PORT)

    print("启动 music-monitor …")
    from app.main import app as monitor_app  # noqa: PLC0415

    serve(monitor_app, MONITOR_PORT)

    base = f"http://127.0.0.1:{MONITOR_PORT}"
    with httpx.Client(base_url=base, timeout=30) as c:
        # ---------------- 1. 健康检查 ----------------
        print("\n[1] 健康检查")
        r = c.get("/api/health").json()
        check("引擎连通", r["engine"]["ok"] is True, str(r["engine"]))
        check("调度器已启动", r["scheduler"]["tick_seconds"] >= 10, str(r["scheduler"]))

        # ---------------- 2. 平台清单 ----------------
        print("\n[2] 平台清单解析")
        r = c.get("/api/platforms").json()
        keys = [p["key"] for p in r["items"]]
        check("解析出 3 个平台", len(keys) == 3, str(keys))
        check("平台中文名解析正确", any(p["label"] == "网易云音乐" for p in r["items"]))
        check("个人歌单能力标记正确", next(p for p in r["items"] if p["key"] == "kugou")["user_playlist"] is False)

        # ---------------- 3. 引擎设置 ----------------
        print("\n[3] 引擎设置读取")
        r = c.get("/api/engine/settings").json()
        check("读到下载目录", r["settings"].get("downloadDir") == "data/downloads")

        # ---------------- 4. 榜单解析 ----------------
        print("\n[4] 榜单解析")
        r = c.get("/api/charts/resolve", params={"key": "netease_hot"}).json()
        check("内置榜单可解析", r["ok"] and r["count"] == 1, str(r.get("message")))
        r = c.get("/api/charts/resolve", params={"link": "https://music.163.com/#/playlist?id=19723756"}).json()
        check("自定义链接可解析", r["ok"] and r["count"] == 3, str(r.get("message")))

        # ---------------- 5. 歌单链接解析 ----------------
        print("\n[5] 歌单链接解析")
        r = c.get("/api/playlists/resolve", params={"link": "https://music.163.com/#/playlist?id=19723756"}).json()
        check("歌单识别成功", r["ok"] and r["count"] == 3, str(r.get("message")))

        # ---------------- 6. 个人收藏夹 ----------------
        print("\n[6] 个人收藏夹")
        r = c.post("/api/favorites/list", json={"sources": ["netease", "qq"]}).json()
        check("读到 2 个收藏夹", len(r["items"]) == 2, str(r.get("message")))
        check("收藏夹 key 格式正确", r["items"][0]["key"] == "netease:fav1")

        # ---------------- 7. 干跑预览 ----------------
        print("\n[7] 干跑预览")
        draft = {
            "kind": "chart",
            "sources": ["netease", "qq", "kugou"],
            "target": {"charts": [{"key": "netease_soaring", "name": "飙升榜", "platform": "netease", "id": "19723756"}]},
            "include_kw": "",
            "exclude_kw": "稻香",
        }
        r = c.post("/api/preview", json=draft).json()
        check("干跑解析到 3 首", r["total"] == 3, str(r.get("warnings")))
        check("排除关键词生效", r["filtered"] == 2, f"filtered={r['filtered']}")

        # ---------------- 8. 创建监控 ----------------
        print("\n[8] 创建监控")
        monitor = {
            "name": "飙升榜 · 无损优先",
            "kind": "chart",
            "enabled": False,          # 先关掉，避免自动调度抢跑
            "sources": ["netease", "qq"],
            "target": {"charts": [{"key": "netease_soaring", "name": "飙升榜", "platform": "netease", "id": "19723756"}]},
            "quality": "lossless",
            "fallback": "best_effort",
            "auto_download": True,
            "embed": True,
            "interval_minutes": 360,
            "max_downloads": 10,
            "include_kw": "",
            "exclude_kw": "稻香",
        }
        r = c.post("/api/monitors", json=monitor).json()
        mid = r["id"]
        check("监控创建成功", isinstance(mid, int) and mid > 0)

        # ---------------- 9. 手动执行 ----------------
        print("\n[9] 手动执行（音质择优 + 换源 + 下载）")
        r = c.post(f"/api/monitors/{mid}/run").json()
        check("任务已触发", r["started"] is True, str(r))

        detail: dict = {}
        for _ in range(60):
            time.sleep(0.5)
            detail = c.get(f"/api/monitors/{mid}").json()
            if detail["runs"] and detail["runs"][0]["finished_at"]:
                break

        run = detail["runs"][0] if detail.get("runs") else {}
        check("运行已完成", bool(run.get("finished_at")), str(run.get("status")))
        check("下载 2 首（稻香被排除）", run.get("downloaded") == 2, f"downloaded={run.get('downloaded')} log={run.get('log')}")
        check("无失败", run.get("failed") == 0, str(run.get("message")))

        # ---------------- 10. 音质与换源验证 ----------------
        print("\n[10] 音质择优与跨平台换源")
        sent = stub_engine.DOWNLOADS
        targets = {d["id"]: d for d in sent}
        check("晴天走原始源无损下载", "s1" in targets and targets["s1"]["source"] == "netease")
        check("夜曲降级时成功换到 QQ 无损源", "q2" in targets and targets["q2"]["source"] == "qq",
              f"实际请求={[ (d['source'], d['id']) for d in sent ]}")
        check("下载时携带了内嵌元数据参数", all(d.get("embed") == "1" for d in sent))

        tracks = c.get("/api/tracks", params={"monitor_id": mid}).json()
        by_id = {t["song_id"]: t for t in tracks["items"]}
        check("晴天记录为无损", "无损" in (by_id.get("s1", {}).get("quality_actual") or ""),
              str(by_id.get("s1", {}).get("quality_actual")))
        check("夜曲记录为无损（换源后）", "无损" in (by_id.get("q2", {}).get("quality_actual") or ""),
              str(by_id.get("q2", {}).get("quality_actual")))
        check("被排除的稻香记为 skipped", by_id.get("s3", {}).get("status") == "skipped",
              str(by_id.get("s3", {}).get("status")))

        # ---------------- 11. 增量去重 ----------------
        print("\n[11] 增量去重（再跑一次不应重复下载）")
        before = len(stub_engine.DOWNLOADS)
        c.post(f"/api/monitors/{mid}/run")
        for _ in range(60):
            time.sleep(0.5)
            runs = c.get(f"/api/monitors/{mid}/runs").json()["items"]
            if len(runs) >= 2 and runs[0]["finished_at"]:
                break
        check("第二次没有新增下载", len(stub_engine.DOWNLOADS) == before,
              f"before={before} after={len(stub_engine.DOWNLOADS)}")

        # ---------------- 12. 严格模式 ----------------
        print("\n[12] fallback=skip 的严格模式")
        strict = dict(monitor, name="严格无损", fallback="skip", max_downloads=10)
        strict["target"] = {"charts": [{"key": "netease_new", "name": "新歌榜", "platform": "netease", "id": "3778678"}]}
        strict["exclude_kw"] = ""
        sid = c.post("/api/monitors", json=strict).json()["id"]
        c.post(f"/api/monitors/{sid}/run")
        for _ in range(60):
            time.sleep(0.5)
            detail = c.get(f"/api/monitors/{sid}").json()
            if detail["runs"] and detail["runs"][0]["finished_at"]:
                break
        rows = c.get("/api/tracks", params={"monitor_id": sid}).json()["items"]
        check("不可用音源记为 failed", rows and rows[0]["status"] == "failed", str(rows[:1]))

        # ---------------- 13. 监控 CRUD ----------------
        print("\n[13] 监控增删改查")
        r = c.patch(f"/api/monitors/{mid}", json={"quality": "high", "enabled": True}).json()
        check("修改生效", r["monitor"]["quality"] == "high" and r["monitor"]["enabled"] is True)
        c.delete(f"/api/monitors/{mid}")
        check("删除后 404", c.get(f"/api/monitors/{mid}").status_code == 404)

        # ---------------- 14. 设置 ----------------
        print("\n[14] 服务设置")
        c.post("/api/settings", json={"default_quality": "high"})
        check("设置已保存", c.get("/api/settings").json()["settings"]["default_quality"] == "high")

        # ---------------- 15. 前端静态资源 ----------------
        print("\n[15] 前端与榜单接口")
        home = c.get("/")
        check("首页可访问", home.status_code == 200 and "music-monitor" in home.text)
        check("样式表可访问", c.get("/static/style.css").status_code == 200)
        js = c.get("/static/app.js")
        check("脚本可访问", js.status_code == 200 and "App" in js.text)
        groups = c.get("/api/charts").json()["groups"]
        check("榜单分组返回正常", len(groups) >= 4 and all("charts" in g for g in groups), f"groups={len(groups)}")
        check("内置榜单键索引完整", "netease_hot" in c.get("/api/charts").json()["index"])
        index = c.get("/api/charts").json()["index"]
        check("QQ/酷狗/酷我 榜单改用榜单接口", all(index[k].get("rank") for k in ("qq_hot", "kugou_top500", "kuwo_hot")))
        check("网易云榜单仍走歌单 ID", bool(index["netease_hot"].get("id")))

        mid2 = c.post("/api/monitors", json=monitor).json()["id"]
        r = c.post(f"/api/monitors/{mid2}/preview").json()
        check("监控干跑接口可用", r["total"] == 3, str(r.get("warnings")))
        c.delete(f"/api/monitors/{mid2}")

    print("\n" + "=" * 62)
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    for item in FAIL:
        print("  × " + item)
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        shutil.rmtree(WORKDIR, ignore_errors=True)
    sys.exit(code)
