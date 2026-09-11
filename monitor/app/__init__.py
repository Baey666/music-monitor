"""music-monitor：在 go-music-dl 之上做「榜单 / 歌单 / 收藏夹」监控与自动下载。

⚠️ 这里是**版本号的唯一权威来源**（single source of truth）。
   不要手改其它文件里的版本号（Dockerfile / .env / docker-compose.yml / CHANGELOG），
   统一用：`scripts/version.sh bump patch|minor|major`
   该脚本会同步所有位置、写 CHANGELOG、自动 commit 并打 git tag。
   校验是否一致：`scripts/version.sh check`
"""

__version__ = "1.0.2"
