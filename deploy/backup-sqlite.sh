#!/usr/bin/env bash
# Резервная копия SQLite и .env для tg_mini_app (запуск на Linux/VPS от root или sudo).
#
# Переменные окружения (необязательно):
#   TG_MINI_APP_ROOT          — корень проекта (по умолчанию /srv/tg_mini_app)
#   TG_MINI_APP_DB            — путь к файлу БД (по умолчанию $ROOT/data/app.db)
#   TG_MINI_APP_BACKUP_DIR    — каталог бэкапов (по умолчанию /root/tg_mini_app_backups)
#   TG_MINI_APP_BACKUP_RETAIN_DAYS — удалять app_*.db старше N дней (по умолчанию 14)
#   TG_MINI_APP_BACKUP_STOP_SERVICES=1 — перед копированием остановить API и бота (systemd)
#
# Пример cron (каждый день в 03:15):
#   15 3 * * * TG_MINI_APP_BACKUP_STOP_SERVICES=1 /srv/tg_mini_app/deploy/backup-sqlite.sh >>/var/log/tg_mini_app_backup.log 2>&1

set -euo pipefail

ROOT="${TG_MINI_APP_ROOT:-/srv/tg_mini_app}"
DB_FILE="${TG_MINI_APP_DB:-$ROOT/data/app.db}"
BACKUP_DIR="${TG_MINI_APP_BACKUP_DIR:-/root/tg_mini_app_backups}"
RETAIN_DAYS="${TG_MINI_APP_BACKUP_RETAIN_DAYS:-14}"
STOP="${TG_MINI_APP_BACKUP_STOP_SERVICES:-0}"

mkdir -p "$BACKUP_DIR"
TS=$(date +%Y%m%d_%H%M%S)

if [ ! -f "$DB_FILE" ]; then
  echo "Файл БД не найден: $DB_FILE" >&2
  exit 1
fi

_do_copy() {
  cp "$DB_FILE" "$BACKUP_DIR/app_${TS}.db"
  if [ -f "$ROOT/.env" ]; then
    cp "$ROOT/.env" "$BACKUP_DIR/env_${TS}.bak"
    chmod 600 "$BACKUP_DIR/env_${TS}.bak"
  else
    echo "Предупреждение: нет $ROOT/.env — копия .env пропущена" >&2
  fi
}

if [ "$STOP" = "1" ]; then
  systemctl stop tg-mini-app-api.service tg-mini-app-bot.service || true
  _do_copy
  systemctl start tg-mini-app-api.service tg-mini-app-bot.service || true
else
  _do_copy
fi

find "$BACKUP_DIR" -maxdepth 1 -name 'app_*.db' -type f -mtime "+${RETAIN_DAYS}" -delete || true

echo "OK: $BACKUP_DIR/app_${TS}.db"
