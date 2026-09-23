#!/bin/sh
set -eu

project_root=${AURORA_ROOT:-/opt/aurora}
backup_root=${AURORA_BACKUP_ROOT:-/var/backups/aurora}
state_root=${AURORA_STATE_DIR:-$project_root/backend}
data_root=${AURORA_DATA_DIR:-$project_root/backend/data}
nginx_conf=${AURORA_NGINX_CONF:-/www/server/panel/vhost/nginx/p.lengxi.cc.conf}
rclone_config=${AURORA_RCLONE_CONFIG:-/var/lib/aurora/rclone/rclone.conf}
rclone_env=${AURORA_RCLONE_ENV:-/etc/rclone-rc.env}
aurora_env=${AURORA_ENV_FILE:-/etc/aurora.env}
rclone_service=${AURORA_RCLONE_SERVICE:-/etc/systemd/system/rclone-rc.service}
stamp=$(date +%Y%m%d-%H%M%S)
archive="$backup_root/aurora-config-$stamp.tar.gz"
archive_list=$(mktemp)
trap 'rm -f -- "$archive_list"' EXIT HUP INT TERM

install -d -m 700 "$backup_root"
add_path() {
  if [ -e "$1" ]; then
    printf '%s\n' "$1" >> "$archive_list"
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
chmod 600 "$archive"
find "$backup_root" -maxdepth 1 -type f -name 'aurora-config-*.tar.gz' -printf '%T@ %p\n' \
  | sort -nr | awk 'NR > 14 { sub(/^[^ ]+ /, ""); print }' | xargs -r rm -f --
printf '%s\n' "$archive"
