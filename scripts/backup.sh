#!/bin/sh
set -eu
# 归档集中了 .auth/.secret/rclone.conf 等全部凭据：tar 以进程 umask（root 默认
# 022 → 0644）创建、压缩完才 chmod 600，期间同机任意用户可读走全部凭据。
# 在创建任何文件前收紧 umask，杜绝这个窗口。
umask 077

project_root=${AURORA_ROOT:-/opt/aurora}
backup_root=${AURORA_BACKUP_ROOT:-/var/backups/aurora}
state_root=${AURORA_STATE_DIR:-$project_root/backend}
data_root=${AURORA_DATA_DIR:-$project_root/backend/data}
nginx_conf=${AURORA_NGINX_CONF:-/etc/nginx/sites-enabled/aurora.conf}
rclone_config=${AURORA_RCLONE_CONFIG:-/var/lib/aurora/rclone/rclone.conf}
rclone_env=${AURORA_RCLONE_ENV:-/etc/rclone-rc.env}
aurora_env=${AURORA_ENV_FILE:-/etc/aurora.env}
rclone_service=${AURORA_RCLONE_SERVICE:-/etc/systemd/system/rclone-rc.service}
stamp=$(date +%Y%m%d-%H%M%S)
archive="$backup_root/aurora-config-$stamp.tar.gz"
archive_list=$(mktemp)
trap 'rm -f -- "$archive_list"' EXIT HUP INT TERM

install -d -m 700 "$backup_root"
# timer 与手动 systemctl start 理论可并发；flock 保证同一时刻只有一份备份在跑
exec 9>>"$backup_root/.lock"
if ! flock -n 9; then
  echo "[aurora-backup] another backup run holds the lock, exiting" >&2
  exit 0
fi
add_path() {
  if [ -e "$1" ]; then
    printf '%s\n' "$1" >> "$archive_list"
  else
    # 关键路径缺失必须可见：静默跳过会产出"看起来成功"却不含凭据/配置的备份，
    # 灾难恢复时才发现已连续多日备份无效
    printf '%s\n' "[aurora-backup] WARN: missing, skipped: $1" >&2
  fi
}

add_path "$state_root/.auth"
add_path "$state_root/.secret"
add_path "$data_root"
add_path "$project_root/backend/main.py"
add_path "$project_root/backend/providers.py"
add_path "$project_root/frontend/src"
add_path "$project_root/frontend/index.html"
add_path "$project_root/frontend/package.json"
add_path "$project_root/frontend/package-lock.json"
add_path "$project_root/compose.yaml"
add_path "$project_root/README.md"
add_path "$project_root/FEATURES.md"
add_path "$project_root/scripts"
add_path "$project_root/tests"
add_path "$project_root/qbit/config"
add_path "$project_root/jellyfin/config"
add_path "$rclone_config"
add_path /root/.config/rclone/rclone.conf
add_path "$rclone_env"
add_path "$aurora_env"
add_path "$rclone_service"
if [ "$rclone_service" != /etc/systemd/system/rclone-rcd.service ]; then
  add_path /etc/systemd/system/rclone-rcd.service
fi
add_path /etc/systemd/system/aurora.service
add_path /etc/systemd/system/aurora.service.d
add_path /etc/systemd/system/aurora-backup.service
add_path /etc/systemd/system/aurora-backup.timer
add_path "$nginx_conf"

tar -czf "$archive" -T "$archive_list"
# 归档里含 qbit/jellyfin 运行中的数据库，热拷可能损坏：创建后立即验证，
# 坏归档留在备份目录里等于"连续 14 天备份全是坏的"这种最坏情况
if ! gzip -t "$archive"; then
  echo "[aurora-backup] ERROR: archive failed gzip integrity check: $archive" >&2
  rm -f -- "$archive"
  exit 1
fi
chmod 600 "$archive"
find "$backup_root" -maxdepth 1 -type f -name 'aurora-config-*.tar.gz' -printf '%T@ %p\n' \
  | sort -nr | awk 'NR > 14 { sub(/^[^ ]+ /, ""); print }' | xargs -r rm -f --
printf '%s\n' "$archive"
