# 更新日志

本项目版本号遵循 [语义化版本 2.0.0](https://semver.org/lang/zh-CN/)：

- **major**（1.x.x → 2.0.0）：不兼容的变更，例如配置格式、卷挂载路径改了
- **minor**（1.0.x → 1.1.0）：新增功能，向后兼容
- **patch**（1.0.0 → 1.0.1）：修 bug，向后兼容

版本号的**唯一权威来源**是 `monitor/app/__init__.py` 里的 `__version__`。
请统一用脚本修改，不要手改其它文件：

```bash
./scripts/version.sh bump patch    # 递增并自动同步 Dockerfile / .env / CHANGELOG + 打 git tag
./scripts/version.sh check         # 校验各处版本号是否一致
```

镜像 tag 由 `docker-compose.yml` 从 `.env` 的 `APP_VERSION` 派生，形如 `baey666/music-monitor:v1.0.0`，
同时会推送一个 `latest`。

## [Unreleased]

## [1.0.5] - 2026-09-12

## [1.0.4] - 2026-09-12

## [1.0.3] - 2026-09-12

## [1.0.2] - 2026-09-12

## [1.0.1] - 2026-09-12

### 修复

- 下载前过滤试听片段、现场/演唱会/跨年等非正式版本
- 跨平台换源增加歌曲时长校验，避免下载错误版本

## [1.0.0] - 2026-09-12

首个正式版本。

### 新增

- 基于 go-music-dl 的「榜单 / 歌单 / 收藏夹」监控与自动下载服务
- 增量比对：只下载新出现的曲目，反复运行不会重复下载
- 音质择优：下载前探测真实码率，不达标自动到其它平台找更接近的版本（相似度 + 时长 + 可播放校验）
- 定时调度器，心跳间隔与下载并发可配置
- 零构建 Web 控制台：监控项管理、曲目记录、运行历史、手动触发、实时日志
- 引擎设置与登录态代写（扫码登录的 Cookie 决定能获取到的音质）

### 部署

- 提供 `docker-compose.yml`，一条命令拉起「引擎 + 监控」两个服务
- 镜像支持 `linux/amd64` 与 `linux/arm64`
- 配置与数据分离：配置集中在 `config/`（备份只需打包它），音乐文件放 `data/`
- 提供 `scripts/deploy-nas.sh`，在 NAS / Linux 上幂等部署（不在本地构建）

[Unreleased]: https://github.com/Baey666/music-monitor/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/Baey666/music-monitor/releases/tag/v1.0.0
