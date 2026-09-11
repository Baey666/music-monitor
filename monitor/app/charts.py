"""各平台热门榜单注册表。

现实情况（重要）：
  * 上游 go-music-dl / music-lib **没有**「获取平台排行榜」的接口，只有「按歌单 ID / 歌单链接取曲目」。
  * 好消息是：很多平台的榜单本身就是一张官方歌单（网易云最典型），所以可以直接当歌单取。
  * 坏消息是：榜单 ID / 榜单页 URL 会随平台改版而失效。

因此这里采用「内置清单 + 可校验 + 可自定义」的设计：
  * `verified=True`   —— 直接当歌单 ID 解析，成功率最高（网易云四大榜）；
  * `verified=False`  —— 用榜单页链接交给引擎做链接解析，可能失效，界面上提供「校验」按钮自检；
  * 用户也可以自己在界面上新增任意榜单（歌单链接 / 歌单 ID），不受本清单限制。

任何条目失效都不会影响其它功能，修一条即可。
"""
from __future__ import annotations

import logging
from typing import Any

from .engine import Engine

log = logging.getLogger("monitor.charts")


def _chart(key: str, name: str, *, id: str = "", link: str = "", verified: bool = False, note: str = "") -> dict[str, Any]:
    return {"key": key, "name": name, "id": id, "link": link, "verified": verified, "note": note}


CHART_GROUPS: list[dict[str, Any]] = [
    {
        "platform": "netease",
        "label": "网易云音乐",
        "region": "国内",
        "charts": [
            _chart("netease_soaring", "飙升榜", id="19723756", verified=True),
            _chart("netease_new", "新歌榜", id="3779629", verified=True),
            _chart("netease_hot", "热歌榜", id="3778678", verified=True),
            _chart("netease_origin", "原创榜", id="2884035", verified=True),
            _chart("netease_west", "欧美热歌榜", id="2809513713", note="ID 可能变动，请先「校验」"),
            _chart("netease_kr", "韩语榜", id="745956260", note="ID 可能变动，请先「校验」"),
            _chart("netease_jp", "日语榜", id="5059642708", note="ID 可能变动，请先「校验」"),
        ],
    },
    {
        "platform": "qq",
        "label": "QQ 音乐",
        "region": "国内",
        "charts": [
            _chart("qq_hot", "热歌榜", link="https://y.qq.com/n/ryqq/toplist/26", note="依赖引擎的链接解析"),
            _chart("qq_new", "新歌榜", link="https://y.qq.com/n/ryqq/toplist/27", note="依赖引擎的链接解析"),
            _chart("qq_soaring", "飙升榜", link="https://y.qq.com/n/ryqq/toplist/62", note="依赖引擎的链接解析"),
            _chart("qq_index", "流行指数榜", link="https://y.qq.com/n/ryqq/toplist/4", note="依赖引擎的链接解析"),
            _chart("qq_mainland", "内地榜", link="https://y.qq.com/n/ryqq/toplist/5", note="依赖引擎的链接解析"),
            _chart("qq_hktw", "港台榜", link="https://y.qq.com/n/ryqq/toplist/6", note="依赖引擎的链接解析"),
            _chart("qq_west", "欧美榜", link="https://y.qq.com/n/ryqq/toplist/3", note="依赖引擎的链接解析"),
            _chart("qq_jp", "日本榜", link="https://y.qq.com/n/ryqq/toplist/17", note="依赖引擎的链接解析"),
            _chart("qq_kr", "韩国榜", link="https://y.qq.com/n/ryqq/toplist/16", note="依赖引擎的链接解析"),
        ],
    },
    {
        "platform": "kugou",
        "label": "酷狗音乐",
        "region": "国内",
        "charts": [
            _chart("kugou_top500", "酷狗 TOP500", link="https://www.kugou.com/yy/rank/home/1-8888.html", note="依赖引擎的链接解析"),
            _chart("kugou_soaring", "飙升榜", link="https://www.kugou.com/yy/rank/home/1-6666.html", note="依赖引擎的链接解析"),
            _chart("kugou_new", "新歌榜", link="https://www.kugou.com/yy/rank/home/1-6727.html", note="依赖引擎的链接解析"),
        ],
    },
    {
        "platform": "kuwo",
        "label": "酷我音乐",
        "region": "国内",
        "charts": [
            _chart("kuwo_hot", "酷我热歌榜", link="https://www.kuwo.cn/rankList", note="榜单页需手动确认可用性"),
        ],
    },
    {
        "platform": "apple",
        "label": "Apple Music",
        "region": "国外",
        "charts": [
            _chart(
                "apple_top100_global",
                "Top 100: Global",
                link="https://music.apple.com/us/playlist/top-100-global/pl.d25f5d1181894928af76c85c967f8f31",
                note="引擎对 Apple 只提供试听片段(preview)下载，完整音频需另行解密",
            ),
            _chart(
                "apple_top100_cn",
                "Top 100: 中国大陆",
                link="https://music.apple.com/cn/playlist/top-100-%E4%B8%AD%E5%9B%BD%E5%A4%A7%E9%99%86/pl.7a1d6d0a4bd83d0b0c5b1b0ea0e5e0f1",
                note="链接可能失效，建议自行在 Apple Music 复制榜单链接后新增",
            ),
        ],
    },
    {
        "platform": "joox",
        "label": "JOOX",
        "region": "国外",
        "charts": [
            _chart("joox_topchart", "JOOX 排行榜", link="https://www.joox.com/hk/topchart", note="依赖引擎的链接解析"),
        ],
    },
]


def chart_index() -> dict[str, dict[str, Any]]:
    """把清单摊平成 key -> chart（附带 platform/label）。"""
    out: dict[str, dict[str, Any]] = {}
    for group in CHART_GROUPS:
        for c in group["charts"]:
            out[c["key"]] = {**c, "platform": group["platform"], "platform_label": group["label"], "region": group["region"]}
    return out


async def resolve_chart(engine: Engine, entry: dict[str, Any]) -> dict[str, Any]:
    """解析一个榜单条目 → 曲目列表。

    entry 支持三种形态：
      {"platform": "netease", "id": "19723756"}
      {"platform": "qq", "link": "https://y.qq.com/n/ryqq/toplist/26"}
      {"link": "https://music.163.com/#/playlist?id=123"}   （平台由引擎自动识别）
    """
    platform = (entry.get("platform") or "").strip()
    chart_id = (entry.get("id") or "").strip()
    link = (entry.get("link") or "").strip()

    errors: list[str] = []

    # 1) 有 ID：直接当歌单取
    if chart_id and platform:
        try:
            songs = await engine.playlist_songs(chart_id, platform)
            if songs:
                return {"ok": True, "songs": songs, "resolved": {"id": chart_id, "source": platform}, "message": ""}
            errors.append("按 ID 取曲目为空")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"按 ID 解析失败: {exc}")

    # 2) 有链接：交给引擎做链接识别 + 歌单解析
    if link:
        try:
            playlists = await engine.search_playlists(link, [platform] if platform else None)
            for pl in playlists:
                songs = await engine.playlist_songs(pl["id"], pl["source"], link=pl.get("link") or "")
                if songs:
                    return {
                        "ok": True,
                        "songs": songs,
                        "resolved": {"id": pl["id"], "source": pl["source"], "name": pl.get("name", "")},
                        "message": "",
                    }
            errors.append("按链接未解析出曲目")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"按链接解析失败: {exc}")

    return {"ok": False, "songs": [], "resolved": None, "message": "；".join(errors) or "未提供 ID 或链接"}
