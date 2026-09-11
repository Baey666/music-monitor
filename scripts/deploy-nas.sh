#!/usr/bin/env bash
# ============================================================================
#  在 NAS / Linux 上用「现成镜像」部署 music-monitor
#
#  适用：镜像已经在仓库里（Docker Hub / ACR / TCR / 私有 registry），
#        NAS 只需拉取运行，**不在本地构建**。也就是 docs/deploy.md 的「方案 B」。
#
#  用法：
#     ./scripts/deploy-nas.sh                        # 用默认镜像地址
#     ./scripts/deploy-nas.sh myid/music-monitor:v1  # 指定镜像地址
#     MONITOR_IMAGE=xxx docker compose ...           # 也可直接改 .env
#
#  脚本是幂等的：重复执行只会重新拉取并重启容器，不会丢数据。
# ============================================================================
set -euo pipefail

# 默认镜像地址：本项目实际推送到 Docker Hub 的地址
DEFAULT_IMAGE="baey666/music-monitor:latest"

# 允许用第一个参数覆盖镜像地址
IMAGE="${1:-$DEFAULT_IMAGE}"

info()  { printf '\033[36m[deploy]\033[0m %s\n' "$*"; }
warn()  { printf '\033[33m[deploy]\033[0m %s\n' "$*" >&2; }
fatal() { printf '\033[31m[deploy]\033[0m %s\n' "$*" >&2; exit 1; }

# ── 0. 切到仓库根目录（脚本可能在 scripts/ 下被调用）────────────────────────
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
cd "$ROOT"
[ -f docker-compose.yml ] || fatal "没找到 docker-compose.yml，请在仓库根目录执行（当前：$ROOT）"

# ── 1. 检查 docker / compose ────────────────────────────────────────────────
command -v docker >/dev/null 2>&1 || fatal "没装 docker，或当前用户没有 docker 命令权限"

if docker compose version >/dev/null 2>&1; then
  DC=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  DC=(docker-compose)          # 老版本独立二进制
else
  fatal "找不到 docker compose 插件，也没有 docker-compose 命令"
fi
info "compose 命令：${DC[*]}"

# ── 2. 生成 / 更新 .env ────────────────────────────────────────────────────
if [ ! -f .env ]; then
  [ -f .env.example ] || fatal "缺少 .env.example，无法生成 .env"
  cp .env.example .env
  info "已从 .env.example 生成 .env"
else
  info "复用已有 .env（不会被覆盖）"
fi

# 写入 / 更新 MONITOR_IMAGE（原地替换，不重排其他内容）
if grep -q '^MONITOR_IMAGE=' .env; then
  # 用 | 作分隔符，避免地址里的 / 冲突
  sed -i.bak "s|^MONITOR_IMAGE=.*|MONITOR_IMAGE=${IMAGE}|" .env && rm -f .env.bak
else
  printf '\nMONITOR_IMAGE=%s\n' "$IMAGE" >> .env
fi
info "MONITOR_IMAGE = $IMAGE"

# ── 3. 数据目录与权限 ──────────────────────────────────────────────────────
# 从 .env 读取宿主机路径（容忍行尾注释与空白），缺省回落到 compose 里的默认值。
# 注意：这里是「宿主机」路径；容器内路径程序内部写死，不在这里改。
envval() {
  local v
  v="$(sed -n "s/^$1=//p" .env | head -1 | sed 's/[[:space:]]*#.*$//' | tr -d '[:space:]')"
  printf '%s' "${v:-$2}"
}
ENGINE_CONFIG_DIR="$(envval ENGINE_CONFIG_DIR ./config/engine)"
MONITOR_CONFIG_DIR="$(envval MONITOR_CONFIG_DIR ./config/monitor)"
DOWNLOADS_DIR="$(envval DOWNLOADS_DIR ./data/downloads)"

