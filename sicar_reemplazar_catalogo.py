#!/usr/bin/env python3
"""
Reemplaza el catalogo visible de SICAR desde un CSV en formato de articulos.

Por defecto corre en modo --dry-run: valida el CSV y genera un resumen sin tocar
la base. Use --apply para crear respaldo, ocultar/desactivar articulos actuales
e insertar/actualizar los servicios del CSV.
"""

from __future__ import annotations

import argparse
import csv
import getpass
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path


EXPECTED_HEADERS = [
    "CLAVE",
    "CLAVE ALTERNA",
    "DESCRIPCION",
    "SERVICIO (S/N)",
    "INV_MIN",
    "INV_MAX",
    "PRECIO COMPRA",
    "PRECIO 1",
    "PRECIO 2",
    "MAYOREO 2",
    "PRECIO 3",
    "MAYOREO 3",
    "PRECIO 4",
    "MAYOREO 4",
    "EXIST.",
    "PESO",
    "CARACTERISTICAS",
    "DEPARTAMENTO",
    "CATEGORIA",
    "RECETA (S/N)",
    "GRANEL (S/N)",
    "IMPUESTO (S/N)",
    "IMP Regular(S/N)",
]


@dataclass(frozen=True)
class ServiceRow:
    clave: str
    clave_alterna: str
    descripcion: str
    inv_min: int
    inv_max: int
    precio_compra: Decimal
    precio1: Decimal
    precio2: Decimal
    mayoreo2: Decimal
    precio3: Decimal
    mayoreo3: Decimal
    precio4: Decimal
    mayoreo4: Decimal
    existencia: Decimal
    peso: Decimal
    caracteristicas: str
    departamento: str
    categoria: str
    receta: int
    granel: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Carga servicios a SICAR ocultando/desactivando el catalogo actual."
    )
    parser.add_argument(
        "--csv",
        default="/Users/america/Downloads/servicios_formato_pos.csv",
        help="Ruta del CSV en formato SICAR.",
    )
    parser.add_argument("--database", default="sicar", help="Base de datos SICAR.")
    parser.add_argument("--mysql-user", default="root", help="Usuario de MySQL.")
    parser.add_argument(
        "--mysql-password",
        default=os.environ.get("MYSQL_PWD"),
        help="Password de MySQL. Si se omite en --apply, se pedira en prompt.",
    )
    parser.add_argument(
        "--mysql-socket",
        default="/private/var/mysql/mysql.sock",
        help="Socket MySQL local.",
    )
    parser.add_argument(
        "--mysql-bin",
        default="/usr/local/mysql/bin/mysql",
        help="Ruta del cliente mysql.",
    )
    parser.add_argument(
        "--mysqldump-bin",
        default="/usr/local/mysql/bin/mysqldump",
        help="Ruta de mysqldump.",
    )
    parser.add_argument(
        "--maintenance-no-password",
        action="store_true",
        help=(
            "Ejecuta --apply sin password de MySQL usando modo mantenimiento local. "
            "Requiere sudo de macOS."
        ),
    )
    parser.add_argument(
        "--mysql-base",
        default="/usr/local/mysql",
        help="Carpeta base de MySQL para modo mantenimiento.",
    )
    parser.add_argument(
        "--launchd-plist",
        default="/Library/LaunchDaemons/com.oracle.oss.mysql.mysqld.plist",
        help="LaunchDaemon de MySQL para detener/reiniciar en modo mantenimiento.",
    )
    parser.add_argument(
        "--maintenance-socket",
        default="/tmp/sicar-mysql-maintenance.sock",
        help="Socket temporal usado en modo mantenimiento.",
    )
    parser.add_argument(
        "--maintenance-pid",
        default="/tmp/sicar-mysql-maintenance.pid",
        help="PID temporal usado en modo mantenimiento.",
    )
    parser.add_argument(
        "--backup-dir",
        default=str(Path.home() / "Documents" / "RespaldoSICAR-Script"),
        help="Carpeta donde se guardara el respaldo SQL.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Valida y resume sin modificar. Es el modo por defecto.",
    )
    mode.add_argument(
        "--apply",
        action="store_true",
        help="Ejecuta respaldo y cambios reales en MySQL.",
    )
    mode.add_argument(
        "--backup-only",
        action="store_true",
        help="Solo crea respaldo SQL del estado actual sin modificar nada.",
    )
    parser.add_argument(
        "--check-db",
        action="store_true",
        help="Conecta a MySQL y muestra conteos actuales de SICAR.",
    )
    return parser.parse_args()


