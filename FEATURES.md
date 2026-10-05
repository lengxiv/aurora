# Aurora · 功能清单

> 记录到 2026-10-05 为止已实现的功能。实际访问地址由部署环境决定（后端 systemd `aurora.service` 或 Docker Compose 容器，FastAPI :8787）。

## 更新记录（2026-10-05 · 闭环批次）
- **RSS 自动订阅**（`/rss`）：基于 qBittorrent 内置 RSS 引擎（4.x/5.x 规则格式双写兼容）；订阅源增删改查/手动刷新/错误徽标；规则支持包含/排除关键字或正则、集数过滤、作用订阅源多选、自动分类与保存目录、完成后自动转存网盘（复用转存标记机制）；匹配预览须先保存规则（可保持停用）。
- **下载完成自动整理**（设置页）：按分类规则把完成任务硬链接/拷贝/移动到媒体库目录（默认硬链接，同盘瞬时且不打断做种），可选整理后自动触发 Jellyfin 全库扫描；支持预览、预演、目标冲突跳过不覆盖、最小体积过滤、按 Hash 防重复处理与执行历史。
- **做种策略可撤销 + 预演**：预演（dry run）完整走判定分支零副作用；删除任务前自动导出 `.torrent` 留档、内容整体移入媒资回收站（qbit 不再直接删文件），「最近清理」区一键撤销 = 文件回原位 + 重加任务；撤销记录保留 7 天上限 50 条；内容不在媒体目录时回退 qbit 直删并在预演中标注「不可恢复」；回收站恢复/彻底删除支持目录条目。
- **验证结果**：后端 102 项测试通过（新增 RSS 规则兼容、整理引擎、策略撤销等 13 项），前端生产构建和 lint 通过。

## 更新记录（2026-10-05）
- **版本号统一**：根目录 `VERSION` 是唯一版本来源；`/api/health`、`/api/info` 返回后端版本，前端构建注入 `__APP_VERSION__`，设置页显示「后端版本 / 前端构建」。
- **在线检查更新**：设置页可对比 GitHub Releases 检查新版本（服务端缓存 30 分钟，需登录，仓库可用 `AURORA_REPO` 配置），发现新版本时给出发布说明链接。
- **容器化部署**：多阶段 `Dockerfile`（非 root、HEALTHCHECK、OCI label）+ `compose.yaml` 的 `aurora` 服务，支持与 qBittorrent/Jellyfin 全栈编排；更新 = 换镜像版本重建容器。
- **发布流水线**：推送 `vX.Y.Z` 标签自动构建 amd64/arm64 镜像到 GHCR 并创建 GitHub Release；CI 增加版本一致性校验与镜像冒烟测试。

## 更新记录（2026-09-24）
- **文件选择性下载**：任务详情文件列表支持勾选下载内容，提交后将未选文件设置为跳过、选中文件恢复为普通优先级；刷新详情后保留 qBittorrent 返回的实际选择状态。
- **分类批量控制**：任务列表支持按 qBittorrent 分类筛选，并批量设置下载/上传限速、移动保存目录和转存网盘；按分类执行时只操作该分类任务。
- **分类默认目标映射**：设置页可为每个 qBittorrent 分类配置本地相对目录、默认网盘和网盘目录；添加磁力或 `.torrent` 时自动继承，手动填写的目标优先，且可选择仅本地下载覆盖默认网盘目标。
- **接口与安全校验**：新增 `/api/torrents/mappings`，扩展 `/api/torrents/batch` 与 `/api/torrents/advanced`；分类、路径、网盘目标、文件数量和限速参数均由后端校验。
- **验证结果**：后端 54 项测试通过，前端生产构建和 lint 通过；在线健康检查、qBittorrent、分类映射和真实任务详情接口已验证，未执行真实批量移动或网盘转存。

