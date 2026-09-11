#!/usr/bin/env bash
# ============================================================================
#  music-monitor 版本管理（单一来源 + 自动同步 + 打标签）
#
#  版本号的唯一权威来源：monitor/app/__init__.py 里的 __version__。
#  本脚本负责把它同步到 Dockerfile / .env.example / .env，并维护 CHANGELOG.md、
#  按需 commit + 打 git tag。镜像 tag（v<版本号>）由 docker-compose.yml 从 .env
#  的 APP_VERSION 派生 —— 所以只要改这一处，代码 / 镜像 / 文档就全对上了。
#
#  用法：
#     ./scripts/version.sh                    # 显示当前版本（同 show）
#     ./scripts/version.sh check              # 校验各处版本号是否一致（提交前 / CI 用）
#     ./scripts/version.sh sync               # 把权威版本同步到其它文件（不改版本号）
#     ./scripts/version.sh bump patch         # 1.0.0 -> 1.0.1   修 bug
#     ./scripts/version.sh bump minor         # 1.0.0 -> 1.1.0   新增功能（向后兼容）
#     ./scripts/version.sh bump major         # 1.0.0 -> 2.0.0   不兼容变更
#
#  bump 可选开关：
#     --no-commit   只改文件，不自动 commit / 打 tag
#     --push        完成后直接 push 提交与 tag（会触发 GitHub Actions 构建镜像）
#     --yes         跳过所有交互确认
#
#  典型发布流程：
#     1) 改代码，并把变更写进 CHANGELOG.md 的 [Unreleased] 小节
#     2) ./scripts/version.sh bump minor        # 本地确认没问题后再 --push
#     GitHub Actions 收到 v* tag 后自动构建 amd64/arm64 镜像并推到 Docker Hub。
# ============================================================================
set -euo pipefail

# ── 路径与常量 ──────────────────────────────────────────────────────────────
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
cd "$ROOT"

INIT_FILE="monitor/app/__init__.py"
DOCKERFILE="monitor/Dockerfile"
ENV_EXAMPLE=".env.example"
ENV_FILE=".env"
CHANGELOG="CHANGELOG.md"

# 镜像仓库（不含 tag），与 docker-compose.yml 的默认值保持一致
DEFAULT_REPO="${MONITOR_REPO:-baey666/music-monitor}"

info()  { printf '\033[36m[version]\033[0m %s\n' "$*"; }
warn()  { printf '\033[33m[version]\033[0m %s\n' "$*" >&2; }
fatal() { printf '\033[31m[version]\033[0m %s\n' "$*" >&2; exit 1; }
ok()    { printf '\033[32m   ok  \033[0m %s\n' "$*"; }
bad()   { printf '\033[31m  FAIL \033[0m %s\n' "$*"; }

[ -f "$INIT_FILE" ] || fatal "没找到 $INIT_FILE，请在仓库根目录执行（当前：$ROOT）"

# ── 读 / 写权威版本 ─────────────────────────────────────────────────────────
read_version() {
  sed -n 's/^__version__ *= *"\([^"]*\)".*/\1/p' "$INIT_FILE" | head -1
}

write_version() {
  sed -i.bak "s|^__version__ *= *\".*\"|__version__ = \"$1\"|" "$INIT_FILE" && rm -f "$INIT_FILE.bak"
}

# 计算下一个语义化版本
next_version() {
  local cur="$1" part="$2" major minor patch
  IFS=. read -r major minor patch <<< "$cur"
  case "$part" in
    major) major=$((major + 1)); minor=0; patch=0 ;;
    minor) minor=$((minor + 1)); patch=0 ;;
    patch) patch=$((patch + 1)) ;;
    *) fatal "递增类型只能是 patch / minor / major，收到：$part" ;;
  esac
  printf '%s.%s.%s' "$major" "$minor" "$patch"
}

# 校验版本号形态 x.y.z
assert_semver() {
  case "${1:-}" in
    [0-9]*.[0-9]*.[0-9]*) ;;
    *) fatal "版本号不符合 x.y.z 格式：'${1:-}'（请检查 $INIT_FILE）" ;;
  esac
}

