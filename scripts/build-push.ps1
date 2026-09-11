# ============================================================================
#  music-monitor 镜像构建 / 推送脚本（Windows PowerShell）
#
#  用法（在项目根目录执行）：
#    powershell -ExecutionPolicy Bypass -File scripts\build-push.ps1 local   # 本地构建并启动
#    powershell -ExecutionPolicy Bypass -File scripts\build-push.ps1 load    # 构建到本地
#    powershell -ExecutionPolicy Bypass -File scripts\build-push.ps1 login   # 登录镜像仓库（默认 Docker Hub）
#    powershell -ExecutionPolicy Bypass -File scripts\build-push.ps1 push    # 多架构构建并推送
#    powershell -ExecutionPolicy Bypass -File scripts\build-push.ps1 save    # 导出 tar 供离线导入
#    powershell -ExecutionPolicy Bypass -File scripts\build-push.ps1 help
#
#  镜像地址取自 .env 的 BUILD_IMAGE（留空则用 MONITOR_IMAGE）。
# ============================================================================
param(
  [Parameter(Position = 0)]
  [ValidateSet('local', 'load', 'login', 'push', 'save', 'help')]
  [string]$Command = 'help',
  [Parameter(Position = 1)]
  [string]$Registry = 'docker.io'
)

# 注意：这里刻意用 Continue 而不是 Stop。
# 原生程序（docker）把提示写到 stderr 时，若为 Stop 会被 PowerShell 当成终止错误，
# 导致 docker buildx inspect 这类"预期可能失败"的探测直接中断脚本。
# 所以统一用 Continue，并在每次原生调用后显式检查 $LASTEXITCODE。
$ErrorActionPreference = 'Continue'
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

function Write-Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Fail($msg) { Write-Host "`n错误：$msg" -ForegroundColor Red; exit 1 }

# ---- 读取 .env --------------------------------------------------------------
function Read-DotEnv($path) {
  $map = @{}
  if (-not (Test-Path $path)) { return $map }
  foreach ($line in Get-Content $path) {
    $text = $line.Trim()
    if ($text -eq '' -or $text.StartsWith('#')) { continue }
    $idx = $text.IndexOf('=')
    if ($idx -lt 1) { continue }
    $key = $text.Substring(0, $idx).Trim()
    $val = $text.Substring($idx + 1)
    # 去掉行尾注释（# 前有空白才算注释）
    $val = [regex]::Replace($val, '\s+#.*$', '')
    $map[$key] = $val.Trim()
  }
  return $map
}

$envMap = Read-DotEnv (Join-Path $Root '.env')
$Image = if ($envMap['BUILD_IMAGE']) { $envMap['BUILD_IMAGE'] }
         elseif ($envMap['MONITOR_IMAGE']) { $envMap['MONITOR_IMAGE'] }
         else { 'music-monitor:latest' }
$Platforms = if ($envMap['PLATFORMS']) { $envMap['PLATFORMS'] } else { 'linux/amd64,linux/arm64' }
$Version = if ($envMap['APP_VERSION']) { $envMap['APP_VERSION'] } else { '1.0.0' }
$Tarball = if ($envMap['TARBALL']) { $envMap['TARBALL'] } else { 'music-monitor.tar' }
$MonitorPort = if ($envMap['MONITOR_PORT']) { $envMap['MONITOR_PORT'] } else { '9090' }

# 构建期源加速（国内构建时在 .env 里设 APT_MIRROR / PIP_INDEX，留空则用官方源）
$AptMirror = $envMap['APT_MIRROR']
$PipIndex  = $envMap['PIP_INDEX']
$buildArgs = @('--build-arg', "APP_VERSION=$Version")
if ($AptMirror) { $buildArgs += @('--build-arg', "APT_MIRROR=$AptMirror") }
if ($PipIndex)  { $buildArgs += @('--build-arg', "PIP_INDEX=$PipIndex") }

function Assert-Docker {
  if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { Fail '没有找到 docker，请先安装 Docker Desktop / Docker Engine。' }
  docker info *> $null
  if ($LASTEXITCODE -ne 0) { Fail 'docker 守护进程不可用（未启动或权限不足）。' }
}

# 从镜像地址里反推 registry 主机名。
# Docker 的判定规则：第一段含 . 或 : 、或者等于 localhost，才算 registry 主机；
# 否则按 Docker Hub 处理（如 baey666/music-monitor 的第一段是命名空间，不是主机）。
function Get-Registry($image) {
  $first = $image.Split('/')[0]
  if ($first -match '[.:]' -or $first -eq 'localhost') { return $first }
  return 'docker.io'
}