## 更新记录（2026-09-23）
- **qBittorrent 队列管理**：设置页可读取和修改队列调度、最大活动任务、最大下载任务、最大做种任务、最大校验任务和新任务置顶；提供“一键不限做种”，使用 `9999` 表示不限，避免 qBittorrent 的 `0` 造成全部任务排队。
- **播放功能增强**：本地视频/音频/Jellyfin 统一续播；增加倍速、上下项切换、画中画、失败重试和音频连续播放。
- **字幕兼容**：按视频同名规则匹配字幕，SRT/ASS/SSA 自动转换为浏览器兼容的 WebVTT，支持同名多字幕切换。
- **播放流稳定性**：本地流明确 MIME 与 byte-range；Jellyfin 流代理增加上游超时、错误处理和缓存头转发。
- **传输任务恢复与重试**：rclone 传输元数据持久化，服务重启后保留任务关联；失败任务保留暂存文件并支持重试；磁力转存不会因任务状态丢失而重复上传。
- **网盘目标目录浏览**：添加磁力或 `.torrent` 时可逐级读取所选网盘目录并选择目标路径，也保留手动输入 bucket / 路径。
- **运行维护加固**：qBittorrent Session 访问串行化，活动日志写入错误进入 systemd 日志，备份纳入 rclone 配置和服务文件。
- **磁力下载位置选择**：添加磁力链接或 `.torrent` 文件时，可选择已有下载目录，或在弹窗内新建目录；默认使用下载根目录。
- **网盘下载目标**：可选择已接入网盘和目标目录；任务先下载到本地，完成后由后台自动上传到网盘，并在任务列表显示等待、上传中、已上传或失败状态。
- **网盘目标选择修复**：添加任务时预加载网盘列表，网盘读取失败可手动刷新，并显示明确的空列表提示。
- **路径安全校验**：后端仅允许使用本地下载盘内的相对目录，并映射为 qBittorrent 的 `/downloads/...` 路径。
- **任务提交接口**：`/api/torrents/add` 和 `/api/torrents/upload` 支持保存目录参数，磁力与种子文件行为保持一致。
- **验证结果**：后端 47 项测试通过，前端生产构建和 lint 通过。
- **队列管理验证**：后端 47 项测试通过，前端生产构建和 lint 通过，qBittorrent 实机队列参数已验证。
- **P0 任务控制中心**：任务详情抽屉读取真实 qBittorrent 任务、文件、Tracker 和 Peer；支持暂停/继续、强制开始、重新校验、重新汇报、队列调整、单任务限速、保存目录调整、单文件优先级和安全删除。
- **P0 分类与标签**：设置页支持 qBittorrent 分类目录和用户标签管理；添加磁力/种子及任务详情支持分类、标签编辑，系统关联标签受保护；任务列表可按名称、分类和标签搜索。
- **P0 做种策略**：设置页支持按 Hash 或分类配置分享率、做种时长、空闲时长，提供预览、手动应用、暂停/通知/网盘转存/移除动作和删除保护；策略默认关闭并写入活动日志。
- **P0 验证结果**：后端 51 项测试通过，前端生产构建和 lint 通过；真实 qBittorrent 任务详情、文件数、Tracker 数、分类标签接口和策略预览已在线验证。功能提交：`3d8aacf`。

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
- **添加磁力**弹窗（真实提交 qBittorrent，可选择本地目录或完成后上传到网盘）
- **上传种子文件**（管理台选择 `.torrent` 文件，单文件最大 20 MB，可选择本地目录或完成后上传到网盘）
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
- **在线点播**：HTML5 播放器，后端 byte-range（可拖动进度），本地视频/音频/Jellyfin 统一续播
- **播放控制**：倍速、上一项/下一项、画中画、全屏、失败重试；视频播放完自动进入下一项，音频同样支持连续播放
- **字幕播放**：按视频同名字幕匹配，SRT/ASS/SSA 转 WebVTT，多语言字幕可在浏览器播放器中切换
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
- `/api/torrents/add` `/api/torrents/{action}` `/api/torrents/batch` `/api/torrents/peers?hash=`（对等方明细；添加接口支持 `save_path` 和网盘目标）
- `/api/torrents/upload`（上传 `.torrent` 文件，支持 `save_path` 和网盘目标）
- `/api/torrents/detail?hash=` `/api/torrents/advanced`（任务详情与白名单高级操作）
- `/api/torrents/labels` `/api/torrents/category*` `/api/torrents/tag*`（分类、标签管理与任务关联）
- `/api/torrents/policies` `/api/torrents/policies/preview` `/api/torrents/policies/apply`（做种策略管理、预览和应用）
- `/api/qbittorrent/queue`（读取和修改队列调度参数）
- `/api/media` `/api/media/stream` `/api/media/delete` `/api/media/rename` `/api/media/move`
- `/api/media/trash` `/api/media/trash/restore` `/api/media/trash/purge`
- `/api/media/dirs`（目录列表） `/api/media/mkdir`（新建目录） `/api/media/rmdir`（删空目录）
- `/api/jellyfin/library`（海报墙条目） `/api/jellyfin/image`（海报代理） `/api/jellyfin/stream`（Range 播放流代理）

