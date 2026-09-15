FROM python:3.12-slim

# ── 构建期源加速（可选，留空即用官方源）──────────────────────────────────
# 国内构建强烈建议在 .env 里打开这两项，否则 apt 和 pip 会非常慢甚至超时：
#   APT_MIRROR=mirrors.aliyun.com
#   PIP_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple
# 注意：这只管「构建过程」的源；拉基础镜像 python:3.12-slim 走的是 Docker 守护进程的
# registry-mirrors，两者要分别配置（见 docs/deploy.md 的「国内网络注意事项」）。
ARG APT_MIRROR=
ARG PIP_INDEX=

# OCI 元数据（便于镜像仓库展示与追溯）
# APP_VERSION 由 docker-compose build args / CI 注入，默认值与 app/__init__.py 的
# __version__ 保持一致；改版本请用 scripts/version.sh bump，勿手改此处。
ARG APP_VERSION=1.2.2
LABEL org.opencontainers.image.title="music-monitor" \
      org.opencontainers.image.description="go-music-dl 之上的榜单/歌单/收藏夹监控自动下载服务" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.source="https://github.com/guohuiyuan/go-music-dl" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai

WORKDIR /app

# 可选：把 Debian 软件源换成国内镜像（新版 deb822 与旧版 sources.list 两种格式都处理）
RUN if [ -n "$APT_MIRROR" ]; then \
      for f in /etc/apt/sources.list /etc/apt/sources.list.d/debian.sources; do \
        if [ -f "$f" ]; then \
          sed -i "s|deb.debian.org|$APT_MIRROR|g; s|security.debian.org|$APT_MIRROR|g" "$f"; \
        fi; \
      done; \
    fi

# 时区与基础工具（curl 便于容器内自检）
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata curl \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# 可选：pip 换国内源
RUN if [ -n "$PIP_INDEX" ]; then \
      pip install --no-cache-dir -i "$PIP_INDEX" -r requirements.txt; \
    else \
      pip install --no-cache-dir -r requirements.txt; \
    fi

COPY app ./app
COPY web ./web

# 运行期数据目录（compose 会挂载出来）
RUN mkdir -p /app/data && chmod 777 /app/data

EXPOSE 9090

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://127.0.0.1:9090/api/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "9090"]
