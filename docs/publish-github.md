# 把代码发到 GitHub

> 这是 [music-monitor](https://github.com/Baey666/music-monitor) 的代码发布文档。
> 镜像怎么构建推送见 [publish-image.md](publish-image.md)，
> 部署到 NAS 见 [deploy.md](deploy.md)。

当前仓库：**https://github.com/Baey666/music-monitor**（public，MIT，默认分支 `main`）

---

## 1. 提交前必须做的安全检查

这个项目的 `.gitignore` 已经排除了 `.env`、`data/`、`*.tar`、各类缓存目录，
但**每次 push 前还是建议确认一遍**，因为一旦推上公开仓库，历史里就很难彻底删掉：

```bash
# 看这次到底会提交哪些文件（最重要的一步）
git status --short

# 如果已经在暂存区，列出全部待提交文件
git diff --cached --name-only

# 扫一遍有没有密钥/登录态混进来
git diff --cached | grep -iE "gho_|ghp_|github_pat_|oauth_token|MUSIC_COOKIE=|password"
```

**绝不应该入库的东西**：`.env`、`data/` 下的任何内容（音乐文件、`monitor.db`、
引擎的 `cookies.json` 和 `settings.db`）、任何 token、`music-monitor.tar`。

---

## 2. 首次推送

### 2.1 设置提交身份

不要用真实邮箱，用 GitHub 提供的 noreply 地址（`<用户id>+<用户名>@users.noreply.github.com`）：

```bash
git config user.name "Baey666"
git config user.email "142658530+Baey666@users.noreply.github.com"
```

加 `--global` 可以设为全局默认；不加则只作用于当前仓库。

### 2.2 仓库已有代码，推到一个新建的空仓库

```bash
git init -b main
git add -A
git commit -m "feat: 首次提交"
git remote add origin https://github.com/你的用户名/仓库名.git
git push -u origin main
```

### 2.3 用 gh CLI 一条命令创建并推送

装了 [GitHub CLI](https://cli.github.com/) 的话更省事（会自动建仓库、加 remote、推送）：

```bash
gh auth login
gh repo create music-monitor --public --source=. --remote=origin --push \
  --description "网易云/QQ/酷狗/Apple Music 榜单与歌单监控，自动择优下载指定音质"
```

---

## 3. 换行符：`.gitattributes` 是必须的

项目里有 `.gitattributes`，其中最关键的一行是：

```
*.sh text eol=lf
```

**为什么必须有**：Windows 上写出来的 shell 脚本是 CRLF 换行，拷到 Linux / NAS 上执行会报
`bash^M: bad interpreter: No such file or directory`。强制 `.sh` 用 LF 存储就避免了这个问题
（`.ps1` 不受影响，Windows 本身接受 LF）。

验证仓库里存的是不是纯 LF：

```bash
git show HEAD:scripts/build-push.sh | grep -c $'\r'    # 输出 0 就是纯 LF
git check-attr text eol -- scripts/build-push.sh       # 应显示 text: set / eol: lf
```

---

## 4. 后续更新

```bash
git add -A
git commit -m "fix: 修了什么"
git push
```

如果只改了文档、想一次性推完：

```bash
git commit -am "docs: 更新说明" && git push
```

---

## 5. 打标签与发 Release

```bash
git tag -a v1.1.0 -m "v1.1.0 - 一句话说明"
git push origin v1.1.0
```

用 gh 基于标签发一个 Release（带说明的正式版本）：

```bash
gh release create v1.1.0 --title "v1.1.0 — 标题" --notes "## 更新内容
- 改动 1
- 改动 2"
```

网页操作也行：仓库页 → Releases → Draft a new release → 选标签 → 填说明 → Publish。

---

## 6. 常见问题

**Q：`git push` 报 `schannel: failed to receive handshake, SSL/TLS connection failed`？**
Windows 自带的 schannel TLS 后端在某些网络环境下握手会被干扰。换成 OpenSSL 后端即可：

```bash
git -c http.sslBackend=openssl push origin main
```

固化到当前仓库（只影响这个仓库）：

```bash
git config http.sslBackend openssl
```

> 注意：`gh` CLI 走的是另一套 HTTP 栈，它正常**不代表** `git push` 正常，反之亦然。

**Q：`git status` 一直显示 `main...origin/main [gone]`，但 push 明明是成功的？**
这是本地 remote-tracking 引用没被写进去导致的显示问题，**不影响推送**。
可靠的做法是对比两边的提交号：

```bash
git ls-remote origin -h refs/heads/main    # 远程的
git rev-parse HEAD                          # 本地的
```

两个哈希一致就是同步的。

**Q：push 要求输入用户名密码，但输了 GitHub 密码被拒？**
GitHub 早已禁用密码推送，必须用 **Personal Access Token**（Settings → Developer settings →
Personal access tokens），勾 `repo` 权限。
更好的方式是配 SSH key 或装 gh CLI 后 `gh auth setup-git`。

**Q：不小心把 `.env` 或 `data/` 推上去了怎么办？**
先立刻去 GitHub 上**轮换掉所有泄露的凭据**（Cookie、token），再清理历史
（`git filter-repo` 或 BFG），最后 `git push --force`。
**光删文件再提交是没用的**，旧提交里还留着。

---

## 7. 分支与协作约定（可选）

单人项目够用的最小约定：

- `main` 始终是可用的，直接在上面提交；
- 大改动开分支：`git switch -c feat/xxx`，完成后合并回 `main`；
- 提交信息用 `feat:` / `fix:` / `docs:` / `refactor:` 前缀，方便日后 `git log --oneline` 阅读。
