# 把镜像发到仓库（Docker Hub / 阿里云 / 腾讯云 / 私有 registry）

> 这是 [music-monitor](https://github.com/Baey666/music-monitor) 的镜像发布文档。
> 部署到 NAS 的完整流程见 [deploy.md](deploy.md)。
> 代码怎么发到 GitHub，见 [publish-github.md](publish-github.md)。

**只有 `monitor` 一个镜像需要构建和上传**，引擎用官方镜像 `guohuiyuan/go-music-dl`。

---

## 1. 镜像地址怎么写

| 仓库 | 地址格式 |
|---|---|
| Docker Hub | `你的DockerID/music-monitor:latest`（**不带主机名**） |
| 阿里云 ACR | `registry.cn-hangzhou.aliyuncs.com/你的命名空间/music-monitor:latest` |
| 腾讯云 TCR | `ccr.ccs.tencentyun.com/你的命名空间/music-monitor:latest` |
| 华为云 SWR | `swr.cn-north-4.myhuaweicloud.com/你的命名空间/music-monitor:latest` |
| 私有 registry | `192.168.1.10:5000/music-monitor:latest` |

判定规则（Docker 自己的规则）：**地址第一段含 `.` 或 `:`、或等于 `localhost`，才算仓库主机名**；
否则按 Docker Hub 处理。所以 `baey666/music-monitor` 里的 `baey666` 是命名空间，不是主机。

填进 `.env`：

```bash
cp .env.example .env
```

```ini
MONITOR_IMAGE=registry.cn-hangzhou.aliyuncs.com/yournamespace/music-monitor:latest
PLATFORMS=linux/amd64,linux/arm64
```

---

## 2. 构建推送脚本

`scripts/build-push.sh`（Linux / macOS / NAS）和 `scripts/build-push.ps1`（Windows）是等价的，
五个子命令：

| 子命令 | 作用 |
|---|---|
| `local` | 在本机 `docker compose` 构建并启动（最省事） |
| `load` | 只构建本机架构镜像到本地，不打标签不推送 |
| `login` | 登录镜像仓库，默认 Docker Hub，可跟 registry 地址 |
| `push` | buildx 多架构构建并推送到仓库 |
| `save` | 构建本机架构并导出 tar，供离线/无外网 NAS 导入 |

```bash
# Linux / macOS / NAS
./scripts/build-push.sh login      # 登录
./scripts/build-push.sh push       # 构建并推送

# Windows（项目根目录）
powershell -ExecutionPolicy Bypass -File scripts\build-push.ps1 login
powershell -ExecutionPolicy Bypass -File scripts\build-push.ps1 push
```

不想用脚本，等价的原始命令：

```bash
docker login -u 你的账号
docker buildx create --name monitor-builder --use       # 首次执行一次
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  --build-arg APP_VERSION=1.0.0 \
  -t 你的仓库地址/music-monitor:latest \
  --push -f monitor/Dockerfile monitor
```

---

## 3. 在 NAS 上拉取

NAS 上只需要 `docker-compose.yml` 和 `.env` 两个文件，`.env` 里 `MONITOR_IMAGE` 填同一个地址：

```bash
docker compose pull
docker compose up -d
```

public 仓库不需要登录；private 仓库在 NAS 上也要先 `docker login`。

---

## 4. Docker Hub 专项说明

Docker Hub 的地址格式最短，但有两个坑：**登录不能再用密码**、**国内直连经常超时**。

### 4.1 准备账号与 Access Token

- 注册/登录 https://hub.docker.com ，记下你的 **Docker ID**（不是邮箱，也不是昵称）。
- 打开 https://hub.docker.com/settings/security → **New Access Token**，
  描述随便填（如 `music-monitor-push`），权限选 **Read & Write**，
  生成后**立刻复制**（只显示一次，关掉就再也看不到）。
- 这个 Token 就是 `docker login` 要输入的密码。用网页登录密码会直接报错——
  Docker 从 2024 年起已禁用密码登录。

### 4.2 写出镜像地址

Docker Hub 的地址**不带主机名**，第一段就是你的 Docker ID 或组织名：

```ini
MONITOR_IMAGE=你的DockerID/music-monitor:latest
```

### 4.3 登录并推送

```bash
./scripts/build-push.sh login      # 用户名填 Docker ID，密码填 Access Token
./scripts/build-push.sh push
```

> **仓库是自动创建的**：第一次 `push` 成功时，Docker Hub 会自动建好
> `你的DockerID/music-monitor` 这个仓库，默认 **public**。
> 想改成 private，进仓库页 Settings → Visibility 切换
> （免费账号总共只能有 **1 个** private，public 不限）。

### 4.4 免费额度与限流

| 项目 | 免费账号 |
|---|---|
| 私有仓库数量 | 1 个 |
| 拉取限流（未登录） | 每 IP / 6 小时 100 次 |
| 拉取限流（已登录） | 每账号 / 6 小时 200 次 |

个人自用完全够。真撞到限流，就在 NAS 上 `docker login` 一次（按账号计额度，比按 IP 宽松），
或者改用云厂商的免费个人版镜像服务。

### 4.5 国内加速（拉取慢必看）

`push` 走上行一般没问题，但 **`pull` 经常超时**。在 NAS / 构建机的
`/etc/docker/daemon.json` 里加加速地址：

```json
{
  "registry-mirrors": ["https://<你的ID>.mirror.aliyuncs.com"]
}
```

阿里云的个人专属加速地址在**容器镜像服务控制台 → 镜像工具 → 镜像加速器**里，免费。
改完重启 Docker：

```bash
sudo systemctl restart docker
# 群晖/威联通在「容器」套件设置里改，或 SSH 进来执行上一行
```

> 加速器只代理**拉取** `docker.io` 的公共镜像，不加速 `push`，也不代理 private 仓库。

### 4.6 本机推不上去？交给 GitHub Actions 代推

有些网络环境下 `registry-1.docker.io`、`auth.docker.io`、`hub.docker.com` **全部超时**，
`docker login` 直接卡住，`docker push` 更不可能成功。
注意：加速器只代理拉取，**换多少个加速器都解决不了推送**。

这种情况最省事的办法是让 GitHub 的 runner 帮你推——runner 在境外，没有被墙的问题。

仓库里已经放好了现成的 workflow：**`.github/workflows/docker-publish.yml`**，
推代码到 GitHub 后会自动跑。你只需要补两样东西：

**第一步：生成 Docker Hub Access Token**

https://hub.docker.com/settings/security → New Access Token → 权限 **Read & Write** → 复制。

**第二步：在 GitHub 仓库里填两个 Secret**

仓库页 → **Settings → Secrets and variables → Actions → New repository secret**：

| Secret 名称 | 填什么 |
|---|---|
| `DOCKERHUB_USERNAME` | 你的 **Docker ID**（不是邮箱、不是昵称） |
| `DOCKERHUB_TOKEN` | 上一步复制的 Access Token |

**第三步：触发构建**

- push 到 `main` 分支 → 自动构建，推 `:latest` 和 `:sha-xxxxxxx`
- 推 `v1.2.3` 形式的 tag → 额外推 `:1.2.3` 和 `:1.2`
- 也可以去仓库的 **Actions** 页点 **Run workflow** 手动触发

构建好的镜像地址就是：

```ini
MONITOR_IMAGE=你的DockerID/music-monitor:latest
```

它同时构建 `linux/amd64` 和 `linux/arm64`，x86 与 ARM 的 NAS 都能直接拉。

> 为什么 workflow 里写了 `cache-from: type=gha`：
> 用了 GitHub Actions 自己的缓存，第二次构建不用重下 pip 包，快很多，不占 Docker Hub 的存储。

**检查结果**：Actions 页里那次 run 变绿后，去 https://hub.docker.com/r/你的DockerID/music-monitor/tags
看有没有新 tag。失败的话点进 run 看日志，常见原因是 Secret 名字拼错或 Token 权限选了只读。

---

## 5. 云厂商镜像服务要点

阿里云 ACR / 腾讯云 TCR / 华为云 SWR 都有**免费个人版**，共同点：

1. 先去控制台建一个「命名空间」（namespace），个人版一个账号通常能建 1~3 个。
2. 命名空间下**不需要**预先建仓库，第一次 `push` 会自动创建（和 Docker Hub 一样）。
3. 登录用控制台里设的**镜像仓库密码**，不是云账号登录密码：
   ```bash
   docker login registry.cn-hangzhou.aliyuncs.com
   ```
4. 地址里的域名就是仓库主机名，能直接看出用的是哪家。

个人版通常对**拉取**不限速也不限次，这也是国内比 Docker Hub 省心的地方。

---

## 6. 私有 registry 要点

局域网自建最简单的方式是在 NAS 上再跑一个官方 registry 容器：

```yaml
registry:
  image: registry:2
  ports:
    - "5000:5000"
  volumes:
    - ./registry-data:/var/lib/docker/registry
  restart: unless-stopped
```

然后 `docker push 192.168.1.10:5000/music-monitor:latest`。

⚠️ 两个必须注意的点：

- **默认没有任何认证**，谁连上 5000 端口都能推镜像。只在纯内网用，别暴露到公网。
- 客户端默认只信任 HTTPS，用 HTTP 的私有 registry 需要在
  `/etc/docker/daemon.json` 里加 `"insecure-registries": ["192.168.1.10:5000"]`。

要给外网或多台机器用，就得自己再套一层 HTTPS 反代和认证，成本比直接用云厂商高。

---

## 7. 常见问题

**Q：`docker login` 输了密码但屏幕上什么都不显示，是卡住了吗？**
不是。Docker **不回显密码**——没有星号、光标也不动，这是正常的，粘进去直接回车即可。
另外 Docker Hub 的密码栏**必须填 Access Token**，账号登录密码从 2024 年起已被禁用，填密码会报
`unauthorized: incorrect username or password`。

**Q：NAS 上 `docker login` 报 `error storing credentials`？**
容器里没装凭据助手。编辑 `~/.docker/config.json`，把 `credsStore` 那一行**整行删掉**
（之后凭据会以明文 base64 存在这个文件里，注意 `chmod 600`），或者装上 `gnome-keyring` /
`pass` 之类的助手。删掉 `credsStore` 后重新 `docker login` 即可。

**Q：登录信息要不要写进 `.env`？**
**不要**。`.env` 是明文、会被备份、也容易误提交。`docker login` 会自动把凭据存到本地
（Linux/NAS 是 `~/.docker/config.json`，Docker Desktop 是系统凭据管理器），
之后 `docker push` / `docker pull` 直接读取，不需要再填第二次。想清除就 `docker logout`。

**Q：push 报 `no matching manifest for linux/arm64`？**
镜像架构和 NAS 不匹配。见 [deploy.md 的架构章节](deploy.md#架构必须匹配最常见的翻车点)。

**Q：push 报 `denied: requested access to the resource is denied`？**
三种可能：没登录 / token 权限不够（要 Read & Write）/ 地址里的命名空间不是你的。

**Q：我能在 Windows / Mac 上装 Docker Desktop 来构建镜像吗？**
可以。构建机不需要是 NAS，装了 Docker Desktop 的电脑一样能构建并推送。

- **要求**：Windows 10 21H2+ 或 Windows 11（64 位）、CPU 支持虚拟化且 BIOS 里已开启、内存 ≥4 GB。
  Windows **家庭版**走 **WSL2** 后端，先在管理员 PowerShell 里执行 `wsl --install` 然后重启。
- **授权**：Docker Desktop 对个人使用、学习、小企业**免费**。
  需要付费订阅的是「员工超过 250 人 **或** 年营收超过 1000 万美元」的企业。
- **不想用 Docker Desktop**：可以换 **Rancher Desktop** 或 **Podman Desktop**，都是开源、无授权限制。
- **架构**：WSL2 后端构建出来的是 `linux/amd64`。NAS 若是 arm64，需要多架构构建，
  见 [deploy.md 的架构章节](deploy.md#架构必须匹配最常见的翻车点)。

**Q：装了 Docker Desktop 之后，每次都要开着吗？**
不用。`docker` 命令依赖守护进程，构建/推送时必须启动 Docker Desktop；
用完可以在托盘图标右键退出，并在设置里关掉「开机自启动」，避免常驻占 1~2 GB 内存。