def normalize_header(value: str) -> str:
    return value.replace("\ufeff", "").strip()


def as_bool_flag(value: str, *, field: str, line: int) -> int:
    normalized = value.strip().lower()
    if normalized in {"s", "si", "sí", "true", "1", "y", "yes"}:
        return 1
    if normalized in {"n", "no", "false", "0", ""}:
        return 0
    raise ValueError(f"Linea {line}: valor invalido en {field}: {value!r}")


def as_int(value: str, *, field: str, line: int) -> int:
    try:
        return int(Decimal(value.strip() or "0"))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Linea {line}: valor entero invalido en {field}: {value!r}") from exc


def as_decimal(value: str, *, field: str, line: int) -> Decimal:
    try:
        return Decimal(value.strip() or "0")
    except InvalidOperation as exc:
        raise ValueError(f"Linea {line}: valor numerico invalido en {field}: {value!r}") from exc


def read_services(csv_path: Path) -> tuple[list[ServiceRow], list[str]]:
    warnings: list[str] = []
    if not csv_path.exists():
        raise FileNotFoundError(f"No existe el CSV: {csv_path}")

    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        headers = [normalize_header(h) for h in (reader.fieldnames or [])]
        if headers != EXPECTED_HEADERS:
            raise ValueError(
                "El CSV no tiene los encabezados esperados.\n"
                f"Esperado: {EXPECTED_HEADERS}\n"
                f"Actual:   {headers}"
            )

        rows: list[ServiceRow] = []
        for idx, raw in enumerate(reader, start=2):
            row = {normalize_header(k): (v or "").strip() for k, v in raw.items()}
            clave = row["CLAVE"]
            descripcion = row["DESCRIPCION"]
            departamento = row["DEPARTAMENTO"]
            categoria = row["CATEGORIA"]
            if not clave:
                raise ValueError(f"Linea {idx}: CLAVE vacia")
            if not descripcion:
                raise ValueError(f"Linea {idx}: DESCRIPCION vacia")
            if not departamento:
                raise ValueError(f"Linea {idx}: DEPARTAMENTO vacio")
            if not categoria:
                raise ValueError(f"Linea {idx}: CATEGORIA vacia")
            if as_bool_flag(row["SERVICIO (S/N)"], field="SERVICIO (S/N)", line=idx) != 1:
                raise ValueError(f"Linea {idx}: SERVICIO (S/N) debe ser true/S")

            rows.append(
                ServiceRow(
                    clave=clave,
                    clave_alterna=row["CLAVE ALTERNA"],
                    descripcion=descripcion,
                    inv_min=as_int(row["INV_MIN"], field="INV_MIN", line=idx),
                    inv_max=as_int(row["INV_MAX"], field="INV_MAX", line=idx),
                    precio_compra=as_decimal(row["PRECIO COMPRA"], field="PRECIO COMPRA", line=idx),
                    precio1=as_decimal(row["PRECIO 1"], field="PRECIO 1", line=idx),
                    precio2=as_decimal(row["PRECIO 2"], field="PRECIO 2", line=idx),
                    mayoreo2=as_decimal(row["MAYOREO 2"], field="MAYOREO 2", line=idx),
                    precio3=as_decimal(row["PRECIO 3"], field="PRECIO 3", line=idx),
                    mayoreo3=as_decimal(row["MAYOREO 3"], field="MAYOREO 3", line=idx),
                    precio4=as_decimal(row["PRECIO 4"], field="PRECIO 4", line=idx),
                    mayoreo4=as_decimal(row["MAYOREO 4"], field="MAYOREO 4", line=idx),
                    existencia=as_decimal(row["EXIST."], field="EXIST.", line=idx),
                    peso=as_decimal(row["PESO"], field="PESO", line=idx),
                    caracteristicas=row["CARACTERISTICAS"],
                    departamento=departamento,
                    categoria=categoria,
                    receta=as_bool_flag(row["RECETA (S/N)"], field="RECETA (S/N)", line=idx),
                    granel=as_bool_flag(row["GRANEL (S/N)"], field="GRANEL (S/N)", line=idx),
                )
            )

    clave_counts = Counter(row.clave for row in rows)
    duplicated_claves = sorted(k for k, count in clave_counts.items() if count > 1)
    if duplicated_claves:
        raise ValueError(f"CLAVE duplicada en CSV: {', '.join(duplicated_claves)}")

    alternate_counts = Counter(row.clave_alterna for row in rows if row.clave_alterna)
    duplicated_alternates = sorted(k for k, count in alternate_counts.items() if count > 1)
    if duplicated_alternates:
        warnings.append(
            "CLAVE ALTERNA repetida, se continuara porque SICAR no la marca como unica: "
            + ", ".join(duplicated_alternates)
        )

    if not rows:
        raise ValueError("El CSV no contiene servicios para importar.")
    return rows, warnings


