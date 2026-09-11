# NAS / 飞牛 Docker Compose 部署

本文用于普通使用者部署 `music-monitor`。不需要构建源码，直接使用已经发布的镜像即可。

## 一、准备部署目录

在飞牛 NAS 上创建一个应用目录，例如：

```text
/vol1/docker/music-monitor/
```

把以下文件放进去：

```text
music-monitor/
├── docker-compose.yml
├── config/
│   ├── engine/
│   └── monitor/
└── data/
    └── downloads/
```

如果使用项目提供的部署版 Compose 文件，里面已经写好了两个镜像：

```yaml
music-dl:
  image: guohuiyuan/go-music-dl:latest

monitor:
  image: docker.1ms.run/baey666/music-monitor:v1.0.4
```

## 二、创建目录和权限

通过飞牛 SSH 执行：

```bash
cd /vol1/docker/music-monitor
mkdir -p config/engine config/monitor data/downloads
chmod -R 777 config data
```

如果音乐需要保存到其他硬盘，可以直接修改 Compose：

```yaml
volumes:
  - ./config/engine:/home/appuser/data
  - "/vol2/1000/机械硬盘/#media/downloads/Music:/home/appuser/data/downloads"
  - ./config/monitor:/app/data
```

注意：

- 左边是飞牛宿主机路径，可以修改
- 右边是容器内路径，不要修改
- go-music-dl 设置中的下载目录仍然填写 `data/downloads`
- 不要把宿主机 `/vol2/.../Music` 填进 go-music-dl 的下载目录设置

## 三、启动服务

```bash
docker compose pull
docker compose up -d
docker compose ps
```

旧版系统如果没有 `docker compose`，使用：

```bash
docker-compose pull
docker-compose up -d
docker-compose ps
```

查看日志：

```bash
docker compose logs -f
```

## 四、访问服务

假设飞牛 IP 是 `192.168.10.88`：

```text
go-music-dl：http://192.168.10.88:8085
监控控制台：http://192.168.10.88:9090
```

如果 `9090` 已被其他服务占用，将 Compose 改为：

```yaml
ports:
  - "9091:9090"
```

然后访问：

```text
http://192.168.10.88:9091
```

## 五、第一次使用

### 1. 初始化管理员

打开：

```text
http://飞牛IP:8085/music/setup
```

在日志中查找初始化令牌：

```bash
docker compose logs go-music-dl | grep "Web setup token"
```

将令牌填入初始化页面，并自行创建管理员账号和密码。

### 2. 设置下载目录

进入 go-music-dl 设置页面，把下载目录设置为：

```text
data/downloads
```

保存后测试下载一首歌曲。

### 3. 登录音乐平台

在 go-music-dl 中扫码登录网易云、QQ 音乐、酷狗等平台。登录状态会保存到：

```text
config/engine/cookies.json
```

### 4. 创建监控

打开监控控制台：

```text
http://飞牛IP:9090
```

先使用「预览曲目」确认歌曲列表，再创建自动下载监控。

## 六、下载文件位置

默认位置：

```text
/vol1/docker/music-monitor/data/downloads/
```

如果使用自定义挂载：

```text
/vol2/1000/机械硬盘/#media/downloads/Music/
```

文件只有在 go-music-dl 成功返回并实际保存后才会出现在该目录。

## 七、下载规则

监控服务会按以下顺序处理歌曲：

```text
原始歌曲
→ 原平台正式版
→ 其他平台正式版
→ 改编版兜底
```

正式版本可用时，不会下载：

- Live
- 现场
- 演唱会
- 跨年版
- DJ 版
- Remix
- 混音版
- 加长版
- 伴奏版

试听片段始终不会下载。每首歌曲会单独等待下载结果，失败会自动重试。

## 八、国内网络问题

如果拉取镜像失败，可以在飞牛 Docker 设置中增加：

```text
https://docker.m.daocloud.io
https://docker.1ms.run
```

如果用户镜像无法拉取，优先使用完整地址：

```yaml
image: docker.1ms.run/baey666/music-monitor:v1.0.4
```

引擎镜像也可以使用：

```yaml
image: docker.1ms.run/guohuiyuan/go-music-dl:latest
```

## 九、更新与重启

更新镜像：

```bash
docker compose pull
docker compose up -d
```

只重启监控：

```bash
docker compose restart monitor
```

停止服务：

```bash
docker compose down
```

## 十、备份

配置目录包含管理员设置、平台登录态、监控任务和下载记录，建议定期备份：

```bash
tar czf config-backup.tar.gz config/
```

重点备份：

```text
config/engine/
config/monitor/
```

音乐文件在 `data/downloads/` 或你自定义的音乐目录中，不需要和配置一起打包。

## 常见问题

### 8085 打不开

查看引擎日志：

```bash
docker compose logs --tail=100 go-music-dl
```

检查端口是否冲突：

```bash
docker ps --format "table {{.Names}}\t{{.Ports}}"
```

### 9090 被占用

把宿主机端口改成其他端口，例如：

```yaml
- "9091:9090"
```

### 下载不到歌曲

可能是歌曲 VIP、版权限制、Cookie 失效或平台没有可用音源。先在 8085 手动下载普通歌曲测试。

### 下载到了错误版本

确认 monitor 镜像已经更新到 `v1.0.4` 或更高版本，并重新创建 monitor 容器：

```bash
docker compose pull monitor
docker compose up -d --force-recreate monitor
```
