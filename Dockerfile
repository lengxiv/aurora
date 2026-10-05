# 多阶段构建：stage 1 用 Node 构建前端静态产物，stage 2 用 Python 运行时托管。
# 基础镜像按大版本钉住；如需完全可复现，可进一步钉到具体 digest。
# 不使用 `# syntax=` 指令：避免构建时额外拉取 buildkit 前端镜像，
# 本文件用到的指令 BuildKit 内置前端全部支持。

ARG NODE_VERSION=22-alpine
ARG PYTHON_VERSION=3.11-slim

# ---------------------------------------------------------------------------
# Stage 1: 构建前端（产物输出到 /build/backend/static）
FROM node:${NODE_VERSION} AS frontend-build

WORKDIR /build
# 先拷 VERSION 与依赖清单，充分利用层缓存
COPY VERSION ./VERSION
COPY frontend/package.json frontend/package-lock.json ./frontend/
WORKDIR /build/frontend
RUN npm ci --no-audit --no-fund

COPY frontend/ ./
# vite build 会读取 ../VERSION（/build/VERSION）注入前端构建版本号
RUN npm run build

# ---------------------------------------------------------------------------
# Stage 2: 运行时
FROM python:${PYTHON_VERSION}

# 发布流水线通过 build-arg 注入 tag 对应的版本号，仅用于 OCI label；
# 运行时版本以镜像内 /app/VERSION 为准
ARG APP_VERSION=dev

LABEL org.opencontainers.image.title="Aurora Media Hub" \
      org.opencontainers.image.description="网盘中转 + 磁力调度 + 流媒体播放 + 实时监控 一体化面板" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.source="https://github.com/lengxiv/aurora" \
      org.opencontainers.image.vendor="aurora" \
      org.opencontainers.image.url="https://github.com/lengxiv/aurora/pkgs/container/aurora"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    AURORA_STATE_DIR=/var/lib/aurora \
    AURORA_DATA_DIR=/var/lib/aurora/data \
    AURORA_LOCAL_MOUNT=/srv/aurora/media

WORKDIR /app/backend

# 依赖单独成层：backend 代码变动时不必重装依赖
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./
COPY --from=frontend-build /build/backend/static ./static
COPY VERSION /app/VERSION

# 非 root 运行；UID/GID 固定 1000，与 compose 中 qBittorrent/Jellyfin 的
# PUID/PGID=1000 及下载目录属主约定一致（不用 --system：固定 UID 1000
# 超出发行版 SYS_UID_MAX，属普通服务账号语义）
RUN groupadd --gid 1000 aurora \
 && useradd --uid 1000 --gid aurora --home-dir /var/lib/aurora --create-home --shell /usr/sbin/nologin aurora \
 && install -d -o aurora -g aurora -m 750 /var/lib/aurora/data \
 && mkdir -p /srv/aurora/media \
 && chown aurora:aurora /srv/aurora/media /app/VERSION

USER aurora

# 运行状态与会话密钥都写在这里，必须挂载持久化
VOLUME ["/var/lib/aurora"]

EXPOSE 8787

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8787/api/health', timeout=4).status == 200 else 1)"

# 容器内必须监听 0.0.0.0 才能映射到宿主机；
# --proxy-headers + 信任内网网段：nginx 追加的 X-Forwarded-For 右起第一个
# 非受信地址即真实客户端，登录限流仍按真实来源 IP 计数，客户端伪造的头不生效
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8787", \
     "--proxy-headers", "--forwarded-allow-ips", "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,127.0.0.1,::1"]
