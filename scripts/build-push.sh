#!/usr/bin/env bash
# ============================================================================
#  music-monitor 镜像构建 / 推送脚本（Linux / macOS / NAS 上直接跑）
#
#  用法：
#    ./scripts/build-push.sh local     # 在本机 docker compose 构建并启动（最简单）
#    ./scripts/build-push.sh load      # 构建本机架构镜像到本地，不打标签推送
#    ./scripts/build-push.sh push      # buildx 多架构构建并推送到仓库
#    ./scripts/build-push.sh save      # 构建本机架构并导出 tar，供离线/无外网 NAS 导入
#    ./scripts/build-push.sh help
#
#  镜像地址取自 .env 的 BUILD_IMAGE（留空则用 MONITOR_IMAGE）。
# ============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# 载入 .env（存在才载入；行尾 # 注释在 shell 里会被正确忽略）
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

IMAGE="${BUILD_IMAGE:-${MONITOR_IMAGE:-music-monitor:latest}}"
PLATFORMS="${PLATFORMS:-linux/amd64,linux/arm64}"
VERSION="${APP_VERSION:-1.0.0}"
TARBALL="${TARBALL:-music-monitor.tar}"

say() { printf '\n\033[36m==> %s\033[0m\n' "$*"; }
die() { printf '\n\033[31m错误：%s\033[0m\n' "$*" >&2; exit 1; }

need_docker() {
  command -v docker >/dev/null 2>&1 || die "没有找到 docker，请先安装 Docker。"
  docker info >/dev/null 2>&1 || die "docker 守护进程不可用（权限或未启动）。"
}

case "${1:-help}" in
  local)
    need_docker
    say "本地构建并启动（docker compose）"
    mkdir -p data/downloads data/monitor
    chmod -R 777 data 2>/dev/null || true
    docker compose up -d --build
    say "完成。控制台： http://<本机IP>:${MONITOR_PORT:-9090}"
    docker compose ps
    ;;

  load)
    need_docker
    say "构建本机架构镜像 → $IMAGE"
    docker build \
      --build-arg APP_VERSION="$VERSION" \
      -t "$IMAGE" \
      -f monitor/Dockerfile monitor
    say "完成。docker images 里可以看到 $IMAGE"
    docker images --format 'table {{.Repository}}:{{.Tag}}\t{{.Size}}' | head -5
    ;;

  push)
    need_docker
    case "$IMAGE" in
      music-monitor:*|music-monitor) die "请先在 .env 里把 MONITOR_IMAGE 改成带命名空间的仓库地址，例如 yourname/music-monitor:latest";;
    esac
    say "多架构构建并推送 → $IMAGE  （平台：$PLATFORMS）"
    echo "第一次用 buildx 多架构会提示安装 QEMU 模拟器，属正常现象。"
    docker buildx inspect monitor-builder >/dev/null 2>&1 \
      || docker buildx create --name monitor-builder --use >/dev/null
    docker buildx use monitor-builder
    docker buildx build \
      --platform "$PLATFORMS" \
      --build-arg APP_VERSION="$VERSION" \
      -t "$IMAGE" \
      --push \
      -f monitor/Dockerfile monitor
    say "推送完成。在 NAS 上执行：docker compose pull && docker compose up -d"
    ;;

  save)
    need_docker
    say "构建本机架构镜像并导出 → $TARBALL"
    docker build \
      --build-arg APP_VERSION="$VERSION" \
      -t "$IMAGE" \
      -f monitor/Dockerfile monitor
    docker save -o "$TARBALL" "$IMAGE"
    say "已生成 $TARBALL（$(du -h "$TARBALL" 2>/dev/null | cut -f1)）"
    echo "拷到目标机器后执行： docker load -i $TARBALL"
    ;;

  help|*)
    sed -n '2,13p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    echo
    echo "当前镜像地址：$IMAGE"
    echo "当前目标架构：$PLATFORMS"
    ;;
esac