switch ($Command) {
  'local' {
    Assert-Docker
    Write-Step '本地构建并启动（docker compose）'
    New-Item -ItemType Directory -Force -Path 'data/downloads', 'data/monitor' | Out-Null
    docker compose up -d --build
    if ($LASTEXITCODE -ne 0) { Fail 'docker compose 失败。' }
    Write-Step "完成。控制台： http://localhost:$MonitorPort"
    docker compose ps
  }

  'load' {
    Assert-Docker
    Write-Step "构建本机架构镜像 -> $Image"
    docker build @buildArgs -t $Image -f monitor/Dockerfile monitor
    if ($LASTEXITCODE -ne 0) { Fail '镜像构建失败。' }
    Write-Step "完成。docker images 里可以看到 $Image"
  }

  'login' {
    Assert-Docker
    Write-Step "登录镜像仓库：$Registry"
    if ($Registry -eq 'docker.io') {
      Write-Host 'Docker Hub 登录提示：' -ForegroundColor Yellow
      Write-Host '  · 用户名 = 你的 Docker ID（不是邮箱，也不是昵称）'
      Write-Host '  · 密码栏请填 Access Token，不要填网页登录密码（2024 起密码登录已被禁用）'
      Write-Host '    获取地址：https://hub.docker.com/settings/security -> New Access Token'
      Write-Host '    权限选 Read & Write 即可'
      docker login
    } else {
      docker login $Registry
    }
    if ($LASTEXITCODE -ne 0) { Fail '登录失败。' }
    Write-Step '登录成功。接下来可以执行 push 子命令。'
  }

  'push' {
    Assert-Docker
    if ($Image -like 'music-monitor*' -or $Image -eq 'music-monitor') {
      Fail '请先在 .env 里把 MONITOR_IMAGE 改成带命名空间的仓库地址，例如 yourname/music-monitor:latest'
    }
    $reg = Get-Registry $Image
    if ($reg -eq 'docker.io') {
      Write-Host '提示：目标仓库是 Docker Hub。若还没登录，先执行 scripts\build-push.ps1 login' -ForegroundColor Yellow
      Write-Host '      免费账号私有仓库只能有 1 个；设为公开则数量不限。' -ForegroundColor Yellow
    }
    Write-Step "多架构构建并推送 -> $Image  （平台：$Platforms）"
    Write-Host '第一次用 buildx 多架构会提示安装 QEMU 模拟器，属正常现象。' -ForegroundColor Yellow
    docker buildx inspect monitor-builder *> $null
    if ($LASTEXITCODE -ne 0) { docker buildx create --name monitor-builder --use | Out-Null }
    docker buildx use monitor-builder
    docker buildx build --platform $Platforms @buildArgs -t $Image --push -f monitor/Dockerfile monitor
    if ($LASTEXITCODE -ne 0) { Fail '推送失败。' }
    Write-Step '推送完成。'
    Write-Host '在 NAS / 目标机器上：'
    Write-Host "  docker pull $Image"
    Write-Host '  # 并把 NAS 上 .env 的 MONITOR_IMAGE 改成同一个地址'
    Write-Host '  docker compose pull; docker compose up -d'
  }

  'save' {
    Assert-Docker
    Write-Step "构建本机架构镜像并导出 -> $Tarball"
    docker build @buildArgs -t $Image -f monitor/Dockerfile monitor
    if ($LASTEXITCODE -ne 0) { Fail '镜像构建失败。' }
    docker save -o $Tarball $Image
    if ($LASTEXITCODE -ne 0) { Fail '导出失败。' }
    $size = [math]::Round((Get-Item $Tarball).Length / 1MB, 1)
    Write-Step "已生成 $Tarball（${size} MB）"
    Write-Host "拷到目标机器后执行： docker load -i $Tarball"
  }

  default {
    # 打印文件头部注释块（遇到第一行非注释即停止）——这样加子命令时不用改行号
    foreach ($line in (Get-Content $PSCommandPath)) {
      if ($line -notmatch '^#') { break }
      Write-Host ($line -replace '^# ?', '')
    }
    Write-Host ''
    Write-Host "当前镜像地址：$Image"
    Write-Host "当前目标架构：$Platforms"
  }
}
