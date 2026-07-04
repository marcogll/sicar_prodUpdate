#!/usr/bin/env bash
set -euo pipefail

MYSQL_BASE="/usr/local/mysql"
MYSQLD="$MYSQL_BASE/bin/mysqld"
MYSQL="$MYSQL_BASE/bin/mysql"
LAUNCHD_PLIST="/Library/LaunchDaemons/com.oracle.oss.mysql.mysqld.plist"
DATA_DIR="$MYSQL_BASE/data"
RESET_SOCKET="/tmp/sicar-mysql-reset.sock"
RESET_PID="/tmp/sicar-mysql-reset.pid"

usage() {
  cat <<'USAGE'
Uso:
  ./sicar_reset_mysql_password.sh
  ./sicar_reset_mysql_password.sh --new-password NUEVA_PASSWORD

Este script resetea la password local de MySQL usado por SICAR.
Requiere sudo de macOS. No requiere conocer la password actual de MySQL.

Despues de correrlo, use:
  /Users/america/sicar_reemplazar_catalogo.py --csv /Users/america/Downloads/servicios_formato_pos.csv --apply

El segundo comando pedira la nueva password de MySQL en prompt oculto.
USAGE
}

NEW_PASSWORD=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --new-password)
      NEW_PASSWORD="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Argumento desconocido: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "$NEW_PASSWORD" ]]; then
  read -rsp "Nueva password para MySQL root: " NEW_PASSWORD
  echo
fi

if [[ "$NEW_PASSWORD" == *"'"* || "$NEW_PASSWORD" == *"\\"* ]]; then
  echo "Por simplicidad, use una password sin comillas simples ni backslash." >&2
  exit 2
fi

if [[ ! -x "$MYSQLD" || ! -x "$MYSQL" ]]; then
  echo "No encontre MySQL en $MYSQL_BASE" >&2
  exit 1
fi

if [[ ! -f "$LAUNCHD_PLIST" ]]; then
  echo "No encontre LaunchDaemon de MySQL: $LAUNCHD_PLIST" >&2
  exit 1
fi

echo "Se pedira la password de administrador de macOS para controlar MySQL."
sudo -v

BACKUP_DIR="$HOME/Documents/RespaldoSICAR-Script/mysql_user_backup_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BACKUP_DIR"

cleanup() {
  if [[ -f "$RESET_PID" ]]; then
    RESET_RUNNING_PID="$(cat "$RESET_PID" 2>/dev/null || true)"
    if [[ -n "${RESET_RUNNING_PID:-}" ]]; then
      sudo kill "$RESET_RUNNING_PID" 2>/dev/null || true
    fi
    sudo rm -f "$RESET_PID" "$RESET_SOCKET" 2>/dev/null || true
  fi
}
trap cleanup EXIT

echo "Deteniendo MySQL de SICAR..."
sudo launchctl unload "$LAUNCHD_PLIST" 2>/dev/null || true
sleep 3

echo "Respaldando tablas internas de usuarios MySQL en: $BACKUP_DIR"
sudo cp -p "$DATA_DIR/mysql/user."* "$BACKUP_DIR/" 2>/dev/null || true

echo "Iniciando MySQL temporal sin tablas de permisos y sin red..."
sudo -u _mysql "$MYSQLD" \
  --user=_mysql \
  --basedir="$MYSQL_BASE" \
  --datadir="$DATA_DIR" \
  --plugin-dir="$MYSQL_BASE/lib/plugin" \
  --skip-grant-tables \
  --skip-networking \
  --socket="$RESET_SOCKET" \
  --pid-file="$RESET_PID" \
  >/tmp/sicar-mysql-reset.log 2>&1 &

for _ in {1..40}; do
  if [[ -S "$RESET_SOCKET" ]]; then
    break
  fi
  sleep 1
done

if [[ ! -S "$RESET_SOCKET" ]]; then
  echo "MySQL temporal no inicio. Log:" >&2
  tail -80 /tmp/sicar-mysql-reset.log >&2 || true
  exit 1
fi

echo "Actualizando password de root..."
"$MYSQL" --user=root --socket="$RESET_SOCKET" mysql <<SQL
UPDATE user SET Password = PASSWORD('$NEW_PASSWORD') WHERE User = 'root';
FLUSH PRIVILEGES;
SQL

echo "Reiniciando MySQL normal..."
cleanup
sleep 2
sudo launchctl load "$LAUNCHD_PLIST"
sleep 5

echo "Probando nueva password..."
MYSQL_PWD="$NEW_PASSWORD" "$MYSQL" --user=root --socket=/private/var/mysql/mysql.sock --batch --raw --execute "SELECT VERSION() AS mysql_version;" >/dev/null

echo "Listo. La password de root de MySQL quedo actualizada."
echo "Respaldo de mysql.user: $BACKUP_DIR"
