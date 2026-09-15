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
# 用一个真实目录当「下载目录只读挂载」。不设的话 /downloads 不存在，
# 所有文件都会判成「不存在」→ 增量去重那条断言会空过（曾经就是这样漏掉了重复推送的 bug）。
DOWNLOADS_ROOT = WORKDIR / "downloads"
DOWNLOADS_ROOT.mkdir(parents=True, exist_ok=True)
os.environ["DOWNLOADS_MOUNT"] = str(DOWNLOADS_ROOT)
# 分批推送：测试里把批次调小，确保多批路径被走到
os.environ["DOWNLOAD_BATCH"] = "2"
os.environ["DOWNLOAD_BATCH_GAP"] = "0"

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


def asgi_get(app, path: str) -> tuple[int, bytes]:
    """直接调 ASGI，拿到 (状态码, 完整 body)，不经过网络。

    有些错在 HTTP 层看不出来：`204` 带 body 时客户端会把 body 丢掉（httpx 读出 `b''`），
    只有 uvicorn 自己会在 send 阶段抛 `RuntimeError: Response content longer than
    Content-Length` 并打断 keep-alive 连接。所以要在这层断言。
    """
    import asyncio  # noqa: PLC0415

    async def run() -> tuple[int, bytes]:
        messages: list[dict] = []

        async def receive() -> dict:
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message: dict) -> None:
            messages.append(message)

        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "root_path": "",
            "headers": [(b"host", b"testserver")],
            "client": ("127.0.0.1", 12345),
            "server": ("127.0.0.1", 80),
        }
        await app(scope, receive, send)
        status = next(m["status"] for m in messages if m["type"] == "http.response.start")
        body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
        return status, body

    return asyncio.run(run())


