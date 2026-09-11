#!/usr/bin/env bash
# ============================================================================
#  music-monitor 镜像构建 / 推送脚本（Linux / macOS / NAS 上直接跑）
#
#  用法：
#    ./scripts/build-push.sh local     # 在本机 docker compose 构建并启动（最简单）
#    ./scripts/build-push.sh load      # 构建本机架构镜像到本地，不打标签推送
#    ./scripts/build-push.sh login     # 登录镜像仓库（默认 Docker Hub，可跟 registry 地址）
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

# 构建期源加速（国内构建时在 .env 里设 APT_MIRROR / PIP_INDEX，留空则用官方源）
BUILD_ARGS=(--build-arg "APP_VERSION=$VERSION")
[ -n "${APT_MIRROR:-}" ] && BUILD_ARGS+=(--build-arg "APT_MIRROR=$APT_MIRROR")
[ -n "${PIP_INDEX:-}" ] && BUILD_ARGS+=(--build-arg "PIP_INDEX=$PIP_INDEX")

say() { printf '\n\033[36m==> %s\033[0m\n' "$*"; }
die() { printf '\n\033[31m错误：%s\033[0m\n' "$*" >&2; exit 1; }

need_docker() {
  command -v docker >/dev/null 2>&1 || die "没有找到 docker，请先安装 Docker。"
  docker info >/dev/null 2>&1 || die "docker 守护进程不可用（权限或未启动）。"
}

# 从镜像地址里反推 registry 主机名。
# Docker 的判定规则：第一段含 . 或 : 、或者等于 localhost，才算 registry 主机；
# 否则按 Docker Hub 处理（如 baey666/music-monitor 的第一段是命名空间，不是主机）。
registry_of() {
  case "${1%%/*}" in
    *.*|*:|localhost) printf '%s' "${1%%/*}" ;;
    *)                printf 'docker.io' ;;
  esac
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
      "${BUILD_ARGS[@]}" \
      -t "$IMAGE" \
      -f monitor/Dockerfile monitor
    say "完成。docker images 里可以看到 $IMAGE"
    docker images --format 'table {{.Repository}}:{{.Tag}}\t{{.Size}}' | head -5
    ;;

  login)
    need_docker
    REG="${2:-docker.io}"
    say "登录镜像仓库：$REG"
    if [ "$REG" = "docker.io" ]; then
      cat <<'TIP'
Docker Hub 登录提示：
  · 用户名 = 你的 Docker ID（不是邮箱，也不是昵称）
  · 密码栏请填 Access Token，不要填网页登录密码（2024 起密码登录已被禁用）
    获取地址：https://hub.docker.com/settings/security → New Access Token
    权限选 Read & Write 即可
TIP
      docker login
    else
      docker login "$REG"
    fi
    ;;

  push)
    need_docker
    case "$IMAGE" in
      music-monitor:*|music-monitor) die "请先在 .env 里把 MONITOR_IMAGE 改成带命名空间的仓库地址，例如 yourname/music-monitor:latest";;
    esac
    REG="$(registry_of "$IMAGE")"
    if [ "$REG" = "docker.io" ]; then
      echo "提示：目标仓库是 Docker Hub。若还没登录，先执行 ./scripts/build-push.sh login"
      echo "      免费账号私有仓库只能有 1 个；设为公开则数量不限。"
    fi
    say "多架构构建并推送 → $IMAGE  （平台：$PLATFORMS）"
    echo "第一次用 buildx 多架构会提示安装 QEMU 模拟器，属正常现象。"
    docker buildx inspect monitor-builder >/dev/null 2>&1 \
      || docker buildx create --name monitor-builder --use >/dev/null
    docker buildx use monitor-builder
    docker buildx build \
      --platform "$PLATFORMS" \
      "${BUILD_ARGS[@]}" \
      -t "$IMAGE" \
      --push \
      -f monitor/Dockerfile monitor
    say "推送完成。"
    echo "在 NAS / 目标机器上："
    echo "  docker pull $IMAGE"
    echo "  # 并把 NAS 上 .env 的 MONITOR_IMAGE 改成同一个地址"
    echo "  docker compose pull && docker compose up -d"
    ;;

  save)
    need_docker
    say "构建本机架构镜像并导出 → $TARBALL"
    docker build \
      "${BUILD_ARGS[@]}" \
      -t "$IMAGE" \
      -f monitor/Dockerfile monitor
    docker save -o "$TARBALL" "$IMAGE"
    say "已生成 $TARBALL（$(du -h "$TARBALL" 2>/dev/null | cut -f1)）"
    echo "拷到目标机器后执行： docker load -i $TARBALL"
    ;;

  help|*)
    # 打印文件头部注释块（跳过 shebang，遇到第一行非注释即停止）——这样加子命令时不用改行号
    awk 'NR==1{next} /^#/{sub(/^# ?/,""); print; next} {exit}' "${BASH_SOURCE[0]}"
    echo
    echo "当前镜像地址：$IMAGE"
    echo "当前目标架构：$PLATFORMS"
    ;;
esac
