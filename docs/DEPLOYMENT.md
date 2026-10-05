# 部署 Aurora Media Hub

本文档用于从一台全新的 Debian 12 / Ubuntu 22.04+ 服务器部署 Aurora。以下命令默认使用 root 执行；如果使用普通运维用户，请为需要 root 权限的命令加上 `sudo`。

默认路径和仓库：

- 源码： `/opt/aurora`
- 运行状态： `/var/lib/aurora`
- 媒体目录： `/srv/aurora/media`
- GitHub： `https://github.com/lengxiv/aurora`
- 后端监听： `127.0.0.1:8787`

qBittorrent、Jellyfin 和 rclone 是可选适配器；不安装它们也不影响 Aurora 基础服务启动。

Aurora 本体支持两种部署方式，选其一即可：

- **方式 A：Docker Compose 容器化部署（推荐）** —— 应用本体跑在容器里，更新 = 拉新镜像重建容器。直接跳到 [第 13 节](#13-容器化部署docker-compose推荐)，完成后再回来做第 7 节（Nginx 和 HTTPS）。
- **方式 B：systemd 裸机部署** —— 按 1–6 节在宿主机安装运行，更新流程见第 11 节。

## 0. 前置条件

1. 把域名的 A/AAAA 记录指向新服务器。
2. 防火墙或云安全组放行 TCP 22、80、443；做种需要再放行 TCP/UDP 39876（BT 对等端口，见第 8 节）。不要放行 8787、8080、8096、5572。
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

下面以 `aurora.example.com` 为例。把域名和邮箱替换成实际值；证书申请前必须先完成 DNS 解析。

```bash
export AURORA_DOMAIN=aurora.example.com
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
# 容器以 PUID/PGID=1000 运行：目录属主必须是 1000:1000，
# 否则 root:root 755 的下载目录对容器只读，种子能添加但落盘失败
chown -R 1000:1000 qbit jellyfin
docker compose config
docker compose up -d
docker compose ps
```

Docker 软件源中如果没有 `docker-compose-plugin`，请按 Docker 官方 Debian 安装说明安装 Compose plugin 后再执行上述命令。

qBittorrent 容器管理端口只绑定到 127.0.0.1。BT 对等端口固定为 39876（compose 已映射），但 qBittorrent 的会话监听端口保存在其配置文件里、没有对应的环境变量：首次启动后在 WebUI 的「设置 → 连接」把「传入连接的端口」改为 39876（或直接编辑 `/opt/aurora/qbit/config/qBittorrent/qBittorrent.conf` 的 `Session\\Port=39876` 后重启容器），否则 compose 映射的 39876 无人监听、入站 peer 不通。容器的 PUID/PGID 应与下载目录的实际所有者一致；否则 Aurora 可能能够读取 API，但无法移动或删除文件。

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
在 `/etc/rclone-rc.env` 中填写只用于 RC 和 WebGUI 的账号，并在 Nginx 中创建同一组账号：

```dotenv
RCLONE_RC_USER=change-me
RCLONE_RC_PASS=change-me-with-at-least-12-chars
```

```bash
source /etc/rclone-rc.env
apt install -y apache2-utils
htpasswd -c /etc/nginx/.htpasswd-aurora-rclone "$RCLONE_RC_USER"
chmod 600 /etc/nginx/.htpasswd-aurora-rclone
```
将相同的 `用户:密码` 写入 `/etc/aurora.env` 的 `AURORA_RCLONE_RC_AUTH`，然后安装服务：

```bash
install -m 644 /opt/aurora/deploy/systemd/rclone-rc.service.example /etc/systemd/system/rclone-rc.service
systemctl daemon-reload
systemctl enable --now rclone-rc
systemctl restart aurora
curl -fsS http://127.0.0.1:8787/api/sources
```

rclone RC 只监听 127.0.0.1；公网 WebGUI 仅通过 HTTPS 的 `/rclone/` 暴露，并同时受 Nginx 与 rclone Basic Auth 保护。不要将 5572 直接暴露到公网，也不要把 RC 密码写进 Git。

## 10. 安装每日备份

备份脚本保存账号文件、运行状态、应用配置、rclone 凭据配置和必要的服务配置，不保存下载内容和媒体文件。归档文件权限为 600，恢复时必须继续按敏感配置处理。

```bash
install -d -m 750 /opt/aurora/qbit/config /opt/aurora/qbit/downloads /opt/aurora/jellyfin/config
chown -R 1000:1000 /opt/aurora/qbit /opt/aurora/jellyfin
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

更新前先确认 Git 状态干净；不要把 `.env`、`.auth`、`.secret`、`backend/data`、`qbit` 或 `jellyfin` 加入 Git。容器化部署的更新方式见第 13.4 节。

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

## 13. 容器化部署（Docker Compose，推荐）

应用本体以多阶段镜像交付：Node 阶段编译前端，Python 阶段以非 root 用户（UID/GID 1000）运行 FastAPI 并托管前端产物。镜像内置 HEALTHCHECK（探测 `/api/health`），状态持久化在卷里，更新只需换镜像重建容器。

### 13.1 前置条件

1. 安装 Docker 与 Compose plugin（同第 8 节）。
2. 在仓库根目录创建 compose 的 `.env`（**这是给 compose 做变量插值的，与凭据环境文件是两个文件**）：

```bash
cd /opt/aurora
cat > .env <<'EOF'
AURORA_ROOT=/opt/aurora
# 钉住镜像版本：跟随 Release 标签，不要用 latest
AURORA_IMAGE_TAG=0.4.0
QBIT_TAG=latest
JELLYFIN_TAG=latest
AURORA_ENV_FILE=/etc/aurora.env
EOF
chmod 600 .env
```

3. 准备凭据环境文件（第 4 节的 `/etc/aurora.env`，容器通过 `env_file` 读取 `AURORA_AUTH_PASS`、qBittorrent/Jellyfin 账号等敏感配置）。

### 13.2 首次启动

```bash
cd /opt/aurora
install -d -o 1000 -g 1000 -m 750 aurora-state
install -d -m 755 qbit/config qbit/downloads
chown -R 1000:1000 qbit          # 容器内 UID=1000，目录属主必须一致
docker compose pull aurora       # 或 docker compose build aurora 从源码构建
docker compose up -d aurora      # 只启动应用本体；需要 qbit/Jellyfin 再 up -d 全部
docker compose ps
curl -fsS http://127.0.0.1:8787/api/health   # 应返回 {"status":"ok","version":"0.4.0",...}
```

说明：

- 容器只把 `127.0.0.1:8787` 映射到宿主机，与 systemd 部署一致，第 7 节的 Nginx 配置无需改动。
- `AURORA_QBIT_URL`/`AURORA_JELLYFIN` 默认指向 compose 服务名（`http://qbittorrent:8080`、`http://jellyfin:8096`）；rclone 在宿主机时默认走 `host.docker.internal:5572`（compose 已配置 `host-gateway`）。要覆盖这些默认值，改 `.env` 或 compose 的 `environment` 段——`environment` 优先级高于 `env_file` 里的同名变量。
- 首次启动会生成随机管理员密码，写入 `aurora-state/.auth`（容器内 `/var/lib/aurora/.auth`）。
- 升级或重建容器不丢数据：状态在 `aurora-state/`，媒体在 `qbit/downloads/`。
- 第 10 节的每日备份在容器模式下照常可用，但要给 `aurora-backup.service` 补两行，否则归档会缺 `.auth`/`.secret` 与运行数据：

```ini
[Service]
Environment=AURORA_STATE_DIR=/opt/aurora/aurora-state
Environment=AURORA_DATA_DIR=/opt/aurora/aurora-state/data
```

### 13.3 全栈一起部署

```bash
cd /opt/aurora
docker compose up -d             # aurora + qBittorrent + Jellyfin
```

qBittorrent/Jellyfin 的初始化与端口注意事项同第 8 节（BT 对等端口 39876 仍需在 WebUI 设置）。

### 13.4 更新与回滚

```bash
cd /opt/aurora
# 更新 .env 里的 AURORA_IMAGE_TAG 到新版本号后：
docker compose pull aurora
docker compose up -d aurora
docker compose logs --tail=50 aurora
# 回滚 = 把 AURORA_IMAGE_TAG 改回旧版本号，重复上面三行
```

镜像版本随 GitHub Release 发布（`ghcr.io/lengxiv/aurora:X.Y.Z`）。应用内「设置 → 检查更新」可以对比当前版本与 GitHub 最新 Release；发现新版本后更新 `.env` 中的 `AURORA_IMAGE_TAG` 即可。systemd 部署同样可以使用该入口检查新版本，然后走第 11 节的 `git pull` 流程。

### 13.5 容器排查

```bash
docker compose ps                     # 健康状态（healthy/unhealthy）
docker compose logs --tail=100 aurora
docker inspect --format '{{json .State.Health}}' aurora
# 镜像内没有 curl，用 python 从容器内部探测
docker compose exec aurora python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8787/api/health').read())"
```
