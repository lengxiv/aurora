# Aurora · Media Hub

一个独立的全栈可视化项目，把「网盘中转 + 磁力调度 + 流媒体播放 + 实时监控」整合成一体，含多个可视化界面：

这是一个可自托管项目。源码仓库不包含账号、token、媒体库、下载内容或服务运行数据；部署配置请参考 [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md)，发布到 GitHub 请先阅读 [`docs/GITHUB.md`](docs/GITHUB.md) 和 [`docs/SECURITY.md`](docs/SECURITY.md)。

功能总览见 [`FEATURES.md`](FEATURES.md)，按日期整理的变更记录见 [`CHANGELOG.md`](CHANGELOG.md)。

| 模块 | 路径 | 内容 |
|------|------|------|
| 资源管理台 | `/` | 挂载状态、磁力调度队列、流量/磁盘统计 |
| 做种监控 | `/seeding` | 做种状态、分享率、上传速率和对等方明细 |
| 网盘文件 | `/netdisk` | remote 配置、目录浏览、文件传输和任务进度 |
| 媒资库 | `/player` | 文件浏览、Jellyfin 海报墙、在线播放和回收站 |
| 实时监控大屏 | `/monitor` | 带宽、24h 流量、挂载、下载队列、在线播放 |
| 设置 | `/settings` | 数据源、通知、安全中心和活动会话 |

## 技术栈

- 前端：React 19 + Vite 8 + Tailwind CSS v4 + lucide-react + react-router
- 后端：FastAPI（SPA 托管 + `/api/metrics` 数据接口）
- 设计：暗色极光视觉、仅 Lucide 图标、全程零 emoji、排版与动效对标 AWards

## 目录结构

```
/opt/aurora
├── backend/
│   ├── .venv/           # FastAPI 虚拟环境
│   ├── static/          # 前端构建产物（vite build 输出）
│   ├── data/            # 活动日志、通知状态和回收站元数据
│   └── main.py          # FastAPI 应用、认证和业务 API
└── frontend/
    ├── src/
    │   ├── lib/api.ts      # API 客户端，后端不可达时回退演示数据
    │   ├── components/ui.tsx
    │   └── views/          # Console / Seeding / Player / Monitor / Settings
    │       └── monitor/    # 监控大屏子组件
    └── vite.config.ts      # 构建产物打到 ../backend/static
├── compose.yaml             # qBittorrent / Jellyfin 可复现部署
└── scripts/backup.sh        # 配置与元数据备份
```

## 运行

已托管为 systemd 服务 `aurora.service`（开机自启，进程崩溃自动拉起，内存上限 512M）：

```bash
systemctl status aurora       # 状态
systemctl restart aurora      # 重启（后端代码改动后）
systemctl stop aurora         # 停止
```

开发/手动直跑（构建好的前端已打进 `backend/static`）：

```bash
cd /opt/aurora/backend
.venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8787
```

> 登录用户名默认为 `admin`，密码由本机受限文件或 `AURORA_AUTH_PASS` 环境变量管理。登录接口有按来源 IP 的失败锁定；不要把管理端口直接暴露到公网。

## 安全与恢复

- Aurora、qBittorrent WebUI、Jellyfin 管理端口均只监听 `127.0.0.1`，公网请求经 Cloudflare 和 Nginx 进入；BT 对等端口 `39876` 保持公开。
- 登录使用服务端会话，支持在设置页查看和撤销其他活动会话。修改密码要求验证当前密码，并使其他会话立即失效。
- 媒资删除先移动到同磁盘隐藏目录 `.aurora-trash`；可在媒资库的「回收站」恢复或彻底删除，同名路径冲突时拒绝覆盖。
- `aurora-backup.timer` 每日约 04:20 自动备份配置、元数据、rclone 配置和相关服务文件到 `/var/backups/aurora`，保留最近 14 份。下载内容和媒体文件不进入配置归档。
- `compose.yaml` 固化 qBittorrent/Jellyfin 的回环管理端口和持久化目录。维护容器时必须保留 `/opt/aurora/qbit`、`/opt/aurora/jellyfin` 及下载目录。

## 数据说明（适配器架构）

后端按「探测 → 驱动」模型聚合，每个真实源可独立对接：

| 模块 | 真实源 | 生效探测 | 对接环境变量 |
|------|--------|----------|--------------|
| 磁盘 / 带宽 | 系统 `df`,`/proc/net/dev`,`/proc/diskstats` | 始终真实 | — |
| 挂载状态 | rclone remote control (rc) | `{rc}/core/version` 可达 | `AURORA_RCLONE_RC`（默认 `http://127.0.0.1:5572`） |
| 下载队列 | qBittorrent Web API | `/api/v2/app/version` 可达 | `AURORA_QBIT_URL` `_USER` `_PASS`（默认 `:8080`） |
| 在线播放 | Jellyfin API | `/System/Info/Public` 可达 | `AURORA_JELLYFIN` `_TOKEN`（默认 `:8096`） |

任一路真实源探测不到 → 该模块自动回退演示数据。`GET /api/sources` 返回每模块当前来源（`system`/`rclone`/`qbittorrent`/`jellyfin`/`demo`），方便确认对接状态。

运行状态路径可通过 `AURORA_STATE_DIR` 和 `AURORA_DATA_DIR` 配置，默认分别使用后端目录和 `backend/data`。生产环境建议把这些目录放在源码目录之外；生产 rclone 配置默认位于 `/var/lib/aurora/rclone/rclone.conf`，不应放进 Git。

**以后接入**：把对应服务跑起来（rclone `--rc` 开 5572、qBittorrent WebUI、Jellyfin），必要时设环境变量，后端重启即自动切真，前端零改动。