def run_and_wait(c: httpx.Client, mid: int, timeout: float = 60.0) -> dict:
    """触发一次执行并等它**真的**结束。

    按 run id 比对新增记录，不能只看 `runs[0].finished_at` —— 上一次运行的记录
    一开始就满足这个条件，会让等待立刻返回，随后的断言全部空过（曾经就这么漏过 bug）。
    """
    before = {r["id"] for r in c.get(f"/api/monitors/{mid}/runs").json()["items"]}
    started = c.post(f"/api/monitors/{mid}/run").json()
    if not started.get("started"):
        print(f"  [WARN] 任务没启动起来：{started.get('message')}")
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(0.4)
        for r in c.get(f"/api/monitors/{mid}/runs").json()["items"]:
            if r["id"] not in before and r["finished_at"]:
                return r
    return {}


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
        run2 = run_and_wait(c, mid)
        check("第二次运行确实执行了", bool(run2.get("finished_at")), str(run2))
        check("第二次没有新增下载", len(stub_engine.DOWNLOADS) == before,
              f"before={before} after={len(stub_engine.DOWNLOADS)} log={run2.get('log')}")

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
        # 204 不能带 body：带 body 时 uvicorn 每次都在 send 阶段抛
        # RuntimeError: Response content longer than Content-Length，并打断 keep-alive 连接。
        # HTTP 层看不出（客户端把 body 丢掉），必须在 ASGI 层断言。
        fav_status, fav_body = asgi_get(monitor_app, "/favicon.ico")
        check(
            "favicon 是空 body 的 204",
            fav_status == 204 and fav_body == b"",
            f"{fav_status} body={fav_body!r}",
        )
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

        # ---------------- 16. 歌手关注 ----------------
        print("\n[16] 歌手关注")
        draft = {
            "kind": "artist",
            "sources": ["netease"],
            "target": {"artists": [{"name": "周杰伦", "sources": []}]},
            "include_kw": "",
            "exclude_kw": "",
        }
        r = c.post("/api/preview", json=draft).json()
        names = [s["artist"] for s in r["songs"]]
        check("歌手搜索命中曲目", r["total"] >= 3, str(r.get("warnings")))
        check("按歌手名过滤掉了翻唱", all("周杰伦" in n for n in names), str(names))
        check("混入的翻唱已被剔除", "某翻唱歌手" not in " ".join(names), str(names))
        check("曲目带歌手来源标记", all(s.get("origin") == "周杰伦" for s in r["songs"]), str(r["songs"][:1]))

        artist_mon = {
            "name": "关注周杰伦",
            "kind": "artist",
            "enabled": False,
            "sources": ["netease"],
            "target": {"artists": [{"name": "周杰伦", "sources": []}]},
            "quality": "lossless",
            "fallback": "best_effort",
            "auto_download": True,
            "embed": True,
            "interval_minutes": 360,
            "max_downloads": 10,
            "include_kw": "",
            "exclude_kw": "",
        }
        aid = c.post("/api/monitors", json=artist_mon).json()["id"]
        check("歌手关注监控创建成功", isinstance(aid, int) and aid > 0)

        before = len(stub_engine.DOWNLOADS)
        run = run_and_wait(c, aid)
        check("歌手关注任务执行完成", bool(run.get("finished_at")), str(run.get("status")))
        check("只下载了周杰伦本人的 3 首", run.get("downloaded") == 3,
              f"downloaded={run.get('downloaded')} log={run.get('log')}")
        a_tracks = c.get("/api/tracks", params={"monitor_id": aid}).json()["items"]
        a_ids = {t["song_id"] for t in a_tracks}
        check("翻唱 s5 未进入曲库", "s5" not in a_ids, str(sorted(a_ids)))
        check("歌手关注触发了下载", len(stub_engine.DOWNLOADS) > before)

        # 歌手监控的增量去重
        mid_before = len(stub_engine.DOWNLOADS)
        run = run_and_wait(c, aid)
        check("歌手关注二次运行确实执行了", bool(run.get("finished_at")), str(run))
        check("歌手关注二次运行不重复下载", len(stub_engine.DOWNLOADS) == mid_before,
              f"before={mid_before} after={len(stub_engine.DOWNLOADS)} log={run.get('log')}")

        # 非法 kind 应被拒绝
        bad = dict(artist_mon, kind="singer")
        check("未知监控类型被拒绝", c.post("/api/monitors", json=bad).status_code == 422)

        detail = c.get(f"/api/monitors/{aid}").json()
        check("歌手关注 target 原样保存", detail["monitor"]["target"]["artists"][0]["name"] == "周杰伦",
              str(detail["monitor"]["target"]))
        c.delete(f"/api/monitors/{aid}")

        # ---------------- 17. 引擎登录校验 ----------------
        # 回归：上游失败时返回的是 200 + 登录页，以前这里用「cookie jar 非空」判成功，
        # 于是乱输的账号密码也显示「登录成功」，还把原本正确的凭据覆盖掉。
        print("\n[17] 引擎登录校验（乱输不能算成功）")
        r = c.post("/api/engine/login", json={"username": "广泛广泛", "password": "ddfsddf"}).json()
        check("错误账号密码必须报失败", r.get("ok") is False, str(r))
        check("失败原因说清了是账号密码问题", "不正确" in (r.get("detail") or ""), str(r.get("detail")))
        check("失败时不落库垃圾凭据",
              (c.get("/api/settings").json()["settings"].get("engine_username") or "") != "广泛广泛",
              str(c.get("/api/settings").json()["settings"].get("engine_username")))
        check("错误登录后不持有会话", c.get("/api/engine/session").json()["session_ok"] is False,
              str(c.get("/api/engine/session").json()))

        r = c.post("/api/engine/login", json={"username": "admin", "password": "wrong-password"}).json()
        check("正确账号+错误密码也是失败", r.get("ok") is False, str(r))

        r = c.post("/api/engine/login", json={"username": "admin", "password": "correct-horse"}).json()
        check("正确账号密码登录成功", r.get("ok") is True, str(r))
        sess = c.get("/api/engine/session").json()
        check("登录后会话校验通过", sess["session_ok"] is True, str(sess))
        check("登录成功后账号已落库", c.get("/api/settings").json()["settings"].get("engine_username") == "admin")
        check("密码不回传浏览器", "engine_password" not in c.get("/api/settings").json()["settings"],
              str(c.get("/api/settings").json()["settings"]))
        check("能读到引擎里已配 Cookie 的平台", "netease" in (c.get("/api/engine/cookies").json().get("configured") or []),
              str(c.get("/api/engine/cookies").json()))
        check("/api/health 报告会话有效", c.get("/api/health").json()["engine"]["session_ok"] is True)

        # 再用一次错误凭据：应该把前面的有效会话清掉，而不是继续「看起来是登录状态」
        r = c.post("/api/engine/login", json={"username": "admin", "password": "nope-nope"}).json()
        check("再次错误登录仍报失败", r.get("ok") is False, str(r))
        check("错误登录后旧会话被清掉", c.get("/api/engine/session").json()["session_ok"] is False,
              str(c.get("/api/engine/session").json()))
        c.post("/api/engine/logout")
        check("退出登录后凭据清空", (c.get("/api/settings").json()["settings"].get("engine_username") or "") == "",
              str(c.get("/api/settings").json()["settings"]))

        # ---------------- 18. 「引擎库里已有」不重复推送 ----------------
        # 回归：上游 skip 时回的是 `skipped:true + path:"" + filename(无扩展名)`。
        # 以前直接把 filename 当路径存下，下一轮就判「文件不存在」→ 每轮重复推送，
        # 引擎侧堆出一大堆 skipped 记录，两边数量永远对不上。
        print("\n[18] 引擎已存在的歌：路径要还原，且不能重复推送")
        stub_engine.ALREADY_ON_DISK.add("s9")
        skip_mon = {
            "name": "已存在用例",
            "kind": "playlist",
            "enabled": False,
            "sources": ["netease"],
            "target": {"playlists": [{"name": "已存在歌单", "source": "netease", "id": "already_have"}]},
            "quality": "lossless",
            "fallback": "best_effort",
            "auto_download": True,
            "embed": True,
            "interval_minutes": 360,
            "max_downloads": 10,
        }
        sid = c.post("/api/monitors", json=skip_mon).json()["id"]
        run = run_and_wait(c, sid)
        check("已存在用例执行完成", bool(run.get("finished_at")), str(run))
        check("引擎跳过也记为 downloaded", run.get("downloaded") == 1, f"downloaded={run.get('downloaded')} log={run.get('log')}")
        check("统计里标出了引擎已存在", run.get("engine_skipped") == 1, str(run))

        rows = c.get("/api/tracks", params={"monitor_id": sid}).json()["items"]
        fp = (rows[0].get("file_path") or "") if rows else ""
        check("skip 时把裸 filename 还原成了真实路径", fp.startswith("data/downloads/") and fp.endswith(".flac"),
              f"file_path={fp!r}")

        pushed_before = len(stub_engine.DOWNLOADS)
        run = run_and_wait(c, sid)
        check("第二轮不再重复推送已存在的歌", len(stub_engine.DOWNLOADS) == pushed_before,
              f"before={pushed_before} after={len(stub_engine.DOWNLOADS)} log={run.get('log')}")
        check("第二轮全部走本地跳过", "跳过 1 首" in (run.get("log") or ""), str(run.get("log")))
        c.delete(f"/api/monitors/{sid}")

        # ---------------- 19. 分批推送 ----------------
        print("\n[19] 分批推送（DOWNLOAD_BATCH=2）")
        batch_mon = {
            "name": "分批用例",
            "kind": "chart",
            "enabled": False,
            "sources": ["netease"],
            "target": {"charts": [{"key": "netease_soaring", "name": "飙升榜", "platform": "netease", "id": "19723756"}]},
            "quality": "lossless",
            "fallback": "best_effort",
            "auto_download": True,
            "embed": True,
            "interval_minutes": 360,
            "max_downloads": 3,      # 3 首 / 每批 2 首 → 应该是 2 批
        }
        bid = c.post("/api/monitors", json=batch_mon).json()["id"]
        run = run_and_wait(c, bid)
        log = run.get("log") or ""
        check("分批用例执行完成", bool(run.get("finished_at")), str(run))
        check("按上限处理了 3 首", run.get("downloaded") == 3, f"downloaded={run.get('downloaded')} log={log}")
        check("日志说明分了几批", "每批 2 首分 2 批" in log, log[:400])
        check("出现了第 1 批", "第 1 批：推送 2 首" in log, log[:400])
        check("出现了第 2 批（一批下完才推下一批）", "第 2 批：推送 1 首" in log, log[:400])
        check("日志给出引擎已存在的口径", "引擎侧已存在" in log, log[-400:])
        c.delete(f"/api/monitors/{bid}")

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
