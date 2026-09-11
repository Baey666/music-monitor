FROM python:3.12-slim

# OCI 元数据（便于镜像仓库展示与追溯）
ARG APP_VERSION=1.0.0
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

# 时区与基础工具（curl 便于容器内自检）
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata curl \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY web ./web

# 运行期数据目录（compose 会挂载出来）
RUN mkdir -p /app/data && chmod 777 /app/data

EXPOSE 9090

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://127.0.0.1:9090/api/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "9090"]