def sql_string(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"


def sql_decimal(value: Decimal) -> str:
    return format(value, "f")


def build_sql(rows: list[ServiceRow]) -> str:
    lines = [
        "SET NAMES utf8mb4;",
        "START TRANSACTION;",
        "UPDATE articulo SET status = 0, oculto = 1;",
    ]

    departments = sorted({row.departamento for row in rows})
    department_category_pairs = sorted({(row.departamento, row.categoria) for row in rows})

    for department in departments:
        dep = sql_string(department)
        lines.extend(
            [
                "INSERT INTO departamento "
                "(nombre, restringido, porcentaje, system, status, imagen, comision) "
                f"VALUES ({dep}, 0, 0.00, 0, 1, NULL, NULL) "
                "ON DUPLICATE KEY UPDATE status = 1;",
            ]
        )

    for department, category in department_category_pairs:
        dep = sql_string(department)
        cat = sql_string(category)
        lines.extend(
            [
                f"SET @dep_id := (SELECT dep_id FROM departamento WHERE nombre = {dep} "
                "ORDER BY IF(system = 0, 0, 1), dep_id LIMIT 1);",
                "INSERT INTO categoria (nombre, system, status, dep_id, imagen, comision) "
                f"SELECT {cat}, 0, 1, @dep_id, NULL, NULL FROM DUAL "
                f"WHERE NOT EXISTS (SELECT 1 FROM categoria WHERE nombre = {cat} AND dep_id = @dep_id);",
                f"UPDATE categoria SET status = 1 WHERE nombre = {cat} AND dep_id = @dep_id;",
            ]
        )

    columns = [
        "clave",
        "claveAlterna",
        "descripcion",
        "servicio",
        "localizacion",
        "invMin",
        "invMax",
        "factor",
        "precioCompra",
        "preCompraProm",
        "margen1",
        "margen2",
        "margen3",
        "margen4",
        "precio1",
        "precio2",
        "precio3",
        "precio4",
        "mayoreo1",
        "mayoreo2",
        "mayoreo3",
        "mayoreo4",
        "existencia",
        "aislado",
        "disponible",
        "caracteristicas",
        "iepsActivo",
        "cuotaIeps",
        "cuentaPredial",
        "lote",
        "receta",
        "granel",
        "tipo",
        "peso",
        "insumo",
        "platillo",
        "favorito",
        "requerirPreparacion",
        "presentacion",
        "presentacionPrecio",
        "pesoAut",
        "claveProdServ",
        "status",
        "unidadCompra",
        "unidadVenta",
        "cat_id",
        "oculto",
        "showEco",
        "etiquetaVenta",
    ]

    for row in rows:
        dep = sql_string(row.departamento)
        cat = sql_string(row.categoria)
        values = [
            sql_string(row.clave),
            sql_string(row.clave_alterna),
            sql_string(row.descripcion),
            "1",
            sql_string(""),
            str(row.inv_min),
            str(row.inv_max),
            "1.000",
            sql_decimal(row.precio_compra),
            sql_decimal(row.precio_compra),
            "0.000000",
            "0.000000",
            "0.000000",
            "0.000000",
            sql_decimal(row.precio1),
            sql_decimal(row.precio2),
            sql_decimal(row.precio3),
            sql_decimal(row.precio4),
            "0.000",
            sql_decimal(row.mayoreo2),
            sql_decimal(row.mayoreo3),
            sql_decimal(row.mayoreo4),
            sql_decimal(row.existencia),
            "0.0000",
            "0.0000",
            sql_string(row.caracteristicas),
            "0",
            "0.0000",
            sql_string(""),
            "0",
            str(row.receta),
            str(row.granel),
            "0",
            sql_decimal(row.peso),
            "0",
            "0",
            "0",
            "0",
            "0",
            "0",
            "0",
            "NULL",
            "1",
            "1",
            "1",
            "@cat_id",
            "0",
            "1",
            "0",
        ]
        update_assignments = [
            "claveAlterna = VALUES(claveAlterna)",
            "descripcion = VALUES(descripcion)",
            "servicio = 1",
            "invMin = VALUES(invMin)",
            "invMax = VALUES(invMax)",
            "precioCompra = VALUES(precioCompra)",
            "preCompraProm = VALUES(preCompraProm)",
            "precio1 = VALUES(precio1)",
            "precio2 = VALUES(precio2)",
            "precio3 = VALUES(precio3)",
            "precio4 = VALUES(precio4)",
            "mayoreo2 = VALUES(mayoreo2)",
            "mayoreo3 = VALUES(mayoreo3)",
            "mayoreo4 = VALUES(mayoreo4)",
            "existencia = VALUES(existencia)",
            "peso = VALUES(peso)",
            "caracteristicas = VALUES(caracteristicas)",
            "receta = VALUES(receta)",
            "granel = VALUES(granel)",
            "cat_id = VALUES(cat_id)",
            "status = 1",
            "oculto = 0",
            "showEco = 1",
        ]
        lines.extend(
            [
                f"SET @cat_id := (SELECT c.cat_id FROM categoria c "
                f"JOIN departamento d ON d.dep_id = c.dep_id "
                f"WHERE c.nombre = {cat} AND d.nombre = {dep} "
                "ORDER BY IF(d.system = 0, 0, 1), c.cat_id LIMIT 1);",
                f"INSERT INTO articulo ({', '.join(columns)}) VALUES ({', '.join(values)}) "
                f"ON DUPLICATE KEY UPDATE {', '.join(update_assignments)};",
            ]
        )

    lines.extend(
        [
            "SELECT COUNT(*) AS articulos_activos FROM articulo WHERE status = 1 AND IFNULL(oculto, 0) = 0;",
            "SELECT COUNT(*) AS servicios_activos FROM articulo WHERE servicio = 1 AND status = 1 AND IFNULL(oculto, 0) = 0;",
            "COMMIT;",
        ]
    )
    return "\n".join(lines) + "\n"


def mysql_env(password: str | None) -> dict[str, str]:
    env = os.environ.copy()
    if password:
        env["MYSQL_PWD"] = password
    return env


def mysql_command(args: argparse.Namespace, *, database: bool = True) -> list[str]:
    command = [
        args.mysql_bin,
        f"--user={args.mysql_user}",
        f"--socket={args.mysql_socket}",
        "--batch",
        "--raw",
    ]
    if database:
        command.append(args.database)
    return command


def mysql_command_for_socket(
    args: argparse.Namespace, socket_path: str, *, database: bool = True
) -> list[str]:
    command = [
        args.mysql_bin,
        f"--user={args.mysql_user}",
        f"--socket={socket_path}",
        "--batch",
        "--raw",
    ]
    if database:
        command.append(args.database)
    return command


def require_tool(path: str, label: str) -> None:
    if Path(path).exists():
        return
    found = shutil.which(path)
    if found:
        return
    raise FileNotFoundError(f"No encontre {label}: {path}")


def prompt_password_if_needed(args: argparse.Namespace) -> str | None:
    if args.mysql_password:
        return args.mysql_password
    if args.apply or args.check_db:
        return getpass.getpass(f"Password MySQL para {args.mysql_user}: ")
    return None


def run_db_check(args: argparse.Namespace, password: str | None) -> None:
    sql = (
        "SELECT COUNT(*) AS articulo_cols "
        "FROM information_schema.columns "
        f"WHERE table_schema = {sql_string(args.database)} AND table_name = 'articulo';"
    )
    result = subprocess.run(
        mysql_command(args, database=False) + ["--execute", sql],
        env=mysql_env(password),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"No se pudo validar MySQL:\n{result.stderr.strip()}")
    if "articulo_cols\n0" in result.stdout:
        raise RuntimeError(f"No encontre tabla articulo en base {args.database!r}.")


def run_db_counts(args: argparse.Namespace, password: str | None) -> None:
    sql = (
        "SELECT "
        "  (SELECT COUNT(*) FROM articulo WHERE status = 1 AND IFNULL(oculto, 0) = 0) AS articulos_activos, "
        "  (SELECT COUNT(*) FROM articulo WHERE servicio = 1 AND status = 1 AND IFNULL(oculto, 0) = 0) AS servicios_activos, "
        "  (SELECT COUNT(*) FROM articulo WHERE status = 0 OR IFNULL(oculto, 0) = 1) AS inactivos_ocultos, "
        "  (SELECT COUNT(*) FROM articulo) AS total_articulos;"
    )
    result = subprocess.run(
        mysql_command(args, database=True) + ["--execute", sql],
        env=mysql_env(password),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"No se pudieron obtener conteos:\n{result.stderr.strip()}")
    for line in result.stdout.strip().splitlines():
        if line and not line.startswith("articulos"):
            parts = line.split("\t")
            if len(parts) == 4:
                print(f"  Articulos activos visibles: {parts[0]}")
                print(f"  Servicios activos visibles: {parts[1]}")
                print(f"  Inactivos/ocultos:          {parts[2]}")
                print(f"  Total articulos:            {parts[3]}")


def run_db_counts_with_socket(args: argparse.Namespace, socket_path: str) -> None:
    sql = (
        "SELECT "
        "  (SELECT COUNT(*) FROM articulo WHERE status = 1 AND IFNULL(oculto, 0) = 0) AS articulos_activos, "
        "  (SELECT COUNT(*) FROM articulo WHERE servicio = 1 AND status = 1 AND IFNULL(oculto, 0) = 0) AS servicios_activos, "
        "  (SELECT COUNT(*) FROM articulo WHERE status = 0 OR IFNULL(oculto, 0) = 1) AS inactivos_ocultos, "
        "  (SELECT COUNT(*) FROM articulo) AS total_articulos;"
    )
    result = subprocess.run(
        mysql_command_for_socket(args, socket_path, database=True) + ["--execute", sql],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"No se pudieron obtener conteos:\n{result.stderr.strip()}")
    for line in result.stdout.strip().splitlines():
        if line and not line.startswith("articulos"):
            parts = line.split("\t")
            if len(parts) == 4:
                print(f"  Articulos activos visibles: {parts[0]}")
                print(f"  Servicios activos visibles: {parts[1]}")
                print(f"  Inactivos/ocultos:          {parts[2]}")
                print(f"  Total articulos:            {parts[3]}")


def create_backup(args: argparse.Namespace, password: str | None) -> Path:
    backup_dir = Path(args.backup_dir).expanduser()
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"backup_sicar_catalogo_{timestamp}.sql"
    command = [
        args.mysqldump_bin,
        f"--user={args.mysql_user}",
        f"--socket={args.mysql_socket}",
        "--single-transaction",
        "--routines",
        "--triggers",
        args.database,
    ]
    with backup_path.open("w", encoding="utf-8") as handle:
        result = subprocess.run(
            command,
            env=mysql_env(password),
            text=True,
            stdout=handle,
            stderr=subprocess.PIPE,
            check=False,
        )
    if result.returncode != 0:
        backup_path.unlink(missing_ok=True)
        raise RuntimeError(f"No se pudo crear respaldo:\n{result.stderr.strip()}")
    return backup_path


def create_backup_with_socket(args: argparse.Namespace, socket_path: str) -> Path:
    backup_dir = Path(args.backup_dir).expanduser()
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"backup_sicar_catalogo_{timestamp}.sql"
    command = [
        args.mysqldump_bin,
        f"--user={args.mysql_user}",
        f"--socket={socket_path}",
        "--skip-lock-tables",
        "--quick",
        "--no-tablespaces",
        args.database,
    ]
    try:
        with backup_path.open("w", encoding="utf-8") as handle:
            result = subprocess.run(
                command,
                text=True,
                stdout=handle,
                stderr=subprocess.PIPE,
                timeout=60,
                check=False,
            )
        if result.returncode != 0:
            raise RuntimeError(
                f"mysqldump fallo (exit {result.returncode}):\n{result.stderr.strip()}"
            )
    except subprocess.TimeoutExpired:
        backup_path.unlink(missing_ok=True)
        raise RuntimeError(
            "mysqldump no respondio en 60 segundos. "
            "Probablemente el servidor MySQL temporal tuvo un problema."
        )
    return backup_path


def apply_sql(args: argparse.Namespace, password: str | None, sql: str) -> str:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".sql", delete=False) as handle:
        handle.write(sql)
        sql_path = Path(handle.name)
    try:
        with sql_path.open("r", encoding="utf-8") as handle:
            result = subprocess.run(
                mysql_command(args),
                env=mysql_env(password),
                text=True,
                stdin=handle,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        if result.returncode != 0:
            raise RuntimeError(f"MySQL rechazo la importacion:\n{result.stderr.strip()}")
        return result.stdout.strip()
    finally:
        sql_path.unlink(missing_ok=True)


def apply_sql_with_socket(args: argparse.Namespace, socket_path: str, sql: str) -> str:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".sql", delete=False) as handle:
        handle.write(sql)
        sql_path = Path(handle.name)
    try:
        with sql_path.open("r", encoding="utf-8") as handle:
            result = subprocess.run(
                mysql_command_for_socket(args, socket_path),
                text=True,
                stdin=handle,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        if result.returncode != 0:
            raise RuntimeError(f"MySQL rechazo la importacion:\n{result.stderr.strip()}")
        return result.stdout.strip()
    finally:
        sql_path.unlink(missing_ok=True)


class MaintenanceMySQL:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.process: subprocess.Popen[str] | None = None

    def __enter__(self) -> str:
        mysql_base = Path(self.args.mysql_base)
        mysqld = mysql_base / "bin" / "mysqld"
        data_dir = mysql_base / "data"
        plugin_dir = mysql_base / "lib" / "plugin"
        launchd_plist = Path(self.args.launchd_plist)
        socket_path = Path(self.args.maintenance_socket)
        pid_path = Path(self.args.maintenance_pid)

        if not mysqld.exists():
            raise FileNotFoundError(f"No encontre mysqld: {mysqld}")
        if not data_dir.exists():
            raise FileNotFoundError(f"No encontre datadir MySQL: {data_dir}")
        if not launchd_plist.exists():
            raise FileNotFoundError(f"No encontre LaunchDaemon: {launchd_plist}")

        print("Se pedira sudo de macOS para usar MySQL en modo mantenimiento.")
        sudo_check = subprocess.run(["sudo", "-v"], check=False)
        if sudo_check.returncode != 0:
            raise RuntimeError("sudo fue rechazado; no puedo entrar a modo mantenimiento.")

        print("Limpiando residuos de ejecuciones anteriores...")
        subprocess.run(["sudo", "rm", "-f", str(socket_path), str(pid_path)], check=False)

        print("Deteniendo MySQL normal...")
        unload = subprocess.run(
            ["sudo", "launchctl", "unload", str(launchd_plist)],
            capture_output=True, text=True, check=False,
        )
        if unload.returncode != 0:
            print(f"  launchctl unload fallo ({unload.returncode}), matando mysqld directamente...")
            pgrep = subprocess.run(
                ["pgrep", "-x", "mysqld"], capture_output=True, text=True, check=False,
            )
            for pid in pgrep.stdout.strip().splitlines():
                pid = pid.strip()
                if pid:
                    subprocess.run(["sudo", "kill", "-9", pid], check=False)
        time.sleep(5)
        still_running = subprocess.run(
            ["pgrep", "-x", "mysqld"], capture_output=True, text=True, check=False,
        )
        if still_running.stdout.strip():
            pids = still_running.stdout.strip().splitlines()
            print(f"  mysqld aun corriendo (PID {', '.join(pids)}), esperando mas...")
            time.sleep(5)
            for pid in pids:
                subprocess.run(["sudo", "kill", "-9", pid], check=False)
            time.sleep(3)
        subprocess.run(["sudo", "rm", "-f", str(socket_path), str(pid_path)], check=False)

        print("Iniciando MySQL temporal sin password y sin red...")
        log_path = Path("/tmp/sicar-mysql-maintenance.log")
        log_handle = log_path.open("w", encoding="utf-8")
        self.process = subprocess.Popen(
            [
                "sudo",
                "-u",
                "_mysql",
                str(mysqld),
                "--user=_mysql",
                f"--basedir={mysql_base}",
                f"--datadir={data_dir}",
                f"--plugin-dir={plugin_dir}",
                "--skip-grant-tables",
                "--skip-networking",
                f"--socket={socket_path}",
                f"--pid-file={pid_path}",
            ],
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            text=True,
        )
        log_handle.close()

        for _ in range(40):
            if socket_path.exists():
                return str(socket_path)
            if self.process.poll() is not None:
                break
            time.sleep(1)

        tail = ""
        if log_path.exists():
            tail = "\n".join(log_path.read_text(errors="replace").splitlines()[-80:])
        raise RuntimeError(f"MySQL temporal no inicio. Log:\n{tail}")

    def __exit__(self, exc_type, exc, traceback) -> None:
        pid_path = Path(self.args.maintenance_pid)
        socket_path = Path(self.args.maintenance_socket)
        subprocess.run(["sudo", "rm", "-f", str(socket_path), str(pid_path)], check=False)
        if self.process and self.process.poll() is None:
            self.process.send_signal(signal.SIGTERM)
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
        print("Reiniciando MySQL normal...")
        subprocess.run(["sudo", "launchctl", "load", self.args.launchd_plist], check=False)
        time.sleep(5)


def print_summary(rows: list[ServiceRow], warnings: list[str]) -> None:
    departments = sorted({row.departamento for row in rows})
    categories = sorted({(row.departamento, row.categoria) for row in rows})
    print(f"Servicios en CSV: {len(rows)}")
    print(f"Departamentos requeridos: {len(departments)}")
    print(f"Categorias requeridas: {len(categories)}")
    print(f"Primer servicio: {rows[0].clave} - {rows[0].descripcion}")
    print(f"Ultimo servicio: {rows[-1].clave} - {rows[-1].descripcion}")
    for warning in warnings:
        print(f"ADVERTENCIA: {warning}")


def main() -> int:
    args = parse_args()
    csv_path = Path(args.csv).expanduser()
    try:
        rows, warnings = read_services(csv_path)
        sql = build_sql(rows)
        print_summary(rows, warnings)

        if args.check_db:
            require_tool(args.mysql_bin, "mysql")
            if args.maintenance_no_password:
                with MaintenanceMySQL(args) as socket_path:
                    print("Estado actual de SICAR (modo mantenimiento):")
                    run_db_counts_with_socket(args, socket_path)
            else:
                password = prompt_password_if_needed(args)
                run_db_check(args, password)
                print("Estado actual de SICAR:")
                run_db_counts(args, password)

        if args.backup_only:
            require_tool(args.mysql_bin, "mysql")
            require_tool(args.mysqldump_bin, "mysqldump")
            if args.maintenance_no_password:
                with MaintenanceMySQL(args) as socket_path:
                    print("Creando respaldo SQL en modo mantenimiento...")
                    backup_path = create_backup_with_socket(args, socket_path)
                    print(f"Respaldo creado: {backup_path}")
            else:
                password = prompt_password_if_needed(args)
                print("Validando conexion MySQL...")
                run_db_check(args, password)
                print("Creando respaldo SQL...")
                backup_path = create_backup(args, password)
                print(f"Respaldo creado: {backup_path}")
            print("Backup-only: no se modifico SICAR.")
            return 0

        if not args.apply:
            print("Dry-run: no se modifico SICAR. Use --apply para ejecutar cambios reales.")
            return 0

        require_tool(args.mysql_bin, "mysql")
        require_tool(args.mysqldump_bin, "mysqldump")
        if args.maintenance_no_password:
            with MaintenanceMySQL(args) as socket_path:
                print("Estado actual de SICAR:")
                run_db_counts_with_socket(args, socket_path)
                print("Creando respaldo SQL en modo mantenimiento...")
                backup_path = create_backup_with_socket(args, socket_path)
                print(f"Respaldo creado: {backup_path}")
                print("Aplicando reemplazo de catalogo visible en modo mantenimiento...")
                output = apply_sql_with_socket(args, socket_path, sql)
                if output:
                    print(output)
            print("Listo. SICAR queda con los servicios del CSV activos y el catalogo anterior oculto.")
            return 0

        password = prompt_password_if_needed(args)
        print("Validando conexion y esquema MySQL...")
        run_db_check(args, password)
        print("Estado actual de SICAR:")
        run_db_counts(args, password)
        print("Creando respaldo SQL...")
        backup_path = create_backup(args, password)
        print(f"Respaldo creado: {backup_path}")
        print("Aplicando reemplazo de catalogo visible...")
        output = apply_sql(args, password, sql)
        if output:
            print(output)
        print("Listo. SICAR queda con los servicios del CSV activos y el catalogo anterior oculto.")
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
