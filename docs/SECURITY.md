# 安全边界

- 不提交 `.env`、`.auth`、`.secret`、Telegram 配置、rclone 配置、qBittorrent 配置、Jellyfin 数据库或任何私钥。
- 不在 issue、截图、日志和公开文档中粘贴密码、Cookie、Bearer token、refresh token 或完整 webhook URL。
- 管理接口只监听回环地址，公网通过经过认证的反向代理或 Cloudflare 访问。
- `AURORA_LOCAL_MOUNT` 应指向专用媒体目录，不要指向 `/`、`/home` 或包含系统凭据的目录。
- GitHub Actions 使用 Secrets 保存部署凭据；不要把 SSH 私钥或服务器密码写进 workflow。
- 一旦密钥进入 Git 历史、构建日志或聊天记录，按已经泄露处理，立即撤销并轮换。
