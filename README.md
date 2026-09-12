# music-monitor

基于 [go-music-dl](https://github.com/guohuiyuan/go-music-dl) 的音乐榜单、歌单和收藏夹监控下载服务。

它会定时检查你关注的内容，发现新歌曲后自动下载，并在下载前检查歌曲版本和音源，尽量避免下载试听片段、现场版、演唱会版、DJ 版或 Remix 版本。

## 主要功能

- 监控热门榜单
- 监控指定歌单
- 监控个人收藏夹和「我喜欢的音乐」
- 发现新歌曲后自动下载
- 支持网易云、QQ 音乐、酷狗、酷我、咪咕、汽水、哔哩哔哩等平台
- 下载前检查音源是否可用、码率是否满足要求
- 正式版优先，改编版仅在正式版不可用时作为兜底
- 过滤试听、Live、现场、演唱会、跨年、DJ、混音、Remix 等版本
- 失败自动重试，逐首等待上游结果，避免一次提交整批任务
- 监控记录、下载记录和平台登录状态持久化保存

## 工作方式

项目由两个容器组成：

```text
music-monitor（监控服务，9090）
        │
        │ 容器内网络 HTTP
        ▼
 go-music-dl（下载引擎，8085）
        │
        ▼
 宿主机音乐目录
```

### music-monitor

负责：

- 定时读取榜单、歌单和收藏夹
- 判断哪些歌曲是新增歌曲
- 检查正式版、时长和可用音源
- 选择合适的下载来源
- 调用 go-music-dl 下载
- 记录成功、失败和跳过原因

### go-music-dl

负责：

- 搜索和解析音乐平台
- 获取音源
- 保存音乐文件
- 管理 Cookie 和平台登录状态
- 管理下载记录和文件名格式

## 部署

### 使用 Docker Compose

准备以下目录：

```text
music-monitor/
├── docker-compose.yml
├── config/
│   ├── engine/
│   └── monitor/
└── data/
    └── downloads/
```

创建目录并设置权限：

```bash
mkdir -p config/engine config/monitor data/downloads
chmod -R 777 config data
```

启动：

```bash
docker compose up -d
```

查看状态：

```bash
docker compose ps
```

查看日志：

```bash
docker compose logs -f
```

如果设备使用旧版 Compose 命令，把 `docker compose` 换成 `docker-compose`。

### 飞牛 NAS

下面是一份适合飞牛 fnOS 的完整配置。它将配置文件保存在 Compose 项目目录，把音乐文件保存到机械硬盘，并让 monitor 以只读方式检查音乐文件是否仍然存在。

在飞牛上创建目录，例如：

```text
/vol1/docker/music-monitor/
```

在该目录创建 `docker-compose.yml`，直接复制以下内容：

```yaml
# 飞牛 fnOS 部署版

services:
  music-dl:
    image: docker.1ms.run/guohuiyuan/go-music-dl:latest
    container_name: go-music-dl
    restart: unless-stopped
    ports:
      - "8085:8080"
    volumes:
      - ./config/engine:/home/appuser/data
      - "/vol2/1000/机械硬盘/#media/downloads/Music:/home/appuser/data/downloads"
    environment:
      - TZ=Asia/Shanghai
    user: "1000:1000"

  monitor:
    image: docker.1ms.run/baey666/music-monitor:v1.0.6
    container_name: music-monitor
    restart: unless-stopped
    ports:
      - "9099:9090"
    volumes:
      - ./config/monitor:/app/data
      - "/vol2/1000/机械硬盘/#media/downloads/Music:/downloads:ro"
    environment:
      - ENGINE_URL=http://music-dl:8080
      - ENGINE_PREFIX=/music
      - TICK_SECONDS=60
      - DOWNLOAD_CONCURRENCY=1
      - DOWNLOAD_RETRIES=2
      - DEFAULT_QUALITY=lossless
      - TZ=Asia/Shanghai
    depends_on:
      - music-dl
    user: "1000:1000"
```

注意：上面配置中的 `/vol2/1000/机械硬盘/#media/downloads/Music` 是示例路径，请改成飞牛上实际的音乐目录，并在两个服务的挂载项中保持一致。`monitor` 的 `:ro` 表示只读，不会修改或删除音乐文件。

在 Compose 项目目录执行：

```bash
cd /vol1/docker/music-monitor
mkdir -p config/engine config/monitor
chmod -R 777 config
docker compose pull
docker compose up -d --force-recreate
docker compose ps
```

如果设备使用旧版 Compose 命令，把 `docker compose` 换成 `docker-compose`。

更新已经部署的服务时，执行：

```bash
docker compose pull monitor
docker compose up -d --force-recreate monitor
```

网页顶部显示的版本号应为 `v1.0.6`。如果 `9099` 端口已被占用，把左侧宿主机端口改为其他端口，例如：

```yaml
ports:
  - "9091:9090"
```

右侧容器端口 `9090` 不要修改。修改后访问：

```text
http://飞牛IP:9091
```

## 访问地址

假设 NAS 地址是 `192.168.10.88`：

```text
音乐下载引擎：http://192.168.10.88:8085
监控控制台：  http://192.168.10.88:9090
```

如果你修改了监控宿主机端口，例如改为 `9091`，就访问 `9091`。

## 首次初始化

### 1. 初始化 go-music-dl 管理员

打开：

```text
http://NAS-IP:8085/music/setup
```

初始化令牌从日志中查看：

```bash
docker compose logs go-music-dl | grep "Web setup token"
```

使用令牌进入初始化页面，然后自行设置管理员用户名和密码。

### 2. 设置下载目录

在 go-music-dl 设置中，把本地下载目录设置为：

```text
data/downloads
```

不要填写 NAS 宿主机路径。

### 3. 登录音乐平台

在 go-music-dl 中扫码登录需要使用的平台。登录状态会保存到：

```text
config/engine/cookies.json
```

是否有会员 Cookie，会影响可获取的音质和歌曲范围。

### 4. 创建监控

打开：

```text
http://NAS-IP:9090
```

然后：

1. 进入「热门榜单」或「歌单 / 收藏夹」
2. 选择平台和目标内容
3. 先点击预览，确认歌曲列表正确
4. 创建监控
5. 按需要打开自动下载

## 配置和数据路径

默认 Compose 挂载如下：

```yaml
volumes:
  - ./config/engine:/home/appuser/data
  - ./data/downloads:/home/appuser/data/downloads
  - ./config/monitor:/app/data
```

对应关系：

| 内容 | 宿主机位置 | 容器内位置 |
|---|---|---|
| go-music-dl 设置和 Cookie | `config/engine/` | `/home/appuser/data/` |
| 监控数据库 | `config/monitor/` | `/app/data/` |
| 音乐文件 | `data/downloads/` | `/home/appuser/data/downloads/` |

想把音乐放到其他硬盘，只修改左侧宿主机路径，例如：

```yaml
volumes:
  - ./config/engine:/home/appuser/data
  - "/vol2/1000/机械硬盘/#media/downloads/Music:/home/appuser/data/downloads"
  - ./config/monitor:/app/data
```

程序中的下载目录仍然填写：

```text
data/downloads
```

不要把宿主机的 `/vol2/.../Music` 填到 go-music-dl 的下载目录设置中。

## 下载版本规则

下载时按以下顺序选择：

```text
1. 优先监控内容中的原始歌曲
2. 优先歌名、歌手和时长匹配的正式版本
3. 原平台不可用时，寻找其他平台的正式版本
4. 只要正式版本可用，就不下载 Live、演唱会、DJ、Remix 等改编版
5. 正式版本全部不可用时，改编版才可以作为兜底
6. 试听片段始终不下载
```

歌曲和候选版本的时长差异过大时，也会被跳过。

## 音质说明

go-music-dl 的下载接口不能强制绕过平台权限。实际音质取决于：

- 平台账号是否登录
- Cookie 是否有效
- 是否拥有对应会员权限
- 歌曲是否存在版权限制
- 平台实际返回的音源

监控服务会在下载前检查码率和格式，但不能绕过 VIP 或版权限制。

推荐在 `.env` 中使用：

```env
DEFAULT_QUALITY=lossless
DOWNLOAD_CONCURRENCY=1
DOWNLOAD_RETRIES=2
```

## 失败和漏下载处理

每首歌曲会：

1. 单独检查音源
2. 单独提交下载
3. 等待上游返回结果
4. 失败后自动重试
5. 确认保存成功后才记录为已下载

失败的歌曲会记录在监控页面中，可以在后续重新执行或重试。

查看日志：

```bash
docker compose logs -f monitor
```

查看引擎日志：

```bash
docker compose logs -f go-music-dl
```

## 常用操作

```bash
# 查看容器状态
docker compose ps

# 查看日志
docker compose logs -f monitor go-music-dl

# 重启监控服务
docker compose restart monitor

# 停止服务
docker compose down

# 更新远程镜像
docker compose pull
docker compose up -d

# 备份配置、Cookie 和监控记录
tar czf config-backup.tar.gz config/
```

## 注意事项

- `config/engine/cookies.json` 包含平台登录状态，不要公开或提交到 Git
- `config/` 和 `data/` 需要保持持久化，否则删除容器后会丢失设置和记录
- SQLite 数据库必须挂载目录，不要只挂载单个 `.db` 文件
- 容器内固定路径不要修改，只修改 Compose 左侧的宿主机路径
- Apple Music 通常只能获取试听片段，不适合作为完整音乐来源
- Spotify 不在当前支持范围内
