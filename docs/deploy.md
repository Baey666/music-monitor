# 部署到 NAS

> 这是 [music-monitor](https://github.com/Baey666/music-monitor) 的部署文档。
> 镜像怎么构建、怎么推仓库，见 [publish-image.md](publish-image.md)。
> 代码怎么发到 GitHub，见 [publish-github.md](publish-github.md)。

**先明确一点**：只有 `monitor` 这一个镜像需要你自己构建。
引擎用的是官方镜像 `guohuiyuan/go-music-dl`，不需要你构建，也不需要你上传。

---

## 三种部署方式怎么选

| | 方案 A：NAS 就地构建 | 方案 B：推镜像仓库 | 方案 C：离线 tar |
|---|---|---|---|
| 需要镜像仓库 | 不需要 | 需要 | 不需要 |
| 需要 NAS 联网 | 需要 | 只需要能拉仓库 | 完全不联网 |
| 架构匹配问题 | 不会有 | 需多架构构建 | 需注意构建机架构 |
| 适合场景 | **先跑起来、单台机器** | 多台机器 / 长期维护 | 内网隔离环境 |

---

## 方案 A：整目录拷到 NAS，在 NAS 上就地构建（最省事，推荐）

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
  国内网络建议先给 Docker 配镜像加速器（见下方[国内网络注意事项](#国内网络注意事项)）。

也可以直接用脚本一条命令搞定（等价于上面的步骤 2）：

```bash
./scripts/build-push.sh local
```

---

## 方案 B：构建镜像推到仓库，NAS 只拉镜像

完整步骤（含 Docker Hub 专项、云厂商地址、限流与加速）见 **[publish-image.md](publish-image.md)**。

NAS 侧最终只需要这两个文件，不用拷 `monitor/` 源码目录：

```
你的部署目录/
├── docker-compose.yml
└── .env              # MONITOR_IMAGE 填镜像仓库地址
```

```bash
docker compose pull
docker compose up -d
```

> compose 文件里 `image:` 和 `build:` 同时存在时：`up -d --build` 走本地构建，
> `pull && up -d` 直接用远端镜像。想彻底禁止在 NAS 上构建，把 `monitor` 服务里的
> `build:` 三行删掉即可（保留 `image:`）。

---

## 方案 C：NAS 完全不能上网 → 导出镜像文件离线导入

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

---

## 架构必须匹配（最常见的翻车点）

镜像平台和 NAS CPU 架构不一致，启动时会报 `no matching manifest for linux/arm64` 之类的错。

先在 NAS 上确认架构：

```bash
uname -m      # x86_64 → amd64 ；aarch64 / armv8 → arm64
```

- **方案 A** 不用管（在 NAS 上构建就是 NAS 的架构）。
- **方案 B** 若构建机是 x86_64 而 NAS 是 arm64，必须多架构构建
  （脚本默认已带 `--platform linux/amd64,linux/arm64`）。
  多架构需要 QEMU 模拟，第一次会提示安装：
  `docker run --privileged --rm tonistiigi/binfmt --install all`
  （Docker Desktop 已内置，无需手动装）。
- 只给自己一台机器用，直接把 `PLATFORMS` 改成单一架构，构建快很多：
  ```ini
  PLATFORMS=linux/amd64
  ```

验证推送上去的镜像里有哪些架构：

```bash
docker manifest inspect 你的仓库地址/music-monitor:latest
```

---

## 国内网络注意事项

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

pip 装依赖慢，可以在 `monitor/Dockerfile` 的 `pip install` 前加一行换源：

```dockerfile
RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple
```

---

## 安全提示

镜像里**只有代码，不含任何音乐文件、Cookie、账号配置**——这些全在挂载出来的 `./data/` 里。所以：

- 把镜像推到公开仓库是安全的；但**不要把 `data/` 目录提交到 Git 或打进镜像**
  （`.gitignore` 已排除，`monitor/.dockerignore` 也已排除）。
- 如果打算公开分享镜像，请注意上游 go-music-dl 是 **AGPL-3.0**：
  本项目只通过 HTTP 调用它、没有链接其代码，但再分发的合规性请自行确认。

---

## 更新已部署的实例

```bash
# 方案 A（NAS 就地构建）
git pull   # 或重新拷贝代码
docker compose up -d --build

# 方案 B（拉新镜像）
docker compose pull && docker compose up -d

# 只想重启监控服务（改完 .env 后）
docker compose up -d monitor
```

升级前建议备份监控数据：

```bash
cp data/monitor/monitor.db ~/monitor-backup.db
```
