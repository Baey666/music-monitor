"""假的 go-music-dl 服务，用来离线验证 music-monitor 的全链路。

它按上游真实的页面结构返回 HTML（歌曲行 / 歌单卡片 / 搜索源复选框）以及 JSON 接口，
所以能真正测到 parser.py、engine.py、pipeline.py 的行为，而不需要联网和开 Docker。

    uvicorn stub_engine:app --port 18085
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse

app = FastAPI()

# 假的「引擎下载目录」——对应 monitor 只读挂载的 /downloads（见 smoke_test 里的 DOWNLOADS_MOUNT）
STUB_DOWNLOADS = Path(os.getenv("DOWNLOADS_MOUNT") or "/tmp/stub-downloads")

# 这些 id 当作「引擎库里已经有了」：下载时回 skipped + 空 path，但文件其实在盘上
ALREADY_ON_DISK: set[str] = set()


def _touch_download(file_name: str) -> None:
    """模拟引擎把文件落盘，这样 monitor 的 _file_exists 才走得到真实分支。"""
    try:
        STUB_DOWNLOADS.mkdir(parents=True, exist_ok=True)
        (STUB_DOWNLOADS / file_name).write_bytes(b"stub")
    except OSError:
        pass

# 每个 (source, id) 对应一个"歌单"，用于 /playlist
PLAYLISTS: dict[str, list[dict[str, Any]]] = {
    "netease:19723756": [
        {"id": "s1", "source": "netease", "name": "晴天", "artist": "周杰伦", "album": "叶惠美", "duration": 269},
        {"id": "s2", "source": "netease", "name": "夜曲", "artist": "周杰伦", "album": "十一月的萧邦", "duration": 227},
        {"id": "s3", "source": "netease", "name": "稻香", "artist": "周杰伦", "album": "魔杰座", "duration": 223},
    ],
    "netease:3778678": [
        {"id": "s4", "source": "netease", "name": "起风了", "artist": "买辣椒也用券", "album": "起风了", "duration": 325},
    ],
    # 专门用来验证「引擎库里已有这首歌」的路径：下载时回 skipped + 空 path
    "netease:already_have": [
        {"id": "s9", "source": "netease", "name": "富士山下", "artist": "陈奕迅", "album": "What's Going On…?", "duration": 258},
    ],
}

# 每首歌的 inspect 结果：故意让 s2 只有 128kbps，用来验证"跨平台补源"
INSPECT: dict[str, dict[str, Any]] = {
    "netease:s1": {"valid": True, "url": "https://cdn.example.com/s1.flac", "size": "32.1 MB", "bitrate": "985 kbps"},
    "netease:s2": {"valid": True, "url": "https://cdn.example.com/s2.mp3", "size": "3.4 MB", "bitrate": "128 kbps"},
    "netease:s3": {"valid": True, "url": "https://cdn.example.com/s3.flac", "size": "28.0 MB", "bitrate": "921 kbps"},
    "netease:s4": {"valid": False, "url": "", "size": "", "bitrate": "-"},
    "netease:s5": {"valid": True, "url": "https://cdn.example.com/s5.flac", "size": "26.4 MB", "bitrate": "905 kbps"},
    "netease:s9": {"valid": True, "url": "https://cdn.example.com/s9.flac", "size": "51.1 MB", "bitrate": "1573 kbps"},
    # 换源后拿到的 QQ 版本
    "qq:q2": {"valid": True, "url": "https://cdn.example.com/q2.flac", "size": "30.2 MB", "bitrate": "902 kbps"},
}

SEARCH_INDEX: dict[str, list[dict[str, Any]]] = {
    "夜曲": [
        {"id": "q2", "source": "qq", "name": "夜曲", "artist": "周杰伦", "album": "十一月的萧邦", "duration": 228},
    ],
    # 歌手关注：按歌手名搜索。故意混入一首翻唱，用来验证按歌手名过滤的逻辑。
    "周杰伦": [
        {"id": "s1", "source": "netease", "name": "晴天", "artist": "周杰伦", "album": "叶惠美", "duration": 269},
        {"id": "s2", "source": "netease", "name": "夜曲", "artist": "周杰伦", "album": "十一月的萧邦", "duration": 227},
        {"id": "s3", "source": "netease", "name": "稻香", "artist": "周杰伦", "album": "魔杰座", "duration": 223},
        {"id": "s5", "source": "netease", "name": "晴天（翻唱）", "artist": "某翻唱歌手", "album": "翻唱合辑", "duration": 250},
    ],
}

DOWNLOADS: list[dict[str, Any]] = []

SOURCES = [
    ("netease", "netease", "网易云音乐"),
    ("qq", "qq", "QQ 音乐"),
    ("kugou", "kugou", "酷狗音乐"),
]


def _song_html(song: dict[str, Any], extra: dict[str, str] | None = None) -> str:
    extra_attr = json.dumps(extra or {}, ensure_ascii=False)
    return (
        f'''<li class="song-card" data-id="{song['id']}" data-source="{song['source']}" data-album-id="" '''
        f'''data-album="{song.get('album', '')}" data-duration="{song.get('duration', 0)}" '''
        f'''data-name="{song['name']}" data-artist="{song.get('artist', '')}" data-cover="cover.jpg" '''
        f"""data-extra='{extra_attr}'></li>"""
    )


def _playlist_html(pl: dict[str, Any]) -> str:
    return f'''<div class="playlist-card" onclick="navigateTo('{pl['detail_url']}')">
      <img src="{pl.get('cover', 'c.jpg')}">
      <div class="playlist-title">{pl['name']}</div>
      <div class="playlist-author"><i></i> {pl.get('creator', '')}</div>
      <div class="playlist-count">共 {pl.get('track_count', 0)} 首</div>
      <button data-name="{pl['name']}" data-source="{pl['source']}" data-external-id="{pl['id']}"
              data-link="{pl.get('link', '')}" data-content-type="playlist">导入本地</button>
    </div>'''


@app.get("/music/healthz")
async def healthz():
    return {"app": "go-music-dl", "status": "ok"}


@app.get("/music/settings")
async def settings():
    return {
        "embedDownload": False,
        "downloadToLocal": True,
        "downloadDir": "data/downloads",
        "downloadFilenameTemplate": "{name} - {artist}",
        "webPageSize": 50,
        "downloadConcurrency": 3,
    }


@app.get("/music/", response_class=PlainTextResponse)
@app.get("/music", response_class=PlainTextResponse)
async def index():
    boxes = "".join(
        f'''<label><input type="checkbox" name="sources" value="{key}" class="source-checkbox"
            data-playlist-supported="true" data-album-supported="true" data-user-playlist-supported="{'true' if key in ('netease', 'qq') else 'false'}">
            <div class="source-card"><div class="source-name">{key}</div><div class="source-desc">{label}</div></div></label>'''
        for key, _pkg, label in SOURCES
    )
    return f'<div class="source-grid">{boxes}</div>'


@app.get("/music/search", response_class=PlainTextResponse)
async def search(q: str = "", type: str = "song", sources: list[str] | None = None, exact_artist: str = ""):
    if q.startswith("http"):
        # 链接解析：返回一张歌单卡片
        return _playlist_html({"name": "来自链接的歌单", "source": "netease", "id": "19723756",
                               "track_count": 3, "detail_url": "/music/playlist?id=19723756&source=netease"})
    if type == "playlist":
        return _playlist_html({"name": "热歌榜", "source": "netease", "id": "3778678", "track_count": 1,
                               "detail_url": "/music/playlist?id=3778678&source=netease"})
    songs = SEARCH_INDEX.get(q, [])
    if exact_artist:
        # 上游 exact_artist 的行为：按歌手名严格匹配。这里故意**只做前缀包含**，
        # 让那首翻唱仍然漏出来，好让 pipeline 的本地区歌手名过滤有用武之地。
        songs = [s for s in songs if exact_artist in (s.get("artist") or "") or (s.get("artist") or "") in exact_artist]
    return "".join(_song_html(s) for s in songs)


@app.get("/music/playlist", response_class=PlainTextResponse)
async def playlist(id: str = "", source: str = ""):
    songs = PLAYLISTS.get(f"{source}:{id}", [])
    return "".join(_song_html(s) for s in songs)


@app.get("/music/user_playlists", response_class=PlainTextResponse)
async def user_playlists(sources: list[str] | None = None):
    return "".join(_playlist_html(pl) for pl in [
        {"name": "我喜欢的音乐", "source": "netease", "id": "fav1", "track_count": 2, "creator": "我",
         "detail_url": "/music/playlist?id=fav1&source=netease"},
        {"name": "通勤歌单", "source": "qq", "id": "pl9", "track_count": 1, "creator": "我",
         "detail_url": "/music/playlist?id=pl9&source=qq"},
    ])


@app.get("/music/inspect")
async def inspect(id: str = "", source: str = "", duration: int = 0):
    return INSPECT.get(f"{source}:{id}", {"valid": False})


@app.get("/music/switch_source")
async def switch_source(name: str = "", artist: str = "", current: str = ""):
    if name == "夜曲":
        return {"id": "q2", "name": "夜曲", "artist": "周杰伦", "album": "十一月的萧邦", "duration": 228,
                "source": "qq", "cover": "", "extra": {}, "score": 0.98, "link": ""}
    return JSONResponse({"error": "no match"}, status_code=404)


@app.post("/music/api/downloads/precheck")
async def precheck(request: Request):
    body = await request.json()
    songs = body.get("songs") or []
    return {"total": len(songs), "skipped": 0}


@app.post("/music/download")
async def download(request: Request):
    params = dict(request.query_params)
    if params.get("save_local") != "1" or request.headers.get("x-requested-with") != "XMLHttpRequest":
        return JSONResponse({"error": "save_local requires POST + XHR"}, status_code=403)
    DOWNLOADS.append(params)
    name = params.get("name", "Unknown")
    artist = params.get("artist", "Unknown")
    ext = "mp3" if params.get("source") == "x" else "flac"
    file_name = f"{name} - {artist}.{ext}"

    # 真实上游「库里已经有这首歌」时的行为：status=ok + skipped=true + **path 是空字符串**，
    # 只剩一个没有扩展名的 filename。monitor 必须能靠它把真实文件找回来，
    # 否则下一轮会判定「文件不存在」而重复推送（线上就是这么堆出 266 条 skipped 的）。
    if params.get("id") in ALREADY_ON_DISK:
        _touch_download(file_name)
        return {"status": "ok", "saved": True, "skipped": True, "path": "",
                "filename": f"{name} - {artist}"}

    _touch_download(file_name)
    return {
        "status": "ok",
        "saved": True,
        "path": f"data/downloads/{file_name}",
        "filename": file_name,
        "skipped": False,
    }


# --------------------------------------------------------------------------- 登录
#
# 复刻真实上游的两种结果（这是本项目踩过的坑，务必保持与线上一致）：
#   成功 → 302 + Set-Cookie: music_dl_session=...
#   失败 → 200 + 登录页 HTML，**不带** Set-Cookie
ENGINE_USER = "admin"
ENGINE_PASSWORD = "correct-horse"
SESSIONS: set[str] = set()


@app.post("/music/login")
async def login(request: Request):
    # 手动解析表单，不走 request.form() —— 那个需要额外装 python-multipart，
    # 测试环境不该为一个假服务引入依赖。
    raw = (await request.body()).decode("utf-8", "replace")
    form = {k: v[0] for k, v in parse_qs(raw).items()}
    if form.get("username") == ENGINE_USER and form.get("password") == ENGINE_PASSWORD:
        token = "stub-session-token"
        SESSIONS.add(token)
        return RedirectResponse("/music", status_code=302,
                                headers={"Set-Cookie": f"music_dl_session={token}; Path=/music; HttpOnly"})
    return HTMLResponse("<!DOCTYPE html><html><head><title>登录 music-dl</title></head>"
                        "<body>账号或密码错误</body></html>", status_code=200)


@app.get("/music/cookies")
async def cookies(request: Request):
    if request.cookies.get("music_dl_session") not in SESSIONS:
        return JSONResponse({"error": "请先登录"}, status_code=401)
    return {"netease": "MUSIC_U=stub", "qq": "uin=stub"}
