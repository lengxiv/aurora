# Aurora · 功能清单

> 记录到 2026-08-12 为止已实现的功能。实际访问地址由部署环境决定（后端 systemd `aurora.service`，FastAPI :8787）。

## 认证与安全
- 应用层登录页（`/login`），用户名默认为 `admin`，密码由 `AURORA_AUTH_PASS` 或受限状态文件管理
- 服务端可撤销 Cookie 会话（HttpOnly + SameSite=Lax + Secure），12h 过期
- 登录失败**限流**：每 IP 连续失败 5 次锁 5 分钟（CF-Connecting-IP 识别）
- 整体鉴权：`/api/*`、SPA 页面均需登录；未登录深链 302 → /login；会话失效自动踢回
- 登出（桌面侧栏 / 移动顶栏）
- **安全中心**：查看当前/其他活动会话的来源 IP、设备和到期时间，支持即时撤销其他会话
- **在线改密**：验证当前密码，新密码要求 12-128 位；成功后撤销其他会话（环境变量托管密码时禁用）
- 跨站状态修改请求拒绝、API 文档端点关闭、CSP/安全响应头、静态资源不可变缓存

## 导航 / 视图
- 四模块 + 设置：管理台 `/`、做种监控 `/seeding`、媒资库 `/player`、实时监控 `/monitor`、设置 `/settings`
- 侧栏（桌面）+ 顶部图标栏（移动端），响应式双端适配
- 暗色极光主题、Lucide 图标、全程零 emoji

## 做种监控（Seeding）
- 汇总卡：做种任务数（含正被拉取数）、实时上传速率、累计上传/下载、总分享率
- **做种列表**：状态、分享率、已上传、上传速率、连接数、S/L；自动选中上传最快任务
- **对等方明细**（`/api/torrents/peers` 实时轮询）：客户端、IP、地区、进度、从你拉取速率、累计从你下载
- 做种/对等方均接 qBittorrent 真实数据，未接入显示空态

## 管理台（Console）
- 挂载存储卡片（真实来源：rclone / 本地盘；状态在线/降级/离线）
- 本地磁盘 df 真实容量 + 可用空间 + 读写速率（/proc/diskstats）
- 磁力调度表：任务列表、进度、速率、S/L；状态中文显示
- **磁力操作**：单条（暂停/续传/删除）+ **批量多选**（全选/多选 → 批量暂停/续传/删除）
- **添加磁力**弹窗（真实提交 qBittorrent）
- **上传种子文件**（管理台选择 `.torrent` 文件，单文件最大 20 MB，真实提交 qBittorrent）
- 磁力实时来源徽标（中文：挂载/磁力/播放/磁盘/带宽 · 真实/未接入）
- 搜索过滤磁力队列

## 实时监控大屏（Monitor）
- 实时带宽（eth0 `/proc/net/dev`，1s 最小采样窗防尖峰，真实曲线）
- 近期实时流量柱状（真实采样）
- 挂载状态、下载队列、**做种状态卡**（做种数/分享率/累计上传 + 正在从你拉取的对等方 Top4，4s 轮询）、在线播放（均接真实/空态）
- 中转磁盘、时钟（秒级）、**最近活动日志**（磁力添加/删除/暂停/续传）
- 来源徽标（中文）、**全屏**开关

## 媒资库（本地媒体浏览器 + Jellyfin 海报墙）
- **双模式**：文件浏览 / 海报墙（头部切换）
- **海报墙（Jellyfin 真实）**：电影/剧集海报网格（2:3），点海报 Jellyfin 流式播放（Range 代理），失败/可回退「本地播放」
- **真实文件浏览**：后端扫描下载盘（`AURORA_LOCAL_MOUNT`，默认 `/opt/aurora/qbit/downloads`）
- 目录树 + 类型筛选（全部/视频/音频/字幕/图片/文件）+ 搜索
- **在线点播**：HTML5 播放器，后端 byte-range（可拖动进度）
- **播放器浮窗**：画中画、可拖拽（标题栏拖动）、全屏、续播记忆（localStorage）
- **图片灯箱**：大图查看、同目录上/下张切换、键盘 ← → Esc
- 文件管理：**删除**（确认）、**重命名**、批量移动/删除
- **回收站**：删除文件先同盘软删除，支持恢复与彻底删除；恢复时检测原路径冲突，隐藏目录不参与媒体扫描
- **做种保护**：做种中文件带琥珀色「做种中」徽标；删除/重命名/部分移动前弹毁种强警告
- **移动跟随**：完整选中的种子移动走 qbit setLocation（自动搬文件 + 更新种子路径，做种不中断）；未选全的种子文件移动会明确警告
- **目录管理**：侧栏一键**新建目录**；每个目录 hover 可**重命名 / 删除空目录**（非空拒绝，杜绝误删）；目录列表含空目录（`/api/media/dirs`）
- 目录穿越安全防护（后端 `_media_safe` 路径校验 + rename/mkdir/rmdir 白名单）

