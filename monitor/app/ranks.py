"""平台排行榜解析。

为什么需要这个模块：
  上游 go-music-dl 只有「歌单」能力（ID / 歌单链接），**没有榜单接口**；
  而榜单页也不是歌单页，所以把榜单页链接丢给上游必然解析不出曲目
  —— 这就是此前 QQ / 酷狗 / 酷我 榜单「按链接未解析出曲目」的根因。
  榜单属于「上游没有的发现能力」，因此由本服务自己取。

这里直接调各平台自己的公开 JSON 榜单接口（都不需要签名），并把字段映射成与
`parser.parse_songs` **完全一致**的 song dict，下游流程无需任何改动。

⚠️ ID 命名空间必须与上游 music-lib 对齐，否则引擎下载会失败。已逐个核对上游源码：
    qq    ID = songmid          extra{songmid, song_id}
    kugou ID = 32 位 FileHash   extra{hash, file_hash, sq_hash, hq_hash, album_id, audio_id, ...}
    kuwo  ID = 纯数字 rid       extra{rid}          （上游会去掉 MUSIC_ 前缀）

榜单 ID / 接口会随平台改版失效，界面上提供「校验」按钮可随时自测。
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Awaitable

import httpx

from .config import settings

log = logging.getLogger("monitor.ranks")


def _int(value: Any) -> int:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return 0


def _join(names: list[Any], sep: str = "、") -> str:
    out: list[str] = []
    for name in names:
        text = str(name or "").strip()
        if text and text not in out:
            out.append(text)
    return sep.join(out)


def _song(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "", "source": "", "name": "", "artist": "", "album": "", "album_id": "",
        "duration": 0, "cover": "", "extra": {}, "link": "",
    }
    base.update(kw)
    return base


async def _get_json(url: str, referer: str = "") -> Any:
    headers = {
        "User-Agent": settings.user_agent,
        "Accept": "application/json, text/plain, */*",
    }
    if referer:
        headers["Referer"] = referer
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(settings.http_timeout, connect=10.0),
        follow_redirects=True,
        headers=headers,
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.json()


# --------------------------------------------------------------------------- QQ
async def _qq(rank: str, limit: int) -> dict[str, Any]:
    url = (
        "https://c.y.qq.com/v8/fcg-bin/fcg_v8_toplist_cp.fcg"
        f"?topid={rank}&song_begin=0&song_num={limit}&format=json&platform=yqq&needNewCode=1&tpl=3"
    )
    data = await _get_json(url, referer=f"https://y.qq.com/n/ryqq/toplist/{rank}")
    songs: list[dict[str, Any]] = []
    for row in data.get("songlist") or []:
        d = row.get("data") or {}
        mid = str(d.get("songmid") or "").strip()
        if not mid:
            continue
        album_mid = str(d.get("albummid") or "").strip()
        songs.append(_song(
            id=mid,
            source="qq",
            name=str(d.get("songname") or "").strip(),
            artist=_join([s.get("name") for s in (d.get("singer") or [])]),
            album=str(d.get("albumname") or "").strip(),
            album_id=album_mid,
            duration=_int(d.get("interval")),
            cover=(f"https://y.gtimg.cn/music/photo_new/T002R300x300M000{album_mid}.jpg" if album_mid else ""),
            extra={"songmid": mid, "song_id": str(d.get("songid") or "")},
            link=f"https://y.qq.com/n/ryqq/songDetail/{mid}",
        ))
    name = str(((data.get("topinfo") or {}).get("ListName")) or "").strip()
    return {"name": name, "songs": songs}


# ------------------------------------------------------------------------- 酷狗
async def _kugou(rank: str, limit: int) -> dict[str, Any]:
    url = (
        "http://mobilecdnbj.kugou.com/api/v3/rank/song"
        f"?version=9108&rankid={rank}&page=1&pagesize={limit}&ranktype=2"
    )
    data = await _get_json(url, referer=f"https://www.kugou.com/yy/rank/home/1-{rank}.html")
    songs: list[dict[str, Any]] = []
    for item in (data.get("data") or {}).get("info") or []:
        h = str(item.get("hash") or "").strip()
        if not h:
            continue
        album_id = str(item.get("album_id") or "").strip()
        trans = item.get("trans_param") or {}
        songs.append(_song(
            id=h,
            source="kugou",
            name=str(item.get("songname") or "").strip(),
            artist=_join([a.get("author_name") for a in (item.get("authors") or [])]),
            album=str(item.get("remark") or "").strip(),
            album_id=album_id,
            duration=_int(item.get("duration")),
            cover=str(item.get("album_sizable_cover") or "").replace("{size}", "240"),
            extra={
                "hash": h,
                "file_hash": h,
                "sq_hash": str(item.get("sqhash") or ""),
                "hq_hash": str(item.get("320hash") or ""),
                "album_id": album_id,
                "audio_id": str(item.get("audio_id") or ""),
                "album_audio_id": str(item.get("album_audio_id") or ""),
                "ogg_320_hash": str(trans.get("ogg_320_hash") or ""),
                "ogg_128_hash": str(trans.get("ogg_128_hash") or ""),
                "privilege": str(item.get("privilege") or ""),
            },
            link=f"https://www.kugou.com/song/#hash={h}",
        ))
    return {"name": "", "songs": songs}


# ------------------------------------------------------------------------- 酷我
async def _kuwo(rank: str, limit: int) -> dict[str, Any]:
    url = (
        "http://kbangserver.kuwo.cn/ksong.s"
        f"?from=pc&fmt=json&type=bang&data=content&id={rank}&pn=0&rn={limit}"
    )
    data = await _get_json(url, referer="https://www.kuwo.cn/rankList")
    songs: list[dict[str, Any]] = []
    for item in data.get("musiclist") or []:
        rid = str(item.get("id") or "").strip()
        if not rid:
            continue
        songs.append(_song(
            id=rid,
            source="kuwo",
            name=str(item.get("name") or "").strip(),
            artist=str(item.get("artist") or "").strip(),
            album=str(item.get("album") or "").strip(),
            album_id=str(item.get("albumid") or "").strip(),
            # 注意：本接口的 `duration` 不是秒（是站内热度序号），时长要用 song_duration。
            duration=_int(item.get("song_duration")),
            cover="",
            extra={"rid": rid},
            link=f"http://www.kuwo.cn/play_detail/{rid}",
        ))
    return {"name": str(data.get("name") or "").strip(), "songs": songs}


FETCHERS: dict[str, Callable[[str, int], Awaitable[dict[str, Any]]]] = {
    "qq": _qq,
    "kugou": _kugou,
    "kuwo": _kuwo,
}


async def fetch_rank(platform: str, rank: str, limit: int = 100) -> dict[str, Any]:
    """取某平台某个榜单的曲目。

    返回 {"name": 榜单名（可能为空）, "songs": [song dict, ...]}。
    """
    fetcher = FETCHERS.get((platform or "").strip())
    if fetcher is None:
        raise ValueError(f"该平台暂不支持榜单解析：{platform}")
    if not str(rank or "").strip():
        raise ValueError("缺少榜单 ID")
    return await fetcher(str(rank).strip(), max(1, min(int(limit), 500)))
