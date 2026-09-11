"""解析 go-music-dl Web 页面。

上游项目的搜索 / 歌单 / 收藏夹接口返回的是服务端渲染的 HTML（不是 JSON），
但页面里的歌曲行、歌单卡片刻意带了稳定的 data-* 属性，供它自己的前端 JS 做批量操作，
所以这里是按「结构化属性」解析而不是按样式/文案解析，稳定性足够。

如果上游某次改版导致解析为空，只需改本文件，其它逻辑不受影响：
  歌曲：li.song-card[data-id][data-source]
  歌单：div.playlist-card  （内部按钮 [data-external-id][data-source]，否则回退解析 onclick 里的 navigateTo 链接）
"""
from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

_NAV_RE = re.compile(r"navigateTo\('([^']+)'\)")
_COUNT_RE = re.compile(r"(\d+)")


def _json_or_empty(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


def _int(raw: Any) -> int:
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return 0


def parse_songs(html: str) -> list[dict[str, Any]]:
    """从页面中提取歌曲列表。"""
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    songs: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for li in soup.select("li.song-card"):
        song_id = (li.get("data-id") or "").strip()
        source = (li.get("data-source") or "").strip()
        if not song_id or not source:
            continue
        key = (source, song_id)
        if key in seen:
            continue
        seen.add(key)

        extra = _json_or_empty(li.get("data-extra"))
        songs.append(
            {
                "id": song_id,
                "source": source,
                "name": (li.get("data-name") or "").strip(),
                "artist": (li.get("data-artist") or "").strip(),
                "album": (li.get("data-album") or "").strip(),
                "album_id": (li.get("data-album-id") or "").strip(),
                "duration": _int(li.get("data-duration")),
                "cover": (li.get("data-cover") or "").strip(),
                # 上游在 extra 里带了 size / bitrate 等线索，后面择优音质时会先用它做粗筛
                "extra": extra,
                "link": "",
            }
        )
    return songs


def parse_playlists(html: str) -> list[dict[str, Any]]:
    """从页面中提取歌单 / 专辑卡片。"""
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    playlists: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for card in soup.select("div.playlist-card"):
        playlist_id = source = link = ""
        btn = card.select_one("[data-external-id][data-source]")
        if btn is not None:
            playlist_id = (btn.get("data-external-id") or "").strip()
            source = (btn.get("data-source") or "").strip()
            link = (btn.get("data-link") or "").strip()

        if not playlist_id:
            # 回退：从 onclick="navigateTo('/music/playlist?id=..&source=..')" 里取
            match = _NAV_RE.search(card.get("onclick") or "")
            if match:
                parsed = urlparse(match.group(1))
                qs = parse_qs(parsed.query)
                playlist_id = (qs.get("id") or [""])[0].strip()
                source = (qs.get("source") or [""])[0].strip()

        if not playlist_id or not source:
            continue
        key = (source, playlist_id)
        if key in seen:
            continue
        seen.add(key)

        title_el = card.select_one(".playlist-title")
        cover_el = card.select_one("img")
        author_el = card.select_one(".playlist-author")
        count_el = card.select_one(".playlist-count")

        track_count = 0
        if count_el is not None:
            m = _COUNT_RE.search(count_el.get_text(" ", strip=True))
            if m:
                track_count = _int(m.group(1))

        name = ""
        if title_el is not None:
            # 标题里可能塞了「打开原始页面」的图标，去掉尾部无意义字符
            name = title_el.get_text(" ", strip=True).strip()

        creator = ""
        if author_el is not None:
            creator = author_el.get_text(" ", strip=True).lstrip("　 ")

        playlists.append(
            {
                "id": playlist_id,
                "source": source,
                "name": name,
                "creator": creator,
                "track_count": track_count,
                "cover": (cover_el.get("src") or "").strip() if cover_el is not None else "",
                "link": link,
            }
        )
    return playlists


def parse_sources(html: str) -> list[dict[str, Any]]:
    """从引擎首页的「搜索源设置」里读出它实际支持的平台清单与能力标记。

    这样平台列表永远与引擎版本一致，不依赖本项目的硬编码。
    """
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    out: list[dict[str, Any]] = []
    for box in soup.select("input.source-checkbox, input[name=sources]"):
        value = (box.get("value") or "").strip()
        if not value:
            continue
        label = box.find_parent("label")
        name_el = label.select_one(".source-name") if label is not None else None
        desc_el = label.select_one(".source-desc") if label is not None else None
        # source-name 是平台 key（netease），source-desc 才是中文名（网易云音乐）
        key_text = name_el.get_text(strip=True) if name_el is not None else value
        desc_text = desc_el.get_text(strip=True) if desc_el is not None else ""
        out.append(
            {
                "key": key_text or value,
                "label": desc_text or key_text or value,
                "desc": desc_text,
                "playlist": box.get("data-playlist-supported") == "true",
                "album": box.get("data-album-supported") == "true",
                "user_playlist": box.get("data-user-playlist-supported") == "true",
            }
        )
    # 去重保序
    seen: set[str] = set()
    uniq: list[dict[str, Any]] = []
    for item in out:
        if item["key"] in seen:
            continue
        seen.add(item["key"])
        uniq.append(item)
    return uniq


def parse_settings_json(payload: Any) -> dict[str, Any]:
    """/music/settings 返回的就是 JSON，这里只做健壮性包装。"""
    return payload if isinstance(payload, dict) else {}
