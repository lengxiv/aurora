# Aurora · Media Hub

[![CI](https://github.com/lengxiv/aurora/actions/workflows/ci.yml/badge.svg)](https://github.com/lengxiv/aurora/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/lengxiv/aurora)](https://github.com/lengxiv/aurora/releases)
[![Docker Image](https://img.shields.io/badge/image-ghcr.io%2Flengxiv%2Faurora-2496ED)](https://github.com/lengxiv/aurora/pkgs/container/aurora)
[![Version](https://img.shields.io/badge/version-0.4.0-1BAFA0)](VERSION)

一个自托管的全栈可视化面板，把「网盘中转 + 磁力调度 + 流媒体播放 + 实时监控」整合成一体。单进程 FastAPI 托管 React 前端，适配器架构按需对接 rclone / qBittorrent / Jellyfin，任何一路真实源缺失时自动回退演示数据，开箱即可探索。

> 安全约定：源码仓库不包含账号、token、媒体库、下载内容或服务运行数据。所有管理端口只监听 `127.0.0.1`，公网流量经 Nginx + HTTPS 进入。

## 功能模块

| 模块 | 路径 | 内容 |
|------|------|------|
| 资源管理台 | `/` | 挂载状态、磁力调度队列、流量/磁盘统计 |
| 做种监控 | `/seeding` | 做种状态、分享率、上传速率和对等方明细 |
| RSS 订阅 | `/rss` | 订阅源管理、自动下载规则、匹配预览 |
| 网盘文件 | `/netdisk` | remote 配置、目录浏览、文件传输和任务进度 |
| 媒资库 | `/player` | 文件浏览、Jellyfin 海报墙、在线播放和回收站 |
| 实时监控大屏 | `/monitor` | 带宽、24h 流量、挂载、下载队列、在线播放 |
| 设置 | `/settings` | 数据源、通知、安全中心、做种策略、自动整理、版本与在线检查更新 |

完整功能清单见 [`FEATURES.md`](FEATURES.md)。

## 技术栈

- **前端**：React 19 + Vite 8 + Tailwind CSS v4 + lucide-react + react-router
- **后端**：FastAPI（SPA 托管 + `/api/metrics` 等数据接口）
- **部署**：Docker Compose（多阶段镜像，GHCR 随 Release 发布）或 systemd + Nginx
- **设计**：暗色极光视觉、仅 Lucide 图标、全程零 emoji

## 快速开始

### 前置要求

| 方式 | 要求 |
|------|------|
| Docker Compose | Docker Engine + Compose plugin |
| systemd 裸机 | Debian 12 / Ubuntu 22.04+，Node.js 20.19+（建议 22）、Python 3.11、Nginx |
| 本地开发 | 同上，另需 git |

### 方式一：Docker Compose（推荐）

```bash
# 1. 克隆
git clone https://github.com/lengxiv/aurora.git /opt/aurora && cd /opt/aurora

# 2. 凭据环境文件（容器通过 env_file 读取；绝不能提交进 Git）
cp .env.example /etc/aurora.env && chmod 600 /etc/aurora.env
#    至少设置 AURORA_AUTH_PASS（不少于 12 位）；qBittorrent/Jellyfin 账号可选

# 3. compose 插值配置：钉住镜像版本，不要跟随 latest
cat > .env <<'EOF'
AURORA_ROOT=/opt/aurora
AURORA_IMAGE_TAG=0.4.0
AURORA_ENV_FILE=/etc/aurora.env
EOF

# 4. 目录属主与容器内 UID=1000 保持一致，否则落盘失败
install -d -o 1000 -g 1000 -m 750 aurora-state
install -d -m 755 qbit/config qbit/downloads
chown -R 1000:1000 qbit

# 5. 启动应用本体（需要 qBittorrent/Jellyfin 时改为 `docker compose up -d` 全栈启动）
docker compose up -d aurora

# 6. 验证（应返回 {"status":"ok","version":"0.4.0",...}）
curl -fsS http://127.0.0.1:8787/api/health
```

镜像内置 HEALTHCHECK（探测 `/api/health`），状态持久化在 `aurora-state/` 卷里，重建容器不丢数据。HTTPS/Nginx 反代、qBittorrent 与 Jellyfin 初始化、rclone RC 对接、每日备份的完整教程见 [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md)（容器部署看第 13 节）。

### 方式二：systemd 裸机部署

```bash
# 1. 安装依赖（完整清单见 docs/DEPLOYMENT.md 第 1 节）
apt install -y ca-certificates curl git nginx python3 python3-pip python3-venv build-essential
curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && apt install -y nodejs

# 2. 源码与凭据
git clone https://github.com/lengxiv/aurora.git /opt/aurora && cd /opt/aurora
cp .env.example /etc/aurora.env && chmod 600 /etc/aurora.env

# 3. 运行用户与目录（aurora.service 以 aurora 用户运行）
if ! id aurora >/dev/null 2>&1; then
  groupadd --system aurora
  useradd --system --gid aurora --home-dir /var/lib/aurora --create-home --shell /usr/sbin/nologin aurora
fi
install -d -o aurora -g aurora -m 750 /var/lib/aurora/data /srv/aurora/media

# 4. 构建并安装服务
python3 -m venv backend/.venv
backend/.venv/bin/pip install -r backend/requirements.txt
npm --prefix frontend ci && npm --prefix frontend run build   # 产物写入 backend/static
install -m 644 deploy/systemd/aurora.service.example /etc/systemd/system/aurora.service
systemctl daemon-reload && systemctl enable --now aurora

# 5. 验证
curl -fsS http://127.0.0.1:8787/api/health
```

Nginx 站点配置、证书申请（certbot）、rclone RC 与备份定时器的安装步骤见 [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md)。

### 本地开发

```bash
# 后端（终端 1）
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m uvicorn main:app --port 8787

# 前端（终端 2）：http://localhost:5173，/api 自动代理到 8787
cd frontend
npm ci && npm run dev
```

提交前请运行完整检查（与 CI 相同）：

```bash
cd backend && python -m unittest discover -s tests -p 'test_*.py'
cd frontend && npm run lint && npm run build
```

## 更新与版本

版本号的单一来源是根目录 [`VERSION`](VERSION)（SemVer），后端在 `/api/health`、`/api/info` 暴露版本，前端构建时注入同名版本号，设置页区分显示「后端版本 / 前端构建」。

**在线检查更新**：设置 → 服务 → 检查更新，对比 GitHub Releases 最新版本（服务端缓存 30 分钟）。仓库地址可用环境变量 `AURORA_REPO` 覆盖。

| 部署方式 | 更新方法 | 回滚 |
|----------|----------|------|
| Docker Compose | 修改 `.env` 中 `AURORA_IMAGE_TAG` → `docker compose pull aurora && docker compose up -d aurora` | 把 tag 改回旧版本，重复同样两行 |
| systemd | `git pull --ff-only` → 重装依赖 → 重新构建前端 → `systemctl restart aurora` | `git checkout v旧版本` 后重跑 |

## 配置

复制 `.env.example` 为私有环境文件（如 `/etc/aurora.env`），全部变量及说明见 [.env.example](.env.example)。常用项：

| 变量 | 默认 | 说明 |
|------|------|------|
| `AURORA_AUTH_USER` | `admin` | 登录用户名 |
| `AURORA_AUTH_PASS` | 空 | 登录密码；留空则首次启动生成随机密码写入 `AURORA_AUTH_FILE`，设置不少于 12 位 |
| `AURORA_STATE_DIR` | `/var/lib/aurora` | 运行状态（会话密钥、活动日志） |
| `AURORA_DATA_DIR` | `/var/lib/aurora/data` | 通知状态、回收站元数据 |
| `AURORA_LOCAL_MOUNT` | `/srv/aurora/media` | 本地媒体根目录（媒资库浏览范围） |
| `AURORA_REPO` | `lengxiv/aurora` | 在线检查更新对比的 GitHub 仓库 |
| `AURORA_QBIT_URL` / `_USER` / `_PASS` | `:8080` | qBittorrent Web API 对接 |
| `AURORA_JELLYFIN` / `_TOKEN` | `:8096` | Jellyfin API 对接 |
| `AURORA_RCLONE_RC` / `_AUTH` | `:5572/rclone` | rclone Remote Control 对接 |
| `AURORA_TG_BOT_TOKEN` / `_CHAT_ID` | 空 | Telegram 通知回退通道 |

## 数据源与适配器

后端按「探测 → 驱动」模型聚合，每个真实源可独立对接；探测不到的模块自动回退演示数据，`GET /api/sources` 可确认当前来源：

| 模块 | 真实源 | 对接环境变量 |
|------|--------|--------------|
| 磁盘 / 带宽 | 系统 `df`、`/proc/net/dev`、`/proc/diskstats` | 始终真实 |
| 挂载状态 | rclone remote control (rc) | `AURORA_RCLONE_RC`、`AURORA_RCLONE_RC_AUTH` |
| 下载队列 | qBittorrent Web API | `AURORA_QBIT_URL`、`_USER`、`_PASS` |
| 在线播放 | Jellyfin API | `AURORA_JELLYFIN`、`AURORA_JELLYFIN_TOKEN` |

把对应服务跑起来、设好环境变量并重启后端即自动切真，前端零改动。网盘（rclone）详细对接步骤见 [`RCLONE-对接.md`](RCLONE-对接.md)。

## 目录结构

```
aurora
├── backend/
│   ├── main.py          # FastAPI 应用、认证和业务 API
│   ├── providers.py     # qBittorrent / rclone / Jellyfin 适配器
│   ├── static/          # 前端构建产物（vite build 输出，Git 忽略）
│   └── data/            # 运行数据（Git 忽略）
├── frontend/
│   ├── src/views/       # Console / Seeding / Player / Monitor / Netdisk / Settings
│   └── vite.config.ts   # 构建产物打到 ../backend/static，注入 VERSION 版本号
├── VERSION              # 版本号单一来源（SemVer）
├── Dockerfile           # 多阶段构建：Node 编译前端 → Python 非 root 运行
├── compose.yaml         # aurora / qBittorrent / Jellyfin 可复现部署
├── .github/workflows/   # CI（测试/构建/镜像冒烟）与 Release（GHCR 发布）
├── deploy/              # Nginx / systemd 配置示例
└── scripts/backup.sh    # 配置与元数据备份
```

## 安全须知

- Aurora、qBittorrent WebUI、Jellyfin、rclone RC 均只监听 `127.0.0.1`，公网仅暴露 Nginx 443 与 BT 对等端口 39876；不要把 8787 直接暴露到公网。
- 登录使用服务端 HttpOnly Cookie 会话，登录接口有按来源 IP 的失败锁定（5 次锁 5 分钟）；可在设置页查看和撤销其他活动会话。
- 媒资删除先进同磁盘回收站 `.aurora-trash`，可在媒资库恢复或彻底删除。
- 建议开启每日备份（`scripts/backup.sh` + systemd timer，保留 14 份）并异地容灾；归档含凭据，按敏感文件处理。
- 更多威胁模型与处置见 [`docs/SECURITY.md`](docs/SECURITY.md)。

常见部署问题（Nginx、证书、端口、容器排查）见 [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) 第 12 节。

## 文档索引

| 文档 | 内容 |
|------|------|
| [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) | 完整部署教程（Docker Compose / systemd / Nginx / 备份 / 排查） |
| [`docs/SECURITY.md`](docs/SECURITY.md) | 安全模型与敏感信息处置 |
| [`docs/GITHUB.md`](docs/GITHUB.md) | 仓库发布与发版（Release）流程 |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | 贡献指南：代码规范、提交规范（Conventional Commits）、版本规范、发布 checklist |
| [`FEATURES.md`](FEATURES.md) | 功能现状清单 |
| [`CHANGELOG.md`](CHANGELOG.md) | 按日期整理的变更记录 |
| [`TODO.md`](TODO.md) | 开发待办与路线图 |
| [`RCLONE-对接.md`](RCLONE-对接.md) | rclone 网盘对接指南 |

## 参与贡献

欢迎 Issue 和 PR。提交前请阅读 [`CONTRIBUTING.md`](CONTRIBUTING.md)，要点：

- 提交信息遵循 Conventional Commits（`feat` / `fix` / `docs` / `refactor` …）。
- 版本号遵循 SemVer；改功能升 MINOR、修缺陷升 PATCH，同步更新 `VERSION`、`frontend/package.json` 与 `CHANGELOG.md`。
- 新功能需带测试；CI（后端测试、前端 lint/build、版本一致性、镜像冒烟）全绿才能合并。

## 许可证

本项目目前未附带开源许可证，代码默认保留所有权利；如需开源，请先添加 LICENSE 文件（推荐 MIT 或 Apache-2.0）并确认仓库无敏感信息（参见 [`docs/GITHUB.md`](docs/GITHUB.md)）。
