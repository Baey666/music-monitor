"""榜单解析自检：用真实抓取的平台榜单响应验证字段映射。

    cd music-monitor/monitor
    python -m tests.test_ranks

重点验证「ID 命名空间与上游 music-lib 一致」，因为这决定引擎能否下载：
    qq → songmid ; kugou → 32 位 hash ; kuwo → 纯数字 rid（无 MUSIC_ 前缀）
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from app import ranks  # noqa: E402

PASS: list[str] = []
FAIL: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASS.append(label)
        print(f"  [OK]   {label}")
    else:
        FAIL.append(f"{label} {detail}")
        print(f"  [FAIL] {label} {detail}")


# 以下三段是真实接口响应裁剪后的片段（保留字段结构）。
QQ_RESP = {
    "code": 0,
    "songlist": [
        {"data": {
            "albummid": "004VSvF52mQoQp", "albumname": "未完成", "interval": 320, "songid": 8136,
            "songmid": "001fsNdn1zuZnA", "songname": "我不难过",
            "singer": [{"id": 109, "mid": "001pWERg3vFgg8", "name": "孙燕姿"}],
            "sizeflac": 35801703, "size320": 12819004,
        }},
    ],
    "topinfo": {"ListName": "巅峰榜·热歌", "topID": "26"},
}

KUGOU_RESP = {
    "status": 1, "errcode": 0,
    "data": {"total": 500, "info": [
        {"songname": "甲乙丙丁 (你我怎么两清)", "remark": "甲乙丙丁", "hash": "213D580CA0BDCC28A5FDBA995FFDA106",
         "sqhash": "85479C21FADC65A7C495989D6FE9396D", "320hash": "B7AC734C6806EFF90C22C74F1AFFA156",
         "album_id": "197648995", "audio_id": 1106816298, "album_audio_id": 920474385,
         "duration": 210, "privilege": 10,
         "album_sizable_cover": "http://imge.kugou.com/stdmusic/{size}/20260630/x.jpg",
         "authors": [{"author_name": "李佳薇"}]},
    ]},
}

KUWO_RESP = {
    "name": "酷我飙升榜",
    "musiclist": [
        {"id": "526058813", "name": "琵琶曲", "artist": "郑浩&冰洁", "album": "琵琶曲",
         "albumid": "88500331", "duration": "12", "song_duration": "235"},
    ],
}

RESPONSES = {
    "c.y.qq.com": QQ_RESP,
    "mobilecdnbj.kugou.com": KUGOU_RESP,
    "kbangserver.kuwo.cn": KUWO_RESP,
}


async def fake_get_json(url: str, referer: str = ""):
    for host, payload in RESPONSES.items():
        if host in url:
            return payload
    raise AssertionError(f"测试未覆盖的地址: {url}")


async def main() -> int:
    ranks._get_json = fake_get_json  # type: ignore[assignment]

    print("— QQ 榜单 —")
    data = await ranks.fetch_rank("qq", "26", limit=100)
    songs = data["songs"]
    check("QQ 解析出曲目", len(songs) == 1, str(len(songs)))
    s = songs[0]
    check("QQ ID 用 songmid", s["id"] == "001fsNdn1zuZnA", s["id"])
    check("QQ extra 带 songmid/song_id", s["extra"].get("songmid") == s["id"] and s["extra"].get("song_id") == "8136")
    check("QQ 歌手/时长/封面", s["artist"] == "孙燕姿" and s["duration"] == 320 and "004VSvF52mQoQp" in s["cover"])
    check("QQ 榜单名回传", data["name"] == "巅峰榜·热歌", data["name"])

    print("— 酷狗榜单 —")
    data = await ranks.fetch_rank("kugou", "8888", limit=100)
    s = data["songs"][0]
    check("酷狗 ID 用 32 位 hash", s["id"] == "213D580CA0BDCC28A5FDBA995FFDA106" and len(s["id"]) == 32, s["id"])
    check("酷狗 extra 含各音质 hash", s["extra"]["sq_hash"] == "85479C21FADC65A7C495989D6FE9396D" and s["extra"]["hq_hash"])
    check("酷狗 extra 含专辑与 audio_id", s["extra"]["album_id"] == "197648995" and s["extra"]["audio_id"] == "1106816298")
    check("酷狗封面 size 占位已替换", "{size}" not in s["cover"] and "240" in s["cover"], s["cover"])
    check("酷狗 专辑字段取 remark", s["album"] == "甲乙丙丁", s["album"])

    print("— 酷我榜单 —")
    data = await ranks.fetch_rank("kuwo", "93", limit=100)
    s = data["songs"][0]
    check("酷我 ID 为纯数字 rid", s["id"] == "526058813", s["id"])
    check("酷我 extra 带 rid", s["extra"]["rid"] == s["id"])
    check("酷我时长取 song_duration 而非 duration", s["duration"] == 235, str(s["duration"]))
    check("酷我榜单名回传", data["name"] == "酷我飙升榜", data["name"])

    print("— 边界 —")
    try:
        await ranks.fetch_rank("apple", "1")
        check("未支持平台应报错", False)
    except ValueError as exc:
        check("未支持平台应报错", "暂不支持" in str(exc), str(exc))
    try:
        await ranks.fetch_rank("qq", "")
        check("缺榜单 ID 应报错", False)
    except ValueError as exc:
        check("缺榜单 ID 应报错", "缺少榜单 ID" in str(exc), str(exc))

    # 注册表里的榜单条目必须能被 fetch_rank 认领（平台与 rank 都对得上）。
    from app import charts

    bad = [c["key"] for c in charts.chart_index().values()
           if c.get("rank") and c["platform"] not in ranks.FETCHERS]
    check("注册表中榜单条目平台均可解析", not bad, str(bad))

    print("\n" + "=" * 62)
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    for item in FAIL:
        print("  × " + item)
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
