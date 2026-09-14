# 飞牛 NAS 音乐自动化：music-monitor 让榜单、歌单、收藏夹自动下载，彻底解放双手

> 📺 **视频演示**：[《飞牛音乐解放双手》Bilibili](https://www.bilibili.com/video/BV1GTYD6jEyG/)（3 分 14 秒，看完整流程演示）
> 🐙 **开源地址**：[github.com/Baey666/music-monitor](https://github.com/Baey666/music-monitor)（AGPL-3.0，支持 amd64 / arm64）
> 📦 **当前版本**：v1.0.7

## 一、music-monitor 是什么

[music-monitor](https://github.com/Baey666/music-monitor) 是一个跑在 NAS 上的**音乐榜单 / 歌单 / 收藏夹监控自动下载服务**，基于开源项目 [go-music-dl](https://github.com/guohuiyuan/go-music-dl) 构建。

它解决一个很具体的问题：**你关注的内容更新了，但你不想到处手动找、手动下。**

部署好之后，它会定时检查你监控的内容——比如网易云热歌榜、某个歌单、你自己的收藏夹——一旦发现新歌曲，就自动挑选合适的音源下载到 NAS 的音乐目录。之后无论你用飞牛的音乐 App、Emby、Jellyfin 还是手机播放器扫描目录，新歌就在那里。

### 核心功能

- **监控热门榜单**：飙升榜、新歌榜、热歌榜等，每天更新自动追
- **监控指定歌单**：粘贴歌单链接即可，别人更新了歌单，你这边自动跟着下
- **监控个人收藏夹和「我喜欢的音乐」**：在手机上点个红心，NAS 里自动多一首歌
- **增量比对**：只下载新出现的曲目，反复运行不会重复下载
- **版本甄别**：下载前过滤试听片段、Live、现场、演唱会、跨年、DJ 版、Remix 等非正式版本
- **跨平台择优**：原平台音源不可用或码率不达标时，自动去其它平台找歌名、歌手、时长都匹配的正式版
- **失败重试**：逐首等待上游结果，失败自动重试，确认保存成功才记录为已下载
- **支持平台**：网易云、QQ 音乐、酷狗、酷我、咪咕、汽水、哔哩哔哩、千千音乐等

### 工作方式

项目由两个 Docker 容器组成，分工明确：

```text
music-monitor（监控服务，编排与决策）
        │  容器内网 HTTP
        ▼
 go-music-dl（下载引擎，搜索 / 解析 / 落盘）
        │
        ▼
 NAS 音乐目录（飞牛音乐 App / Emby / Jellyfin 直接扫描）
```

- **go-music-dl**：负责搜索解析各音乐平台、获取音源、保存文件、管理平台 Cookie 登录态
- **music-monitor**：负责定时读取榜单 / 歌单 / 收藏夹、判断新增歌曲、校验版本与码率、择优选源、调用引擎下载、记录成功与失败原因

monitor 只做「发现 + 比对 + 择优 + 调度」，下载能力完全复用 go-music-dl，不重复造轮子。

## 二、飞牛 fnOS 部署教程

只需要 Docker Compose，全程不用编译。

### 1. 创建部署目录

在飞牛上创建一个目录，例如：

```text
/vol1/docker/music-monitor/
```

### 2. 写入 docker-compose.yml

在该目录创建 `docker-compose.yml`，直接复制以下内容（镜像走国内加速地址，可直连拉取）：

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
    image: docker.1ms.run/baey666/music-monitor:latest
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

**两处需要你改的地方**：配置中的 `/vol2/1000/机械硬盘/#media/downloads/Music` 是示例音乐目录，请改成飞牛上实际的音乐路径，并且在**两个服务的挂载里保持一致**。`monitor` 这条挂载带 `:ro`（只读），它只用来看文件是否存在，绝不会改你的音乐文件。

其余不用动：`8085` 是下载引擎网页端口，`9099` 是监控控制台网页端口（容器内固定 `9090`，只改冒号左边）。

### 3. 创建目录并启动

SSH 连上飞牛，执行：

```bash
cd /vol1/docker/music-monitor
mkdir -p config/engine config/monitor
chmod -R 777 config
docker compose pull
docker compose up -d --force-recreate
docker compose ps
```

> 如果你的系统没有 `docker compose` 子命令，把上面命令换成 `docker-compose` 即可。

看到 `go-music-dl` 和 `music-monitor` 两个容器都是 `running` 就成功了。也可以用飞牛 Docker 界面直接「导入 Compose」完成以上步骤。

### 4. 访问服务

假设飞牛 IP 是 `192.168.10.88`：

| 服务 | 地址 |
|---|---|
| 下载引擎 go-music-dl | `http://192.168.10.88:8085` |
| 监控控制台 music-monitor | `http://192.168.10.88:9099` |

如果 `9099` 端口被占用，把 Compose 里改成如 `"9091:9090"`，访问 `9091` 即可。

> 拉取镜像失败？在飞牛 Docker 设置中添加镜像加速地址：`https://docker.1ms.run`、`https://docker.m.daocloud.io`。

## 三、首次初始化（4 步）

### 第 1 步：初始化 go-music-dl 管理员

打开 `http://NAS-IP:8085/music/setup`，初始化令牌从日志里拿：

```bash
docker compose logs go-music-dl | grep "Web setup token"
```

把令牌填进页面，然后自己设置管理员用户名和密码。

### 第 2 步：设置下载目录

进入 go-music-dl 的设置页面，把**本地下载目录**设置为：

```text
data/downloads
```

⚠️ 注意：这里填的是**容器内路径**，不是 NAS 宿主机路径。千万不要把 `/vol2/.../Music` 填进去——Compose 已经把宿主机音乐目录挂到了容器的 `data/downloads`，引擎写进 `data/downloads` 的文件会直接出现在你的音乐目录里。

### 第 3 步：登录音乐平台

在 go-music-dl 中扫码登录网易云、QQ 音乐、酷狗等你要用的平台。登录状态保存在 `config/engine/cookies.json`，重启不丢。

> **音质由 Cookie 决定**：实际能拿到的音质取决于你对应平台账号的会员等级（无损音质需要对应平台的会员 Cookie）。monitor 会在下载前探测真实码率，不达标时自动换源，但不能绕过 VIP 或版权限制。

### 第 4 步：创建监控

打开监控控制台 `http://NAS-IP:9099`，界面共 5 个页面：**监控、榜单、歌单/收藏夹、记录、设置**。

1. 进入「热门榜单」或「歌单 / 收藏夹」
2. 选择平台，挑选要监控的榜单 / 歌单 / 收藏夹
3. 先点**预览**，确认歌曲列表无误
4. 创建监控，按需打开**自动下载**开关

搞定。之后每隔 60 秒（`TICK_SECONDS` 可调）它会自动检查一次，发现新歌就自动下载。每首歌的下载状态、成功 / 失败原因、运行历史都可以在「记录」页面查看，失败的可手动重试。

## 四、下载规则：只下对的版本

自动下载最怕下到错的东西，music-monitor 内置了一套择优逻辑，按以下顺序选源：

```text
1. 优先监控内容中的原始歌曲
2. 优先歌名、歌手、时长都匹配的正式版本
3. 原平台不可用时，自动寻找其他平台的正式版本
4. 只要正式版可用，绝不下载 Live / 演唱会 / DJ / Remix 等改编版
5. 正式版全部不可用时，改编版才作为兜底
6. 试听片段始终不下载；时长差异过大的候选版本也会被跳过
```

目标音质可在 Compose 里通过 `DEFAULT_QUALITY` 设置：`standard`（128k）/ `high`（320k）/ `lossless`（无损）/ `hires`。达不到目标音质时会自动降级到能拿到的最好版本。

## 五、日常维护

```bash
# 查看状态 / 日志
docker compose ps
docker compose logs -f monitor go-music-dl

# 重启监控 / 停止服务
docker compose restart monitor
docker compose down

# 更新到最新版
docker compose pull monitor
docker compose up -d --force-recreate monitor

# 备份（配置、Cookie、监控记录全在 config/，几 MB 而已）
tar czf config-backup.tar.gz config/
```

网页顶部会显示当前运行版本号，更新镜像后刷新即可确认。音乐文件不随配置备份走，它们在你指定的音乐目录里。

## 六、常见问题

**Q：8085 打不开？**
查看引擎日志 `docker compose logs --tail=100 go-music-dl`，并用 `docker ps` 检查端口是否冲突。

**Q：监控里显示已下载，但目录里没文件？**
v1.0.5 起监控服务会以只读方式核对文件是否真实存在，删除文件后重新进入下载流程。请确保 monitor 镜像是最新版。

**Q：某首歌一直下不下来？**
多半是 VIP / 版权限制、Cookie 失效或全平台无可用音源。先去 8085 手动搜这首歌测试，检查对应平台登录状态。

**Q：下到了错误版本（Live / DJ 版）？**
把 monitor 镜像更新到 v1.0.4 以上并重建容器，v1.0.4 起才有完整的版本过滤与时长校验。

**Q：支持 Spotify / Apple Music 吗？**
不支持 Spotify；Apple Music 通常只能拿到试听片段，不适合作为完整来源。

## 七、写在最后

这个项目的定位很简单：**「发现新歌 → 挑对版本 → 下载落盘」这件事，交给 NAS，不交给你。**

配合飞牛自带的「音乐」App 或 Emby / Jellyfin，你在手机上点个红心、关注一个歌单，家里的 NAS 就会默默把歌备好——回家连上 Wi-Fi，新歌已经在播放列表里了。

如果觉得有用，欢迎到 [GitHub 仓库](https://github.com/Baey666/music-monitor) 点个 Star，部署遇到问题也可以提 Issue。别忘了看视频版的完整演示：[《飞牛音乐解放双手》](https://www.bilibili.com/video/BV1GTYD6jEyG/)。

> ⚠️ 免责声明：本项目仅用于个人学习与 NAS 私有部署，音乐版权归各平台及版权方所有，请在平台规则允许的范围内使用，勿用于商业用途。
