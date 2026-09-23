# 部署 Aurora Media Hub

本文档用于从一台全新的 Debian 12 / Ubuntu 22.04+ 服务器部署 Aurora。以下命令默认使用 root 执行；如果使用普通运维用户，请为需要 root 权限的命令加上 `sudo`。

默认路径和仓库：

- 源码： `/opt/aurora`
- 运行状态： `/var/lib/aurora`
- 媒体目录： `/srv/aurora/media`
- GitHub： `https://github.com/lengxiv/aurora`
- 后端监听： `127.0.0.1:8787`

qBittorrent、Jellyfin 和 rclone 是可选适配器；不安装它们也不影响 Aurora 基础服务启动。

## 0. 前置条件

1. 把域名的 A/AAAA 记录指向新服务器。
2. 防火墙或云安全组放行 TCP 22、80、443；不要放行 8787、8080、8096、5572。
3. 确认 80 端口在申请证书时可以从公网访问。

## 1. 安装基础依赖

Debian 12 自带的 Node.js 版本可能低于项目构建要求。项目锁定的 Vite 依赖要求 Node.js 20.19+ 或 22.12+，这里安装 Node.js 22：

```bash
apt update
apt install -y ca-certificates curl git nginx certbot python3 python3-pip python3-venv build-essential
curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
apt install -y nodejs
node --version
npm --version
```

## 2. 创建运行用户和目录

```bash
if ! getent group aurora >/dev/null 2>&1; then
    groupadd --system aurora
fi
if ! id aurora >/dev/null 2>&1; then
    useradd --system --gid aurora --home-dir /var/lib/aurora --create-home --shell /usr/sbin/nologin aurora
fi
install -d -o aurora -g aurora -m 750 /var/lib/aurora/data
install -d -o aurora -g aurora -m 750 /srv/aurora/media
```

## 3. 拉取源码

```bash
git clone https://github.com/lengxiv/aurora.git /opt/aurora
chown -R root:root /opt/aurora
```

如果目录已经存在，只更新代码，不要重复 `git clone`：

```bash
cd /opt/aurora
git pull --ff-only origin main
```

## 4. 配置环境变量

```bash
cp /opt/aurora/.env.example /etc/aurora.env
chmod 600 /etc/aurora.env
editor /etc/aurora.env
```

至少确认以下值；`AURORA_AUTH_PASS` 请改成长度不少于 12 位的强密码：

```dotenv
AURORA_HOST=127.0.0.1
AURORA_PORT=8787
AURORA_STATE_DIR=/var/lib/aurora
AURORA_DATA_DIR=/var/lib/aurora/data
AURORA_LOCAL_MOUNT=/srv/aurora/media
AURORA_AUTH_USER=admin
AURORA_AUTH_PASS=
```

如果密码留空，首次启动会生成 `/var/lib/aurora/.auth`；生产环境建议显式设置 `AURORA_AUTH_PASS`，并始终保持环境文件权限为 600。

## 5. 安装后端依赖并构建前端

```bash
cd /opt/aurora
python3 -m venv backend/.venv
backend/.venv/bin/pip install --upgrade pip
backend/.venv/bin/pip install -r backend/requirements.txt
npm --prefix frontend ci
npm --prefix frontend run lint
npm --prefix frontend run build
chown -R root:root backend/static
```

前端构建产物会写入 `backend/static/`。该目录由 Git 忽略，部署或更新时都在服务器本地重新构建。

## 6. 安装并启动 Aurora systemd 服务

```bash
install -m 644 /opt/aurora/deploy/systemd/aurora.service.example /etc/systemd/system/aurora.service
chown -R aurora:aurora /var/lib/aurora /srv/aurora/media
systemctl daemon-reload
systemctl enable --now aurora
systemctl status aurora --no-pager
```

服务只监听回环地址，先在服务器本机检查：

```bash
curl -fsS http://127.0.0.1:8787/api/health
journalctl -u aurora -n 50 --no-pager
```

## 7. 安装 Nginx 和 HTTPS

下面以 `p.lengxi.cc` 为例。把域名和邮箱替换成实际值；证书申请前必须先完成 DNS 解析。

```bash
export AURORA_DOMAIN=p.lengxi.cc
export AURORA_EMAIL=your-real-email@example.com
systemctl stop nginx
certbot certonly --standalone --agree-tos --no-eff-email --email "$AURORA_EMAIL" -d "$AURORA_DOMAIN"
install -m 644 /opt/aurora/deploy/nginx/aurora.conf.example /etc/nginx/sites-available/aurora.conf
sed -i "s/dashboard.example.com/$AURORA_DOMAIN/g" /etc/nginx/sites-available/aurora.conf
ln -sfn /etc/nginx/sites-available/aurora.conf /etc/nginx/sites-enabled/aurora.conf
nginx -t
systemctl enable --now nginx
certbot renew --dry-run
```