## 部署
- systemd `aurora.service`：开机自启、崩溃自动拉起、内存上限 512M
- Aurora systemd 服务使用独立低权限用户、回环监听和系统级沙箱限制
- qBittorrent / Jellyfin：由 `compose.yaml` 固化持久化挂载；WebUI/管理端口仅回环监听，39876 BT 端口公开
- Nginx 源站仅允许 localhost 和 Cloudflare 地址段，禁止通过服务器公网 IP 绕过 Cloudflare
- `aurora-backup.timer` 每日备份配置与元数据，保留最近 14 份
- 前端 Vite 构建后由 FastAPI 托管（单页应用回退）

## 语言
- 全站用户可见文案**中文**（状态映射：下载中/做种中/排队中/已暂停/出错/已完成、在线/降级/离线、播放中/缓冲中等）

## 环境变量
`AURORA_HOST` `AURORA_PORT` `AURORA_AUTH_USER/PASS` `AURORA_LOCAL_MOUNT`
`AURORA_QBIT_URL/USER/PASS` `AURORA_RCLONE_RC` `AURORA_JELLYFIN/TOKEN`

## 网盘对接（2026-08-14 新增）
- rclone v1.75 systemd `rclone-rc`（:5572，rc 端点 /rclone，Basic Auth aurora）
- **设置页「网盘对接」面板**：在线状态、remote 列表（类型+修改+删除）、添加网盘弹窗（WebDAV/S3/阿里云盘/Google Drive/OneDrive 表单化创建）；已接入网盘支持修改名称和配置，敏感字段留空保持原值
- **网盘连通性测试**：对每个 remote 读取根目录，返回连接结果和响应耗时
- **网盘文件管理与传输中心**：远程目录浏览、面包屑导航、多选、当前目录搜索与排序、上传到当前目录、本地下载、remote 间复制/移动、重命名、递归删除、新建目录；上传实时显示本地读取/提交进度，随后切换为网盘侧 rclone 进度/速度；支持取消、失败/取消重试和清理历史任务
- 高级配置入口 `/rclone/`（rclone WebGUI，OAuth 授权类网盘走这里）
- 后端 `/api/rclone/remotes` GET 列表 / POST 创建 / POST delete / POST test；`GET /api/rclone/remotes/config` 读取脱敏配置，`POST /api/rclone/remotes/update` 修改配置（rc `config/create` / `config/update` 使用 name+parameters JSON）
- 文件 API：`GET /api/rclone/files`、`POST /api/rclone/files/mkdir|rename|delete`、`POST /api/rclone/transfers/upload|download|copy`、`GET /api/rclone/transfers`、`POST /api/rclone/transfers/cancel|retry|clear`
- 无 remote 自动回退本地盘；配 remote 后 ≤2s 自动切 rclone 真实（挂载卡/监控大屏/来源徽标）
