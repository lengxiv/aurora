# Aurora · rclone 网盘对接指南

> 2026-08-14 启用。rclone v1.75.0 + systemd `rclone-rcd.service`（:5572，回环，POST rc API）。
> Aurora 后端 `RcloneProvider` 已适配：探测/调用走 POST，`--rc-serve` 无效已去掉。
> rclone 在线但**未配置 remote 时自动回退本地盘**；配置 remote 后自动切换，前端零改动。

## 示例部署

| 项 | 值 |
|----|----|
| rclone | `/usr/bin/rclone` v1.75.0 |
| rc 服务 | `rclone-rcd.service`（enabled，开机自启） |
| rc 端点 | `http://127.0.0.1:5572/rclone`（仅回环） |
| 认证 | Basic Auth（账号和密码由部署环境管理） |
| 可视化配置入口 | **https://<your-domain>/rclone/**（如启用公网入口，必须经过 HTTPS 和访问控制） |
| 配置目录 | `~/.config/rclone/rclone.conf` |
| Aurora 环境变量 | `AURORA_RCLONE_RC=http://127.0.0.1:5572/rclone`，`AURORA_RCLONE_RC_AUTH=<private-value>` |

## 可视化接入（推荐，无需命令行）

1. 浏览器打开 **https://<your-domain>/rclone/**
2. 弹出 HTTP 认证框 → 输入部署环境配置的账号和密码
3. WebUI 内再填一次：地址 `https://<your-domain>/rclone/` + 同上账号密码 → Login
4. 进入后点 **Configs** → **Create/Add Remote** → 选网盘类型 → 按表单填授权
5. 保存后回到 **Dashboard**，或直接在 **Explorer** 浏览网盘文件

> Aurora 侧无需任何操作：保存 remote 后 ≤2s 自动从「本地」切到「rclone 真实」。

### 3. Aurora 自动生效

配置 remote 后，Aurora 下一次轮询（≤2s）即切到 rclone 来源：

- 管理台挂载卡 / 实时监控「挂载状态」：显示每个 remote（名字、容量、在线状态）
- 顶部来源徽标：`挂载 · rclone 真实`
- `GET /api/info` → `providers.rclone.online: true`、`sources.mounts: "rclone"`

> 容量显示注意：`RcloneProvider` 对 remote 的 `usedGb/capGb` 返回 0（rclone rc 无统一容量协议），挂载行容量区显示 `—`，前端 Bar 已做 NaN 防护。

## 常用网盘配置示例

### 阿里云盘（OpenAPI，rclone ≥1.63 内置）

```bash
rclone config
# n → 名字 aliyun → 32 → aliyundrive
# 填 client_id / client_secret（阿里云盘开放平台申请，或留空用默认）
# 浏览器授权 → 得到 refresh_token
```

验证：`rclone lsd aliyun:`

### Google Drive（OAuth）

```bash
rclone config
# n → 名字 gd → 18 → drive
# client_id/secret 留空用默认
# scope 选 drive（完整）或 drive.readonly
# 浏览器授权
```

验证：`rclone about gd:`

### OneDrive

```bash
rclone config
# n → 名字 od → 25 → onedrive
# 选国家/区域（China 用 21vianet）
# 浏览器授权
```

### Cloudflare R2 / S3 兼容（无 OAuth，最快）

```bash
rclone config
# n → 名字 r2 → 5 → s3
# provider 选 Cloudflare
# endpoint: https://<ACCOUNT_ID>.r2.cloudflarestorage.com
# access_key_id / secret_access_key
# region: auto
```

验证：`rclone lsd r2:`

### WebDAV（通用，挂任意 WebDAV 服务）

```bash
rclone config
# n → 名字 dav → 33 → webdav
# url: https://dav.example.com
# vendor: 按服务商选（nextcloud/owncloud/other）
# user / pass
```

### 百度网盘

原生 rclone 不支持，需第三方 fork（如 `qjfoidnh/BaiduPCS-Go` WebDAV 桥）或社区 `rclone-baidunetdisk`。接好后按 WebDAV 方式接入。

## 常见问题

| 现象 | 原因/处理 |
|------|-----------|
| `GET /core/version` 返回 404 | rclone rc API 只接受 **POST**（Aurora 已改 POST，此现象只影响手动 curl） |
| 配置了 remote 但 Aurora 仍显示「本地」 | 检查 WebUI 里 remote 是否保存成功；看 `journalctl -u rclone-rcd` |
| 挂载容量显示 `—` | rclone rc 无容量协议，属预期；容量看 WebUI 或 `rclone about <remote>:` |
| 想隐藏某 remote | WebUI Configs 里 Delete，或编辑 `~/.config/rclone/rclone.conf` |

## 运维

```bash
systemctl status rclone-rcd    # rc 服务状态
journalctl -u rclone-rcd -n 50 # 日志
systemctl restart rclone-rcd   # 改配置后无需重启（配置动态读），仅服务异常时用
```

- rclone.conf 含网盘 token，注意权限（`chmod 600 /root/.config/rclone/rclone.conf`）
- rc 服务仅监听回环 + no-auth，公网不可达；若未来要公网暴露必须加 `--rc-user/--rc-pass` 并同步改 Aurora 认证逻辑