# ── 同步到其它文件 ──────────────────────────────────────────────────────────
# 用法：set_versions <版本号> [--quiet]
set_versions() {
  local v="$1" quiet="${2:-}"

  # Dockerfile: ARG APP_VERSION=x.y.z
  if [ -f "$DOCKERFILE" ]; then
    sed -i.bak "s|^ARG APP_VERSION=.*|ARG APP_VERSION=$v|" "$DOCKERFILE" && rm -f "$DOCKERFILE.bak"
    [ -n "$quiet" ] || ok "Dockerfile        ARG APP_VERSION=$v"
  fi

  # .env.example / .env: APP_VERSION=x.y.z（缺失则追加到末尾）
  local f
  for f in "$ENV_EXAMPLE" "$ENV_FILE"; do
    [ -f "$f" ] || continue
    if grep -q '^APP_VERSION=' "$f"; then
      sed -i.bak "s|^APP_VERSION=.*|APP_VERSION=$v|" "$f" && rm -f "$f.bak"
    else
      printf '\nAPP_VERSION=%s\n' "$v" >> "$f"
    fi
    [ -n "$quiet" ] || ok "$f    APP_VERSION=$v"
  done
  [ -n "$quiet" ] || info "已把版本号同步为 $v"
}

# ── CHANGELOG 维护 ──────────────────────────────────────────────────────────
# 在 [Unreleased] 之后插入新版本小节；用户写在该小节下的条目会自动归入新版本。
add_changelog_entry() {
  local v="$1" date="$2" tmp
  if [ ! -f "$CHANGELOG" ]; then
    warn "没有 $CHANGELOG，跳过更新日志（建议补一个）"
    return 0
  fi
  tmp="$ROOT/.changelog.tmp.$$"
  if grep -q '^## \[Unreleased\]' "$CHANGELOG"; then
    awk -v ver="$v" -v date="$date" '
      { print }
      /^## \[Unreleased\]/ && !done { print ""; print "## [" ver "] - " date; done=1 }
    ' "$CHANGELOG" > "$tmp"
    mv "$tmp" "$CHANGELOG"
    rm -f "$tmp"
    ok "CHANGELOG.md    新增小节 ## [$v] - $date"
  else
    warn "$CHANGELOG 里没有 ## [Unreleased] 小节，未自动插入版本记录"
  fi
}

# ── git 辅助（兼容本机 Windows 需走 openssl 的坑）────────────────────────────
git_push_ref() {
  case "$(uname -s 2>/dev/null || echo)" in
    MINGW*|MSYS*|CYGWIN*) git -c http.sslBackend=openssl push origin "$1" ;;
    *) git push origin "$1" ;;
  esac
}

confirm() {
  [ "$ASSUME_YES" = "1" ] && return 0
  printf '\033[33m[version]\033[0m %s [y/N] ' "$1"
  local ans=""
  read -r ans || true
  case "$ans" in y|Y|yes|YES) return 0 ;; *) return 1 ;; esac
}

# ── 子命令 ──────────────────────────────────────────────────────────────────
cmd_show() {
  local v; v="$(read_version)"
  [ -n "$v" ] || fatal "读不到版本号，请检查 $INIT_FILE"
  assert_semver "$v"
  printf '当前版本   %s\n' "$v"
  printf '镜像 tag   %s:v%s  （并同步推送 latest）\n' "$DEFAULT_REPO" "$v"
  printf 'git tag    v%s\n' "$v"
}

cmd_check() {
  local v; v="$(read_version)"
  [ -n "$v" ] || fatal "读不到版本号，请检查 $INIT_FILE"
  info "权威版本（$INIT_FILE）：$v"
  local fail=0 got

  got="$(sed -n 's/^ARG APP_VERSION=//p' "$DOCKERFILE" 2>/dev/null | head -1)"
  if [ "$got" = "$v" ]; then ok "Dockerfile        $got"; else bad "Dockerfile        期望 $v，实际 '${got:-缺失}'"; fail=1; fi

  local f
  for f in "$ENV_EXAMPLE" "$ENV_FILE"; do
    [ -f "$f" ] || { info "跳过 $f（不存在）"; continue; }
    got="$(sed -n 's/^APP_VERSION=//p' "$f" | head -1 | sed 's/[[:space:]]*#.*$//' | tr -d '[:space:]')"
    if [ "$got" = "$v" ]; then ok "$f    $got"; else bad "$f    期望 $v，实际 '${got:-缺失}'"; fail=1; fi
  done

  if [ -f "$CHANGELOG" ]; then
    if grep -q "^## \[$v\]" "$CHANGELOG"; then ok "CHANGELOG.md      含 ## [$v]"
    else bad "CHANGELOG.md      缺少 ## [$v] 小节"; fail=1; fi
  fi

  if [ "$fail" = "0" ]; then
    info "版本号一致，一切正常 ✓"
  else
    warn "存在不一致。修复：./scripts/version.sh sync"
    exit 1
  fi
}

