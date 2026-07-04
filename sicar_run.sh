#!/usr/bin/env bash
# sicar_run.sh — One-liner: descarga scripts y CSV, reemplaza catálogo SICAR
# Modo de uso:
#   bash <(curl -sL https://raw.githubusercontent.com/anomalyco/sicar_prodUpdate/main/sicar_run.sh)
#
# O localmente:
#   ./sicar_run.sh
set -euo pipefail

BASE_URL="https://raw.githubusercontent.com/anomalyco/sicar_prodUpdate/main"
TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

echo "Descargando scripts e CSV..."
curl -sL "$BASE_URL/sicar_reemplazar_catalogo.py" -o "$TMPDIR/sicar_reemplazar_catalogo.py"
curl -sL "$BASE_URL/servicios_formato_pos.csv" -o "$TMPDIR/servicios_formato_pos.csv"
chmod +x "$TMPDIR/sicar_reemplazar_catalogo.py"

cd "$TMPDIR"
exec ./sicar_reemplazar_catalogo.py \
  --csv servicios_formato_pos.csv \
  --apply \
  --maintenance-no-password
