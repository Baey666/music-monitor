# music-monitor

在 [go-music-dl](https://github.com/guohuiyuan/go-music-dl) 之上加了一层**「榜单 / 歌单 / 收藏夹」监控 → 自动下载**的编排服务。

go-music-dl 本身是一个「搜索 → 下载」的工具：你得先找到歌，它才下载。
这个项目补的是前半段——**让歌自己找上门**：

- 持续盯着各平台的热门榜单，一有新歌就自动下载；
- 盯着你指定的歌单链接，歌单更新了自动补齐；
- 盯着你自己的收藏夹 / 我喜欢的音乐，收藏一首自动落盘；
- 下载前会**探测真实码率**，不达标就去别的平台找同曲目的更好版本。

---

## 目录结构

```
music-monitor/
├── docker-compose.yml      # 两个服务：引擎 + 监控
├── .env.example            # 端口 / 音质 / 并发 / 镜像仓库地址
├── scripts/                # 镜像构建与推送脚本
│   ├── build-push.sh       #   Linux / macOS / NAS
│   └── build-push.ps1      #   Windows PowerShell
├── data/                   # 宿主机数据目录（引擎和下载产物都在这）
│   ├── downloads/          #   ← 下载下来的音乐
│   └── monitor/            #   ← 监控自己的 SQLite（监控配置、曲目记录）
└── monitor/                # 本项目新增的服务
    ├── Dockerfile
    ├── .dockerignore
    ├── requirements.txt
    ├── app/
    │   ├── main.py         # FastAPI 入口 + 生命周期
    │   ├── api.py          # REST 接口
    │   ├── runtime.py      # 共享运行时对象
    │   ├── config.py       # 环境变量配置
    │   ├── db.py           # SQLite（监控 / 曲目 / 运行记录）
    │   ├── engine.py       # go-music-dl 客户端
    │   ├── parser.py       # 解析引擎返回的 HTML 页面
    │   ├── charts.py       # 内置榜单注册表
    │   ├── quality.py      # 音质等级与择优策略
    │   ├── pipeline.py     # 核心：发现 → 去重 → 择优 → 下载
    │   └── scheduler.py    # 轮询调度器
    ├── tests/              # 离线端到端测试（不进镜像）
    └── web/                # 零构建前端（原生 JS 单页）
```

---

## 架构

```
        ┌──────────────────────────────────────────────┐
        │  monitor （本项目，端口 9090）                 │
        │  · 定时轮询榜单 / 歌单 / 收藏夹                 │
        │  · 增量比对，只处理新出现的歌                   │
        │  · /inspect 探测码率，不达标则跨平台找更好的版本  │
        │  · 触发引擎下载，记录结果                       │
        └───────────────┬──────────────────────────────┘
                        │ HTTP（容器内网）
                        ▼
        ┌──────────────────────────────────────────────┐
        │  music-dl （官方镜像，端口 8085）              │
        │  · 多平台搜索 / 歌单解析 / 换源                 │
        │  · 按平台 Cookie 的会员等级取最佳音质           │
        │  · 落盘 + 去重 + 文件名模板 + 可选 WebDAV 上传   │
        └───────────────┬──────────────────────────────┘
                        ▼
                  ./data/downloads/
```

**为什么不让监控服务自己下载？**
因为音质、去重、文件名模板、WebDAV 这些能力上游已经做好了，重复实现只会更差。
监控服务只做上游没有的那部分：**榜单发现 + 增量比对 + 音质择优**。

---

## 快速开始

### 0. 前置

- Docker + Docker Compose
- 宿主机终端能执行 `mkdir` / `chmod`

### 1. 初始化数据目录

官方镜像以 `uid=1000` 运行，数据目录必须可写，否则容器起不来或写不进文件。

```bash
cd music-monitor
mkdir -p data/downloads data/monitor
chmod -R 777 data
```

> 如果你不用 1000 这个 uid，请同步修改 `docker-compose.yml` 里两个服务的 `user:`。

### 2.（可选但推荐）配置 .env

```bash
cp .env.example .env
```

关键项：

| 变量 | 默认 | 说明 |
|---|---|---|
| `ENGINE_PORT` | 8085 | go-music-dl 网页端口 |
| `MONITOR_PORT` | 9090 | 本监控控制台端口 |
| `TICK_SECONDS` | 60 | 调度心跳，决定「最快多久发现一次新歌」 |
| `DOWNLOAD_CONCURRENCY` | 3 | 单监控并发下载数 |
| `DEFAULT_QUALITY` | lossless | 默认目标音质 |

### 3. 启动

```bash
docker compose up -d
docker compose logs -f monitor      # 看监控服务日志
```

打开控制台：`http://<NAS-IP>:9090`

### 4. 初始化 go-music-dl（必须做一次）

打开 `http://<NAS-IP>:8085`：

1. **创建管理员账号**：初始化令牌在容器日志里——
   ```bash
   docker compose logs go-music-dl | grep "Web setup token"
   ```
2. **设置本地下载目录**为 `data/downloads`（设置面板里选「自定义目录」）。
   这样下载产物会落到宿主机 `./data/downloads/`。
3. **扫码登录有会员的平台**（网易云 / QQ / 酷狗 / Bilibili）。这一步决定你能拿到什么音质。
4. 建议打开 **「自动选择无效音源批量换源」**。

### 5. 回到 9090 建第一个监控

控制台 →「热门榜单」→ 选平台 → 点某榜单的「预览曲目」→ 确认有曲目 → 「创建监控」。

---

## 部署与发布（镜像怎么上传 / 怎么发到 NAS）

**先明确一点**：只有 `monitor` 这一个镜像需要你自己构建。
引擎用的是官方镜像 `guohuiyuan/go-music-dl`，不需要你构建，也不需要你上传。

按你的场景选一种，从简单到复杂排列：

### 方案 A：整目录拷到 NAS，在 NAS 上就地构建（最省事，推荐）

不需要任何镜像仓库，不需要管 CPU 架构。

```bash
# 1. 把 music-monitor 整个文件夹拷到 NAS（SMB 共享 / scp / U 盘都行）
#    Windows 直接拖进 NAS 共享目录即可

# 2. 在 NAS 的 SSH 里执行
cd /vol1/1000/docker/music-monitor          # 换成你的实际路径
mkdir -p data/downloads data/monitor
chmod -R 777 data
docker compose up -d --build                # 关键：--build
docker compose logs -f monitor
```

- 优点：NAS 自己编译出**本机架构**的镜像，不存在架构不匹配问题；改动配置后 `--build` 重跑即可。
- 前置：NAS 能访问外网（拉 `python:3.12-slim` 基础镜像 + pip 装依赖）。
  国内网络建议先给 Docker 配镜像加速器（见下方「国内网络注意事项」）。

### 方案 B：构建镜像推到仓库，NAS 只拉镜像（多台机器 / 长期维护推荐）

需要一个镜像仓库。国内用云厂商的通常最省心；Docker Hub 也能用，但要配镜像加速，见下面的专项说明。

| 仓库 | 地址格式 |
|---|---|
| 阿里云 ACR | `registry.cn-hangzhou.aliyuncs.com/你的命名空间/music-monitor:latest` |
| 腾讯云 TCR | `ccr.ccs.tencentyun.com/你的命名空间/music-monitor:latest` |
| 华为云 SWR | `swr.cn-north-4.myhuaweicloud.com/你的命名空间/music-monitor:latest` |
| Docker Hub | `你的DockerID/music-monitor:latest`（不带主机名） |
| 私有 registry | `192.168.1.10:5000/music-monitor:latest` |

**第 1 步：在构建机上改 `.env`**

```bash
cp .env.example .env
```
把 `MONITOR_IMAGE` 改成你的仓库地址：
```ini
MONITOR_IMAGE=registry.cn-hangzhou.aliyuncs.com/yournamespace/music-monitor:latest
```
并确认 `PLATFORMS`（默认 `linux/amd64,linux/arm64`，两个架构都构建）。

**第 2 步：登录仓库**

```bash
docker login registry.cn-hangzhou.aliyuncs.com
```

**第 3 步：构建并推送**

```bash
# Windows（在项目根目录）
powershell -ExecutionPolicy Bypass -File scripts\build-push.ps1 push

# Linux / macOS / NAS
chmod +x scripts/build-push.sh
./scripts/build-push.sh push
```

不想用脚本，等价的原始命令是：

```bash
docker buildx create --name monitor-builder --use       # 首次执行一次
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  --build-arg APP_VERSION=1.0.0 \
  -t registry.cn-hangzhou.aliyuncs.com/yournamespace/music-monitor:latest \
  --push -f monitor/Dockerfile monitor
```

**第 4 步：在 NAS 上部署**

NAS 上只需要这两个文件（不用拷 `monitor/` 源码目录）：

```
你的部署目录/
├── docker-compose.yml
└── .env              # MONITOR_IMAGE 填同一个仓库地址
```

```bash
docker login registry.cn-hangzhou.aliyuncs.com    # 私有仓库需要
docker compose pull
docker compose up -d
```

> compose 文件里 `image:` 和 `build:` 同时存在时：`up -d --build` 走本地构建，
> `pull && up -d` 直接用远端镜像。想彻底禁止在 NAS 上构建，把 `monitor` 服务里的
> `build:` 三行删掉即可（保留 `image:`）。

#### 如果你用 Docker Hub 做仓库（专项说明）

Docker Hub 的地址格式最短，但有两个坑：**登录不能再用密码**、**国内直连经常超时**。

**1. 准备账号与 Access Token**

- 注册/登录 https://hub.docker.com ，记下你的 **Docker ID**（不是邮箱，也不是昵称）。
- 打开 https://hub.docker.com/settings/security → **New Access Token**，
  描述随便填（如 `music-monitor-push`），权限选 **Read & Write**，
  生成后**立刻复制**（只显示一次，关掉就再也看不到）。
- 这个 Token 就是下面 `docker login` 要输入的密码。用网页登录密码会直接报错——
  Docker 从 2024 年起已禁用密码登录。

**2. 写出镜像地址**

Docker Hub 的地址**不带主机名**，第一段就是你的 Docker ID 或组织名：

```ini
MONITOR_IMAGE=你的DockerID/music-monitor:latest
```

**3. 登录并推送**

```bash
# Linux / macOS / NAS
./scripts/build-push.sh login      # 用户名填 Docker ID，密码填 Access Token
./scripts/build-push.sh push

# Windows（项目根目录）
powershell -ExecutionPolicy Bypass -File scripts\build-push.ps1 login
powershell -ExecutionPolicy Bypass -File scripts\build-push.ps1 push
```

不想用脚本，等价的原始命令：

```bash
docker login -u 你的DockerID                        # 密码填 Access Token
docker buildx create --name monitor-builder --use   # 首次执行一次
docker buildx build --platform linux/amd64,linux/arm64 \
  --build-arg APP_VERSION=1.0.0 \
  -t 你的DockerID/music-monitor:latest \
  --push -f monitor/Dockerfile monitor
```

> **仓库是自动创建的**：第一次 `push` 成功时，Docker Hub 会自动建好
> `你的DockerID/music-monitor` 这个仓库，默认 **public**。
> 想改成 private，进仓库页 Settings → Visibility 切换
> （免费账号总共只能有 **1 个** private，public 不限）。

**4. 在 NAS 上拉取**

```bash
docker pull 你的DockerID/music-monitor:latest
# 并让 NAS 上的 .env 里 MONITOR_IMAGE 填同一个地址
docker compose pull && docker compose up -d
```

public 仓库不需要登录；private 仓库在 NAS 上也要先 `docker login`。

**5. 免费额度与限流**

| 项目 | 免费账号 |
|---|---|
| 私有仓库数量 | 1 个 |
| 拉取限流（未登录） | 每 IP / 6 小时 100 次 |
| 拉取限流（已登录） | 每账号 / 6 小时 200 次 |

个人自用完全够。真撞到限流，就在 NAS 上 `docker login` 一次（按账号计额度，比按 IP 宽松），
或者改用云厂商的免费个人版镜像服务。

**6. 国内加速（拉取慢必看）**

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

### 方案 C：NAS 完全不能上网 → 导出镜像文件离线导入

```bash
# 在能上网的机器上：构建 + 导出（脚本自带 save 子命令）
./scripts/build-push.sh save            # 生成 music-monitor.tar

# 引擎镜像也要一起导出，否则 NAS 上拉不到
docker pull guohuiyuan/go-music-dl:latest
docker save guohuiyuan/go-music-dl:latest -o go-music-dl.tar

# 把两个 tar 和 docker-compose.yml / .env 拷到 NAS
docker load -i go-music-dl.tar
docker load -i music-monitor.tar
docker compose up -d --no-build         # --no-build 确保只用导入的镜像
```

### 架构必须匹配（最常见的翻车点）

镜像平台和 NAS CPU 架构不一致，启动时会报 `no matching manifest for linux/arm64` 之类的错。

先在 NAS 上确认架构：

```bash
uname -m      # x86_64 → amd64 ；aarch64 / armv8 → arm64
```

- **方案 A** 不用管（在 NAS 上构建就是 NAS 的架构）。
- **方案 B** 若构建机是 x86_64 而 NAS 是 arm64，必须多架构构建（脚本默认已带 `--platform linux/amd64,linux/arm64`）。
  多架构需要 QEMU 模拟，第一次会提示安装：`docker run --privileged --rm tonistiigi/binfmt --install all`
  （Docker Desktop 已内置，无需手动装）。
- 只给自己一台机器用，直接把 `PLATFORMS` 改成单一架构，构建快很多：
  ```ini
  PLATFORMS=linux/amd64
  ```

验证推送上去的镜像有哪些架构：

```bash
docker manifest inspect registry.cn-hangzhou.aliyuncs.com/yournamespace/music-monitor:latest
```

### 国内网络注意事项

拉取/推送慢或超时，给 Docker 守护进程配镜像加速（NAS 上编辑 `/etc/docker/daemon.json`）：

```json
{
  "registry-mirrors": [
    "https://docker.m.daocloud.io",
    "https://dockerproxy.com",
    "https://mirror.ccs.tencentyun.com"
  ]
}
```
改完 `systemctl restart docker`（飞牛 / 群晖在 Docker 应用设置里改）。

pip 装依赖慢可以在 `monitor/Dockerfile` 的 `pip install` 前加一行换源：

```dockerfile
RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple
```

### 安全提示

镜像里**只有代码，不含任何音乐文件、Cookie、账号配置**——这些全在挂载出来的 `./data/` 里。
所以：

- 把镜像推到公开仓库是安全的；但**不要把 `data/` 目录提交到 Git 或打进镜像**（`.gitignore` 已排除，`monitor/.dockerignore` 也已排除）。
- 如果打算公开分享镜像，请注意上游 go-music-dl 是 **AGPL-3.0**：本项目只通过 HTTP 调用它、没有链接其代码，
  但再分发的合规性请自行确认。

### 更新已部署的实例

```bash
# 方案 A（NAS 就地构建）
git pull   # 或重新拷贝代码
docker compose up -d --build

# 方案 B（拉新镜像）
docker compose pull && docker compose up -d

# 只想重启监控服务（改完 .env 后）
docker compose up -d monitor
```

升级前建议备份监控数据：`cp data/monitor/monitor.db ~/monitor-backup.db`

---

## 音质是怎么工作的（务必读）

**go-music-dl 的下载接口没有 `quality` 参数**，实际音质由**该平台账号 Cookie 的会员等级**决定。
所以本项目不做「指定音质参数」这种假装能绕过会员的事，而是做**择优 + 校验**：

```
榜单给到曲目 (source=netease, id=xxx)
        │
        ├─ /inspect 探测真实码率 + 体积
        │     达标 → 直接下载
        │
        ├─ 不达标 → /switch_source 让引擎在其它平台找最接近的版本（相似度+时长+可播放校验）
        │     │  达标 → 换源下载
        │
        ├─ 还不达标 → 用「歌名 + 歌手」在允许的平台里搜索，逐个探测候选
        │
        └─ 全都不达标 → 按每个监控的策略处理：
              best_effort：取所有候选里码率最高的那个，音质降级但不丢歌
              skip        ：不下载，记为「未达音质」，等升级 Cookie 后重试
```

| 等级 | 判据 | 现实预期 |
|---|---|---|
| `standard` | ≥ 96 kbps | 无会员也能拿到 |
| `high` | ≥ 256 kbps | 多数源可拿到 |
| `lossless` | ≥ 700 kbps，或扩展名为 flac/ape/wav | **需要会员 Cookie**，网易云/QQ/酷狗/Bilibili 支持 |
| `hires` | ≥ 1400 kbps | 极少见，基本都会降级 |

> 曲目记录里会写明**实际拿到的音质**（例如 `无损 FLAC 985kbps`、`MP3 320kbps`），
> 不会被「目标音质」的标签糊弄过去。

想批量写入会员 Cookie：控制台 →「设置」→ 填引擎管理员账号并登录 →「代写平台 Cookie」粘贴 JSON。

---

## 三种监控类型

### 1. 热门榜单（chart）

可多选平台与榜单。内置清单见 `monitor/app/charts.py`。

| 平台 | 榜单 | 置信度 |
|---|---|---|
| 网易云 | 飙升榜 / 新歌榜 / 热歌榜 / 原创榜 | 高（官方歌单 ID，直接解析） |
| 网易云 | 欧美热歌榜 / 韩语榜 / 日语榜 | 中（ID 可能变动，界面可「校验」） |
| QQ 音乐 | 热歌榜 / 新歌榜 / 飙升榜 / 流行指数 / 内地 / 港台 / 欧美 / 日本 / 韩国 | 中（用榜单页链接交给引擎识别） |
| 酷狗 | TOP500 / 飙升榜 / 新歌榜 | 中 |
| 酷我 | 热歌榜 | 低（需自行确认） |
| Apple Music | Top 100: Global / 中国大陆 | 中（**只能下 preview 试听片段**） |
| JOOX | 排行榜 | 中 |

**榜单会因为平台改版失效**，这是行业常态，不是 bug。界面提供了「校验」按钮：
- 校验通过的榜单会显示 `N 首`；
- 失效的会显示红色原因，删掉或换成自定义榜单即可；
- 也可以随时用「自定义榜单」粘贴任意歌单链接（引擎支持解析的平台都行）。

### 2. 指定歌单（playlist）

粘贴歌单链接（支持多行），引擎会自动识别来源平台。歌单更新后，新增曲目会被自动下载。

### 3. 个人收藏夹（favorites）

先到「歌单 / 收藏夹」页面勾选平台 → 「读取我的收藏」，会列出你在该平台账号下的歌单与收藏夹
（网易云 / QQ / 酷狗 / 汽水支持；QQ 的「我喜欢的歌曲」也在其中）。
勾选要跟踪的，一键创建监控。

---

## 与引擎的接口对照

监控服务对上游的依赖（都是公开接口，无需登录）：

| 用途 | 上游接口 | 说明 |
|---|---|---|
| 健康检查 | `GET /music/healthz` | |
| 平台清单 | `GET /music/` | 解析「搜索源设置」里的能力标记 |
| 搜索 | `GET /music/search?q=&type=song\|playlist\|album&sources=` | 返回 HTML，按 `data-*` 属性解析 |
| 歌单曲目 | `GET /music/playlist?id=&source=` | 同上 |
| 个人歌单 | `GET /music/user_playlists?sources=` | 需引擎侧已配置 Cookie |
| 音质探测 | `GET /music/inspect?id=&source=&duration=` | 返回 `{valid,url,size,bitrate}` |
| 跨平台换源 | `GET /music/switch_source?name=&artist=&current=` | 引擎内置相似度 + 时长 + 可播放校验 |
| 去重预检 | `POST /music/api/downloads/precheck` | 引擎的持久化下载指纹 |
| 触发下载 | `POST /music/download?save_local=1&...` | 需 `X-Requested-With: XMLHttpRequest` |
| 平台 Cookie | `GET/POST /music/cookies` | 需要引擎管理员会话 |
| 引擎设置 | `GET/POST /music/settings` | GET 公开，POST 需登录 |

> 上游把搜索类接口做成了服务端渲染的 HTML（它自己的前端也是整页跳转）。
> 本项目在 `parser.py` 里按页面稳定的 `data-*` 属性解析——这些属性是上游自己做批量操作时用的，
> 比按样式或文案解析稳得多。万一上游改版导致解析为空，只需改 `parser.py` 一个文件。

---

## 常用操作

```bash
# 查看日志
docker compose logs -f monitor go-music-dl

# 只重启监控服务（改完配置后）
docker compose up -d --build monitor

# 停掉
docker compose down

# 备份监控配置与记录
cp data/monitor/monitor.db ~/monitor-backup.db

# 登录镜像仓库（默认 Docker Hub，密码填 Access Token）
./scripts/build-push.sh login

# 构建并推送镜像（多架构）
./scripts/build-push.sh push
```

数据说明：
- `data/monitor/monitor.db` —— 监控配置、曲目记录、运行历史（删掉会重置所有监控）
- `data/downloads/` —— 音乐文件（和监控服务解耦，删监控不影响文件）
- `data/settings.db`、`data/cookies.json` —— 引擎的账号、设置与平台登录态

---

## 本地开发与测试

不需要 Docker 也能跑测试：项目自带一个**假的 go-music-dl 引擎**，按上游真实的页面结构返回数据，
因此可以离线验证「解析 → 择优 → 换源 → 下载 → 去重」全链路。

```bash
cd music-monitor
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r monitor/requirements.txt   # Linux/macOS 用 .venv/bin/python
cd monitor && ../.venv/Scripts/python.exe -m tests.smoke_test
```

期望输出 `通过 35 项，失败 0 项`。

测试文件：
- `monitor/tests/stub_engine.py` —— 假引擎（HTML 页面 + JSON 接口）
- `monitor/tests/smoke_test.py` —— 端到端断言

也可以只跑前端联调：

```bash
cd monitor
../.venv/Scripts/python.exe -m uvicorn app.main:app --port 9090   # 需自行设置 ENGINE_URL 指向真实引擎
```

---

## 已知限制

1. **榜单会失效**：平台改版后 ID/链接就可能变。用「校验」发现，用「自定义榜单」修。
2. **无损依赖会员**：没有对应平台会员 Cookie，`lossless` 会自动降级为 MP3/M4A，界面会如实标注。
3. **Apple Music 只能下 preview**：`music-lib` 的能力限制，完整音频需要额外的解密工具，本项目不做。
4. **Spotify 不在支持列表里**：`music-lib` 没有 Spotify 适配器，粘链接也无法解析。
5. **解析依赖 HTML 结构**：见上节说明，改版时改 `parser.py` 即可，逻辑层不受影响。
6. **酷我 / 咪咕 / 千千 / Jamendo 的榜单接入不完整**：这些平台的歌单能力本身就不稳定。

---

## 常见问题

**Q：容器起来后 8085 打不开？**
先看日志：`docker compose logs go-music-dl`。多半是 `data` 目录权限问题，执行 `chmod -R 777 data`。

**Q：监控控制台显示「引擎不可用」？**
docker-compose 里两个服务在同一个 network，`ENGINE_URL` 必须是 `http://music-dl:8080`（容器名:容器内端口），
不是宿主机端口。

**Q：为什么下载的是 128kbps 而不是无损？**
引擎里该平台的 Cookie 没有会员，或者没登录。到 8085 的引擎页面扫码登录后，用「设置 → 代写平台 Cookie」确认，
再对失败/降级的曲目点「重试」。

**Q：会不会重复下载？**
两层去重：监控服务按 `(平台, 歌曲ID)` 与「歌名+歌手指纹」去重；引擎自己还有持久化下载指纹去重
（跨监控共享，不同监控命中同一首歌也不会重复落盘）。

**Q：能多个人一起用吗？**
可以，但要注意：曲目记录是全局的，两个监控抓同一首歌时后一个会被指纹去重跳过——这是刻意的设计。

---

## 免责声明

本项目只是一个自动化编排层，不提供也不托管任何音乐内容。
下载的音源来自第三方平台，请遵守各平台的服务条款与当地法律法规，
仅用于个人学习与备份，**下载的内容请在 24 小时内删除**。
因使用本工具产生的任何后果由使用者自行承担。

---

## 许可证

本项目（监控编排层，即 `monitor/` 与 `scripts/` 部分）以 **MIT License** 发布，详见 [LICENSE](LICENSE)。

上游引擎 [guohuiyuan/go-music-dl](https://github.com/guohuiyuan/go-music-dl) 采用 **AGPL-3.0**。
本项目**未修改、未链接**其代码，仅通过 HTTP 接口在同一 Docker 网络中调用，二者保持独立进程与独立镜像。