# 两个容器都以 uid=1000 运行，宿主机目录必须可写，否则容器起不来或写不进文件
mkdir -p "$ENGINE_CONFIG_DIR" "$MONITOR_CONFIG_DIR" "$DOWNLOADS_DIR"
chmod -R 777 "$ENGINE_CONFIG_DIR" "$MONITOR_CONFIG_DIR" "$DOWNLOADS_DIR" 2>/dev/null \
  || warn "chmod 失败，若容器报权限错误请手动处理：sudo chmod -R 777 '$ENGINE_CONFIG_DIR' '$MONITOR_CONFIG_DIR' '$DOWNLOADS_DIR'"
info "引擎配置/登录态 : $ENGINE_CONFIG_DIR/   (settings.db, cookies.json)"
info "监控配置        : $MONITOR_CONFIG_DIR/monitor.db"
info "音乐文件        : $DOWNLOADS_DIR/"

# ── 4. 拉取镜像 ────────────────────────────────────────────────────────────
info "拉取镜像（国内直连 Docker Hub 常超时，下面若失败请看脚本末尾的加速器提示）..."
if ! "${DC[@]}" pull; then
  warn "--------------------------------------------------------------------"
  warn "镜像拉取失败。国内网络下 registry-1.docker.io 经常不可达，需要配镜像加速："
  warn ""
  warn "  sudo mkdir -p /etc/docker"
  warn "  sudo tee /etc/docker/daemon.json >/dev/null <<'EOF'"
  warn "  { \"registry-mirrors\": [\"https://docker.1ms.run\"] }"
  warn "  EOF"
  warn "  sudo systemctl restart docker"
  warn ""
  warn "飞牛 / 群晖 / 威联通也可在「Docker 设置 → 镜像加速」里填 https://docker.1ms.run"
  warn "注意：只有 docker.1ms.run 实测能代理「用户命名空间」镜像（如 baey666/xxx），"
  warn "      daocloud 只代理官方库镜像，拉用户镜像会 403。"
  warn ""
  warn "不想改全局配置的话，也可以把镜像地址写成带前缀的形式，"
  warn "并把 docker-compose.yml 里 music-dl 的 image 一起改掉："
  warn "  docker.1ms.run/baey666/music-monitor:latest"
  warn "  docker.1ms.run/guohuiyuan/go-music-dl:latest"
  warn "--------------------------------------------------------------------"
  exit 1
fi

# ── 5. 启动 ────────────────────────────────────────────────────────────────
info "启动容器..."
"${DC[@]}" up -d

# ── 6. 检查 ────────────────────────────────────────────────────────────────
info "容器状态："
"${DC[@]}" ps

PORT="$(envval MONITOR_PORT 9090)"
EPORT="$(envval ENGINE_PORT 8085)"
cat <<EOF

================================================================
部署完成  （删容器不丢数据；配置全部集中在 config/，备份就打包它）

  引擎配置与登录态   $ENGINE_CONFIG_DIR/
                     ├── settings.db      引擎设置（含 downloadDir）
                     └── cookies.json     各平台登录态
  监控配置库         $MONITOR_CONFIG_DIR/monitor.db
  音乐文件           $DOWNLOADS_DIR/

  监控控制台   http://<NAS-IP>:${PORT}
  下载引擎     http://<NAS-IP>:${EPORT}

  首次使用必做（见 README「快速开始」第 4 步）：
   1. 打开引擎页面 :${EPORT}，用日志里的初始化令牌建管理员账号
        ${DC[*]} logs go-music-dl | grep "Web setup token"
   2. 引擎设置里「下载目录 / downloadDir」保持默认 data/downloads 即可
        · 它填的是**容器内路径**，默认值正好落在 $DOWNLOADS_DIR/
        · 想看当前生效值：curl -s http://<NAS-IP>:${EPORT}/music/settings
        · 改成容器内其它路径的话，必须同时在 compose 里加挂载，否则宿主机看不到文件
   3. 扫码登录有会员的平台（这决定能拿到什么音质）
   4. 回到 :${PORT} 建第一个监控

  查看日志   ${DC[*]} logs -f monitor
  停止       ${DC[*]} down
  更新       ${DC[*]} pull && ${DC[*]} up -d
  备份配置   tar czf config-backup-\$(date +%F).tar.gz '$ENGINE_CONFIG_DIR' '$MONITOR_CONFIG_DIR'
================================================================
EOF
