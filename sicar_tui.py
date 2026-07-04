#!/usr/bin/env python3
"""
TUI interactivo para reemplazar el catalogo visible de SICAR.
Ofrece: ver estado, solo respaldo, aplicar cambios, o ambos.
Usa modo mantenimiento (sudo, no password de MySQL).
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from sicar_reemplazar_catalogo import (
    MaintenanceMySQL,
    apply_sql_with_socket,
    build_sql,
    create_backup_with_socket,
    print_summary,
    read_services,
    run_db_counts_with_socket,
)


class Args:
    mysql_bin = "/usr/local/mysql/bin/mysql"
    mysqldump_bin = "/usr/local/mysql/bin/mysqldump"
    mysql_user = "root"
    mysql_base = "/usr/local/mysql"
    launchd_plist = "/Library/LaunchDaemons/com.oracle.oss.mysql.mysqld.plist"
    maintenance_socket = "/tmp/sicar-mysql-maintenance.sock"
    maintenance_pid = "/tmp/sicar-mysql-maintenance.pid"
    database = "sicar"
    backup_dir = str(Path.home() / "Documents" / "RespaldoSICAR-Script")


def menu() -> str:
    print()
    print("=" * 60)
    print("  SICAR — Reemplazo de catalogo visible")
    print("=" * 60)
    print("  1) Ver estado actual de SICAR")
    print("  2) Solo respaldo SQL")
    print("  3) Aplicar cambios (respaldar + cargar CSV)")
    print("  4) Salir")
    print("=" * 60)
    while True:
        choice = input("Opcion [1-4]: ").strip()
        if choice in ("1", "2", "3", "4"):
            return choice
        print("Opcion invalida. Elija 1, 2, 3 o 4.")


def confirm(prompt: str) -> bool:
    while True:
        r = input(f"{prompt} [s/N]: ").strip().lower()
        if r in ("s", "si"):
            return True
        if r in ("", "n", "no"):
            return False


def main() -> int:
    script_dir = Path(__file__).parent
    csv_path = script_dir / "servicios_formato_pos.csv"

    if not csv_path.exists():
        print(f"ERROR: No encontre {csv_path}", file=sys.stderr)
        return 1

    try:
        rows, warnings = read_services(csv_path)
        sql = build_sql(rows)
    except Exception as exc:
        print(f"ERROR leyendo CSV: {exc}", file=sys.stderr)
        return 1

    print_summary(rows, warnings)

    while True:
        try:
            choice = menu()

            if choice == "1":
                with MaintenanceMySQL(Args()) as socket_path:
                    run_db_counts_with_socket(Args(), socket_path)

            elif choice == "2":
                with MaintenanceMySQL(Args()) as socket_path:
                    print("Creando respaldo SQL...")
                    backup_path = create_backup_with_socket(Args(), socket_path)
                    print(f"Respaldo creado: {backup_path}")
                print("Solo respaldo: no se modifico SICAR.")

            elif choice == "3":
                with MaintenanceMySQL(Args()) as socket_path:
                    print("Estado actual de SICAR:")
                    run_db_counts_with_socket(Args(), socket_path)
                    print()
                    if not confirm("Crear respaldo SQL antes de aplicar?"):
                        print("Omitiendo respaldo.")
                    else:
                        print("Creando respaldo SQL...")
                        try:
                            backup_path = create_backup_with_socket(Args(), socket_path)
                            print(f"Respaldo creado: {backup_path}")
                        except Exception as exc:
                            print(f"Respaldo fallo: {exc}")
                            if not confirm("Continuar con la aplicacion sin respaldo?"):
                                print("Cancelado.")
                                continue
                    print("Aplicando reemplazo de catalogo visible...")
                    output = apply_sql_with_socket(Args(), socket_path, sql)
                    if output:
                        print(output)
                print("Listo. SICAR queda con los servicios del CSV activos y el catalogo anterior oculto.")
                return 0

            elif choice == "4":
                print("Saliendo.")
                return 0

        except KeyboardInterrupt:
            print()
            print("Interrumpido.")
            return 1
        except Exception as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            traceback.print_exc()
            print()
            continue

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
