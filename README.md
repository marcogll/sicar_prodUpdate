# SICAR — Reemplazo de catálogo visible

Reemplaza el catálogo activo de SICAR por los servicios del CSV, ocultando/desactivando los artículos actuales.

**No necesita password de MySQL.** Usa modo mantenimiento (sudo de macOS).

---

## One-liner (vía curl, sin descargar nada)

```bash
bash <(curl -sL https://raw.githubusercontent.com/anomalyco/sicar_prodUpdate/main/sicar_run.sh)
```

Esto descarga scripts y CSV temporalmente, pide sudo, hace respaldo y aplica los cambios. No deja archivos en tu sistema.

---

## Instalación local

```bash
git clone https://github.com/anomalyco/sicar_prodUpdate.git
cd sicar_prodUpdate
```

O descarga los archivos manualmente y ponlos en una misma carpeta.

---

## Archivos

| Archivo | Descripción |
|---|---|
| `servicios_formato_pos.csv` | 57 servicios a cargar |
| `sicar_run.sh` | One-liner para curl (descarga todo y ejecuta) |
| `sicar_tui.py` | Menú interactivo (recomendado para uso local) |
| `sicar_reemplazar_catalogo.py` | Script principal (uso directo con flags) |
| `sicar_reset_mysql_password.sh` | Helper para resetear password MySQL (solo si necesario) |

---

## Modo de uso

### Menú interactivo (recomendado)

```bash
./sicar_tui.py
```

Muestra:

```
  1) Ver estado actual de SICAR
  2) Solo respaldo SQL
  3) Aplicar cambios (respaldar + cargar CSV)
  4) Salir
```

Usa modo mantenimiento — pide sudo, NO password de MySQL.

### Aplicar cambios directo

```bash
./sicar_reemplazar_catalogo.py --csv servicios_formato_pos.csv --apply --maintenance-no-password
```

Flujo completo:
1. Pide **sudo de macOS** (la de tu usuario, no la de MySQL)
2. Detiene MySQL de SICAR
3. Levanta MySQL temporal en modo mantenimiento (sin contraseña, sin red)
4. Muestra estado actual (artículos activos, servicios, etc.)
5. Crea respaldo SQL en `~/Documents/RespaldoSICAR-Script/`
6. Desactiva/oculta el catálogo anterior (`status = 0, oculto = 1`)
7. Inserta o actualiza departamentos, categorías y los 57 servicios del CSV
8. Reinicia MySQL normal

### Solo respaldo (sin modificar)

```bash
./sicar_reemplazar_catalogo.py --csv servicios_formato_pos.csv --backup-only --maintenance-no-password
```

Crea respaldo en `~/Documents/RespaldoSICAR-Script/` y sale sin modificar nada.

### Ver estado actual de SICAR

```bash
./sicar_reemplazar_catalogo.py --csv servicios_formato_pos.csv --check-db --maintenance-no-password
```

Muestra:
```
  Articulos activos visibles: 209
  Servicios activos visibles: 107
  Inactivos/ocultos:          0
  Total articulos:            254
```

### Validar CSV solamente (dry-run, no toca MySQL)

```bash
./sicar_reemplazar_catalogo.py --csv servicios_formato_pos.csv --dry-run
```

### Si sabes la password de MySQL

Puedes omitir `--maintenance-no-password`. El script te pedirá la contraseña:

```bash
./sicar_reemplazar_catalogo.py --csv servicios_formato_pos.csv --apply
```

---

## Si algo sale mal

### Restaurar respaldo

```bash
/usr/local/mysql/bin/mysql --user=root --socket=/private/var/mysql/mysql.sock sicar < ~/Documents/RespaldoSICAR-Script/backup_sicar_catalogo_*.sql
```

### MySQL no arranca después de una ejecución fallida

```bash
sudo launchctl load /Library/LaunchDaemons/com.oracle.oss.mysql.mysqld.plist
```

### Resetear password de MySQL

Si algún día necesitas cambiar la contraseña de root de MySQL:

```bash
./sicar_reset_mysql_password.sh
```

Pide sudo, detiene MySQL, lo levanta sin permisos, cambia la password y lo reinicia.

---

## Flags del script principal

```
--csv PATH                    Ruta del CSV (default: ~/Downloads/...)
--apply                       Ejecutar cambios reales (requiere --backup-dir o default)
--backup-only                 Solo respaldo, no modifica
--dry-run                     Solo validar CSV y resumir (default)
--check-db                    Conectar a MySQL y mostrar conteos
--maintenance-no-password     Usar modo mantenimiento (sudo, sin password MySQL)
--mysql-password PASSWORD     Password MySQL (si no usas maintenance mode)
--mysql-user USER             Usuario MySQL (default: root)
--mysql-socket PATH           Socket MySQL (default: /private/var/mysql/mysql.sock)
--mysql-bin PATH              Cliente mysql (default: /usr/local/mysql/bin/mysql)
--mysqldump-bin PATH          mysqldump (default: /usr/local/mysql/bin/mysqldump)
--mysql-base PATH             Carpeta base MySQL (default: /usr/local/mysql)
--backup-dir PATH             Carpeta respaldo (default: ~/Documents/RespaldoSICAR-Script/)
```

---

## Requisitos

- macOS (probado en Sequoia)
- MySQL instalado en `/usr/local/mysql` (instalación oficial Oracle)
- SICAR con base de datos local
- Usuario con acceso a `sudo`
