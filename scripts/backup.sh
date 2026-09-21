#!/bin/sh
set -eu

project_root=${AURORA_ROOT:-/opt/aurora}
backup_root=${AURORA_BACKUP_ROOT:-/root/backup/aurora}
state_root=${AURORA_STATE_DIR:-$project_root/backend}
data_root=${AURORA_DATA_DIR:-$project_root/backend/data}
nginx_conf=${AURORA_NGINX_CONF:-/www/server/panel/vhost/nginx/p.lengxi.cc.conf}
stamp=$(date +%Y%m%d-%H%M%S)
archive="$backup_root/aurora-config-$stamp.tar.gz"

install -d -m 700 "$backup_root"
set -- \
  "$state_root/.auth" \
  "$state_root/.secret" \
  "$data_root" \
  "$project_root/backend/main.py" \
  "$project_root/backend/providers.py" \
  "$project_root/frontend/src" \
  "$project_root/frontend/index.html" \
  "$project_root/frontend/package.json" \
  "$project_root/frontend/package-lock.json" \
  "$project_root/compose.yaml" \
  "$project_root/README.md" \
  "$project_root/FEATURES.md" \
  "$project_root/scripts" \
  "$project_root/tests" \
  "$project_root/qbit/config" \
  "$project_root/jellyfin/config" \
  /etc/systemd/system/aurora.service \
  /etc/systemd/system/aurora.service.d \
  /etc/systemd/system/aurora-backup.service \
  /etc/systemd/system/aurora-backup.timer
[ -f "$nginx_conf" ] && set -- "$@" "$nginx_conf"
tar -czf "$archive" "$@"
chmod 600 "$archive"
find "$backup_root" -maxdepth 1 -type f -name 'aurora-config-*.tar.gz' -printf '%T@ %p\n' \
  | sort -nr | awk 'NR > 14 { sub(/^[^ ]+ /, ""); print }' | xargs -r rm -f --
printf '%s\n' "$archive"
