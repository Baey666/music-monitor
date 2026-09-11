"""音质策略。

重要背景：go-music-dl 的下载接口**没有** quality 参数，实际音质由「平台 Cookie 的会员等级」决定。
所以本服务做的是**择优 + 校验**，而不是「指定参数」：

  1. 拿榜单/歌单里的原始曲目（引擎给的 source + id）；
  2. 调 /inspect 探测它真实的码率与体积；
  3. 达不到目标音质时，依次尝试：
       a. 该曲目在其它平台的版本（/switch_source，引擎自己做相似度+时长+可播放校验）；
       b. 用歌名+歌手在允许的平台里搜索同名曲目；
  4. 在候选里挑「满足目标音质且分数最高」的那个；全都达不到时按 fallback 策略处理。

因此真正想要 FLAC 无损，必须先在 go-music-dl 里为对应平台配置会员 Cookie。
本服务会在界面上明确提示这一点，不会假装能绕过。
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

LOSSLESS_EXTS = {"flac", "ape", "wav", "aiff", "aif", "alac", "dsf", "dff"}

QUALITY_LEVELS: dict[str, dict[str, Any]] = {
    "standard": {"label": "标准 (≈128 kbps)", "min_kbps": 96, "lossless": False, "rank": 1},
    "high": {"label": "较高 (≈320 kbps)", "min_kbps": 256, "lossless": False, "rank": 2},
    "lossless": {"label": "无损 (FLAC)", "min_kbps": 700, "lossless": True, "rank": 3},
    "hires": {"label": "Hi-Res (≥24bit/96kHz)", "min_kbps": 1400, "lossless": True, "rank": 4},
}

QUALITY_ORDER = ["standard", "high", "lossless", "hires"]

# 优先在支持无损/高码率的平台取源（仅在需要跨平台补源时作为排序偏好）
SOURCE_PRIORITY = ["netease", "qq", "kugou", "bilibili", "kuwo", "migu", "soda", "qianqian", "joox", "apple", "jamendo", "fivesing"]

_BITRATE_RE = re.compile(r"(\d+)\s*kbps", re.I)


def normalize_quality(name: str | None) -> str:
    name = (name or "").strip().lower()
    return name if name in QUALITY_LEVELS else "lossless"


def quality_label(name: str) -> str:
    return QUALITY_LEVELS[normalize_quality(name)]["label"]


def parse_kbps(bitrate: str | None) -> int | None:
    """把 '320 kbps' 解析为 320；'-' 或无法解析时返回 None。"""
    if not bitrate:
        return None
    m = _BITRATE_RE.search(str(bitrate))
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def infer_ext(url: str | None) -> str:
    """从下载直链里推断文件扩展名（引擎不返回 ext，只能看 URL）。"""
    if not url:
        return ""
    path = urlparse(str(url)).path
    if "." not in path:
        return ""
    ext = path.rsplit(".", 1)[-1].lower()
    return ext if len(ext) <= 5 and ext.isalnum() else ""


def source_rank(source: str) -> int:
    try:
        return SOURCE_PRIORITY.index(source)
    except ValueError:
        return len(SOURCE_PRIORITY)


def evaluate(target_quality: str, info: dict[str, Any]) -> dict[str, Any]:
    """把 /inspect 的结果换算成可比较的评估结果。"""
    level = QUALITY_LEVELS[normalize_quality(target_quality)]
    valid = bool(info.get("valid"))
    kbps = parse_kbps(info.get("bitrate"))
    ext = infer_ext(info.get("url"))
    lossless = ext in LOSSLESS_EXTS or (kbps is not None and kbps >= 700)

    satisfies = False
    if valid:
        if level["lossless"]:
            satisfies = lossless and kbps is not None and kbps >= level["min_kbps"]
        else:
            satisfies = kbps is not None and kbps >= level["min_kbps"]

    return {
        "valid": valid,
        "bitrate": info.get("bitrate") or "-",
        "kbps": kbps,
        "size": info.get("size") or "",
        "ext": ext,
        "lossless": lossless,
        "satisfies": satisfies,
    }


def actual_quality_desc(ev: dict[str, Any]) -> str:
    """给出一句人话描述实际拿到的音质，用于界面和日志。"""
    if not ev.get("valid"):
        return "不可用"
    ext = (ev.get("ext") or "").upper()
    kbps = ev.get("kbps")
    if ev.get("lossless"):
        return f"无损 {ext or 'FLAC'} {kbps or ''}kbps".strip()
    if kbps:
        return f"{ext or 'MP3'} {kbps}kbps"
    return ext or "未知"


def sort_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """候选排序：先按是否来自原平台，再按平台优先级（越靠前越可能拿到无损）。"""

    def key(c: dict[str, Any]) -> tuple[int, int, float]:
        return (0 if c.get("is_primary") else 1, source_rank(c.get("source", "")), -float(c.get("similarity") or 0.0))

    return sorted(candidates, key=key)
