"""go-music-dl 引擎客户端。

设计原则：本项目**不重新实现**搜索/解析/下载，全部委托给上游引擎，
这样音质（取决于引擎里配置的平台 Cookie/会员）、去重、文件名模板、WebDAV 等能力
都由上游保证，本服务只做「发现 → 增量比对 → 择优 → 触发」的编排。
"""
from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlencode

import httpx

from . import parser
from .config import settings

log = logging.getLogger("monitor.engine")

JSON_HEADERS = {"Accept": "application/json, text/plain, */*", "X-Requested-With": "XMLHttpRequest"}
HTML_HEADERS = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}


class EngineError(RuntimeError):
    pass


class Engine:
    def __init__(self, base_url: str | None = None, prefix: str | None = None) -> None:
        self.base = (base_url or settings.engine_url).rstrip("/")
        self.prefix = prefix or settings.engine_prefix
        self._client: httpx.AsyncClient | None = None
        # 引擎若开启了登录保护，可选地保留一个已认证会话
        self._session_cookies: dict[str, str] = {}
        self._username: str = ""
        self._password: str = ""

    # ------------------------------------------------------------------ HTTP
    async def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(settings.http_timeout, connect=10.0),
                follow_redirects=True,
                headers={"User-Agent": settings.user_agent},
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    def url(self, path: str) -> str:
        return f"{self.base}{self.prefix}{path}"

    async def _get_json(self, path: str, params: Any = None) -> Any:
        c = await self.client()
        r = await c.get(self.url(path), params=params, headers=JSON_HEADERS, cookies=self._session_cookies or None)
        r.raise_for_status()
        try:
            return r.json()
        except ValueError as exc:  # 上游返回了 HTML（通常是登录页）
            raise EngineError(f"引擎返回非 JSON（可能需要登录或路由前缀不对）: {path}") from exc

    async def _get_html(self, path: str, params: Any = None) -> str:
        c = await self.client()
        r = await c.get(self.url(path), params=params, headers=HTML_HEADERS, cookies=self._session_cookies or None)
        r.raise_for_status()
        return r.text

    # ------------------------------------------------------------------ 基础
    async def healthz(self) -> dict[str, Any]:
        c = await self.client()
        try:
            r = await c.get(self.url("/healthz"), headers=JSON_HEADERS, timeout=5.0)
            if r.status_code == 200:
                return {"ok": True, "detail": r.json()}
            return {"ok": False, "detail": f"HTTP {r.status_code}"}
        except Exception as exc:  # noqa: BLE001 - 健康检查不该抛错
            return {"ok": False, "detail": str(exc)}

    async def settings(self) -> dict[str, Any]:
        """引擎的公开设置（含下载目录、文件名模板等）。"""
        try:
            return parser.parse_settings_json(await self._get_json("/settings"))
        except Exception as exc:  # noqa: BLE001
            log.warning("读取引擎设置失败: %s", exc)
            return {}

    async def sources(self) -> list[dict[str, Any]]:
        """引擎实际支持的平台清单（从首页的搜索源设置里解析，永远与引擎版本一致）。"""
        try:
            html = await self._get_html("/")
            return parser.parse_sources(html)
        except Exception as exc:  # noqa: BLE001
            log.warning("读取平台清单失败: %s", exc)
            return []

    # ------------------------------------------------------------------ 搜索/解析
    async def search_songs(
        self,
        keyword: str,
        sources: list[str] | None = None,
        *,
        exact_artist: str = "",
        page_size: int = 100,
    ) -> list[dict[str, Any]]:
        params: list[tuple[str, str]] = [("q", keyword), ("type", "song"), ("page_size", str(page_size))]
        for s in sources or []:
            params.append(("sources", s))
        if exact_artist:
            params.append(("exact_artist", exact_artist))
        html = await self._get_html("/search?" + urlencode(params))
        return parser.parse_songs(html)

    async def search_playlists(
        self,
        keyword: str,
        sources: list[str] | None = None,
        *,
        page_size: int = 60,
    ) -> list[dict[str, Any]]:
        """关键词或平台链接 → 歌单卡片列表（链接会被引擎自动识别来源）。"""
        params: list[tuple[str, str]] = [("q", keyword), ("type", "playlist"), ("page_size", str(page_size))]
        for s in sources or []:
            params.append(("sources", s))
        html = await self._get_html("/search?" + urlencode(params))
        return parser.parse_playlists(html)

    async def playlist_songs(self, playlist_id: str, source: str, *, link: str = "") -> list[dict[str, Any]]:
        params: dict[str, str] = {"id": playlist_id, "source": source, "page_size": "500"}
        if link:
            params["link"] = link
        html = await self._get_html("/playlist", params=params)
        return parser.parse_songs(html)

    async def album_songs(self, album_id: str, source: str) -> list[dict[str, Any]]:
        html = await self._get_html("/album", params={"id": album_id, "source": source, "page_size": "500"})
        return parser.parse_songs(html)

    async def user_playlists(self, sources: list[str] | None = None) -> list[dict[str, Any]]:
        """已登录平台的个人歌单 / 收藏夹（需要引擎里配置了 Cookie）。"""
        params: list[tuple[str, str]] = []
        for s in sources or []:
            params.append(("sources", s))
        html = await self._get_html("/user_playlists?" + urlencode(params) if params else "/user_playlists")
        return parser.parse_playlists(html)

    async def recommend_playlists(self, sources: list[str] | None = None) -> list[dict[str, Any]]:
        params: list[tuple[str, str]] = []
        for s in sources or []:
            params.append(("sources", s))
        html = await self._get_html("/recommend?" + urlencode(params) if params else "/recommend")
        return parser.parse_playlists(html)

    # ------------------------------------------------------------------ 音质
    async def inspect(self, song: dict[str, Any]) -> dict[str, Any]:
        """探测某首歌曲可下载链接的有效性 / 体积 / 码率。

        返回 {valid, url, size, bitrate}；bitrate 形如 "320 kbps"，无法计算时为 "-"。
        """
        if not song.get("id") or not song.get("source"):
            return {"valid": False}
        params: dict[str, str] = {"id": str(song["id"]), "source": str(song["source"])}
        if song.get("duration"):
            params["duration"] = str(int(song["duration"]))
        if song.get("extra"):
            import json

            params["extra"] = json.dumps(song["extra"], ensure_ascii=False)
        try:
            return await self._get_json("/inspect", params=params)
        except Exception as exc:  # noqa: BLE001
            log.debug("inspect 失败 %s/%s: %s", song.get("source"), song.get("id"), exc)
            return {"valid": False}

    async def switch_source(
        self,
        name: str,
        artist: str,
        current: str,
        *,
        duration: int = 0,
        target: str = "",
    ) -> dict[str, Any] | None:
        """让引擎在其它平台里找一首最接近的可用版本（相似度 + 时长 + 可播放性校验）。"""
        params: dict[str, str] = {"name": name, "artist": artist, "current": current}
        if duration:
            params["duration"] = str(int(duration))
        if target:
            params["target"] = target
        try:
            return await self._get_json("/switch_source", params=params)
        except Exception as exc:  # noqa: BLE001
            log.debug("switch_source 失败 %s - %s: %s", name, artist, exc)
            return None

    # ------------------------------------------------------------------ 去重 / 下载
    async def precheck_one(self, song: dict[str, Any]) -> bool:
        """询问引擎：这首歌是否已经在它的下载库里（跨监控、跨平台的持久化去重集合）。

        上游 `/api/downloads/precheck` 只返回 {total, skipped} 的统计值，
        所以这里按单首提交，用 skipped == 1 作为「已存在」判据。
        """
        if not song.get("name"):
            return False
        c = await self.client()
        payload = {"songs": [{"name": song.get("name", ""), "artist": song.get("artist", "")}]}
        try:
            r = await c.post(
                self.url("/api/downloads/precheck"),
                json=payload,
                headers={**JSON_HEADERS, "Content-Type": "application/json"},
                cookies=self._session_cookies or None,
            )
            r.raise_for_status()
            data = r.json()
        except Exception as exc:  # noqa: BLE001
            log.debug("precheck 失败 %s: %s", song.get("name"), exc)
            return False
        return int(data.get("skipped") or 0) == 1

    async def download(self, song: dict[str, Any], *, embed: bool = True) -> dict[str, Any]:
        """触发引擎把歌曲落盘到它自己的下载目录。

        上游要求：POST + X-Requested-With: XMLHttpRequest + 不携带跨站来源头，
        这样它才会走「保存到本地」分支并返回 JSON 结果（含 filename / skipped）。
        """
        if not song.get("id") or not song.get("source"):
            raise EngineError("缺少 id/source，无法下载")

        params: dict[str, str] = {
            "save_local": "1",
            "id": str(song["id"]),
            "source": str(song["source"]),
            "name": song.get("name") or "Unknown",
            "artist": song.get("artist") or "Unknown",
        }
        if song.get("album"):
            params["album"] = song["album"]
        if song.get("cover"):
            params["cover"] = song["cover"]
        if embed:
            params["embed"] = "1"
        if song.get("extra"):
            import json

            params["extra"] = json.dumps(song["extra"], ensure_ascii=False)

        c = await self.client()
        r = await c.post(self.url("/download"), params=params, headers=JSON_HEADERS)
        if r.status_code >= 400:
            body = r.text[:300]
            raise EngineError(f"下载失败 HTTP {r.status_code}: {body}")
        try:
            data = r.json()
        except ValueError:
            raise EngineError("下载接口返回非 JSON，无法确认是否已保存") from None
        if not isinstance(data, dict):
            raise EngineError("下载接口返回格式异常，无法确认是否已保存")
        if data.get("status") not in {"ok", "success", "downloaded"} and not data.get("saved"):
            detail = data.get("error") or data.get("message") or data.get("warning") or "上游未确认保存"
            raise EngineError(f"上游下载未成功：{detail}")
        if data.get("saved") is False:
            detail = data.get("error") or data.get("message") or data.get("warning") or "上游未保存文件"
            raise EngineError(f"上游下载未成功：{detail}")
        return data

    # ------------------------------------------------------------------ 可选：引擎登录
    async def login(self, username: str, password: str) -> dict[str, Any]:
        """登录引擎，换取会话 Cookie。

        只有当你需要由本服务代为写入平台 Cookie / 修改引擎设置时才需要登录。
        引擎的搜索与下载接口本身就是公开的。
        """
        c = await self.client()
        try:
            r = await c.post(
                self.url("/login"),
                data={"username": username, "password": password},
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "text/html,*/*",
                },
                follow_redirects=False,
            )
        except httpx.HTTPError as exc:
            return {"ok": False, "detail": str(exc)}

        cookies = {k: v for k, v in r.cookies.items()}
        if not cookies:
            cookies = {k: v for k, v in c.cookies.items()}
        self._session_cookies = cookies
        ok = bool(cookies)
        if ok:
            self._username, self._password = username, password
        return {"ok": ok, "detail": "已登录" if ok else "未取到会话 Cookie，请检查账号密码"}

    async def push_cookies(self, mapping: dict[str, str]) -> dict[str, Any]:
        """把平台 Cookie 写入引擎（等价于在引擎设置页粘贴 Cookie）。

        这是拿到无损/高码率的关键：引擎按平台 Cookie 的会员等级决定实际音质。
        """
        if not self._session_cookies:
            raise EngineError("尚未登录引擎，无法写入 Cookie（请在「设置」中填写引擎管理员账号）")
        c = await self.client()
        r = await c.post(
            self.url("/cookies"),
            json=mapping,
            headers={**JSON_HEADERS, "Content-Type": "application/json"},
            cookies=self._session_cookies,
        )
        if r.status_code >= 400:
            raise EngineError(f"写入 Cookie 失败 HTTP {r.status_code}: {r.text[:200]}")
        return r.json()

    async def get_cookies(self) -> dict[str, str]:
        if not self._session_cookies:
            return {}
        c = await self.client()
        r = await c.get(self.url("/cookies"), headers=JSON_HEADERS, cookies=self._session_cookies)
        if r.status_code >= 400:
            return {}
        data = r.json()
        return data if isinstance(data, dict) else {}

    async def save_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self._session_cookies:
            raise EngineError("尚未登录引擎，无法修改引擎设置")
        c = await self.client()
        r = await c.post(
            self.url("/settings"),
            json=payload,
            headers={**JSON_HEADERS, "Content-Type": "application/json"},
            cookies=self._session_cookies,
        )
        if r.status_code >= 400:
            raise EngineError(f"保存引擎设置失败 HTTP {r.status_code}: {r.text[:200]}")
        return r.json()


engine = Engine()