## 全局交互 / UI
- ⌘/Ctrl+K **命令面板**（搜视图跳转 / 刷新 / 退出）
- Toast 全局反馈（操作成功/失败）
- 骨架屏（Skeleton）加载态、卡片淡入（card-in）、尊重 `prefers-reduced-motion`
- 空态插画化（图标+提示）

## 通知与告警
- 磁力任务**下载完成/失败**推送（qBittorrent `completion_on` 迁移检测 + 状态持久化，重启不丢/不重发/首启基线不补发）
- **每日做种日报**：每天定时（默认 21:00，设置页可改）推送 TG——做种数、今日上传增量、当前上传速率、累计上传、总分享率、每种子明细、整机带宽、磁盘占用
- 日报按种子上传量快照（`data/torrent_daily.json`）算当日增量，发送后更新基线；5 分钟窗口补发 + 按日去重
- Telegram 主/备双 Bot、测试按钮、磁盘阈值告警（横幅 + 推送）
- 设置页开关：下载完成/失败通知、磁盘告警、TG 总开关、每日日报

## 数据真实对接
- 磁盘 / 带宽：系统真实（df、/proc/net/dev、/proc/diskstats）
- **磁力：qBittorrent 真实下载**（Docker `linuxserver/qbittorrent`，WebUI :8080，BanDuration=0，凭据由部署环境管理）
- **本地挂载**：LocalMountProvider（无需远程，`df` 真实）
- 适配器架构：rclone（远程盘）/ qBittorrent（磁力）/ Jellyfin（播放）/ system 全可插拔，运行探测自动切真/空态
- `GET /api/sources` 返回每模块来源；来源徽标中文显示
- 待接（可选）：rclone 远程网盘挂载
- **Jellyfin 已接入**：Docker 容器（:8096，媒体目录只读挂载为 `/media`），凭据由 Jellyfin 自身和 `AURORA_JELLYFIN_TOKEN` 管理；`/api/jellyfin/library|image|stream` 三个代理端点（海报、Range 播放流均带鉴权）

## 后端接口（鉴权）
- `/api/auth/login|/logout|/me`
- `/api/auth/sessions` `/api/auth/sessions/revoke` `/api/auth/password`
- `/api/metrics` `/api/sources` `/api/info` `/api/logs`
- `/api/torrents/add` `/api/torrents/{action}` `/api/torrents/batch` `/api/torrents/peers?hash=`（对等方明细）
- `/api/torrents/upload`（上传 `.torrent` 文件）
- `/api/media` `/api/media/stream` `/api/media/delete` `/api/media/rename` `/api/media/move`
- `/api/media/trash` `/api/media/trash/restore` `/api/media/trash/purge`
- `/api/media/dirs`（目录列表） `/api/media/mkdir`（新建目录） `/api/media/rmdir`（删空目录）
- `/api/jellyfin/library`（海报墙条目） `/api/jellyfin/image`（海报代理） `/api/jellyfin/stream`（Range 播放流代理）

## 部署
- systemd `aurora.service`：开机自启、崩溃自动拉起、内存上限 512M
- Aurora systemd 服务使用独立低权限用户、回环监听和系统级沙箱限制
- qBittorrent / Jellyfin：由 `compose.yaml` 固化持久化挂载；WebUI/管理端口仅回环监听，6881 BT 端口公开
- Nginx 源站仅允许 localhost 和 Cloudflare 地址段，禁止通过服务器公网 IP 绕过 Cloudflare
- `aurora-backup.timer` 每日备份配置与元数据，保留最近 14 份
- 前端 Vite 构建后由 FastAPI 托管（单页应用回退）

## 语言
- 全站用户可见文案**中文**（状态映射：下载中/做种中/排队中/已暂停/出错/已完成、在线/降级/离线、播放中/缓冲中等）

## 环境变量
`AURORA_HOST` `AURORA_PORT` `AURORA_AUTH_USER/PASS` `AURORA_LOCAL_MOUNT`
`AURORA_QBIT_URL/USER/PASS` `AURORA_RCLONE_RC` `AURORA_JELLYFIN/TOKEN`

## 网盘对接（2026-08-14 新增）
- rclone v1.75 systemd `rclone-rcd`（:5572，rc 端点 /rclone，Basic Auth aurora）
- **设置页「网盘对接」面板**：在线状态、remote 列表（类型+修改+删除）、添加网盘弹窗（WebDAV/S3/阿里云盘/Google Drive/OneDrive 表单化创建）；修改时敏感字段留空保持原值
- **网盘连通性测试**：对每个 remote 读取根目录，返回连接结果和响应耗时
- 高级配置入口 `/rclone/`（rclone WebGUI，OAuth 授权类网盘走这里）
- 后端 `/api/rclone/remotes` GET 列表 / POST 创建 / POST delete / POST test；`GET /api/rclone/remotes/config` 读取脱敏配置，`POST /api/rclone/remotes/update` 修改配置（rc `config/create` / `config/update` 使用 name+parameters JSON）
- 无 remote 自动回退本地盘；配 remote 后 ≤2s 自动切 rclone 真实（挂载卡/监控大屏/来源徽标）