cmd_sync() {
  local v; v="$(read_version)"
  [ -n "$v" ] || fatal "读不到版本号，请检查 $INIT_FILE"
  assert_semver "$v"
  set_versions "$v"
}

cmd_bump() {
  local part="${1:-}"
  [ -n "$part" ] || fatal "用法：./scripts/version.sh bump patch|minor|major"

  local cur new
  cur="$(read_version)"
  [ -n "$cur" ] || fatal "读不到当前版本号，请检查 $INIT_FILE"
  assert_semver "$cur"
  new="$(next_version "$cur" "$part")"

  local today; today="$(date +%F)"
  info "版本：$cur  ->  $new   （$today）"

  # 工作区脏时提醒（bump 会生成提交）
  if [ "$NO_COMMIT" = "0" ] && [ -n "$(git status --porcelain 2>/dev/null || true)" ]; then
    warn "当前工作区有未提交改动："
    git status --short || true
    confirm "这些改动会一并进入本次发布提交，继续？" || { info "已取消"; exit 0; }
  fi

  if [ "$NO_COMMIT" = "1" ]; then
    confirm "确认把版本号改为 $new（不提交、不打 tag）？" || { info "已取消"; exit 0; }
  else
    if git rev-parse -q --verify "refs/tags/v$new" >/dev/null 2>&1; then
      fatal "git tag v$new 已存在，换个递增类型或先删除该 tag"
    fi
    confirm "确认发布 v$new（改版本 + 提交 + 打 tag）？" || { info "已取消"; exit 0; }
  fi

  write_version "$new"
  ok "$INIT_FILE    __version__ = \"$new\""
  set_versions "$new" quiet
  ok "Dockerfile / .env.example / .env 已同步"
  add_changelog_entry "$new" "$today"

  if [ "$NO_COMMIT" = "1" ]; then
    info "已改完文件（--no-commit）。记得自己提交并打 tag：git tag -a v$new -m \"release v$new\""
    return 0
  fi

  git add -A
  git commit -m "chore(release): v$new" >/dev/null
  git tag -a "v$new" -m "release v$new"
  ok "git 提交与 tag v$new 已建立"

  if [ "$DO_PUSH" = "1" ]; then
    info "推送 main 与 tag v$new ..."
    git_push_ref HEAD
    git_push_ref "v$new"
    info "已推送。GitHub Actions 会自动构建并推送镜像 $DEFAULT_REPO:v$new"
  else
    cat <<EOF

下一步（推送后 GitHub Actions 才会构建镜像）：
    git push origin HEAD
    git push origin v$new

或直接重跑：./scripts/version.sh bump $part --push   （会因 tag 已存在而拒绝，改用下面两条）
EOF
  fi
}

# ── 入口 ────────────────────────────────────────────────────────────────────
CMD="${1:-show}"
shift || true

NO_COMMIT=0; DO_PUSH=0; ASSUME_YES=0
POSITIONAL=()
for arg in "$@"; do
  case "$arg" in
    --no-commit) NO_COMMIT=1 ;;
    --push)      DO_PUSH=1 ;;
    --yes|-y)    ASSUME_YES=1 ;;
    -*)          fatal "未知开关：$arg" ;;
    *)           POSITIONAL+=("$arg") ;;
  esac
done

case "$CMD" in
  show|"")     cmd_show ;;
  check)       cmd_check ;;
  sync)        cmd_sync ;;
  bump)        cmd_bump "${POSITIONAL[0]:-}" ;;
  -h|--help|help)
    sed -n '2,30p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    ;;
  *)           fatal "未知命令：$CMD（可用：show / check / sync / bump）" ;;
esac