如果服务器已有 Nginx 站点，请不要覆盖现有配置；将本项目 server 块合并到现有配置后再执行 `nginx -t`。

## 8. 可选：安装 qBittorrent 和 Jellyfin

只有需要磁力下载或 Jellyfin 播放时才安装 Docker 服务：

```bash
apt install -y docker.io docker-compose-plugin
systemctl enable --now docker
docker compose version
cd /opt/aurora
install -d -m 755 qbit/config qbit/downloads jellyfin/config jellyfin/cache
docker compose config
docker compose up -d
docker compose ps
```

Docker 软件源中如果没有 `docker-compose-plugin`，请按 Docker 官方 Debian 安装说明安装 Compose plugin 后再执行上述命令。

qBittorrent 容器默认管理端口只绑定到 127.0.0.1，BT 对等端口为 6881。容器的 PUID/PGID 应与下载目录的实际所有者一致；否则 Aurora 可能能够读取 API，但无法移动或删除文件。

使用 Compose 下载目录作为本地媒体目录时，把 `AURORA_LOCAL_MOUNT` 改为 `/opt/aurora/qbit/downloads`，并确认 systemd 服务允许访问该目录。完成 qBittorrent/Jellyfin 初始配置后，把对应账号、密码和 Token 写入 `/etc/aurora.env`，然后执行：

```bash
systemctl restart aurora
curl -fsS http://127.0.0.1:8787/api/sources
```

## 9. 可选：安装 rclone Remote Control

```bash
apt install -y rclone
install -d -o aurora -g aurora -m 700 /var/lib/aurora/rclone
install -d -o aurora -g aurora -m 700 /var/lib/aurora/.cache
runuser -u aurora -- rclone config --config /var/lib/aurora/rclone/rclone.conf
editor /etc/rclone-rc.env
chmod 600 /etc/rclone-rc.env
```

在 `/etc/rclone-rc.env` 中填写一组只用于回环 RC 接口的账号：

```dotenv
RCLONE_RC_USER=change-me
RCLONE_RC_PASS=change-me
```

将相同的 `用户:密码` 写入 `/etc/aurora.env` 的 `AURORA_RCLONE_RC_AUTH`，然后安装服务：

```bash
install -m 644 /opt/aurora/deploy/systemd/rclone-rc.service.example /etc/systemd/system/rclone-rc.service
systemctl daemon-reload
systemctl enable --now rclone-rc
systemctl restart aurora
curl -fsS http://127.0.0.1:8787/api/sources
```

rclone RC 只监听 127.0.0.1，不要将 5572 直接暴露到公网，也不要把 RC 密码写进 Git。

## 10. 安装每日备份

备份脚本保存账号文件、运行状态、应用配置、rclone 凭据配置和必要的服务配置，不保存下载内容和媒体文件。归档文件权限为 600，恢复时必须继续按敏感配置处理。

```bash
install -d -m 750 /opt/aurora/qbit/config /opt/aurora/qbit/downloads /opt/aurora/jellyfin/config
install -m 644 /opt/aurora/deploy/systemd/aurora-backup.service.example /etc/systemd/system/aurora-backup.service
install -m 644 /opt/aurora/deploy/systemd/aurora-backup.timer.example /etc/systemd/system/aurora-backup.timer
systemctl daemon-reload
systemctl enable --now aurora-backup.timer
systemctl start aurora-backup.service
systemctl list-timers aurora-backup.timer --no-pager
ls -lah /var/backups/aurora
```

默认保留最近 14 份备份。备份目录权限为 700；还应将 `/var/backups/aurora` 复制到另一台存储或对象存储，避免备份和服务器一起损坏。

## 11. 更新部署

```bash
cd /opt/aurora
git pull --ff-only origin main
backend/.venv/bin/pip install -r backend/requirements.txt
npm --prefix frontend ci
npm --prefix frontend run build
systemctl restart aurora
curl -fsS http://127.0.0.1:8787/api/health
```

更新前先确认 Git 状态干净；不要把 `.env`、`.auth`、`.secret`、`backend/data`、`qbit` 或 `jellyfin` 加入 Git。

## 12. 常用排查

```bash
systemctl status aurora --no-pager
journalctl -u aurora -n 100 --no-pager
nginx -t
docker compose -f /opt/aurora/compose.yaml ps
docker compose -f /opt/aurora/compose.yaml logs --tail=100
curl -fsS http://127.0.0.1:8787/api/health
```

如果首页返回登录跳转但 API 健康检查正常，说明后端已经运行，应继续检查 Nginx、DNS 和证书，而不是直接公开 8787 端口。

如果现网使用旧的 `rclone-rcd.service` 单元，也可以继续使用；更新备份脚本后它会同时归档 `rclone-rcd.service` 和文档中的 `rclone-rc.service`（存在才归档）。建议后续将 RC 密码放入 `/etc/rclone-rc.env`，不要直接写入 systemd `ExecStart` 命令行。
