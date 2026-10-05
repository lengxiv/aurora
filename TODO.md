# Aurora 开发待办

> 维护日期：2026-10-05。优先级按用户收益、实现风险和对现有架构的影响排序。

> 本文件只保留未完成事项。已完成内容和验证结果见 [`FEATURES.md`](FEATURES.md) 与 [`CHANGELOG.md`](CHANGELOG.md)。

## P0：任务控制中心

### 做种策略和生命周期

- [ ] 增加策略撤销入口的批量操作（逐条撤销已支持，见 2026-10-05 闭环批次）。
- [ ] 清理未注册种子、孤立文件时提供预览和干运行模式（回收站式删除与撤销已落地）。

验收标准：自动清理支持预览、干运行、回收站和撤销，且不会影响做种保护任务。

## P1：实时性与自动化

### 队列和传输体验

- [ ] 用 qBittorrent 增量同步接口减少全量轮询，任务状态变化尽快反映到界面。
- [ ] 显示排队原因、活动槽位占用、等待时长和预计开始状态。
- [ ] 增加全局限速、备用速度模式和按时间段限速。
- [ ] qBittorrent 与 rclone 任务统一显示事件时间线、重试次数和失败原因。

### RSS、搜索和规则自动添加

- [ ] RSS 订阅、关键词/正则过滤、排除规则和自动保存目录。
- [ ] 对接 Prowlarr/Torznab，支持搜索、结果筛选和手动添加。
- [ ] 通过 Webhook 对接 Sonarr/Radarr，不在 Aurora 内重复实现索引器爬虫。
- [ ] 自动化规则提供测试运行、执行日志、暂停和单次重试。

验收标准：规则默认只预览不提交；用户确认后才添加任务，重复种子、非法路径和来源不可用时不会产生孤立任务。

## P2：生态联动和运维增强

### Cross-seed 和 Tracker 运维

- [ ] 对接 cross-seed，扫描已有内容并展示可交叉做种结果。
- [ ] Tracker 在线状态、错误码、最后汇报时间和批量重新汇报。
- [ ] 重复内容检测、硬链接/复用文件提示和磁盘占用预估。

### 媒资与播放

- [ ] 播放进度从浏览器本地存储升级为服务端同步，支持多设备续播。
- [ ] Jellyfin 播放会话、暂停/停止控制和正在播放列表。
- [ ] 下载完成后的媒体整理、重命名和库刷新流程可配置化。

### 网盘与备份

- [ ] rclone 上传校验、断点续传、冲突策略和带宽/并发控制。
- [ ] 定时同步、远程到远程复制和任务优先级。
- [ ] 设置、网盘配置和任务元数据的一键备份/恢复，恢复前提供差异预览。

## 开发原则

- 优先调用 qBittorrent、rclone、Jellyfin、Prowlarr 等成熟 API，不重复实现成熟服务的核心能力。
- 破坏性操作默认保护文件、可预览、可审计、可重试。
- 新增真实服务对接必须有在线/离线空态、超时、错误提示和后端测试。
- 每完成一个待办，同步更新本文件、`FEATURES.md` 和 `CHANGELOG.md`，记录验证结果与提交号。

## 调研参考

- [qBittorrent](https://github.com/qbittorrent/qBittorrent)：下载、队列、Tracker、Peer、分类和限速能力。
- [VueTorrent](https://github.com/VueTorrent/VueTorrent)：任务详情、文件选择、标签分类和响应式 WebUI 体验。
- [qui](https://github.com/autobrr/qui)：自动化、Cross-seed、备份恢复和现代 qBittorrent 管理界面。
- [qbit_manage](https://github.com/StuffAnThings/qbit_manage)：分享策略、自动标签、孤立文件和回收站治理。
- [cross-seed](https://github.com/cross-seed/cross-seed)：交叉做种匹配和自动添加。
- [autobrr](https://github.com/autobrr/autobrr)：RSS、Torznab、Webhook 和事件驱动自动化。
- [Prowlarr](https://github.com/Prowlarr/Prowlarr)、[Sonarr](https://github.com/Sonarr/Sonarr)、[Radarr](https://github.com/Radarr/Radarr)：索引器、媒体搜索和自动整理生态。
