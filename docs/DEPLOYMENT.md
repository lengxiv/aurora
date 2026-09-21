# 部署 Aurora

以下步骤适用于一台新的 Debian/Ubuntu 服务器。生产部署建议把源码放在 `/opt/aurora`，把运行状态放在 `/var/lib/aurora`，这样更新代码时不会覆盖账号、会话和通知数据。

## 安装依赖

```bash
apt update
apt install -y git python3 python3-venv nodejs npm
git clone git@github.com:YOUR_ACCOUNT/aurora-media-hub.git /opt/aurora
cd /opt/aurora
```

## 配置环境

```bash
install -d -m 750 /var/lib/aurora/data
cp .env.example /etc/aurora.env
chmod 600 /etc/aurora.env
editor /etc/aurora.env
```

至少检查 `AURORA_STATE_DIR`、`AURORA_DATA_DIR` 和 `AURORA_LOCAL_MOUNT`。密码和各类 token 只写入 `/etc/aurora.env` 或服务器上的受限文件，不写入 Git。

## 安装 Python 和构建前端

```bash
cd /opt/aurora
python3 -m venv backend/.venv
backend/.venv/bin/pip install --upgrade pip
backend/.venv/bin/pip install -r backend/requirements.txt
npm --prefix frontend ci
npm --prefix frontend run build
```

前端构建产物会生成到 `backend/static`。该目录默认不提交到 Git，部署时在服务器本地构建即可。

## 启动后端

开发检查可以直接运行：

```bash
set -a
. /etc/aurora.env
set +a
cd /opt/aurora/backend
.venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8787
```

生产环境使用 systemd，确保 Nginx 或 Cloudflare Tunnel 只把请求转发到 `127.0.0.1:8787`，不要直接公开 Uvicorn 端口。反向代理配置还应正确传递 `Host`、`X-Forwarded-For` 和 `X-Forwarded-Proto`。

## 关联 qBittorrent / Jellyfin / rclone

这些服务是可选适配器。先启动对应服务，再在环境文件中配置 `AURORA_QBIT_*`、`AURORA_JELLYFIN*` 或 `AURORA_RCLONE_*`，最后重启 Aurora。没有可用适配器时，界面会显示未接入状态，不影响系统磁盘和带宽数据。

## 验证

```bash
curl -I http://127.0.0.1:8787/login
python3 -m unittest discover -s tests -p 'test_*.py'
```

首次启动生成的 `.auth` 和 `.secret` 必须位于 `AURORA_STATE_DIR`，权限应为 `600`。生产备份使用 `scripts/backup.sh`，并确保备份目录权限为 `700`。
