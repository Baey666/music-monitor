"""各平台热门榜单注册表。

现实情况（重要）：
  * 上游 go-music-dl / music-lib **没有**「获取平台排行榜」的接口，只有「按歌单 ID / 歌单链接取曲目」。
  * 网易云的榜单本身就是官方歌单，所以直接当歌单取（`id`）。
  * 其它平台的榜单**不是**歌单页，上游的 ParsePlaylist 认不出来 —— 这也是此前
    「按链接未解析出曲目」的根因。这类榜单改用本服务自己的榜单接口取（`rank`，见 ranks.py）。

因此这里采用「内置清单 + 可校验 + 可自定义」的设计：
  * `id`    —— 直接当歌单 ID 解析（网易云四大榜）；
  * `rank`  —— 用平台自己的榜单接口解析（QQ / 酷狗 / 酷我，见 ranks.py）；
  * 用户也可以自己在界面上新增任意歌单链接 / 歌单 ID，不受本清单限制。

榜单 ID / 接口会随平台改版失效，界面上有「校验」按钮可自测。任何条目失效都不会影响其它功能。
"""
from __future__ import annotations

import logging
from typing import Any

from . import ranks
from .config import settings
from .engine import Engine

log = logging.getLogger("monitor.charts")


def _chart(
    key: str,
    name: str,
    *,
    id: str = "",
    link: str = "",
    rank: str = "",
    verified: bool = False,
    note: str = "",
) -> dict[str, Any]:
    return {"key": key, "name": name, "id": id, "link": link, "rank": rank, "verified": verified, "note": note}


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
            _chart("qq_hot", "热歌榜", rank="26", note="榜单 ID 随平台改版可能变动，建议先「校验」"),
            _chart("qq_new", "新歌榜", rank="27", note="榜单 ID 随平台改版可能变动，建议先「校验」"),
            _chart("qq_soaring", "飙升榜", rank="62", note="榜单 ID 随平台改版可能变动，建议先「校验」"),
            _chart("qq_index", "流行指数榜", rank="4", note="榜单 ID 随平台改版可能变动，建议先「校验」"),
            _chart("qq_mainland", "内地榜", rank="5", note="榜单 ID 随平台改版可能变动，建议先「校验」"),
            _chart("qq_west", "欧美榜", rank="3", note="榜单 ID 随平台改版可能变动，建议先「校验」"),
            _chart("qq_jp", "日本榜", rank="17", note="榜单 ID 随平台改版可能变动，建议先「校验」"),
            _chart("qq_kr", "韩国榜", rank="16", note="榜单 ID 随平台改版可能变动，建议先「校验」"),
            _chart("qq_hk", "香港地区榜", rank="59", note="榜单 ID 随平台改版可能变动，建议先「校验」"),
            _chart("qq_tw", "台湾地区榜", rank="61", note="榜单 ID 随平台改版可能变动，建议先「校验」"),
            _chart("qq_net", "网络歌曲榜", rank="28", note="榜单 ID 随平台改版可能变动，建议先「校验」"),
            _chart("qq_douyin", "抖音热歌榜", rank="60", note="榜单 ID 随平台改版可能变动，建议先「校验」"),
            _chart("qq_guofeng", "国风热歌榜", rank="65", note="榜单 ID 随平台改版可能变动，建议先「校验」"),
            _chart("qq_dance", "DJ 舞曲榜", rank="63", note="榜单 ID 随平台改版可能变动，建议先「校验」"),
        ],
    },
    {
        "platform": "kugou",
        "label": "酷狗音乐",
        "region": "国内",
        "charts": [
            _chart("kugou_top500", "TOP500", rank="8888", note="榜单 ID 随平台改版可能变动，建议先「校验」"),
            _chart("kugou_soaring", "飙升榜", rank="6666", note="榜单 ID 随平台改版可能变动，建议先「校验」"),
            _chart("kugou_new", "新歌榜", rank="74534", note="榜单 ID 随平台改版可能变动，建议先「校验」"),
            _chart("kugou_mainland", "内地榜", rank="31308", note="榜单 ID 随平台改版可能变动，建议先「校验」"),
            _chart("kugou_west", "欧美榜", rank="31310", note="榜单 ID 随平台改版可能变动，建议先「校验」"),
            _chart("kugou_hk", "香港地区榜", rank="31313", note="榜单 ID 随平台改版可能变动，建议先「校验」"),
            _chart("kugou_tw", "台湾地区榜", rank="54848", note="榜单 ID 随平台改版可能变动，建议先「校验」"),
            _chart("kugou_kr", "韩国榜", rank="31311", note="榜单 ID 随平台改版可能变动，建议先「校验」"),
            _chart("kugou_jp", "日本榜", rank="31312", note="榜单 ID 随平台改版可能变动，建议先「校验」"),
            _chart("kugou_cantonese", "粤语金曲榜", rank="33165", note="榜单 ID 随平台改版可能变动，建议先「校验」"),
            _chart("kugou_net", "网络热歌榜", rank="82831", note="榜单 ID 随平台改版可能变动，建议先「校验」"),
            _chart("kugou_vip", "会员热歌榜", rank="35811", note="榜单 ID 随平台改版可能变动，建议先「校验」"),
            _chart("kugou_dj", "DJ 热歌榜", rank="24971", note="榜单 ID 随平台改版可能变动，建议先「校验」"),
            _chart("kugou_folk", "民谣榜", rank="51341", note="榜单 ID 随平台改版可能变动，建议先「校验」"),
            _chart("kugou_electro", "电音榜", rank="33160", note="榜单 ID 随平台改版可能变动，建议先「校验」"),
            _chart("kugou_joox", "JOOX 香港热歌榜", rank="42807", note="酷狗收录的 JOOX 榜单"),
        ],
    },
    {
        "platform": "kuwo",
        "label": "酷我音乐",
        "region": "国内",
        "charts": [
            _chart("kuwo_hot", "热歌榜", rank="16", note="榜单 ID 随平台改版可能变动，建议先「校验」"),
            _chart("kuwo_soaring", "飙升榜", rank="93", note="榜单 ID 随平台改版可能变动，建议先「校验」"),
            _chart("kuwo_new", "新歌榜", rank="17", note="榜单 ID 随平台改版可能变动，建议先「校验」"),
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
      {"platform": "qq", "rank": "26"}                       榜单接口（ranks.py）
      {"platform": "netease", "id": "19723756"}              当歌单取
      {"link": "https://music.163.com/#/playlist?id=123"}    交给引擎做歌单链接解析
    """
    platform = (entry.get("platform") or "").strip()
    chart_id = (entry.get("id") or "").strip()
    link = (entry.get("link") or "").strip()
    rank = str(entry.get("rank") or "").strip()

    errors: list[str] = []

    # 1) 榜单接口：上游没有这个能力，由本服务自己取
    if rank and platform:
        try:
            data = await ranks.fetch_rank(platform, rank, limit=settings.max_songs_per_playlist)
            songs = data.get("songs") or []
            if songs:
                return {
                    "ok": True,
                    "songs": songs,
                    "resolved": {"rank": rank, "source": platform, "name": data.get("name", "")},
                    "message": "",
                }
            errors.append(f"榜单 {platform}:{rank} 返回 0 首曲目")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"榜单解析失败({platform}:{rank}): {exc}")

    # 2) 有 ID：直接当歌单取
    if chart_id and platform:
        try:
            songs = await engine.playlist_songs(chart_id, platform)
            if songs:
                return {"ok": True, "songs": songs, "resolved": {"id": chart_id, "source": platform}, "message": ""}
            errors.append("按歌单 ID 取曲目为空")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"按歌单 ID 解析失败: {exc}")

    # 3) 有链接：交给引擎做歌单链接识别 + 解析（注意：只支持歌单链接，不支持榜单页链接）
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
            errors.append("按歌单链接未解析出曲目（榜单页链接不是歌单页，请改用「歌单链接」或平台榜单）")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"按歌单链接解析失败: {exc}")

    return {"ok": False, "songs": [], "resolved": None, "message": "；".join(errors) or "未提供榜单 ID、歌单 ID 或歌单链接"}
